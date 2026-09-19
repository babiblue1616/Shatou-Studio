# -*- coding: utf-8 -*-
import os
import re
import json
import time
import requests

WEIBO_UID = "7360795486"
KEYWORDS = ["孙颖莎", "王楚钦"]

WEIBO_API_URL = f"https://weibo.com/ajax/statuses/mymblog?uid={WEIBO_UID}&page=1&feature=0"
PUSHPLUS_API_URL = "https://www.pushplus.plus/send"
SENT_RECORD_FILE = "sent_ids.json"


def load_sent_ids():
    if os.path.exists(SENT_RECORD_FILE):
        with open(SENT_RECORD_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent_ids(ids):
    ids_list = list(ids)[-200:]
    with open(SENT_RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_list, f, ensure_ascii=False)


def fetch_weibo_list():
    cookie = os.environ.get("WEIBO_COOKIE", "")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Cookie": cookie,
        "Referer": f"https://weibo.com/u/{WEIBO_UID}",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = requests.get(WEIBO_API_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    posts = data.get("data", {}).get("list", [])
    if not posts:
        print(f"[调试] 接口返回内容: {str(data)[:200]}")
    return posts


def clean_text(html_text):
    text = re.sub(r"<[^>]+>", "", html_text or "")
    text = text.replace("&nbsp;", " ")
    return text.strip()


def extract_images(mblog):
    pics = mblog.get("pic_infos", {})
    urls = []
    for pic_id, pic_data in pics.items():
        url = pic_data.get("large", {}).get("url") or pic_data.get("original", {}).get("url")
        if url:
            urls.append(url)
    return urls


def build_weibo_link(mblog):
    bid = mblog.get("bid", "")
    mid = mblog.get("id", "")
    return f"https://weibo.com/{WEIBO_UID}/{bid}" if bid else f"https://weibo.com/detail/{mid}"


def send_pushplus(title, text, link, image_urls):
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    topic = os.environ.get("PUSHPLUS_TOPIC", "")
    html_parts = [f"<p>{text}</p>"]
    for img in image_urls[:3]:
        html_parts.append(f'<img src="{img}" style="max-width:100%"/>')
    html_parts.append(f'<p><a href="{link}">查看原微博</a></p>')
    content = "".join(html_parts)
    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "html",
    }
    if topic:
        payload["topic"] = topic
    resp = requests.post(PUSHPLUS_API_URL, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 200:
        print(f"[警告] PushPlus推送失败: {result}")
    else:
        print(f"[成功] 已推送: {title}")


def main():
    sent_ids = load_sent_ids()
    try:
        posts = fetch_weibo_list()
    except Exception as e:
        print(f"[错误] 抓取微博失败: {e}")
        return
    print(f"[调试] 抓到 {len(posts)} 条微博")
    new_sent_ids = set(sent_ids)
    hit_count = 0
    for mblog in reversed(posts):
        weibo_id = str(mblog.get("id"))
        if weibo_id in sent_ids:
            continue
        raw_text = mblog.get("text_raw") or mblog.get("text", "")
        text = clean_text(raw_text)
        matched_keywords = [kw for kw in KEYWORDS if kw in text]
        if not matched_keywords:
            new_sent_ids.add(weibo_id)
            continue
        title = f"乒乓球资讯：{'/'.join(matched_keywords)}"
        link = build_weibo_link(mblog)
        images = extract_images(mblog)
        send_pushplus(title, text, link, images)
        new_sent_ids.add(weibo_id)
        hit_count += 1
        time.sleep(1)
    save_sent_ids(new_sent_ids)
    print(f"本次检查完成，新增推送 {hit_count} 条。")


if __name__ == "__main__":
    main()
