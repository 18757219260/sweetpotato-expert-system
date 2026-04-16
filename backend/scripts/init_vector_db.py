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
from openai import OpenAI  

load_dotenv()

# ── 配置 ─────────────────────────────────────────────────────────────────────
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "")
KB_PATH = Path(__file__).parent.parent /"data" / "knowledge_base.json"
COLLECTION_NAME = "sweet_potato_knowledge"
EMBEDDING_MODEL = "text-embedding-v3"
EMBED_BATCH_SIZE = 10 

# ── 通义千问 Embedding 客户端 ─────────────────────────────────────────────────
qwen_client = OpenAI(
    api_key=QWEN_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)


def build_full_text(record: dict) -> str:
    parts = [
        f"名称：{record.get('name', '')}",
        f"类别：{record.get('category', '')}",
    ]
    if record.get("aliases"):
        parts.append(f"别名：{'、'.join(record['aliases'])}")

    symptoms = record.get("symptoms", {})
    if isinstance(symptoms, dict):
        if symptoms.get("description"):
            parts.append(f"症状描述：{symptoms['description']}")
        if symptoms.get("differential_diagnosis"):
            parts.append(f"鉴别诊断：{symptoms['differential_diagnosis']}")
    elif isinstance(symptoms, str):  # 向后兼容
        parts.append(f"症状：{symptoms}")

    if record.get("causes"):
        parts.append(f"原因：{record['causes']}")

    control = record.get("control_measures", {})
    if isinstance(control, dict):
        if control.get("preventive"):
            parts.append(f"预防措施：{'；'.join(control['preventive'])}")
        if control.get("chemical"):
            parts.append(f"化学防治：{'；'.join(control['chemical'])}")
    elif record.get("treatment"): 
        parts.append(f"防治方法：{record['treatment']}")
    elif record.get("prevention"): 
        parts.append(f"预防措施：{record['prevention']}")


    if record.get("growth_stages"):
        parts.append(f"生育期：{'、'.join(record['growth_stages'])}")
    if record.get("environmental_factors"):
        parts.append(f"环境因素：{'、'.join(record['environmental_factors'])}")
    if record.get("applicable_regions"):
        parts.append(f"适用地区：{'、'.join(record['applicable_regions'])}")
    if record.get("soil_types"):
        parts.append(f"土壤类型：{'、'.join(record['soil_types'])}")

    return "\n".join(parts)


def compute_chunk_id(record_id: str, chunk_index: int, chunk_text: str) -> str:
    content = f"{record_id}_{chunk_index}_{chunk_text}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def get_embeddings(texts: list[str]) -> list[list[float]]:
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
            time.sleep(0.5)  
    return all_embeddings


def init_vector_db(reset: bool = False):
    """主入库流程 - 已修改为强制全量重新向量化"""

    os.makedirs(CHROMA_DB_PATH, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    try:
        chroma_client.delete_collection(COLLECTION_NAME)
        print(f"[全量更新] 已删除旧集合，开始重新向量化：{COLLECTION_NAME}")
    except Exception:
        pass

    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}, 
    )


    with open(KB_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)
    print(f"[加载] 读取知识库记录 {len(records)} 条")


    to_embed_texts: list[str] = []
    to_embed_ids: list[str] = []
    to_embed_metas: list[dict] = []

    for record in records:
        full_text = build_full_text(record)
        entity_id = compute_chunk_id(record["id"], 0, full_text)

        to_embed_texts.append(full_text)
        to_embed_ids.append(entity_id)
        to_embed_metas.append({
            "record_id": record["id"],
            "name": record.get("name", ""),
            "category": record.get("category", ""),
            "image_id": record.get("image_id", ""),
            "keywords": ",".join(record.get("keywords", [])),
            "growth_stages": ",".join(record.get("growth_stages", [])),
            "environmental_factors": ",".join(record.get("environmental_factors", [])),
            "applicable_regions": ",".join(record.get("applicable_regions", [])),
        })

    if not to_embed_texts:
        print("[完成] 知识库无内容")
        return

    print(f"[向量化] 重新处理 {len(to_embed_texts)} 个实体，开始调用 Embedding API...")


    embeddings = get_embeddings(to_embed_texts)

    collection.add(
        ids=to_embed_ids,
        embeddings=embeddings,
        documents=to_embed_texts,
        metadatas=to_embed_metas,
    )

    print(f"[完成] 成功重新入库 {len(to_embed_texts)} 个实体，ChromaDB 总计 {collection.count()} 个")


def query_test(query: str, n_results: int = 3):

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
        print(f"[{i+1}] 相似度: {1 - dist:.4f} | 来源: {meta['name']} ({meta['category']})")
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