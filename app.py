import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

# Gemini API の設定
import google.generativeai as genai
from google.generativeai.types import Content
from flask import Flask, request

# LINE Bot の設定
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# ==============================================================================
# 設定値 (Constants)
# ==============================================================================
# データベースファイル名
DB_NAME = 'chatbot_memory.db'
# 要約を行う最大往復回数 (10往復)
MAX_TURNS = 10 
# 短期記憶に保持するメッセージ数の上限 (例: 10往復 x ユーザー/AI = 20メッセージ)
MAX_SHORT_TERM_MESSAGES = MAX_TURNS * 2

# Vercelの環境変数からAPIキーを取得
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# LINE Botの設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# APIキーとLINE SDKの初期化
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ==============================================================================
# データベース管理 (SQLiteManager)
# ==============================================================================

class SQLiteManager:
    """ユーザーごとの会話履歴をSQLiteデータベースで管理するクラス。"""
    
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self._initialize_db()

    def _initialize_db(self):
        """データベースファイルを初期化し、テーブルが存在しない場合は作成します。"""
        # サーバーレス環境（Vercelなど）では一時的な /tmp ディレクトリを使用するのが一般的
        # ただし、SQLiteは永続性が課題になるため、AWS S3や他の永続DBの使用を推奨します。
        # ローカル実行時は DB_NAME を使用します。
        
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # history (短期記憶: JSON文字列), summary (長期記憶: テキスト), turn_count (往復回数)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS user_memory (
                user_id TEXT PRIMARY KEY,
                history TEXT NOT NULL,
                summary TEXT NOT NULL,
                turn_count INTEGER NOT NULL,
                last_updated TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def get_memory(self, user_id: str) -> Dict[str, Any]:
        """指定されたユーザーIDの記憶データを取得します。"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute("SELECT history, summary, turn_count FROM user_memory WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            history_json = result[0]
            try:
                # historyはJSON文字列として保存されているため、ロードする
                history_list = json.loads(history_json)
            except json.JSONDecodeError:
                history_list = [] # デコード失敗時は空リスト
                
            return {
                "user_id": user_id,
                "history": history_list,
                "summary": result[1],
                "turn_count": result[2]
            }
        
        # 記憶がない場合の初期値
        return {
            "user_id": user_id,
            "history": [],
            "summary": "",
            "turn_count": 0
        }

    def save_memory(self, user_id: str, history: List[Dict], summary: str, turn_count: int):
        """記憶データを保存または更新します。"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # historyをJSON文字列に変換して保存
        history_json = json.dumps(history, ensure_ascii=False)
        
        # UPSERT (存在すれば更新、なければ挿入)
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO user_memory (user_id, history, summary, turn_count, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                history = excluded.history,
                summary = excluded.summary,
                turn_count = excluded.turn_count,
                last_updated = excluded.last_updated
        """, (user_id, history_json, summary, turn_count, now))
        
        conn.commit()
        conn.close()


# ==============================================================================
# AI連携クラス (ChatBot)
# ==============================================================================

class ChatBot:
    """LINEの応答と階層メモリの管理を行うメインクラス。"""
    
    def __init__(self, db_manager: SQLiteManager):
        self.db_manager = db_manager
        
        # ユーザーが提供したキャラクター設定（長期記憶の一部として機能）
        self.personality_prompt = (
            "【キャラクター設定】あなたは「あだおか」または「あだT」というキャラクターのLINEのチャットAIです。1997年生まれ、岐阜県出身・在住の女性。本名あだちがモデル。MBTIは典型的なINFP。INFPがあたおか（頭おかしい）と言われることが、キャラクター名の由来。とある企業の安全健康管理室に勤め、孤立しがちな環境で真面目に社畜として働いている。内面はぶっ飛んでおり、ネットスラング（例：ｗｗｗ、爆笑、かあいい、ねぇｗｗｗｗちょっとまってｗｗｗｗｗｗｗ）は【適度に使用】するが、会話の意味はしっかり通じるようにする。\n\n"
            "【性格・話し方の特徴】\n"
            "- 軽快で自然な口調。皮肉やブラックジョークを交えたユーモアが特徴。\n"
            "- 会話中、必要な箇所だけにネットスラングを適度に混ぜる。\n"
            "- 「♪」や顔文字（＾＾、(´∀｀)など）は一切使用しない。\n\n"
            "【会話ルール】\n"
            "- 回答は1～2行の短文で返す。\n"
            "- ユーザーの発言に適切に反応し、自然な会話を展開する。\n\n"
            "【あだおかの語録（適度に使用）】\n"
            "ねぇｗｗｗｗｗちょっとまってｗｗｗｗｗｗｗ\n"
            "わろた\n"
            "いただきました\n"
            "ぱわぁ💪\n"
            "かあいい\n"
            "まって爆笑爆笑爆笑爆笑爆笑爆笑爆笑\n"
            "言われた通りやったけどできなかったよ！！無能っ！！\n"
            "会話の治安わるすぎて草\n"
            "今日も無理難題にこたえてて本当に偉い！！！！！！！！！！\n"
            "四肢爆裂"
        )
        
        # モデルが未設定の場合はエラーを出す
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEYが設定されていません。")


    def _call_gemini_api(self, messages: List[Dict], model_name: str, is_summary: bool = False) -> str:
        """
        Gemini APIを呼び出し、応答を取得します。
        """
        # Gemini APIの 'contents' 形式に変換
        contents: List[Content] = []
        for message in messages:
            # roleの変換: 'user'はそのまま、'assistant'を'model'に変換
            role = message.get("role")
            if role == "assistant":
                role = "model"
            
            # parts (content) はリストであることを想定
            content_parts = message.get("content")
            if isinstance(content_parts, str):
                 content_parts = [{"text": content_parts}] # 文字列の場合はテキストパートに変換
            elif isinstance(content_parts, list):
                 content_parts = [{"text": p} for p in content_parts if isinstance(p, str)]

            contents.append(Content(role=role, parts=content_parts))
        
        # システムインストラクションを分離（Gemini APIの引数として渡すため）
        system_instruction_text: Optional[str] = None
        if contents and contents[0].role == 'system':
            # 最初の要素がシステムプロンプトなら、それを抽出
            system_instruction_text = contents[0].parts[0].text
            contents = contents[1:] # contentsリストから削除

        try:
            model = genai.GenerativeModel(model_name)
            
            # API呼び出し
            response = model.generate_content(
                contents,
                system_instruction=system_instruction_text,
            )
            return response.text.strip()
            
        except Exception as e:
            app.logger.error(f"Gemini API Error (Model: {model_name}, Summary: {is_summary}): {e}")
            return f"Gemini側でエラーが発生しました: {e}"


    def generate_response(self, user_id: str, user_message: str, version: str) -> str:
        """
        ユーザーメッセージを受け取り、階層メモリに基づいた応答を生成します。
        """
        # モデルの決定
        model_name = "gemini-2.5-pro" if version == "1.5" else "gemini-2.5-flash"

        # 1. 記憶データを取得
        memory = self.db_manager.get_memory(user_id)
        history: List[Dict] = memory['history'] # 短期記憶
        summary: str = memory['summary']       # 長期記憶
        turn_count: int = memory['turn_count']
        
        app.logger.info(f"User:{user_id}, Turn:{turn_count}, History:{len(history)} messages.")

        # 2. 【長期記憶処理】要約判定と実行
        if turn_count >= MAX_TURNS:
            app.logger.info(">>> 要約を開始します。")
            
            # 要約プロンプトの構成
            # 長期記憶のコンテキストを渡し、その上で短期記憶を要約させる
            summary_system_prompt = (
                "あなたは会話履歴を圧縮する専門家です。以下の過去の要約と直近の会話履歴を結合し、"
                "今後の文脈維持に役立つように簡潔に要約し、要約文のみを返答してください。"
            )
            
            # 要約タスクに送るメッセージリストを構築
            summary_history_text = "\n".join(
                f"{msg['role'].capitalize()}: {msg['content']}" for msg in history
            )
            
            summary_messages = [
                {"role": "system", "content": summary_system_prompt},
                {"role": "user", "content": f"【これまでの長期要約】:\n{summary}\n\n【直近の会話履歴】:\n{summary_history_text}"}
            ]

            # AIに要約を依頼 (プロンプトには要約依頼文は不要、システムプロンプトが役割を指示)
            new_summary_text = self._call_gemini_api(summary_messages, model_name, is_summary=True)
            
            # 長期記憶の更新 (既存の要約を置き換えるか、統合する。今回は置き換え)
            summary = new_summary_text.strip()
            
            # 短期記憶とターン数をリセット
            history = []
            turn_count = 0
            app.logger.info(">>> 要約完了。")


        # 3. 【短期記憶処理】AIへのプロンプト構成
        
        # 最終的なメッセージリスト
        messages: List[Dict] = []
        
        # 3a. システムメッセージ (人格 + 長期記憶) を統合
        combined_system_prompt = self.personality_prompt
        if summary:
             combined_system_prompt += f"\n\n【これまでの会話の長期要約】: {summary}"
             
        messages.append({"role": "system", "content": combined_system_prompt})

        # 3b. 短期記憶の会話履歴を追加 (Gemini APIの形式: role='user' or 'model')
        for msg in history:
            # 既存の履歴ロールをAPIが期待する 'user'/'model' に合わせる
            role = "user" if msg['role'] == 'user' else "model" 
            messages.append({"role": role, "content": msg['content']})
        
        # 3c. 今回のユーザーメッセージを追加
        messages.append({"role": "user", "content": user_message})

        # 4. AIを呼び出し、応答を取得
        ai_response = self._call_gemini_api(messages, model_name, is_summary=False)
        
        # 5. 記憶を更新
        
        # 新しい会話を短期記憶に追加
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": ai_response}) # DB保存時は 'assistant' ロールで保存
        
        # 短期記憶の長さをチェックし、古いものを削除 (念のための安全措置)
        if len(history) > MAX_SHORT_TERM_MESSAGES:
             history = history[-MAX_SHORT_TERM_MESSAGES:]

        # ターン数をインクリメント
        turn_count += 1

        # データベースに保存
        self.db_manager.save_memory(user_id, history, summary, turn_count)
        
        return ai_response

# ==============================================================================
# LINE Webhook と Flask の設定
# ==============================================================================

# SQLite Managerをグローバルでインスタンス化（サーバーレス環境では初期化を工夫が必要）
# 今回はシンプルにここで初期化します。
db_manager = SQLiteManager(DB_NAME)

@app.route("/line_webhook", methods=["POST"])
def line_webhook():
    """LINEからのWebhookリクエストを受け取るエンドポイント"""
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    
    if not LINE_CHANNEL_SECRET:
         app.logger.error("LINE_CHANNEL_SECRETが設定されていません。")
         return "LINE channel secret not configured", 500

    try:
        webhook_handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Check your channel secret.")
        return "Invalid signature", 400
    except Exception as e:
        app.logger.error(f"Webhook handling error: {e}")
        return "Internal Error", 500
        
    return "OK"

@webhook_handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """テキストメッセージイベントを処理する関数"""
    user_text = event.message.text
    source_type = event.source.type
    
    # ユーザーIDまたはグループIDを取得
    if source_type == "user":
        source_id = event.source.user_id
    elif source_type == "group":
        source_id = event.source.group_id
    elif source_type == "room":
        source_id = event.source.room_id
    else:
        return # 未対応のソースタイプ

    # グループチャットでのメンション対応
    if source_type in ["group", "room"]:
        # 環境変数からBotのメンション名を取得（なければ "あだT" を使う）
        bot_name = os.getenv("BOT_MENTION_NAME", "あだT") 
        if bot_name not in user_text:
            return # メンションされてなければ何もしない
        
        # メンション部分を削除して、純粋なメッセージを抽出
        user_text = user_text.replace(f"@{bot_name}", "").strip()
        if not user_text:
             user_text = "何か話しかけているみたいだけど？" # メンションだけの場合の対応

    # ChatBot インスタンスを作成
    # SQLiteManagerはグローバルで初期化されたものを使用
    try:
        chatbot = ChatBot(db_manager)

        # ★★★ ここでバージョンを切り替え ★★★
        # version="2.0" → gemini-2.5-flash (無料版) / version="1.5" → gemini-2.5-pro (有料版)
        reply_text = chatbot.generate_response(source_id, user_text, version="2.0")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    except Exception as e:
        app.logger.error(f"Chatbot processing failed: {e}")
        # ユーザーにエラーを通知
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ごめんね、今ちょっと内部エラーでぶっ飛んでるわ。もう一回試してみて！"))


@app.route("/")
def home():
    """ヘルスチェック用のルート"""
    return "あだおか LINE Bot is running with Hierarchical Memory!"

# サーバーレス環境での実行 (Vercelなど) に必要な処理
# if __name__ == "__main__":
#     app.run(debug=True)
