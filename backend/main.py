# !! 必须在所有 import 之前打 ChromaDB SQLite 补丁 !!
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from backend.api.deps import rate_limit_key
from backend.api import auth, chat, history, sessions, voice
from backend.database import init_db

load_dotenv()

STATIC_IMAGES_DIR = os.getenv("STATIC_IMAGES_DIR", "./backend/static/images")


# ── 应用生命周期 ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化 SQLite 数据库（幂等）
    init_db()
    # 确保静态资源目录存在
    os.makedirs(STATIC_IMAGES_DIR, exist_ok=True)
    yield
    # 关闭时（如需清理资源可在此添加）


# ── 限流器 ────────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=rate_limit_key, default_limits=[])

# ── FastAPI 应用 ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="甘薯专家系统",
    version="1.0.0",
    description="基于 RAG + 千问的甘薯病害智能问答后端",
    lifespan=lifespan,
)

# 将 limiter 挂到 app state，供 slowapi 中间件读取
app.state.limiter = limiter

# ── 中间件 ────────────────────────────────────────────────────────────────────
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 生产环境改为实际域名
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 限流超限错误处理 ──────────────────────────────────────────────────────────
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": f"请求过于频繁，每日最多提问 {os.getenv('RATE_LIMIT_PER_DAY', '20')} 次，请明天再试。"},
    )

# ── 路由注册 ──────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(history.router)
app.include_router(sessions.router)
app.include_router(voice.router)

# ── 静态资源（病害图片）────────────────────────────────────────────────────────
app.mount(
    "/static/images",
    StaticFiles(directory=STATIC_IMAGES_DIR),
    name="images",
)

# ── 健康检查 ──────────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "甘薯病害专家系统"}
