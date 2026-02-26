"""
services/llm_service.py - RAG 与核心大模型服务

功能：
1. Query Rewrite：将口语化提问提炼为专业术语，提升 ChromaDB 命中率
2. RAG 检索：向量化查询 + 相似度过滤
3. 核心流式对话：结合 RAG 片段 + 兜底 Prompt + 图片触发 JSON 提取
4. 对话历史滑动窗口（保留最近 3 轮）
"""

# !! ChromaDB SQLite 补丁必须在所有 import 之前 !!
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import json
import os
import re
from typing import AsyncGenerator, Optional

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── 配置 ─────────────────────────────────────────────────────────────────────
QWEN_API_KEY   = os.getenv("QWEN_API_KEY")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./backend/data/chroma_db")
COLLECTION_NAME   = "sweet_potato_knowledge"
EMBEDDING_MODEL   = "text-embedding-v3"
CHAT_MODEL_PRO    = "qwen3.5-plus"   # Pro 模式：查询重写 + 高精度回答
CHAT_MODEL_FLASH  = "qwen3.5-flash"  # Flash 模式：直接检索 + 快速回答
REWRITE_MODEL     = "qwen3.5-flash"  # 查询重写始终用轻量模型
TOP_K             = 4                    # 每次检索返回片段数
SIMILARITY_THRESH = 0.3                  # 余弦相似度阈值（低于则视为未命中）
MAX_HISTORY_TURNS = 3                    # 滑动窗口保留轮数

# ── 客户端初始化 ──────────────────────────────────────────────────────────────
_qwen = OpenAI(
    api_key=QWEN_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

_chroma_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[chromadb.Collection] = None


def _get_collection() -> chromadb.Collection:
    """懒加载 ChromaDB 集合（FastAPI 启动后首次调用时初始化）"""
    global _chroma_client, _collection
    if _collection is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


# ── 1. 查询重写 (Query Rewrite) ───────────────────────────────────────────────
def rewrite_query(user_question: str) -> str:
    """
    将用户口语化提问转换为专业检索关键词。
    例："叶子发黄了怎么办" → "甘薯叶片黄化 缺氮 病毒病 症状"
    """
    prompt = (
        "你是甘薯农业专家。用户提了一个问题，请提取其中最关键的农业专业术语和症状描述，"
        "输出 5-8 个关键词（空格分隔），不要输出任何解释，只输出关键词。\n\n"
        f"用户问题：{user_question}"
    )
    response = _qwen.chat.completions.create(
        model=REWRITE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=60,
        temperature=0.0,
        extra_body={"enable_thinking": False},
    )
    rewritten = response.choices[0].message.content.strip()
    # 若重写结果为空或异常，回退到原始问题
    return rewritten if rewritten else user_question


# ── 2. RAG 检索 ───────────────────────────────────────────────────────────────
def retrieve_context(query: str) -> tuple[str, bool]:
    """
    向量化查询并检索相关知识片段。
    返回：(格式化上下文字符串, 是否命中)
    """
    collection = _get_collection()
    if collection.count() == 0:
        return "", False

    # 向量化查询词
    embed_resp = _qwen.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[query],
        encoding_format="float",
    )
    query_embedding = embed_resp.data[0].embedding

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(TOP_K, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]  # 余弦距离，越小越相似

    # 过滤低相似度片段
    filtered = [
        (doc, meta)
        for doc, meta, dist in zip(docs, metas, distances)
        if (1 - dist) >= SIMILARITY_THRESH
    ]

    if not filtered:
        return "", False

    context_parts = []
    for doc, meta in filtered:
        source = f"{meta.get('category', '')} · {meta.get('name', '')}"
        context_parts.append(f"【{source}】\n{doc}")

    return "\n\n".join(context_parts), True


# ── 3. 构建系统 Prompt ────────────────────────────────────────────────────────
_SYSTEM_TEMPLATE = """\
你是一位专业的甘薯种植与病害防治专家助手，服务于广大农户。

【本地知识库片段】:
{context}

【回答要求】:
1. 优先基于【本地知识库片段】回答，保持专业、准确、通俗易懂。
2. 如果片段为空或无法回答用户的问题，你必须首先明确声明："本地专家知识库中暂未收录该特定信息。"声明后，可调用你自身的大模型通用知识给出建议，并在句末提醒："以上建议基于大模型通用知识，仅供参考，请以实际农技站指导为准。"
3. 回答要针对农民实际操作，给出具体的用药名称、剂量、操作步骤。
4. 语气亲切，避免过于学术化。
5. 如果用户的问题与甘薯种植、病害防治、农业生产完全无关，请礼貌拒绝并说明你只能回答甘薯相关问题，不要尝试回答。

【图片插入要求】:
在回答正文中，每当你介绍某种具体病害或农事操作时，在该段落末尾紧接着插入对应的图片标记（格式：[图片:标识]）。
图片标识对应关系：soft_rot（软腐病）、black_spot（黑斑病）、stem_nematode（茎线虫病）、root_rot（根腐病）、virus（病毒病）、scab（疮痂病）、hornworm（天蛾）、weevil（蚁象）、armyworm（斜纹夜蛾）、fertilizer（施肥）、storage（贮藏）、irrigation（灌溉）。
例如：介绍软腐病后写 [图片:soft_rot]，介绍施肥后写 [图片:fertilizer]。
每种图片标识只插入一次，不要重复。
如果本次回答不涉及以上任何病害或农事，则不插入任何图片标记。\
"""


