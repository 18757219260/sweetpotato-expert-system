"""
backend/api/farm.py - 农场档案管理

GET  /api/farm/profile - 获取当前用户的农场档案
POST /api/farm/profile - 创建或更新农场档案
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from backend.api.deps import get_current_user, get_db
from backend.database import User, FarmProfile

router = APIRouter(prefix="/api/farm", tags=["farm"])


class FarmProfileRequest(BaseModel):
    province: str
    city: str
    district: str
    area_mu: Optional[float] = None
    soil_type: Optional[str] = None
    other_info: Optional[str] = None


class FarmProfileResponse(BaseModel):
    province: str
    city: str
    district: str
    area_mu: Optional[float]
    soil_type: Optional[str]
    other_info: Optional[str]


@router.get("/profile", response_model=FarmProfileResponse)
def get_farm_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的农场档案"""
    profile = db.query(FarmProfile).filter(FarmProfile.user_id == user.id).first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="农场档案不存在，请先创建"
        )

    return FarmProfileResponse(
        province=profile.province,
        city=profile.city,
        district=profile.district,
        area_mu=profile.area_mu,
        soil_type=profile.soil_type,
        other_info=profile.other_info,
    )


@router.post("/profile", response_model=FarmProfileResponse)
def save_farm_profile(
    body: FarmProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建或更新农场档案"""
    profile = db.query(FarmProfile).filter(FarmProfile.user_id == user.id).first()

    if profile:
        # 更新现有档案
        profile.province = body.province
        profile.city = body.city
        profile.district = body.district
        profile.area_mu = body.area_mu
        profile.soil_type = body.soil_type
        profile.other_info = body.other_info
    else:
        # 创建新档案
        profile = FarmProfile(
            user_id=user.id,
            province=body.province,
            city=body.city,
            district=body.district,
            area_mu=body.area_mu,
            soil_type=body.soil_type,
            other_info=body.other_info,
        )
        db.add(profile)

    db.commit()
    db.refresh(profile)

    return FarmProfileResponse(
        province=profile.province,
        city=profile.city,
        district=profile.district,
        area_mu=profile.area_mu,
        soil_type=profile.soil_type,
        other_info=profile.other_info,
    )
