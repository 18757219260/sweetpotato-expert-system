"""
scripts/init_vector_db.py - 甘薯知识库向量化入库脚本

功能：
1. 读取 backend/data/knowledge_base.json
2. 对每条记录进行文本分块（500字/块，50字重叠）
3. 调用通义千问 Embedding 接口向量化
4. 基于 chunk ID 的增量更新，避免重复调用浪费 Token
5. 持久化存入 ChromaDB

用法：
    python scripts/init_vector_db.py           # 增量更新
    python scripts/init_vector_db.py --reset   # 清空后重建
"""

# !! 必须在所有 import 之前打 ChromaDB SQLite 补丁 !!
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

import chromadb
from dotenv import load_dotenv
from openai import OpenAI  # 通义千问兼容 OpenAI SDK

load_dotenv()

# ── 配置 ─────────────────────────────────────────────────────────────────────
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./backend/data/chroma_db")
KB_PATH = Path(__file__).parent.parent /"data" / "knowledge_base.json"
COLLECTION_NAME = "sweet_potato_knowledge"
EMBEDDING_MODEL = "text-embedding-v3"
CHUNK_SIZE = 500      # 每块字符数
CHUNK_OVERLAP = 50    # 块间重叠字符数
EMBED_BATCH_SIZE = 10 # 每批调用 Embedding 数量（避免超限）

# ── 通义千问 Embedding 客户端 ─────────────────────────────────────────────────
qwen_client = OpenAI(
    api_key=QWEN_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """将文本按字符数切分为重叠块"""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def build_full_text(record: dict) -> str:
    """将知识库记录各字段拼接为完整文本，供向量化使用"""
    parts = [
        f"名称：{record.get('name', '')}",
        f"类别：{record.get('category', '')}",
    ]

    # 处理别名（新字段）
    if record.get("aliases"):
        parts.append(f"别名：{'、'.join(record['aliases'])}")

    # 处理嵌套症状对象
    symptoms = record.get("symptoms", {})
    if isinstance(symptoms, dict):
        if symptoms.get("description"):
            parts.append(f"症状描述：{symptoms['description']}")
        if symptoms.get("differential_diagnosis"):
            parts.append(f"鉴别诊断：{symptoms['differential_diagnosis']}")
    elif isinstance(symptoms, str):  # 向后兼容
        parts.append(f"症状：{symptoms}")

    # 处理原因
    if record.get("causes"):
        parts.append(f"原因：{record['causes']}")

    # 处理嵌套防治措施对象
    control = record.get("control_measures", {})
    if isinstance(control, dict):
        if control.get("preventive"):
            parts.append(f"预防措施：{'；'.join(control['preventive'])}")
        if control.get("chemical"):
            parts.append(f"化学防治：{'；'.join(control['chemical'])}")
    elif record.get("treatment"):  # 向后兼容
        parts.append(f"防治方法：{record['treatment']}")
    elif record.get("prevention"):  # 向后兼容
        parts.append(f"预防措施：{record['prevention']}")

    # 处理新增数组字段
    if record.get("growth_stages"):
        parts.append(f"生育期：{'、'.join(record['growth_stages'])}")
    if record.get("environmental_factors"):
        parts.append(f"环境因素：{'、'.join(record['environmental_factors'])}")
    if record.get("applicable_regions"):
        parts.append(f"适用地区：{'、'.join(record['applicable_regions'])}")
    if record.get("soil_types"):
        parts.append(f"土壤类型：{'、'.join(record['soil_types'])}")

    # 处理关键词
    if record.get("keywords"):
        parts.append(f"关键词：{'、'.join(record['keywords'])}")

    return "\n".join(parts)


def compute_chunk_id(record_id: str, chunk_index: int, chunk_text: str) -> str:
    """基于内容哈希生成 chunk ID，实现增量更新检测"""
    content = f"{record_id}_{chunk_index}_{chunk_text}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """批量调用通义千问 Embedding 接口"""
    all_embeddings = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i: i + EMBED_BATCH_SIZE]
        response = qwen_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=batch,
            encoding_format="float",
        )
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)
        if i + EMBED_BATCH_SIZE < len(texts):
            time.sleep(0.5)  # 避免触发 QPS 限制
    return all_embeddings


def init_vector_db(reset: bool = False):
    """主入库流程"""
    # 1. 初始化 ChromaDB
    os.makedirs(CHROMA_DB_PATH, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    if reset:
        try:
            chroma_client.delete_collection(COLLECTION_NAME)
            print(f"[重置] 已删除旧集合：{COLLECTION_NAME}")
        except Exception:
            pass

    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
    )

    # 2. 读取知识库
    with open(KB_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)
    print(f"[加载] 读取知识库记录 {len(records)} 条")

    # 3. 获取已存在的 chunk ID（用于增量跳过）
    existing_ids: set[str] = set()
    if collection.count() > 0:
        existing = collection.get(include=[])
        existing_ids = set(existing["ids"])
        print(f"[增量] ChromaDB 中已有 {len(existing_ids)} 个 chunk，将跳过重复项")

    # 4. 构建待入库的 chunk 列表
    to_embed_texts: list[str] = []
    to_embed_ids: list[str] = []
    to_embed_metas: list[dict] = []

    for record in records:
        full_text = build_full_text(record)
        chunks = chunk_text(full_text)

        for idx, chunk in enumerate(chunks):
            chunk_id = compute_chunk_id(record["id"], idx, chunk)

            if chunk_id in existing_ids:
                continue  # 内容未变，跳过

            to_embed_texts.append(chunk)
            to_embed_ids.append(chunk_id)
            to_embed_metas.append({
                "record_id": record["id"],
                "name": record.get("name", ""),
                "category": record.get("category", ""),
                "image_id": record.get("image_id", ""),
                "chunk_index": idx,
                "keywords": ",".join(record.get("keywords", [])),
                "growth_stages": ",".join(record.get("growth_stages", [])),
                "environmental_factors": ",".join(record.get("environmental_factors", [])),
                "applicable_regions": ",".join(record.get("applicable_regions", [])),
            })

    if not to_embed_texts:
        print("[完成] 知识库无更新，无需重新入库")
        return

    print(f"[向量化] 需要处理 {len(to_embed_texts)} 个新 chunk，开始调用 Embedding API...")

    # 5. 批量获取 Embedding
    embeddings = get_embeddings(to_embed_texts)

    # 6. 写入 ChromaDB
    collection.add(
        ids=to_embed_ids,
        embeddings=embeddings,
        documents=to_embed_texts,
        metadatas=to_embed_metas,
    )

    print(f"[完成] 成功入库 {len(to_embed_texts)} 个 chunk，ChromaDB 总计 {collection.count()} 个")


def query_test(query: str, n_results: int = 3):
    """简单检索测试"""
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    collection = chroma_client.get_collection(COLLECTION_NAME)

    response = qwen_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[query],
        encoding_format="float",
    )
    query_embedding = response.data[0].embedding

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    print(f"\n[检索测试] 查询：'{query}'")
    print("-" * 60)
    for i, (doc, meta, dist) in enumerate(zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    )):
        print(f"[{i+1}] 相似度: {1 - dist:.4f} | 来源: {meta['name']} (chunk {meta['chunk_index']})")
        print(f"     内容: {doc[:100]}...")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="甘薯知识库向量化入库工具")
    parser.add_argument("--reset", action="store_true", help="清空 ChromaDB 后重新全量入库")
    parser.add_argument("--test", type=str, default=None, help="入库后执行检索测试，传入查询词")
    args = parser.parse_args()

    init_vector_db(reset=args.reset)

    if args.test:
        query_test(args.test)
