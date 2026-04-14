
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
from backend.api import auth, chat, history, sessions, voice, upload, farm
from backend.database import init_db
from backend.services.llm_service import init_chroma

load_dotenv()

STATIC_IMAGES_DIR = os.getenv("STATIC_IMAGES_DIR", "./backend/static/images")



@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    init_db()

    os.makedirs(STATIC_IMAGES_DIR, exist_ok=True)

    init_chroma()

    try:
        from backend.services.cv_service import _load_model
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _load_model)
        print("[startup] CV 模型预热完成")
    except Exception as e:
        print(f"[startup] CV 模型预热失败（将在首次请求时加载）：{e}")
    yield
   



limiter = Limiter(key_func=rate_limit_key, default_limits=[])


app = FastAPI(
    title="甘薯专家系统",
    version="1.0.0",
    description="基于 RAG + 千问的甘薯病害智能问答后端",
    lifespan=lifespan,
)


app.state.limiter = limiter


app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": f"请求过于频繁，每日最多提问 {os.getenv('RATE_LIMIT_PER_DAY', '100')} 次，请明天再试。"},
    )


app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(history.router)
app.include_router(sessions.router)
app.include_router(voice.router)
app.include_router(upload.router)
app.include_router(farm.router)


app.mount(
    "/static/images",
    StaticFiles(directory=STATIC_IMAGES_DIR),
    name="images",
)

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "甘薯病害专家系统"}
