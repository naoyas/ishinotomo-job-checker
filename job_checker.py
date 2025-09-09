import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import json
import os

URL = "https://ishinotomo-tensyoku.com/parttime/subject/%E7%94%A3%E6%A5%AD%E5%8C%BB/"
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]  # GitHub Secrets から注入

JST = timezone(timedelta(hours=9))
now = datetime.now(JST)
yesterday_13 = (now - timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0)

print(f"チェック基準時刻: {yesterday_13.isoformat()}")

# リクエスト（軽い偽装＋タイムアウト）
headers = {
    "User-Agent": "Mozilla/5.0 (JobChecker/1.0; +https://github.com/)",
    "Accept-Language": "ja-JP,ja;q=0.9"
}
res = requests.get(URL, headers=headers, timeout=20)
res.raise_for_status()

soup = BeautifulSoup(res.text, "html.parser")
jobs = soup.select("li.result_list_content.parttime")

new_jobs = []
for job in jobs:
    title_el = job.select_one("h2.title_type_2")
    date_el = job.select_one("p.update_date")
    link_el = job.select_one("a.link_recruit_info")

    if not (title_el and date_el and link_el):
        continue

    title = title_el.get_text(strip=True)
    update_text = date_el.get_text(strip=True)
    # 例: "2025/09/08 13:02:14　ID:69406" → 左側だけ取り出す
    update_str = update_text.split("　")[0].strip()
    update_time = datetime.strptime(update_str, "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST)

    if update_time > yesterday_13:
        url = link_el["href"]

        # dl配下のフル情報収集（施設形態 / 所在地 / 募集科目 / 勤務時間 など）
        details = {}
        for dl in job.select("div.offer_info_container dl"):
            dt = dl.select_one("dt")
            dd = dl.select_one("dd")
            if not (dt and dd):
                continue
            k = dt.get_text(strip=True)
            v = dd.get_text(strip=True)
            details[k] = v

        new_jobs.append({
            "title": title,
            "url": url,
            "updated": update_time.strftime("%Y-%m-%d %H:%M:%S"),
            "details": details
        })

def post_to_slack(items):
    if not items:
        print("新しい求人はありません。")
        return

    text = "*新しい求人が見つかりました！*\n"
    for j in items:
        text += f"\n• *{j['title']}*\n  更新日時: {j['updated']}\n  URL: {j['url']}\n"
        for k, v in j["details"].items():
            text += f"  {k}: {v}\n"

    payload = {"text": text}
    r = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=20)
    print(f"Slack送信結果: {r.status_code} {r.text[:200]}")

post_to_slack(new_jobs)
