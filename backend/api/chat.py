"""
backend/api/chat.py - 核心流式问答路由

POST /api/chat/stream
  - 需要 JWT 鉴权
  - slowapi 限流：每用户每天最多 20 次
  - 返回 SSE（text/event-stream）流式响应
  - 前端（小程序）使用 wx.request({enableChunked: true}) 接收

SSE 事件格式：
  data: {"type": "text", "content": "..."}      ← 文本增量
  data: {"type": "done", "images": [...]}        ← 结束信号含图片
  data: {"type": "error", "detail": "..."}       ← 错误
"""

import json
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, rate_limit_key
from backend.database import ChatSession, Conversation, User
from backend.services.llm_service import chat_stream

RATE_LIMIT = os.getenv("RATE_LIMIT_PER_DAY", "20")

limiter = Limiter(key_func=rate_limit_key)
router  = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    mode: str = "pro"           # "pro" 或 "flash"
    session_id: Optional[int] = None


def _sse(data: dict) -> str:
    """将 dict 格式化为 SSE 数据帧"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _generate(
    question: str,
    user_id: int,
    db: Session,
    mode: str = "pro",
    session_id: Optional[int] = None,
):
    """
    异步生成器：执行 RAG + LLM 流式问答，并在结束时落库。
    """
    # 若未传 session_id，自动新建会话（取问题前 20 字为标题）
    if session_id is None:
        title = question[:20] if len(question) > 20 else question
        new_session = ChatSession(user_id=user_id, title=title)
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        session_id = new_session.id

    # 拉取该会话的近期历史（仅 role/content，无 RAG 片段）
    history_rows = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id, Conversation.session_id == session_id)
        .order_by(Conversation.created_at.desc())
        .limit(6)
        .all()
    )
    history = [
        {"role": row.role, "content": row.content}
        for row in reversed(history_rows)
    ]

    clean_answer = ""
    images: list[str] = []

    try:
        async for chunk in chat_stream(question, history, mode=mode):
            if chunk["type"] == "text":
                yield _sse({"type": "text", "content": chunk["content"]})
            elif chunk["type"] == "done":
                clean_answer = chunk["clean_answer"]
                images = chunk["images"]
                yield _sse({"type": "done", "images": images, "segments": chunk["segments"], "clean_answer": clean_answer, "session_id": session_id})

    except Exception as exc:
        yield _sse({"type": "error", "detail": str(exc)})
        return

    # 落库：仅保存用户问题与最终回答（不含 RAG 片段）
    db.add(Conversation(user_id=user_id, session_id=session_id, role="user",      content=question))
    db.add(Conversation(user_id=user_id, session_id=session_id, role="assistant", content=clean_answer))
    db.commit()


@router.post("/stream")
@limiter.limit(f"{RATE_LIMIT}/day")
async def chat_stream_endpoint(
    request: Request,
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    核心打字机问答接口（SSE 流式）。
    小程序端使用 wx.request({ enableChunked: true }) 配合 onChunkReceived 接收。
    """
    # 在 Session 仍有效时提取纯整数 user_id，避免 async generator 里 DetachedInstanceError
    user_id: int = current_user.id
    return StreamingResponse(
        _generate(body.question, user_id, db, mode=body.mode, session_id=body.session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 关闭 Nginx 缓冲，确保实时推送
        },
    )
