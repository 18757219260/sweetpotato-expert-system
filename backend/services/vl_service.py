"""
backend/services/vl_service.py - Qwen-VL 视觉语言模型服务

功能：
1. 使用通义千问 qwen3-vl-flash-2026-01-22 分析图片内容
2. 判断图片类型（病虫害识别、品种识别、种植相关、无关内容）
3. 返回图片描述和分类结果

依赖：
- openai>=1.0.0（通义千问兼容 OpenAI SDK）
"""

import os
import base64
import json
from pathlib import Path
from typing import Dict, Optional
from dotenv import load_dotenv

load_dotenv()

# ── 配置 ─────────────────────────────────────────────────────────────────────
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
VL_MODEL = "qwen3-vl-flash-2026-01-22"  # 通义千问最新视觉模型

# 全局客户端实例（懒加载）
_qwen_client: Optional[object] = None


def _get_qwen_client():
    """懒加载 Qwen 客户端"""
    global _qwen_client

    if _qwen_client is not None:
        return _qwen_client

    try:
        from openai import OpenAI

        if not QWEN_API_KEY:
            raise ValueError(
                "QWEN_API_KEY 未配置，请在 .env 文件中设置：\n"
                "QWEN_API_KEY=your_api_key_here"
            )

        _qwen_client = OpenAI(
            api_key=QWEN_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

        print(f"[VL] Qwen-VL 客户端初始化成功（模型: {VL_MODEL}）")
        return _qwen_client

    except ImportError:
        raise ImportError(
            "openai 库未安装，请运行: pip install openai>=1.0.0"
        )


def analyze_image_with_vl(image_path: str, user_description: str = "") -> Dict:
    """
    使用 qwen-vl 分析图片内容并分类

    Args:
        image_path: 图片文件路径
        user_description: 用户补充的文字描述（可选）

    Returns:
        {
            "description": "图片详细描述",
            "category": "disease_pest" | "variety" | "cultivation" | "other",
            "confidence": 0.95,
            "keywords": ["关键词1", "关键词2"]
        }
    """
    client = _get_qwen_client()

    # 1. 读取图片并转换为 base64
    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"图片文件不存在：{image_path}")

    # 2. 构造提示词
    prompt = f"""请分析这张图片，判断它属于以下哪个类别：

1. **病虫害识别**：图片显示甘薯的病害症状（如叶片病斑、根部腐烂）或虫害（如害虫、虫蛀痕迹）
2. **品种识别**：图片显示完整的甘薯块根，用于识别品种
3. **种植相关**：图片显示种植环境、土壤、生长状况、田间管理等
4. **无关内容**：图片与甘薯种植完全无关

用户补充描述：{user_description if user_description else "无"}

请以 JSON 格式返回：
{{
    "description": "详细描述图片内容",
    "category": "disease_pest/variety/cultivation/other",
    "confidence": 0.0-1.0,
    "keywords": ["关键词列表"]
}}"""

    # 3. 调用 Qwen-VL API
    try:
        response = client.chat.completions.create(
            model=VL_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }],
            temperature=0.1,
        )

        # 4. 解析响应
        content = response.choices[0].message.content

        # 尝试提取 JSON（可能包含在 markdown 代码块中）
        if "```json" in content:
            # 提取 ```json ... ``` 之间的内容
            json_start = content.find("```json") + 7
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        elif "```" in content:
            # 提取 ``` ... ``` 之间的内容
            json_start = content.find("```") + 3
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        else:
            json_str = content.strip()

        result = json.loads(json_str)

        # 验证必需字段
        required_fields = ["description", "category", "confidence", "keywords"]
        for field in required_fields:
            if field not in result:
                raise ValueError(f"VL 响应缺少必需字段：{field}")

        # 验证 category 值
        valid_categories = ["disease_pest", "variety", "cultivation", "other"]
        if result["category"] not in valid_categories:
            print(f"[VL] 警告：未知的 category 值 '{result['category']}'，默认为 'other'")
            result["category"] = "other"

        print(f"[VL] 分析完成：category={result['category']}, confidence={result['confidence']}")
        return result

    except json.JSONDecodeError as e:
        print(f"[VL] JSON 解析失败：{e}")
        print(f"[VL] 原始响应：{content}")
        # 返回默认结果
        return {
            "description": content,
            "category": "other",
            "confidence": 0.5,
            "keywords": []
        }
    except Exception as e:
        print(f"[VL] API 调用失败：{e}")
        raise RuntimeError(f"Qwen-VL API 调用失败：{str(e)}")


# ── 测试代码 ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 测试 VL 服务
    test_image = Path(__file__).parent.parent / "data" / "uploads" / "test.jpg"

    if test_image.exists():
        result = analyze_image_with_vl(str(test_image), "叶片发黄")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"测试图片不存在：{test_image}")
