
import os
import base64
import json
from pathlib import Path
from typing import Dict, Optional
from dotenv import load_dotenv

load_dotenv()


QWEN_API_KEY = os.getenv("QWEN_API_KEY")
VL_MODEL = "qwen3-vl-flash-2026-01-22" 


_qwen_client: Optional[object] = None


def _get_qwen_client():
  
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
            "openai 库未安装"
        )


def analyze_image_with_vl(image_path: str, user_description: str = "") -> Dict:
    
    client = _get_qwen_client()

    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"图片文件不存在：{image_path}")

    prompt = f"""请分析这张图片,可能与以下相关：
1. 种植：图片显示种植环境、土壤、生长状况、田间管理等
2. 病害识别：图片显示甘薯的病害症状（如叶片病斑、根部腐烂）
3. 草害识别：图片显示与甘薯无关的杂草（（如马唐、香附子、牛筋草））
4. 品种识别：图片显示完整的甘薯块根，用于识别品种
5. 无关内容：图片与甘薯种植完全无关

用户补充描述：{user_description if user_description else "无"}

请以 JSON 格式返回：
{{
    "description": "详细描述图片内容",
    "category": "disease/variety/weedamage/cultivation/other",
    "confidence": 0.0-1.0,
    "keywords": ["关键词列表"]
}}"""
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

        content = response.choices[0].message.content

        if "```json" in content:
    
            json_start = content.find("```json") + 7
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        elif "```" in content:
      
            json_start = content.find("```") + 3
            json_end = content.find("```", json_start)
            json_str = content[json_start:json_end].strip()
        else:
            json_str = content.strip()

        result = json.loads(json_str)

       
        required_fields = ["description", "category", "confidence", "keywords"]
        for field in required_fields:
            if field not in result:
                raise ValueError(f"VL 响应缺少必需字段：{field}")


        valid_categories = [ "disease", "variety", "weedamage", "cultivation", "other"]
        if result["category"] not in valid_categories:
            result["category"] = "other"
        return result

    except json.JSONDecodeError as e:
  

        return {
            "description": content,
            "category": "other",
               "confidence": 0.5,
            "keywords": []
        }
        
    except Exception as e:
        print(f"[VL] API 调用失败：{e}")
        raise RuntimeError(f"Qwen-VL API 调用失败：{str(e)}")


if __name__=="__main__":
    
    test_image = Path(__file__).parent.parent / "static" / "images" / "cellar_storage"/"1.jpg"

    if test_image.exists():
        result = analyze_image_with_vl(str(test_image), "这是啥")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"测试图片不存在：{test_image}")
