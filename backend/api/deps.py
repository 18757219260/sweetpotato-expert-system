"""
backend/api/deps.py - FastAPI 共享依赖

包含：
- JWT Token 解析与身份验证
- 数据库 Session 注入
- 限流所用的 openid key 函数
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.database import SessionLocal, User
from dotenv import load_dotenv
import os

load_dotenv()

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "changeme")
JWT_ALGORITHM  = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MIN = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))

_bearer = HTTPBearer()


# ── DB Session ────────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── JWT 工具 ──────────────────────────────────────────────────────────────────
def create_access_token(openid: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MIN)
    payload = {"sub": openid, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> str:
    """解码并返回 openid，失败则抛出 401"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        openid: Optional[str] = payload.get("sub")
        if not openid:
            raise ValueError("missing sub")
        return openid
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
        ) from e


# ── FastAPI 依赖：获取当前用户 ─────────────────────────────────────────────────
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    openid = _decode_token(credentials.credentials)
    user = db.query(User).filter(User.openid == openid).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    # 更新最后活跃时间
    user.last_active = datetime.utcnow()
    db.commit()
    return user


# ── slowapi 限流 key 函数：优先用 openid，降级到 IP ───────────────────────────
def rate_limit_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            return _decode_token(auth[7:])
        except HTTPException:
            pass
    return request.client.host if request.client else "unknown"
