import os

from aip import AipSpeech
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/voice", tags=["voice"])

_client = AipSpeech(
    os.getenv("BD_APP_ID"),
    os.getenv("BD_API_KEY"),
    os.getenv("BD_SECRET_KEY"),
)


@router.post("/recognize")
async def recognize(file: UploadFile = File(...)):
    audio_bytes = await file.read()
    result = _client.asr(audio_bytes, 'wav', 16000, {'dev_pid': 1537})
    if result.get('err_no') == 0:
        return {"text": result['result'][0]}
    return JSONResponse(status_code=400, content={"detail": result.get('err_msg', '识别失败')})
