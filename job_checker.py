# job_checker.py

import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# === 共通: タイムゾーン/HTTP設定 ===
JST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
DEFAULT_TIMEOUT = 15

def _get(url, *, timeout=DEFAULT_TIMEOUT, headers=HEADERS, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            return requests.get(url, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_err = e
    raise last_err if last_err else RuntimeError("HTTP request failed")

# === Slack通知関数 ===
def notify_slack(message, webhook_url):
    if not webhook_url:
        print("Slack Webhook URLが設定されていません")
        return
    payload = {"text": message}
    try:
        res = requests.post(webhook_url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"Slack通知エラー: {res.status_code}, {res.text}")
    except requests.exceptions.RequestException as e:
        print(f"Slack通知例外: {e}")

# -----------------------
# JobMedley: JSON配列を安全に抽出する関数
# -----------------------
def extract_json_array(text):
    # "jmJobOffers":[ ... ] を手動でブラケットバランスで抽出
    key = '"jmJobOffers":['
    start = text.find(key)
    if start == -1:
        return None
    start += len('"jmJobOffers":')

    bracket_count = 0
    result = ""
    in_array = False

    for ch in text[start:]:
        result += ch
        if ch == '[':
            bracket_count += 1
            in_array = True
        elif ch == ']':
            bracket_count -= 1
            if bracket_count == 0 and in_array:
                break

    result = result.strip()
    if result.startswith('[') and result.endswith(']'):
        return result
    return None

# -----------------------
# JobMedley求人ページから求人データを取得（フォーマット統一）
# -----------------------
def fetch_job_medley_jobs(target_date):
    """
    取得元: https://job-medley.com/phn/feature552/?order=2
    返却フォーマット（他サイトと統一）:
    {
        "title": str,
        "url": str | None,
        "updated": "YYYY-MM-DD HH:MM:SS",
        "details": {
            "施設名": str | None,
            "住所": str | None,
            "給与": str | None
        }
    }
    """
    url = "https://job-medley.com/phn/feature552/?order=2"

    try:
        res = _get(url)
    except Exception as e:
        print(f"JobMedley接続エラー: {e}")
        return []

    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")

    jm_data = None
    for tag in soup.find_all("script"):
        if '"jmJobOffers":' in (tag.text or ""):
            jm_json_text = extract_json_array(tag.text)
            if jm_json_text:
                try:
                    jm_data = json.loads(jm_json_text)
                except json.JSONDecodeError as e:
                    print(f"JobMedley JSONデコードエラー: {e}")
                break

    if not jm_data:
        print("JobMedley: jmJobOffersデータが見つかりませんでした。")
        return []

    results = []
    for job in jm_data:
        # 更新日時の解釈
        updated_raw = job.get("updatedAt") or job.get("updated_at")
        if not updated_raw:
            continue
        # ISO8601のZを+09:00へ(サイトはUTCの場合が多いためJSTに寄せる)
        try:
            updated_dt = datetime.fromisoformat(updated_raw.replace("Z", "+00:00")).astimezone(JST)
        except Exception:
            # フォールバック: 数パターンを試みる
            try:
                updated_dt = datetime.strptime(updated_raw.split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(JST)
            except Exception:
                continue

        # ベースコードと同様、日付一致で判定
        if updated_dt.date() != target_date.date():
            continue

        title = job.get("jobOfferCardTitle") or job.get("title") or "タイトル不明"

        # URLは項目名が変わりやすいので候補を順にチェック
        url_candidate = (
            job.get("jobOfferCardLink")
            or job.get("link")
            or job.get("url")
        )

        # 詳細を details に格納（既存フォーマットに合わせる）
        facility = (job.get("facility") or {}).get("name")
        address = (job.get("facility") or {}).get("addressEtc") or (job.get("facility") or {}).get("address")
        salary_list = job.get("jobOfferCardSalaryList") or []
        salary = " / ".join(salary_list) if isinstance(salary_list, list) else salary_list

        results.append({
            "title": title,
            "url": url_candidate,
            "updated": updated_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "details": {
                "施設名": facility,
                "住所": address,
                "給与": salary
            }
        })
    return results

# === dRサイト ===
def fetch_dr_jobs(target_date):
    URL = "https://ishinotomo-tensyoku.com/parttime/subject/%E7%94%A3%E6%A5%AD%E5%8C%BB/"
    try:
        res = _get(URL)
    except Exception as e:
        print(f"dRサイト接続エラー: {e}")
        return []
    soup = BeautifulSoup(res.text, "html.parser")
    jobs = soup.select("li.result_list_content.parttime")

    new_jobs = []
    for job in jobs:
        title_tag = job.select_one("h2.title_type_2")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)

        update_tag = job.select_one("p.update_date")
        if not update_tag:
            continue
        update_text = update_tag.get_text(strip=True)

        try:
            update_time = datetime.strptime(update_text.split("　")[0], "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST)
        except Exception:
            continue

        if update_time.date() == target_date.date():
            url_tag = job.select_one("a.link_recruit_info")
            url = url_tag["href"] if url_tag and url_tag.has_attr("href") else None

            details = {}
            for dl in job.select("div.offer_info_container dl"):
                dt = (dl.select_one("dt") or "").get_text(strip=True) if dl.select_one("dt") else None
                dd = (dl.select_one("dd") or "").get_text(strip=True) if dl.select_one("dd") else None
                if dt:
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
    try:
        response = _get(url)
    except Exception as e:
        print(f"マイナビDOCTOR接続エラー: {e}")
        return []
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
        link = "https://doctor.mynavi.jp" + link_tag["href"] if link_tag and link_tag.has_attr("href") else None

        loc_dd = job.find("dt", string="勤務地")
        location = loc_dd.find_next("dd").get_text(strip=True) if loc_dd else None

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
            except Exception:
                pass

        job_no = ""
        if update_tag and "求人No." in update_text:
            try:
                job_no = update_text.split("求人No.")[-1].strip(" :：")
            except Exception:
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
    try:
        res = _get(url)
    except Exception as e:
        print(f"Doctor Agent接続エラー: {e}")
        return []
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")

    jobs = []
    for li in soup.select("div.jobOfferDetailContent ul li._content"):
        title_tag = li.select_one("h3._title a")
        title = title_tag.get_text(strip=True) if title_tag else "タイトル不明"

        info_text = li.select_one("p.text-size-smaller.text-color-pale")
        update_date, job_id = None, None
        if info_text:
            m_date = re.search(r"掲載更新日\s*:\s*(\d{4}年\d{2}月\d{2}日)", info_text.text)
            if m_date:
                try:
                    update_date = datetime.strptime(m_date.group(1), "%Y年%m月%d日").date()
                except Exception:
                    update_date = None
            m_id = re.search(r"案件番号\s*:\s*([A-Za-z0-9\-]+)", info_text.text)
            if m_id:
                job_id = m_id.group(1)

        detail_url = f"https://www.doctor-agent.com/part-time/Detail/{job_id}" if job_id else None

        if update_date and update_date == target_date.date():
            jobs.append({
                "title": title,
                "url": detail_url,
                "update_date": update_date,
                "job_id": job_id
            })
    return jobs

# === マイナビ看護師（産業保健師） ===
def fetch_mynavi_nurse_jobs(target_date):
    url = "https://kango.mynavi.jp/r/wk_0401/"
    try:
        res = _get(url)
    except Exception as e:
        print(f"マイナビ看護師接続エラー: {e}")
        return []
    res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")

    jobs = []
    for card in soup.select("div.job-card"):
        corp = card.select_one("p.corporate-name")
        corp_name = corp.get_text(strip=True) if corp else "企業名不明"

        title_tag = card.select_one("h2.job-name")
        title = title_tag.get_text(strip=True) if title_tag else "タイトル不明"

        update_li = card.select_one("li.update_time")
        update_date = None
        if update_li:
            try:
                update_str = update_li.get_text(strip=True).replace("更新日:", "").replace("更新日：", "").strip()
                try:
                    update_date = datetime.strptime(update_str, "%Y年%m月%d日").date()
                except Exception:
                    update_date = datetime.strptime(update_str, "%Y-%m-%d").date()
            except Exception:
                update_date = None

        job_no_li = card.select_one("li.job_number")
        job_no = job_no_li.get_text(strip=True).replace("求人番号:", "").replace("求人番号：", "").strip() if job_no_li else ""

        link_tag = card.select_one("a.link-area")
        link = "https://kango.mynavi.jp" + link_tag["href"] if link_tag and link_tag.has_attr("href") else None

        if update_date and update_date == target_date.date():
            jobs.append({
                "corp": corp_name,
                "title": title,
                "job_no": job_no,
                "url": link,
                "update_date": update_date
            })
    return jobs


# === メイン処理 ===
if __name__ == "__main__":
    now = datetime.now(JST)
    # 前日の日付を基準に
    baseline = now - timedelta(days=1)

    SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

    dr_jobs = fetch_dr_jobs(baseline)
    mynavi_jobs = fetch_mynavi_jobs(baseline)
    agent_jobs = fetch_doctor_agent_jobs(baseline)
    mynavi_nurse_jobs = fetch_mynavi_nurse_jobs(baseline)
    jobmedley_jobs = fetch_job_medley_jobs(baseline)

    all_jobs = [
        ("dRサイト", dr_jobs),
        ("マイナビDOCTOR", mynavi_jobs),
        ("Doctor Agent", agent_jobs),
        ("マイナビ看護師", mynavi_nurse_jobs),
        ("JobMedley（産業保健師）", jobmedley_jobs),
    ]

    for site_name, jobs in all_jobs:
        message = f"=== {site_name} ===\n"
        if jobs:
            for j in jobs:
                message += f"タイトル: {j.get('title')}\n"
                if j.get("corp"):
                    message += f"企業名: {j.get('corp')}\n"
                if j.get("job_no"):
                    message += f"求人番号: {j.get('job_no')}\n"
                if j.get("job_id"):
                    message += f"案件番号: {j.get('job_id')}\n"
                if j.get("location"):
                    message += f"勤務地: {j.get('location')}\n"
                message += f"URL: {j.get('url')}\n"
                message += f"更新日: {j.get('update_date') or j.get('updated')}\n"
                if "details" in j and isinstance(j["details"], dict):
                    for k, v in j["details"].items():
                        message += f"{k}: {v}\n"
                message += "-" * 30 + "\n"
        else:
            message += "新着求人はありませんでした。\n"

        print(message)
        notify_slack(message, SLACK_WEBHOOK_URL)
