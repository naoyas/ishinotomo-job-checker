# job_checker.py

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
import os

# === Slack通知関数 ===
def notify_slack(message, webhook_url):
    if not webhook_url:
        print("Slack Webhook URLが設定されていません")
        return
    payload = {"text": message}
    res = requests.post(webhook_url, json=payload)
    if res.status_code != 200:
        print(f"Slack通知エラー: {res.status_code}, {res.text}")

# === dRサイト ===
def fetch_dr_jobs(target_date):
    URL = "https://ishinotomo-tensyoku.com/parttime/subject/%E7%94%A3%E6%A5%AD%E5%8C%BB/"
    JST = timezone(timedelta(hours=9))
    res = requests.get(URL)
    soup = BeautifulSoup(res.text, "html.parser")
    jobs = soup.select("li.result_list_content.parttime")

    new_jobs = []
    for job in jobs:
        title = job.select_one("h2.title_type_2").get_text(strip=True)
        update_text = job.select_one("p.update_date").get_text(strip=True)
        update_time = datetime.strptime(update_text.split("　")[0], "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST)

        if update_time.date() == target_date.date():
            url = job.select_one("a.link_recruit_info")["href"]

            details = {}
            for dl in job.select("div.offer_info_container dl"):
                dt = dl.select_one("dt").get_text(strip=True)
                dd = dl.select_one("dd").get_text(strip=True)
                details[dt] = dd

            new_jobs.append({
                "title": title,
                "url": url,
                "updated": update_time.strftime("%Y-%m-%d %H:%M:%S"),
                "details": details
            })
    return new_jobs

# === マイナビDOCTOR ===
def fetch_mynavi_jobs(target_date):
    url = "https://doctor.mynavi.jp/search/parttime/result/feature_div_cd/02gb/"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")

    jobs = soup.find_all("article", class_="job-card")
    new_jobs = []

    for job in jobs:
        title_tag = job.find("h2", class_="job-title")
        title = title_tag.get_text(strip=True) if title_tag else "タイトル不明"

        # 「産業医」を含むタイトルのみ
        if "産業医" not in title:
            continue

        link_tag = title_tag.find("a") if title_tag else None
        link = "https://doctor.mynavi.jp" + link_tag["href"] if link_tag else None

        loc_dd = job.find("dt", string="勤務地")
        location = loc_dd.find_next("dd").get_text(strip=True) if loc_dd else "勤務地不明"

        update_tag = job.find("div", class_="job-number")
        update_text = update_tag.get_text(strip=True) if update_tag else ""
        update_date = None
        if "求人更新日" in update_text:
            try:
                update_str = update_text.split("求人更新日")[-1].replace(":", "").replace("：", "").strip()
                update_str = update_str.replace("求人No.", "").strip()
                update_str = update_str.replace("\u3000", " ").replace("\xa0", " ")
                update_str = update_str.split()[0]
                update_date = datetime.strptime(update_str, "%Y/%m/%d").date()
            except:
                pass

        job_no = ""
        if update_tag and "求人No." in update_text:
            try:
                job_no = update_text.split("求人No.")[-1].strip(" :：")
            except:
                pass

        if update_date and update_date == target_date.date():
            new_jobs.append({
                "title": title,
                "location": location,
                "update_date": update_date,
                "job_no": job_no,
                "url": link
            })
    return new_jobs

# === Doctor Agent ===
def fetch_doctor_agent_jobs(target_date):
    url = "https://www.doctor-agent.com/part-time/result?t=0&w2=4&x=1"
    res = requests.get(url)
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")

    jobs = []
    for li in soup.select("div.jobOfferDetailContent ul li._content"):
        title_tag = li.select_one("h3._title a")
        title = title_tag.get_text(strip=True) if title_tag else "タイトル不明"

        info_text = li.select_one("p.text-size-smaller.text-color-pale")
        update_date, job_id = None, None
        if info_text:
            match_date = re.search(r"掲載更新日\s*:\s*(\d{4}年\d{2}月\d{2}日)", info_text.text)
            if match_date:
                update_date = datetime.strptime(match_date.group(1), "%Y年%m月%d日").date()
            match_id = re.search(r"案件番号\s*:\s*([A-Za-z0-9\-]+)", info_text.text)
            if match_id:
                job_id = match_id.group(1)

        detail_url = f"https://www.doctor-agent.com/part-time/Detail/{job_id}" if job_id else None

        if update_date and update_date == target_date.date():
            jobs.append({
                "title": title,
                "url": detail_url,
                "update_date": update_date,
                "job_id": job_id
            })
    return jobs

# === メイン処理 ===
if __name__ == "__main__":
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    # 前日の日付を基準に
    baseline = now - timedelta(days=1)

    SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

    dr_jobs = fetch_dr_jobs(baseline)
    mynavi_jobs = fetch_mynavi_jobs(baseline)
    agent_jobs = fetch_doctor_agent_jobs(baseline)

    all_jobs = [
        ("dRサイト", dr_jobs),
        ("マイナビDOCTOR", mynavi_jobs),
        ("Doctor Agent", agent_jobs)
    ]

    for site_name, jobs in all_jobs:
        message = f"=== {site_name} ===\n"
        if jobs:
            for j in jobs:
                message += f"タイトル: {j.get('title')}\n"
                message += f"URL: {j.get('url')}\n"
                message += f"更新日: {j.get('updated') or j.get('update_date')}\n"
                if "details" in j:
                    for k, v in j["details"].items():
                        message += f"{k}: {v}\n"
                message += "-"*30 + "\n"
        else:
            message += "新着求人はありませんでした。\n"

        print(message)
        notify_slack(message, SLACK_WEBHOOK_URL)
