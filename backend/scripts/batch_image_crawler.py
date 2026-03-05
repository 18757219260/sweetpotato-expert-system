"""
scripts/batch_image_crawler.py - 批量图片爬取脚本



用法：
    python backend/scripts/batch_image_crawler.py           # 爬取所有缺失的图片
    python backend/scripts/batch_image_crawler.py --force   # 强制重新爬取所有
    python backend/scripts/batch_image_crawler.py --limit 10 # 仅爬取前10个
"""

import os
import json
import time
import argparse
import re
import urllib.parse
from pathlib import Path
from typing import List, Set
import requests

# ── 配置 ─────────────────────────────────────────────────────────────────────
KB_PATH = Path(__file__).parent.parent / "data" / "knowledge_base.json"
IMAGES_DIR = Path(__file__).parent.parent / "static" / "images"
MAX_IMAGES_PER_ID = 50
REQUEST_DELAY = 1.0  # 每个 image_id 之间延迟（秒），避免被封


def download_images_for_id(image_id: str, save_dir: Path, existing_count: int = 0, max_attempts: int = 50, chinese_name: str = "") -> int:
    """
    为单个 image_id 下载图片

    Args:
        image_id: 图片 ID
        query: 搜索关键词
        save_dir: 保存目录
        existing_count: 已有图片数量
        max_attempts: 最大尝试次数

    Returns:
        成功下载的图片数量
    """
    save_dir.mkdir(parents=True, exist_ok=True)

    # 优先使用中文名称搜索，如果没有则使用英文
    search_query = f"甘薯 {chinese_name}" 

    # 构造必应图片搜索 URL，增加结果数量
    encoded_query = urllib.parse.quote(search_query)
    url = f"https://www.bing.com/images/search?q={encoded_query}&form=HDRSC2&first=1&count=150"

    print(f"  实际搜索词: {search_query}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.5"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  ❌ 搜索请求失败: {e}")
        return 0

    # 提取图片 URL
    img_urls = re.findall(r'murl&quot;:&quot;(.*?)&quot;', response.text)
    img_urls = list(dict.fromkeys(img_urls))  # 去重

    print(f"  找到 {len(img_urls)} 个图片链接")

    success_count = 0
    attempt_count = 0
    file_index = existing_count  # 从已有数量开始编号

    for img_url in img_urls:
        # 达到最大尝试次数后停止
        if attempt_count >= max_attempts:
            break

        # 计入尝试次数（每个链接都计数，不管是否被过滤）
        attempt_count += 1

        # 过滤非 jpg/png 格式（但已计入尝试次数）
        if any(img_url.lower().endswith(ext) for ext in ['.gif', '.webp', '.svg']):
            print(f"  [{attempt_count}/50] 跳过（格式不支持）")
            continue

        try:
            # 设置更完整的请求头，模拟真实浏览器
            download_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://www.bing.com/",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
            }

            img_response = requests.get(img_url, headers=download_headers, timeout=5, stream=True)
            img_response.raise_for_status()

            # 验证 Content-Type
            content_type = img_response.headers.get('Content-Type', '').lower()
            if 'image/jpeg' in content_type:
                ext = '.jpg'
            elif 'image/png' in content_type:
                ext = '.png'
            else:
                # 格式不符合，跳到下一个链接
                print(f"  [{attempt_count}/50] 跳过（Content-Type: {content_type}）")
                continue

            # 保存图片（使用递增的文件索引）
            file_path = save_dir / f"{file_index}{ext}"
            with open(file_path, 'wb') as f:
                for chunk in img_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            success_count += 1
            file_index += 1
            print(f"  [{attempt_count}/50] ✓ 成功下载 ({success_count} 张)")

        except Exception as e:
            # 任何错误都立即跳到下一个链接，不重试
            print(f"  [{attempt_count}/50] ✗ 失败: {str(e)[:50]}")
            continue

    return success_count


def extract_image_ids(kb_path: Path) -> List[tuple]:
    """从知识库提取所有 image_id 和对应的中文名称"""
    with open(kb_path, 'r', encoding='utf-8') as f:
        records = json.load(f)

    # 返回 (image_id, chinese_name) 元组列表
    image_data = {}
    for r in records:
        if r.get('image_id'):
            image_id = r['image_id']
            chinese_name = r.get('name', '')
            # 如果同一个 image_id 出现多次，保留第一个
            if image_id not in image_data:
                image_data[image_id] = chinese_name

    return sorted([(k, v) for k, v in image_data.items()])


def get_existing_image_count(image_dir: Path) -> int:
    """获取指定目录中已有的有效图片数量"""
    if not image_dir.exists() or not image_dir.is_dir():
        return 0

    valid_images = [
        f for f in image_dir.iterdir()
        if f.is_file() and f.suffix.lower() in ['.jpg', '.jpeg', '.png']
    ]
    return len(valid_images)





def main():
    parser = argparse.ArgumentParser(description="批量爬取甘薯知识库图片")
    parser.add_argument("--force", action="store_true", help="强制重新爬取所有图片")
   
    args = parser.parse_args()

    # 1. 提取所有 image_id 和中文名称
    print(f"[1/4] 读取知识库：{KB_PATH}")
    all_image_data = extract_image_ids(KB_PATH)
    print(f"      共找到 {len(all_image_data)} 个 image_id")

    # 2. 检查已有图片并筛选需要下载的
    print(f"[2/4] 检查已有图片...")
    to_download = []
    skip_count = 0

    for image_id, chinese_name in all_image_data:
        image_dir = IMAGES_DIR / image_id
        existing_count = get_existing_image_count(image_dir)

        if not args.force and existing_count > 0:
            # 已有图片，跳过
            skip_count += 1
        else:
            # 没有图片，需要下载
            to_download.append((image_id, chinese_name, existing_count))

    print(f"      已有图片的 image_id: {skip_count} 个，将跳过")
    print(f"      没有图片的 image_id: {len(to_download)} 个")


    print(f"[3/4] 需要处理 {len(to_download)} 个 image_id")

    if not to_download:
        print("✅ 所有图片已充足，无需下载")
        return

    # 3. 批量下载
    print(f"[4/4] 开始批量下载...")
    success_count = 0
    fail_count = 0

    for i, (image_id, chinese_name, existing_count) in enumerate(to_download, 1):
        
        save_dir = IMAGES_DIR / image_id

        print(f"\n[{i}/{len(to_download)}] {image_id} ({chinese_name})")
        print(f"  已有图片: {existing_count} 张")
        print(f"  将尝试 50 次请求...")

        downloaded = download_images_for_id(
            image_id, save_dir, existing_count, max_attempts=50, chinese_name=chinese_name
        )

        total_count = existing_count + downloaded
        print(f"  ✅ 本次下载 {downloaded} 张，总计 {total_count} 张")

        if downloaded > 0:
            success_count += 1
        else:
            fail_count += 1

        # 延迟避免被封
        if i < len(to_download):
            time.sleep(REQUEST_DELAY)

    # 4. 总结
    print("\n" + "="*60)
    print(f"✅ 成功下载: {success_count} 个 image_id")
    print(f"⚠️  下载失败: {fail_count} 个 image_id")
    print(f"📁 图片保存路径: {IMAGES_DIR.absolute()}")
    print("="*60)


if __name__ == "__main__":
    main()

