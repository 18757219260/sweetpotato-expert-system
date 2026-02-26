"""
backend/api/history.py - 对话历史路由

GET  /api/history       - 拉取当前用户历史记录
POST /api/history/clear - 清空当前用户所有对话记忆
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.database import Conversation, User

router = APIRouter(prefix="/api/history", tags=["history"])


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: str

    class Config:
        from_attributes = True


class HistoryResponse(BaseModel):
    total: int
    messages: list[MessageOut]


class ClearResponse(BaseModel):
    deleted: int


@router.get("", response_model=HistoryResponse)
def get_history(
    limit: int = 20,
    session_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    拉取当前登录用户的对话历史（按时间正序，默认最近 20 条）。
    可通过 session_id 过滤特定会话。
    """
    q = db.query(Conversation).filter(Conversation.user_id == current_user.id)
    if session_id is not None:
        q = q.filter(Conversation.session_id == session_id)
    rows = q.order_by(Conversation.created_at.desc()).limit(limit).all()
    messages = [
        MessageOut(
            role=row.role,
            content=row.content,
            created_at=row.created_at.isoformat(),
        )
        for row in reversed(rows)
    ]
    return HistoryResponse(total=len(messages), messages=messages)


@router.post("/clear", response_model=ClearResponse)
def clear_history(
    session_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    清空当前用户的对话记忆。若传 session_id 则只清空该会话。
    """
    q = db.query(Conversation).filter(Conversation.user_id == current_user.id)
    if session_id is not None:
        q = q.filter(Conversation.session_id == session_id)
    deleted = q.delete()
    db.commit()
    return ClearResponse(deleted=deleted)
