
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
from backend.database import ChatSession, Conversation, User, FarmProfile
from backend.services.llm_service import chat_stream

RATE_LIMIT = os.getenv("RATE_LIMIT_PER_DAY", "100")

limiter = Limiter(key_func=rate_limit_key)
router  = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    mode: str = "pro"           # "pro" 或 "flash"
    session_id: Optional[int] = None


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _generate(
    question: str,
    user_id: int,
    db: Session,
    mode: str = "pro",
    session_id: Optional[int] = None,
):

    question = str(question) if question is not None else ""

    # print(f"[DEBUG _generate] question type: {type(question)}, value: {question[:50]}...")
    # print(f"[DEBUG _generate] user_id type: {type(user_id)}, value: {user_id}")
    # print(f"[DEBUG _generate] mode type: {type(mode)}, value: {mode}")
    # print(f"[DEBUG _generate] session_id type: {type(session_id)}, value: {session_id}")

    if session_id is None:
        title = question[:20] if len(question) > 20 else question
        new_session = ChatSession(user_id=user_id, title=title)
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        session_id = new_session.id

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

    # print(f"[DEBUG _generate] history type: {type(history)}, length: {len(history)}")
    # print(f"[DEBUG _generate] history content: {history}")

    # 获取用户农场档案
    farm_profile = db.query(FarmProfile).filter(FarmProfile.user_id == user_id).first()
    farm_context = None
    if farm_profile:
        location = f"{farm_profile.province}{farm_profile.city}{farm_profile.district}"
        farm_context = f"用户农场信息：位于{location}"
        if farm_profile.area_mu:
            farm_context += f"，种植面积{farm_profile.area_mu}亩"
        if farm_profile.soil_type:
            farm_context += f"，土壤类型为{farm_profile.soil_type}"
        if farm_profile.other_info:
            farm_context += f"，其他信息：{farm_profile.other_info}"
        # print(f"[DEBUG _generate] farm_context: {farm_context}")

    clean_answer = ""
    images: list[str] = []

    try:
        async for chunk in chat_stream(question, history, mode=mode, farm_context=farm_context):
            if chunk["type"] == "text":
                yield _sse({"type": "text", "content": chunk["content"]})
            elif chunk["type"] == "done":
                clean_answer = chunk["clean_answer"]
                images = chunk["images"]
                yield _sse({"type": "done", "images": images, "segments": chunk["segments"], "clean_answer": clean_answer, "session_id": session_id})

    except Exception as exc:
        yield _sse({"type": "error", "detail": str(exc)})
    finally:
        if question or clean_answer:
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
    
    user_id: int = current_user.id
    return StreamingResponse(
        _generate(body.question, user_id, db, mode=body.mode, session_id=body.session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  
        },
    )
