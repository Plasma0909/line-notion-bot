import os

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import requests
import re
import time

# ===== LINE設定 =====
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# ===== Google / Notion設定 =====
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

app = Flask(__name__)

# ===== Google Maps → Notion 登録関数 =====
def register_google_maps_url(google_maps_url):
    headers = {"User-Agent": "Mozilla/5.0"}

    # リトライ付きでHTML取得
    for _ in range(3):
        html = requests.get(google_maps_url, headers=headers).text
        if "Google Maps" not in html:
            break
        time.sleep(2)

    # 店名取得
    match = re.search(r'<meta content="([^"]+)" itemprop="name">', html)
    if not match:
        raise Exception("店名を取得できませんでした")

    place_text = match.group(1)

    # Places API検索
    search_url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress"
    }

    payload = {
        "textQuery": place_text,
        "languageCode": "ja"
    }

    for _ in range(3):
        res = requests.post(search_url, json=payload, headers=headers)
        data = res.json()
        if "places" in data:
            break
        time.sleep(2)

    if "places" not in data:
        raise Exception("Places API 検索失敗")

    place = data["places"][0]
    place_id = place["id"]
    place_name = place["displayName"]["text"]

    # 詳細取得
    details_url = f"https://places.googleapis.com/v1/places/{place_id}?languageCode=ja"
    headers["X-Goog-FieldMask"] = "formattedAddress,displayName,regularOpeningHours,primaryType"

    details = requests.get(details_url, headers=headers).json()

    address = details.get("formattedAddress", "情報なし")

    # 営業時間 日本語化
    week_map = {
        "Monday": "月", "Tuesday": "火", "Wednesday": "水",
        "Thursday": "木", "Friday": "金",
        "Saturday": "土", "Sunday": "日",
        "Closed": "定休日"
    }

    jp_lines = []
    closed_days = []

    if "regularOpeningHours" in details:
        for line in details["regularOpeningHours"]["weekdayDescriptions"]:
            for en, jp in week_map.items():
                line = line.replace(en, jp)

            if "定休日" in line:
                closed_days.append(line.split(":")[0])
            else:
                jp_lines.append(line.replace(":", ": "))

    opening_text = "\n".join(jp_lines)
    closed_text = "定休日：" + "・".join(closed_days) if closed_days else "年中無休"

    # カテゴリ自動判定
    category_map = {
        "ramen_restaurant": "ラーメン",
        "restaurant": "レストラン",
        "cafe": "カフェ",
        "hamburger_restaurant": "ハンバーガー"
    }

    primary_type = details.get("primaryType", "restaurant")
    category = category_map.get(primary_type, "その他")

    # Notion登録
    notion_url = "https://api.notion.com/v1/pages"

    notion_headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    notion_payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名前": {"title": [{"text": {"content": place_name}}]},
            "GoogleMaps": {"url": google_maps_url},
            "住所": {"rich_text": [{"text": {"content": address}}]},
            "カテゴリ": {"multi_select": [{"name": category}]},
            "営業時間": {"rich_text": [{"text": {"content": opening_text}}]},
            "営業日": {"rich_text": [{"text": {"content": closed_text}}]},
            "place_id": {"rich_text": [{"text": {"content": place_id}}]}
        }
    }

    res = requests.post(notion_url, json=notion_payload, headers=notion_headers)

    if res.status_code != 200:
        raise Exception("Notion登録失敗: " + res.text)

# ===== LINE Webhook =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    if "maps.app.goo.gl" in text or "google.com/maps" in text:
        try:
            register_google_maps_url(text)
            reply = "📍Notionに登録しました！"
        except Exception as e:
            reply = f"⚠️ エラー: {str(e)}"
    else:
        reply = "Google MapsのURLを送ってください"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    app.run(port=5000)