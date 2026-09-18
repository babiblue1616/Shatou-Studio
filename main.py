# -*- coding: utf-8 -*-
"""
微博关键词监控 -> 微信推送
功能：
1. 抓取指定微博博主的最新微博
2. 检测内容是否包含指定运动员关键词
3. 命中且未推送过的，调用 WxPusher 推送给所有订阅者
4. 用一个本地 JSON 文件记录"已推送过的微博ID"，避免重复推送

注意：
- 需要在 GitHub Actions 的 Secrets 中配置：
  WEIBO_COOKIE      你的微博登录Cookie
  WXPUSHER_APP_TOKEN WxPusher的appToken
  WXPUSHER_UIDS      逗号分隔的接收人UID列表（可选，如果用主题推送则不需要）
  WXPUSHER_TOPIC_ID  WxPusher主题ID（推荐用主题，大家自行订阅）
"""

import os
import re
import json
import time
import requests

# ========== 配置区 ==========
WEIBO_UID = "7360795486"  # 目标博主UID
KEYWORDS = ["孙颖莎", "王楚钦"]  # 监控关键词，可自行增减

# 微博移动端接口，比PC端更轻量、更容易抓
WEIBO_API_URL = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={WEIBO_UID}&containerid=107603{WEIBO_UID}"

WXPUSHER_API_URL = "https://wxpusher.zjiecode.com/api/send/message"

SENT_RECORD_FILE = "sent_ids.json"  # 记录已推送过的微博ID


def load_sent_ids():
    """读取已推送过的微博ID列表"""
    if os.path.exists(SENT_RECORD_FILE):
        with open(SENT_RECORD_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent_ids(ids):
    """保存已推送过的微博ID列表，只保留最近200条防止文件无限增长"""
    ids_list = list(ids)[-200:]
    with open(SENT_RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_list, f, ensure_ascii=False)


def fetch_weibo_list():
    """抓取该博主最新微博列表"""
    cookie = os.environ.get("WEIBO_COOKIE", "")
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        "Cookie": cookie,
        "Referer": f"https://m.weibo.cn/u/{WEIBO_UID}",
    }
    resp = requests.get(WEIBO_API_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("ok") != 1:
        raise RuntimeError(f"微博接口返回异常，可能是Cookie失效: {data}")

    cards = data.get("data", {}).get("cards", [])
    posts = []
    for card in cards:
        # card_type == 9 是普通微博卡片
        if card.get("card_type") == 9:
            mblog = card.get("mblog", {})
            if mblog:
                posts.append(mblog)
    return posts


def clean_text(html_text):
    """去掉微博正文里的html标签"""
    text = re.sub(r"<[^>]+>", "", html_text or "")
    text = text.replace("&nbsp;", " ")
    return text.strip()


def extract_images(mblog):
    """提取微博图片URL列表"""
    pics = mblog.get("pics", [])
    urls = []
    for p in pics:
        # 优先用大图
        url = p.get("large", {}).get("url") or p.get("url")
        if url:
            urls.append(url)
    return urls


def build_weibo_link(mblog):
    """拼出微博原文链接"""
    uid = mblog.get("user", {}).get("id", WEIBO_UID)
    bid = mblog.get("bid", "")
    return f"https://m.weibo.cn/detail/{mblog.get('id')}" if not bid else f"https://m.weibo.cn/{uid}/{bid}"


def send_wxpusher(title, content_summary, link, image_urls):
    """
    调用WxPusher发送消息
    使用 contentType=3 (markdown)，可以把图片和链接都放进去，体验更像服务通知
    """
    app_token = os.environ.get("WXPUSHER_APP_TOKEN", "")
    topic_id = os.environ.get("WXPUSHER_TOPIC_ID", "")
    uids_env = os.environ.get("WXPUSHER_UIDS", "")

    # 拼markdown内容
    md_lines = [f"### {title}", "", content_summary, ""]
    for img in image_urls[:3]:  # 最多放3张图，避免消息过长
        md_lines.append(f"![]({img})")
    md_lines.append("")
    md_lines.append(f"[查看原微博]({link})")
    content_md = "\n".join(md_lines)

    payload = {
        "appToken": app_token,
        "content": content_md,
        "summary": title[:20],  # 消息列表里显示的摘要
        "contentType": 3,  # 3 = markdown
        "topicIds": [int(topic_id)] if topic_id else [],
        "uids": [u.strip() for u in uids_env.split(",") if u.strip()],
    }

    resp = requests.post(WXPUSHER_API_URL, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 1000:
        print(f"[警告] WxPusher推送失败: {result}")
    else:
        print(f"[成功] 已推送: {title}")


def main():
    sent_ids = load_sent_ids()

    try:
        posts = fetch_weibo_list()
    except Exception as e:
        print(f"[错误] 抓取微博失败: {e}")
        return

    new_sent_ids = set(sent_ids)
    hit_count = 0

    # 倒序处理，先推旧的再推新的，保证微信里消息顺序正常
    for mblog in reversed(posts):
        weibo_id = str(mblog.get("id"))
        if weibo_id in sent_ids:
            continue  # 已经推送过，跳过

        raw_text = mblog.get("longText", {}).get("longTextContent") or mblog.get("text", "")
        text = clean_text(raw_text)

        # 关键词检测
        matched_keywords = [kw for kw in KEYWORDS if kw in text]
        if not matched_keywords:
            new_sent_ids.add(weibo_id)  # 没命中也记录，避免下次重复判断
            continue

        # 命中关键词，准备推送
        title = f"乒乓球资讯：{'/'.join(matched_keywords)}"
        link = build_weibo_link(mblog)
        images = extract_images(mblog)

        send_wxpusher(title, text, link, images)
        new_sent_ids.add(weibo_id)
        hit_count += 1

        time.sleep(1)  # 避免推送过快

    save_sent_ids(new_sent_ids)
    print(f"本次检查完成，新增推送 {hit_count} 条。")


if __name__ == "__main__":
    main()