def build_system_prompt(context: str) -> str:
    return _SYSTEM_TEMPLATE.format(context=context if context else "（本次查询未检索到相关知识片段）")


# ── 4. 对话历史滑动窗口 ───────────────────────────────────────────────────────
def trim_history(history: list[dict]) -> list[dict]:
    """
    仅保留最近 MAX_HISTORY_TURNS 轮对话（1轮 = 1 user + 1 assistant）。
    严禁将往期 RAG 片段带入历史，history 中只存用户问题与大模型最终回答。
    """
    # 每轮 2 条消息（user + assistant）
    max_messages = MAX_HISTORY_TURNS * 2
    return history[-max_messages:] if len(history) > max_messages else history


# ── 5. 从大模型回答中提取图片标记 ───────────────────────────────────────────
_IMAGE_TAG_RE = re.compile(r'\[图片:(\w+)\]')


def extract_images_and_clean(raw_answer: str) -> tuple[str, list[str]]:
    """
    从大模型原始输出中提取 [图片:xxx] 标记，返回 (带位置信息的片段列表, 图片标识列表)。
    片段列表格式：[{"type": "text", "content": "..."}, {"type": "image", "id": "soft_rot"}, ...]
    同一图片标识只保留第一次出现的位置，后续重复标记直接删除。
    """
    images: list[str] = []
    segments: list[dict] = []
    seen: set[str] = set()
    last_end = 0

    for m in _IMAGE_TAG_RE.finditer(raw_answer):
        img_id = m.group(1)
        if img_id in seen:
            # 重复图片：把标记前的文字并入上一个文字段，跳过图片
            text_before = raw_answer[last_end:m.start()].strip()
            if text_before:
                if segments and segments[-1]["type"] == "text":
                    segments[-1]["content"] += "\n" + text_before
                else:
                    segments.append({"type": "text", "content": text_before})
            last_end = m.end()
            continue
        seen.add(img_id)
        images.append(img_id)
        text_before = raw_answer[last_end:m.start()].strip()
        if text_before:
            segments.append({"type": "text", "content": text_before})
        segments.append({"type": "image", "id": img_id})
        last_end = m.end()

    remaining = raw_answer[last_end:].strip()
    if remaining:
        segments.append({"type": "text", "content": remaining})

    # clean_answer 用于落库（纯文本，无标记）
    clean_answer = _IMAGE_TAG_RE.sub("", raw_answer).strip()

    # 若无任何标记，segments 退化为单个文本段
    if not segments:
        segments = [{"type": "text", "content": clean_answer}]

    return clean_answer, images, segments


# ── 6. 核心流式对话（主入口） ─────────────────────────────────────────────────
async def chat_stream(
    user_question: str,
    history: list[dict],
    mode: str = "pro",   # "pro" = 查询重写+plus | "flash" = 直接检索+flash
) -> AsyncGenerator[dict, None]:
    """
    核心问答流式生成器。

    mode="pro"  : 查询重写（flash）→ 精准检索 → qwen3.5-plus 回答（准确率优先）
    mode="flash": 直接检索 → qwen3.5-flash 回答（速度优先）

    每次 yield 一个 dict：
      {"type": "text",   "content": "..."}      # 文本增量片段
      {"type": "done",   "images": [...],
       "clean_answer": "..."}                    # 结束信号，含图片列表与完整回答
    """
    import asyncio
    loop = asyncio.get_event_loop()

    if mode == "flash":
        # Flash：直接用原始问题检索，跳过查询重写
        context, kb_hit = await loop.run_in_executor(None, retrieve_context, user_question)
        chat_model = CHAT_MODEL_FLASH
    else:
        # Pro：查询重写与直接检索并发，取更好的结果
        rewrite_task = loop.run_in_executor(None, rewrite_query, user_question)
        direct_task  = loop.run_in_executor(None, retrieve_context, user_question)
        rewritten, (context_direct, kb_hit_direct) = await asyncio.gather(rewrite_task, direct_task)
        if kb_hit_direct:
            context, kb_hit = context_direct, kb_hit_direct
        else:
            context, kb_hit = await loop.run_in_executor(None, retrieve_context, rewritten)
        chat_model = CHAT_MODEL_PRO

    # Step 3: 构建消息列表
    system_prompt = build_system_prompt(context)
    trimmed_history = trim_history(history)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(trimmed_history)
    messages.append({"role": "user", "content": user_question})

    # Step 4: 调用通义千问流式接口
    stream = _qwen.chat.completions.create(
        model=chat_model,
        messages=messages,
        stream=True,
        temperature=0.7,
        max_tokens=1500,
        extra_body={"enable_thinking": False},
    )

    raw_answer_parts: list[str] = []

    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            raw_answer_parts.append(delta.content)
            yield {"type": "text", "content": delta.content}

    # Step 5: 提取图片标识并清理回答
    raw_answer = "".join(raw_answer_parts)
    clean_answer, images, segments = extract_images_and_clean(raw_answer)

    yield {
        "type": "done",
        "clean_answer": clean_answer,
        "images": images,
        "segments": segments,
        "kb_hit": kb_hit,
    }
