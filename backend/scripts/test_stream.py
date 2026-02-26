"""
backend/scripts/test_stream.py - 后端流式接口速度测试脚本

用法：
    .venv/bin/python backend/scripts/test_stream.py
    .venv/bin/python backend/scripts/test_stream.py --mode flash
    .venv/bin/python backend/scripts/test_stream.py --mode pro --question "块根表面黑色凹陷"
    .venv/bin/python backend/scripts/test_stream.py --host 192.168.1.6 --port 8000
"""

__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from jose import jwt
from sqlalchemy import create_engine, Column, String, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# 加载 .env（从项目根目录）
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ── 内联 JWT 生成 ─────────────────────────────────────────────────────────────
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "changeme")
JWT_ALGORITHM  = os.getenv("JWT_ALGORITHM", "HS256")

def create_token(openid: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=10080)
    return jwt.encode({"sub": openid, "exp": expire}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

# ── 内联数据库访问 ────────────────────────────────────────────────────────────
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "./backend/data/app.db")
engine = create_engine(f"sqlite:///{SQLITE_DB_PATH}", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id      = Column(Integer, primary_key=True)
    openid  = Column(String(64), unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

def get_test_token() -> str:
    Base.metadata.create_all(engine)
    db = Session()
    user = db.query(User).first()
    if not user:
        user = User(openid="test_script_user")
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return create_token(user.openid)

# ── 测试主逻辑 ────────────────────────────────────────────────────────────────
def run_test(host: str, port: int, question: str, mode: str):
    token = get_test_token()
    url = f"http://{host}:{port}/api/chat/stream"

    print(f"\n{'='*60}")
    print(f"  模式    : {mode.upper()}")
    print(f"  问题    : {question}")
    print(f"  接口    : {url}")
    print(f"{'='*60}\n")

    t0 = time.time()
    first_token_time = None
    chunk_count = 0
    images = []

    def elapsed():
        return time.time() - t0

    print(f"[{elapsed():.2f}s] 发送请求...")

    with httpx.stream(
        "POST",
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={"question": question, "mode": mode},
        timeout=60.0,
    ) as resp:
        print(f"[{elapsed():.2f}s] HTTP {resp.status_code} — 开始接收流...")

        if resp.status_code != 200:
            print(f"[错误] 状态码 {resp.status_code}")
            print(resp.text)
            return

        buffer = ""
        for raw_chunk in resp.iter_text():
            buffer += raw_chunk
            parts = buffer.split("\n\n")
            buffer = parts.pop()

            for part in parts:
                for line in part.splitlines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    t = elapsed()
                    if event["type"] == "text":
                        chunk_count += 1
                        if first_token_time is None:
                            first_token_time = t
                            print(f"[{t:.2f}s] ★ 收到首个响应。开始流式输出：\n\n", end="")
                        
                        # 🌟 核心修改：把大模型吐出来的字实时打印到屏幕上，不换行
                        print(event["content"], end="", flush=True)

                    elif event["type"] == "done":
                        # 🌟 打印完文字后，先换两行，把输出隔开
                        print("\n\n") 
                        images = event.get("images", [])
                        total = elapsed()
                        print(f"[{total:.2f}s] ✓ 接收完成")
                        print(f"\n{'='*60}")
                        print(f"  首 token 耗时 : {first_token_time:.2f}s" if first_token_time else "  首 token 耗时 : 无输出")
                        print(f"  总耗时        : {total:.2f}s")
                        print(f"  文本 chunks   : {chunk_count}")
                        # 🌟 图片数组会在这里清晰地打印出来
                        print(f"  触发匹配图片  : {images if images else '无'}") 
                        print(f"{'='*60}\n")

                    elif event["type"] == "error":
                        print(f"\n\n[{elapsed():.2f}s] ✗ 错误: {event.get('detail')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="流式接口速度测试")
    parser.add_argument("--host",     default="127.0.0.1",    help="后端地址（默认 127.0.0.1）")
    parser.add_argument("--port",     default=8000, type=int, help="后端端口（默认 8000）")
    parser.add_argument("--mode",     default="flash",        choices=["flash", "pro"])
    parser.add_argument("--question", default="甘薯叶片发黄是什么病")
    args = parser.parse_args()

    run_test(args.host, args.port, args.question, args.mode)
