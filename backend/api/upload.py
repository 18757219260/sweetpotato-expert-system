
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

RATE_LIMIT = os.getenv("RATE_LIMIT_PER_DAY", "100")
limiter = Limiter(key_func=rate_limit_key)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# 临时文件存储目录
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _sse(data: dict) -> str:
    """SSE 格式化"""
    import json
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


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
        a,top_confidence = results[0]
        cv_text = format_classification_result(results)

        if top_confidence < 0.85:
            # 低置信度：调用 qwen-vl，不传 resnet 结果
            from backend.services.vl_service import analyze_image_with_vl
            vl_result = analyze_image_with_vl(image_path, description)
            vl_desc = vl_result.get("description", "")
            if description:
                question = f"""图片分析结果：{vl_desc}\n\n用户补充描述：{description}\n\n请你作为甘薯种植专家，根据以上信息给出专业建议。"""
            else:
                question = f"""图片分析结果：{vl_desc}\n\n请你作为甘薯种植专家，根据以上信息给出专业建议。"""
            from backend.api.chat import _generate
            async for chunk in _generate(question, user_id, db, mode, session_id):
                yield chunk
            return

        if description:
            question = f"""图片识别结果显示：{cv_text}

用户补充描述：{description}

请你作为甘薯病害专家，首先明确告知用户图片识别的结果和最高概率，然后详细说明该病害的症状、成因和防治方法。如果用户描述与识别结果有出入，请结合两者综合分析。"""
        else:
            question = f"""图片识别结果显示：{cv_text}

请你作为甘薯病害专家，首先明确告知用户图片识别的结果和最高概率，然后详细说明该病害的症状、成因和防治方法。"""

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
    session_id: Optional[str] = Form(None),
    description: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):


    # 1. 验证文件类型
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持图片文件")

    # 2. 保存临时文件
    MAX_UPLOAD_SIZE = 10 * 1024 * 1024
    file_ext = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
    temp_filename = f"{uuid.uuid4()}{file_ext}"
    temp_path = UPLOAD_DIR / temp_filename

    try:
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="图片文件不能超过 10MB")
        with open(temp_path, "wb") as f:
            f.write(content)

        user_id = current_user.id
        session_id_int = int(session_id) if session_id and session_id != "null" else None

        # 3. 返回流式响应
        return StreamingResponse(
            _generate_from_image(str(temp_path), user_id, db, mode, session_id_int, description),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as e:
        # 如果保存失败，立即清理
        if temp_path.exists():
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"文件处理失败：{str(e)}")
