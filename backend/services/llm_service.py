
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import json
import os
import re
import requests
from typing import AsyncGenerator, Optional
import random
import chromadb
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from backend.services.mcp_service import TOOLS, execute_tool, format_tool_result
from datetime import datetime, timezone, timedelta
load_dotenv()

# ── 配置 ─────────────────────────────────────────────────────────────────────
QWEN_API_KEY   = os.getenv("QWEN_API_KEY")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./backend/data/chroma_db")
COLLECTION_NAME   = "sweet_potato_knowledge"
EMBEDDING_MODEL   = "text-embedding-v3"
CHAT_MODEL_PRO    = "qwen3.5-plus"   # 查询重写 + 高精度回答
CHAT_MODEL_FLASH  = "qwen3.5-flash"  # 直接检索快速回答
REWRITE_MODEL     = "qwen3.5-flash"  # 查询重写模型
TOP_K_INITIAL     = 10                   # ChromaDB 召回数
TOP_K_FINAL       = 3                    # Rerank 后最终保留数
RERANK_MODEL      = "gte-rerank"         # DashScope Rerank 模型
SIMILARITY_THRESH = 0.4                 # 余弦相似度阈值（Rerank 前预过滤）
MAX_HISTORY_TURNS = 5                    # 滑动窗口保留轮数
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../static/images"))
# ── 客户端初始化 ──────────────────────────────────────────────────────────────
_qwen = OpenAI(
    api_key=QWEN_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
_qwen_async = AsyncOpenAI(
    api_key=QWEN_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

_chroma_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[chromadb.Collection] = None


def _get_collection() -> chromadb.Collection:
    global _chroma_client, _collection
    if _collection is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def init_chroma():

    _get_collection()



def analyze_query_intent(user_question: str) -> dict:
  
    prompt = (
        "你是甘薯农业专家。分析以下用户问题，以JSON格式输出意图分析结果。\n\n"
        "字段说明：\n"
        "- search_query：提取5-8个专业检索关键词（空格分隔）\n"
        "- filters.category：仅当可明确判断时填写，值必须严格是以下之一："
        "病害、虫害、品种资源、草害、农业灾害、农艺管理。不确定时不包含此字段。\n\n"
        f"用户问题：{user_question}\n\n"
        '输出格式（严格JSON）：{"search_query": "...", "filters": {}}'
    )

    try:
        response = _qwen.chat.completions.create(
            model=REWRITE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.0,
            extra_body={"enable_thinking": False},
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        result = json.loads(raw)

        if "search_query" not in result or not result["search_query"]:
            result["search_query"] = user_question
        if "filters" not in result or not isinstance(result["filters"], dict):
            result["filters"] = {}

        result["filters"] = {
            k: v for k, v in result["filters"].items()
            if k == "category" and v
        }
        return result

    except Exception:
        return {"search_query": user_question, "filters": {}}



def build_chroma_where(filters: dict) -> Optional[dict]:

    if filters.get("category"):
        return {"category": {"$eq": filters["category"]}}
    return None


def rerank_results(
    query: str,
    docs: list[str],
    metas: list[dict],
) -> list[tuple[str, dict]]:
   
    n = min(TOP_K_FINAL, len(docs))
    try:
        resp = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
            headers={
                "Authorization": f"Bearer {QWEN_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": RERANK_MODEL,
                "input": {"query": query, "documents": docs},
                "parameters": {"top_n": n, "return_documents": False},
            },
            timeout=5,
        )
        resp.raise_for_status()
        items = resp.json()["output"]["results"]
        return [(docs[it["index"]], metas[it["index"]]) for it in items]
    except Exception:
        return list(zip(docs[:n], metas[:n]))


def retrieve_context(query_or_intent) -> tuple[str, bool]:

    if isinstance(query_or_intent, dict):
        search_query = query_or_intent.get("search_query", "")
        filters = query_or_intent.get("filters", {})
    else:
        search_query = query_or_intent
        filters = {}

    collection = _get_collection()
    if collection.count() == 0:
        return "", False

    embed_resp = _qwen.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[search_query],
        encoding_format="float",
    )
    query_embedding = embed_resp.data[0].embedding

    query_params = {
        "query_embeddings": [query_embedding],
        "n_results": min(TOP_K_INITIAL, collection.count()),
        "include": ["documents", "metadatas", "distances"],
    }

    where_clause = build_chroma_where(filters) if filters else None
    used_filter = False

    if where_clause:
        query_params["where"] = where_clause
        used_filter = True

    results = collection.query(**query_params)

    if used_filter and not results["documents"][0]:
        del query_params["where"]
        results = collection.query(**query_params)

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    filtered = [
        (doc, meta)
        for doc, meta, dist in zip(docs, metas, distances)
        if (1 - dist) >= SIMILARITY_THRESH
    ]

    if not filtered:
        return "", False

    if len(filtered) > TOP_K_FINAL:
        f_docs, f_metas = zip(*filtered)
        final = rerank_results(search_query, list(f_docs), list(f_metas))
    else:
        final = filtered

    context_parts = []
    for doc, meta in final:
        source = f"{meta.get('category', '')} · {meta.get('name', '')}"
        image_id = meta.get("image_id", "")
        img_instruction = f" (关联图片标识: {image_id})" if image_id else ""
        context_parts.append(f"【{source}】\n{doc}{img_instruction}")

    return "\n\n".join(context_parts), True


# ── 3. 构建系统 Prompt ────────────────────────────────────────────────────────
_SYSTEM_TEMPLATE ="""\
你是一位专业的甘薯种植与病害防治专家助手，服务于广大农户。
【当前系统时间】: {current_date}
【本地知识库片段】:
{context}

{farm_info}

【回答要求】:
1. 严格基于本地知识库片段回答，保持专业、准确、通俗易懂。
2. 当回答涉及多个病害、虫害、草害、品种或灾害时，优先详细介绍检索相关度更高的片段内容。
3. 当检索不到相关知识片段时，明确告知用户"未在本地知识库检索到相关知识片段"，然后用自己的通用农业知识来回答问题。
4. 如果用户的问题与甘薯无关，请礼貌拒绝并引导用户提问甘薯相关问题。
5. 如果提供了用户农场信息，请结合当地气候、土壤特点给出针对性建议。
6. 当用户询问天气、气温、降雨、是否适合打药等时效性问题时，你可以调用工具获取实时天气信息。

【图片插入要求 (极其严格)】:
在回答正文中，每当你详细介绍某种病害或农事操作时，你必须检查【本地知识库片段】的标题中是否为其标注了"(关联图片标识: xxx)"。
如果标注了，你必须在该段落末尾紧接着插入对应的图片标记，格式严格为：[图片:xxx]。
例如：如果你介绍了软腐病，且片段标题显示为"(关联图片标识: soft_rot)"，则写 [图片:soft_rot]。
绝对禁止捏造、猜测或输出片段中未提供的图片标识。如果片段未提供标识，则不插入任何标记。\
"""

def build_system_prompt(context: str, farm_context: str = None) -> str:
    farm_info = ""
    if farm_context:
        farm_info = f"【{farm_context}】"
    real_current_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日")
    return _SYSTEM_TEMPLATE.format(current_date=real_current_date,
        context=context if context else "（本次查询未检索到相关知识片段）",
        farm_info=farm_info
    )


# ── 4. 对话历史滑动窗口 ───────────────────────────────────────────────────────
def trim_history(history: list[dict]) -> list[dict]:
 

    max_messages = MAX_HISTORY_TURNS * 2
    return history[-max_messages:] if len(history) > max_messages else history



_IMAGE_TAG_RE = re.compile(r'\[图片:(\w+)\]')


def extract_images_and_clean(raw_answer: str) -> tuple[str, list[str]]:
    images: list[str] = []
    segments: list[dict] = []
    seen: set[str] = set()
    last_end = 0

    for m in _IMAGE_TAG_RE.finditer(raw_answer):
        base_id = m.group(1) 
        if base_id in seen:
            text_before = raw_answer[last_end:m.start()].strip()
            if text_before:
                if segments and segments[-1]["type"] == "text":
                    segments[-1]["content"] += "\n" + text_before
                else:
                    segments.append({"type": "text", "content": text_before})
            last_end = m.end()
            continue
            
        seen.add(base_id)

        dir_path = os.path.join(STATIC_DIR, base_id)
        final_relative_path = ""

        if os.path.isdir(dir_path):

            valid_exts = ('.jpg', '.jpeg', '.png')
            valid_files = [f for f in os.listdir(dir_path) if f.lower().endswith(valid_exts)]
            
            if valid_files:

                chosen_file = random.choice(valid_files)
                final_relative_path = f"{base_id}/{chosen_file}"        
        images.append(final_relative_path)
        
        text_before = raw_answer[last_end:m.start()].strip()
        if text_before:
            segments.append({"type": "text", "content": text_before})

        segments.append({"type": "image", "id": final_relative_path})
        last_end = m.end()
    remaining = raw_answer[last_end:].strip()
    if remaining:
        segments.append({"type": "text", "content": remaining})

    clean_answer = _IMAGE_TAG_RE.sub("", raw_answer).strip()

    if not segments:
        segments = [{"type": "text", "content": clean_answer}]

    return clean_answer, images, segments


async def chat_stream(
    user_question: str,
    history: list[dict],
    mode: str = "pro",  
    farm_context: str = None,  
) -> AsyncGenerator[dict, None]:
    
    import asyncio
    loop = asyncio.get_running_loop()

    if mode == "flash":
      
        context, kb_hit = await loop.run_in_executor(None, retrieve_context, user_question)
        chat_model = CHAT_MODEL_FLASH
    else:
    
        intent_data = await loop.run_in_executor(None, analyze_query_intent, user_question)
        context, kb_hit = await loop.run_in_executor(None, retrieve_context, intent_data)
        chat_model = CHAT_MODEL_PRO


    system_prompt = build_system_prompt(context, farm_context)
    trimmed_history = trim_history(history)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(trimmed_history)
    messages.append({"role": "user", "content": user_question})

    
    stream = await _qwen_async.chat.completions.create(
        model=chat_model,
        messages=messages,
        tools=TOOLS,
        stream=True,
        temperature=0.7,
        max_tokens=1500,
        extra_body={"enable_thinking": False},
    )

    raw_answer_parts: list[str] = []
    tool_calls_acc: dict = {}

    async for chunk in stream:
        delta = chunk.choices[0].delta
 
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": "", "type": "function",
                                           "function": {"name": "", "arguments": ""}}
                if tc.id:
                    tool_calls_acc[idx]["id"] = tc.id
                if tc.function and tc.function.name:
                    tool_calls_acc[idx]["function"]["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments
    
        if delta.content:
            raw_answer_parts.append(delta.content)
            yield {"type": "text", "content": delta.content}

    if tool_calls_acc:
       
        tool_calls = list(tool_calls_acc.values())
        messages.append({
            "role": "assistant",
            "content": "".join(raw_answer_parts),
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])
            tool_result = execute_tool(tool_name, tool_args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tool_name,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })
        raw_answer_parts = []
        stream2 = await _qwen_async.chat.completions.create(
            model=chat_model,
            messages=messages,
            stream=True,
            temperature=0.7,
            max_tokens=1500,
            extra_body={"enable_thinking": False},
        )
        async for chunk in stream2:
            delta = chunk.choices[0].delta
            if delta.content:
                raw_answer_parts.append(delta.content)
                yield {"type": "text", "content": delta.content}

    raw_answer = "".join(raw_answer_parts)
    clean_answer, images, segments = extract_images_and_clean(raw_answer)

    yield {
        "type": "done",
        "clean_answer": clean_answer,
        "images": images,
        "segments": segments,
        "kb_hit": kb_hit,
    }
