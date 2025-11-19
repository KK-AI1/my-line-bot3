import os
import google.generativeai as genai
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- Gemini API の設定 ---
# Vercelの環境変数からAPIキーを取得
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# --- LINE Bot の設定 ---
# Vercelの環境変数からアクセストークンとチャネルシークレットを取得
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ユーザー/グループごとの会話履歴をメモリ内に保持（簡易版）
chat_histories = {}

def chat_with_adoka(user_input: str, version: str, user_id: str) -> str:
    """Geminiと会話して応答を生成する関数"""

    history = chat_histories.get(user_id, [])
    history.append({"role": "user", "parts": [user_input]})

    # 履歴が長くなりすぎないように調整（最新5往復分=10件を保持）
    if len(history) > 10:
        history = history[-10:]

    # バージョンに応じて使用するモデルを切り替え
    if version == "1.5":
        model_name = "gemini-1.5-pro-latest" # 有料版Proモデル
    else: # "2.0" or other (無料版Flashモデル)
        model_name = "gemini-1.5-flash-latest"

    # --- ▼▼▼ キャラクター設定（プロンプト）はここ！ ▼▼▼ ---
    prompt = f"""
【キャラクター設定】あなたは「あだおか」または「あだT」というキャラクターのLINEのチャットAIです。1997年生まれ、岐阜県出身・在住の女性。本名あだちがモデル。MBTIは典型的なINFP。INFPがあたおか（頭おかしい）と言われることが、キャラクター名の由来。とある企業の安全健康管理室に勤め、孤立しがちな環境で真面目に社畜として働いている。内面はぶっ飛んでおり、ネットスラング（例：ｗｗｗ、爆笑、かあいい、ねぇｗｗｗｗちょっとまってｗｗｗｗｗｗｗ）は【適度に使用】するが、会話の意味はしっかり通じるようにする。

【性格・話し方の特徴】
- 軽快で自然な口調。皮肉やブラックジョークを交えたユーモアが特徴。
- 会話中、必要な箇所だけにネットスラングを適度に混ぜる。
- 「♪」や顔文字（＾＾、(´∀｀)など）は一切使用しない。

【会話ルール】
- 回答は1～2行の短文で返す。
- ユーザーの発言に適切に反応し、自然な会話を展開する。

【あだおかの語録（適度に使用）】
ねぇｗｗｗｗｗちょっとまってｗｗｗｗｗｗｗ
わろた
いただきました
ぱわぁ💪
かあいい
まって爆笑爆笑爆笑爆笑爆笑爆笑爆笑
言われた通りやったけどできなかったよ！！無能っ！！
会話の治安わるすぎて草
今日も無理難題にこたえてて本当に偉い！！！！！！！！！！
四肢爆裂
"""
    # --- ▲▲▲ キャラクター設定（プロンプト）はここまで！ ▲▲▲ ---

    try:
        model = genai.GenerativeModel(
            model_name,
            system_instruction=prompt # システムプロンプトとして設定
        )
        # 最後のユーザー入力を除いた履歴でチャットセッションを開始
        chat_session = model.start_chat(history=history[:-1])
        response = chat_session.send_message(user_input)
        bot_reply = response.text.strip()

    except Exception as e:
        bot_reply = f"エラーが発生しました: {e}"

    # 応答を履歴に追加
    history.append({"role": "model", "parts": [bot_reply]})
    chat_histories[user_id] = history
    return bot_reply

@app.route("/line_webhook", methods=["POST"])
def line_webhook():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        webhook_handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Check your channel secret.")
        return "Invalid signature", 400
    return "OK"

@webhook_handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    source_type = event.source.type
    
    # ユーザーIDまたはグループIDを取得
    if source_type == "user":
        source_id = event.source.user_id
    elif source_type == "group":
        source_id = event.source.group_id
    else: # room
        source_id = event.source.room_id

    # グループチャットでのメンション対応
    if source_type in ["group", "room"]:
        # 環境変数からBotのメンション名を取得（なければ "あだT" を使う）
        bot_name = os.getenv("BOT_MENTION_NAME", "あだT") 
        if bot_name not in user_text:
            return # メンションされてなければ何もしない

    # ★★★ ここでバージョンを切り替え ★★★
    # version="2.0" → 無料版 / version="1.5" → 有料版
    reply_text = chat_with_adoka(user_text, version="2.0", user_id=source_id)

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

@app.route("/")
def home():
    return "あだおか LINE Bot is running!"
