"""
backend/api/upload.py - 图片上传与 CV 检测接口

POST /api/chat/upload_image
  - 接收图片文件
  - 调用 CV 模型分类
  - 将分类结果转化为文本上下文
  - 调用 LLM 生成诊断建议
  - 返回流式响应（与 /api/chat/stream 格式一致）
"""

import os
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, File, UploadFile, Request, HTTPException, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional

from backend.api.deps import get_current_user, get_db, rate_limit_key
from backend.database import User
from slowapi import Limiter

RATE_LIMIT = os.getenv("RATE_LIMIT_PER_DAY", "20")
limiter = Limiter(key_func=rate_limit_key)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# 临时文件存储目录
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _sse(data: dict) -> str:
    """SSE 格式化"""
    import json
    return f"data: {json.dumps(data, ensure_ascii=False)}\\n\\n"


async def _generate_from_image(
    image_path: str,
    user_id: int,
    db: Session,
    mode: str = "pro",
    session_id: Optional[int] = None,
    description: str = "",
):
    """
    基于图片分类结果生成诊断建议（流式）
    """
    try:
        # 1. CV 模型推理
        from backend.services.cv_service import classify_image, format_classification_result

        results = classify_image(image_path, top_k=3)
        top_class, top_confidence = results[0]
        cv_text = format_classification_result(results)

        # 2. 置信度阈值判断
        if top_confidence < 0.50:
            # 置信度过低，不调用 LLM
            yield _sse({"type": "content", "content": f"🔍 识别结果：\n{cv_text}\n\n"})
            yield _sse({"type": "content", "content": "图片识别概率较低，建议上传更清晰的甘薯病害图片哦~ 📷"})
            yield _sse({"type": "done", "images": []})
            return

        # 3. 构造提问文本（整合 CV 结果和用户描述）
        if description:
            question = f"""图片识别结果显示：{cv_text}

用户补充描述：{description}

请你作为甘薯病害专家，首先明确告知用户图片识别的结果和概率，然后详细说明该病害的症状、成因和防治方法。如果用户描述与识别结果有出入，请结合两者综合分析。"""
        else:
            question = f"""图片识别结果显示：{cv_text}

请你作为甘薯病害专家，首先明确告知用户图片识别的结果和概率，然后详细说明该病害的症状、成因和防治方法。"""

        # 4. 调用 LLM 流式生成（复用 chat.py 的逻辑）
        from backend.api.chat import _generate

        async for chunk in _generate(question, user_id, db, mode, session_id):
            yield chunk

    except FileNotFoundError as e:
        yield _sse({"type": "error", "detail": f"CV 模型未找到：{str(e)}"})
    except ImportError as e:
        yield _sse({"type": "error", "detail": f"缺少依赖库：{str(e)}"})
    except Exception as e:
        yield _sse({"type": "error", "detail": f"图片识别失败：{str(e)}"})

    finally:
        # 清理临时文件
        if os.path.exists(image_path):
            os.remove(image_path)


