#!/usr/bin/env python3
"""
Chat Memory System Dashboard Server

メモリシステムの状態を監視するためのシンプルなFastAPIダッシュボードサーバー
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Dict, Any, List
import uvicorn
from datetime import datetime

# FastAPIアプリケーション初期化
app = FastAPI(
    title="Chat Memory System Dashboard",
    description="会話記憶システムの監視ダッシュボード",
    version="1.0.0"
)

# サンプルセッションストア（実際の実装では外部ストレージから読み込み）
sample_session_store = {
    "user001": {
        "last_activity": "2025-08-13 10:30:00",
        "memory_store": {
            "shortMemories": "[2025-08-13 10:30:00] 今日はPythonのメモリシステムについて議論し、longLogの保持期間を52件に変更した。",
            "midMemories": "[2025-08-12 15:45:00] 昨日はAIシステムの最適化について話し合い、パフォーマンス改善案をいくつか提案された。",
            "longMemories": "[2025-08-01 09:00:00] 今月は機械学習プロジェクトの進捗について継続的に相談しており、データ前処理と特徴量エンジニアリングに重点を置いている。",
            "totalMemories": "[2025-08-13 10:30:00] ユーザーは継続的にAI・機械学習システムの開発に取り組んでおり、特にメモリシステムとパフォーマンス最適化に強い関心を示している。データ処理技術とシステム設計について深い知識を持ち、実装とテストの両面で積極的に活動している。"
        },
        "long_conversation_logs": {
            "very_long_log": [
                {"role": "user", "content": "メモリシステムのテストを実行してください", "timestamp": "2025-08-13 10:25:00"},
                {"role": "assistant", "content": "メモリシステムのテストを実行します。11個のテストが成功しました。", "timestamp": "2025-08-13 10:25:05"}
            ],
            "midLog": [
                "2025-08-12の要約: メモリシステムの設計について相談し、階層化されたアプローチを検討。",
                "2025-08-11の要約: AIモデルの性能向上について議論し、新しいアルゴリズムを試験。"
            ],
            "longLog": [
                "週次要約1: メモリシステムの基本設計完了",
                "週次要約2: テスト実装とドキュメント作成"
            ]
        }
    }
}

# データモデル定義
class MemoryStats(BaseModel):
    client_id: str
    last_activity: str
    very_long_log_count: int
    midlog_count: int
    longlog_count: int
    has_short_memories: bool
    has_mid_memories: bool
    has_long_memories: bool

class SystemStatus(BaseModel):
    total_clients: int
    active_clients: int
    total_memories: int
    timestamp: str

# ルート: ダッシュボードHTML
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """ダッシュボードページを返す"""
    try:
        with open("session_store_dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dashboard HTML file not found")

# API: システム状態取得
@app.get("/api/status", response_model=SystemStatus)
async def get_system_status():
    """システム全体の状態を取得"""
    total_clients = len(sample_session_store)
    active_clients = sum(1 for client in sample_session_store.values() 
                        if client.get("last_activity", ""))
    total_memories = sum(
        len(client.get("long_conversation_logs", {}).get("very_long_log", [])) +
        len(client.get("long_conversation_logs", {}).get("midLog", [])) +
        len(client.get("long_conversation_logs", {}).get("longLog", []))
        for client in sample_session_store.values()
    )
    
    return SystemStatus(
        total_clients=total_clients,
        active_clients=active_clients,
        total_memories=total_memories,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

# API: 全クライアントのメモリ統計
@app.get("/api/memory-stats", response_model=List[MemoryStats])
async def get_memory_stats():
    """全クライアントのメモリ統計を取得"""
    stats = []
    for client_id, client_data in sample_session_store.items():
        logs = client_data.get("long_conversation_logs", {})
        memory_store = client_data.get("memory_store", {})
        
        stat = MemoryStats(
            client_id=client_id,
            last_activity=client_data.get("last_activity", ""),
            very_long_log_count=len(logs.get("very_long_log", [])),
            midlog_count=len(logs.get("midLog", [])),
            longlog_count=len(logs.get("longLog", [])),
            has_short_memories=bool(memory_store.get("shortMemories", "").strip()),
            has_mid_memories=bool(memory_store.get("midMemories", "").strip()),
            has_long_memories=bool(memory_store.get("longMemories", "").strip())
        )
        stats.append(stat)
    
    return stats

# API: 特定クライアントの詳細情報
@app.get("/api/client/{client_id}")
async def get_client_details(client_id: str):
    """特定クライアントの詳細情報を取得"""
    if client_id not in sample_session_store:
        raise HTTPException(status_code=404, detail="Client not found")
    
    client_data = sample_session_store[client_id]
    
    # 最新の会話ログを5件まで取得
    recent_logs = client_data.get("long_conversation_logs", {}).get("very_long_log", [])
    recent_conversations = recent_logs[-5:] if len(recent_logs) > 5 else recent_logs
    
    return {
        "client_id": client_id,
        "last_activity": client_data.get("last_activity", ""),
        "memory_store": client_data.get("memory_store", {}),
        "recent_conversations": recent_conversations,
        "log_counts": {
            "very_long_log": len(client_data.get("long_conversation_logs", {}).get("very_long_log", [])),
            "midLog": len(client_data.get("long_conversation_logs", {}).get("midLog", [])),
            "longLog": len(client_data.get("long_conversation_logs", {}).get("longLog", []))
        }
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
