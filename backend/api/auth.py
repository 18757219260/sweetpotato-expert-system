"""
backend/api/auth.py - 微信登录路由

POST /api/login
  1. 接收微信小程序 wx.login() 返回的 code
  2. 请求微信服务器换取 openid
  3. 创建或更新用户记录
  4. 返回 JWT Token（身份隔离核心）
"""

import os

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import create_access_token, get_db
from backend.database import User

load_dotenv()

WX_APP_ID     = os.getenv("WX_APP_ID", "")
WX_APP_SECRET = os.getenv("WX_APP_SECRET", "")
WX_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    code: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    """
    微信小程序登录。
    前端调用 wx.login() 获得临时 code，传入此接口换取 JWT Token。
    """
    # 1. 请求微信服务器换取 openid（最多重试 2 次，应对偶发超时）
    transport = httpx.AsyncHTTPTransport(retries=2)
    async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
        resp = await client.get(
            WX_CODE2SESSION_URL,
            params={
                "appid": WX_APP_ID,
                "secret": WX_APP_SECRET,
                "js_code": body.code,
                "grant_type": "authorization_code",
            },
        )

    wx_data = resp.json()

    if "errcode" in wx_data and wx_data["errcode"] != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"微信授权失败：{wx_data.get('errmsg', '未知错误')}",
        )

    openid: str = wx_data.get("openid", "")
    if not openid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无法获取 openid，请检查 AppID/AppSecret 配置",
        )

    # 2. 创建或更新用户（upsert）
    user = db.query(User).filter(User.openid == openid).first()
    is_new = user is None
    if is_new:
        user = User(openid=openid)
        db.add(user)
        db.commit()
        db.refresh(user)

    # 3. 签发 JWT
    token = create_access_token(openid)
    return LoginResponse(access_token=token, is_new_user=is_new)