@router.post("/upload_image")
@limiter.limit(f"{RATE_LIMIT}/day")
async def upload_image_endpoint(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("pro"),
    session_id: Optional[str] = Form(None),  # 改为 str，因为 Form 数据都是字符串
    stream: bool = Form(False),  # 新增：是否返回流式响应
    description: str = Form(""),  # 新增：用户补充的文字描述
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    图片上传 + CV 检测 + LLM 诊断

    支持两种响应模式：
    1. stream=true: SSE 流式响应（适用于 Web 前端）
    2. stream=false: 完整 JSON 响应（适用于微信小程序 wx.uploadFile）

    前端调用方式：
    wx.uploadFile({
        url: `${API_BASE}/api/chat/upload_image`,
        filePath: tempFilePath,
        name: 'file',
        formData: { mode: 'pro', session_id: 123, stream: 'false', description: '叶片发黄' },
        header: { Authorization: `Bearer ${token}` },
        success: (res) => { /* 处理 JSON 响应 */ }
    })
    """
    # 1. 验证文件类型
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持图片文件")

    # 2. 保存临时文件
    file_ext = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
    temp_filename = f"{uuid.uuid4()}{file_ext}"
    temp_path = UPLOAD_DIR / temp_filename

    try:
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        user_id = current_user.id

        # 转换 session_id 为整数（如果提供）
        session_id_int = int(session_id) if session_id and session_id != "null" else None

        # 3. 根据 stream 参数选择响应模式
        if stream:
            # 流式响应（Web 前端）
            return StreamingResponse(
                _generate_from_image(str(temp_path), user_id, db, mode, session_id_int, description),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # 完整响应（微信小程序）
            from backend.services.cv_service import classify_image, format_classification_result
            from backend.api.chat import _generate
            import json

            # 步骤 1：先用 ResNet18 识别
            print(f"[CV] 开始识别图片：{temp_path}")
            results = classify_image(str(temp_path), top_k=3)
            top_class, top_confidence = results[0]
            cv_text = format_classification_result(results)
            print(f"[CV] Top prediction: {top_class}, confidence: {top_confidence:.2%}")

            # 步骤 2：根据置信度选择处理流程
            if top_confidence >= 0.50:
                # 置信度足够 - 正常流程：CV 识别 + 知识库 + LLM
                print(f"[CV] 置信度足够，使用 CV 识别结果")

                # 清理临时文件
                if os.path.exists(temp_path):
                    os.remove(temp_path)

                # 构造提问（整合 CV 结果和用户描述）
                if description:
                    question = f"""图片识别结果：{cv_text}

用户补充描述：{description}

请你作为甘薯病害专家，详细说明该病害的症状、成因和防治方法。如果用户描述与识别结果有出入，请结合两者综合分析。"""
                else:
                    question = f"""图片识别结果：{cv_text}

请你作为甘薯病害专家，详细说明该病害的症状、成因和防治方法。"""

                print(f"[DEBUG] Question for LLM:\n{question}\n")
                print(f"[DEBUG] Starting to collect LLM response...")

                # 收集 LLM 响应
                full_response = ""
                images = []
                segments = []
                chunk_count = 0
                try:
                    async for chunk in _generate(question, user_id, db, mode, session_id_int):
                        chunk_count += 1
                        print(f"[DEBUG] Received chunk {chunk_count}: {chunk[:100]}...")
                        if chunk.startswith("data: "):
                            json_str = chunk[6:].strip()
                            data = json.loads(json_str)
                            print(f"[DEBUG] Chunk {chunk_count}: type={data.get('type')}, content_len={len(data.get('content', ''))}")
                            if data.get("type") == "text":
                                content = data.get("content", "")
                                full_response += content
                                print(f"[DEBUG] Added {len(content)} chars to response")
                            elif data.get("type") == "done":
                                segments = data.get("segments", [])
                                images = data.get("images", [])
                                print(f"[DEBUG] Got done event with {len(segments)} segments and {len(images)} images")
                except Exception as e:
                    print(f"[DEBUG] Error collecting LLM response: {e}")
                    import traceback
                    traceback.print_exc()

                print(f"[DEBUG] Total chunks: {chunk_count}, full_response length: {len(full_response)}")
                print(f"[DEBUG] Segments: {len(segments)}, Images: {len(images)}")

                return {
                    "type": "success",
                    "cv_result": cv_text,
                    "llm_response": full_response,
                    "segments": segments,
                    "images": images,
                    "top_predictions": [{"class": cls, "confidence": conf} for cls, conf in results]
                }

            else:
                # 置信度过低 - 调用 qwen-vl 理解图片内容
                print(f"[CV] 置信度过低 ({top_confidence:.2%})，调用 qwen-vl 分析")
                from backend.services.vl_service import analyze_image_with_vl

                vl_result = analyze_image_with_vl(str(temp_path), description)
                print(f"[VL] Description: {vl_result['description']}")

                # 清理临时文件
                if os.path.exists(temp_path):
                    os.remove(temp_path)

                # 使用 VL 描述 + CV 结果（作为参考）结合知识库给出建议
                question = f"""图片识别结果（概率较低，仅供参考）：{cv_text}

图片内容描述：{vl_result['description']}

用户描述：{description if description else "无"}

虽然图片识别概率较低，但请根据图片内容描述、识别结果和用户描述，靠自己综合分析判断是否输出有关甘薯信息。"""

                print(f"[DEBUG] Question for LLM:\n{question}\n")
                print(f"[DEBUG] Starting to collect LLM response...")

                # 收集 LLM 响应
                full_response = ""
                images = []
                segments = []
                chunk_count = 0
                try:
                    async for chunk in _generate(question, user_id, db, mode, session_id_int):
                        chunk_count += 1
                        print(f"[DEBUG] Received chunk {chunk_count}: {chunk[:100]}...")
                        if chunk.startswith("data: "):
                            json_str = chunk[6:].strip()
                            data = json.loads(json_str)
                            print(f"[DEBUG] Chunk {chunk_count}: type={data.get('type')}, content_len={len(data.get('content', ''))}")
                            if data.get("type") == "text":
                                content = data.get("content", "")
                                full_response += content
                                print(f"[DEBUG] Added {len(content)} chars to response")
                            elif data.get("type") == "done":
                                segments = data.get("segments", [])
                                images = data.get("images", [])
                                print(f"[DEBUG] Got done event with {len(segments)} segments and {len(images)} images")
                except Exception as e:
                    print(f"[DEBUG] Error collecting LLM response: {e}")
                    import traceback
                    traceback.print_exc()

                print(f"[DEBUG] Total chunks: {chunk_count}, full_response length: {len(full_response)}")
                print(f"[DEBUG] Segments: {len(segments)}, Images: {len(images)}")

                return {
                    "type": "low_confidence",
                    "vl_description": vl_result['description'],
                    "llm_response": full_response,
                    "segments": segments,
                    "images": images
                }

    except Exception as e:
        # 如果保存失败，立即清理
        if temp_path.exists():
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"文件处理失败：{str(e)}")
