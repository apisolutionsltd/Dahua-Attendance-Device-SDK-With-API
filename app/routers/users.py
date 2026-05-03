"""User CRUD + face enrollment."""
import logging

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import Response

from app.config import get_settings
from app.core.dahua_client import DahuaClient
from app.core.device_pool import DevicePool
from app.core.rate_limit import limiter
from app.dependencies import get_pool, require_auth
from app.exceptions import DeviceError
from app.schemas.user import EnrollResponse, UserDetail, UserSummary
from app.services import user_service

router = APIRouter(
    prefix="/devices/{device_name}/users",
    tags=["users"],
)
log = logging.getLogger(__name__)


@router.get("", response_model=list[UserSummary])
async def list_users(device_name: str, pool: DevicePool = Depends(get_pool)):
    return await pool.run(device_name, user_service.list_users)


@router.get("/{user_id}", response_model=UserDetail)
async def get_user(device_name: str, user_id: str, pool: DevicePool = Depends(get_pool)):
    def _get(c: DahuaClient):
        return user_service.get_user_detail(c, user_id, device_name)
    return await pool.run(device_name, _get)


@router.get("/{user_id}/face")
async def get_user_face(device_name: str, user_id: str, pool: DevicePool = Depends(get_pool)):
    def _get_face(c: DahuaClient):
        return user_service.get_user_face(c, user_id, device_name)
    photo = await pool.run(device_name, _get_face)
    if not photo:
        raise HTTPException(404, f"No face on file for {user_id}")
    return Response(content=photo, media_type="image/jpeg")


@router.delete("/{user_id}")
@limiter.limit(lambda: get_settings().rate_limit_write)
async def delete_user(
    request: Request, device_name: str, user_id: str,
    pool: DevicePool = Depends(get_pool),
):
    def _del(c: DahuaClient):
        user_service.delete_user(c, user_id, device_name)
        return {"deleted": user_id, "device": device_name}
    result = await pool.run(device_name, _del)
    log.info("user_deleted", extra={"device": device_name, "user_id": user_id})
    return result


@router.post("", response_model=EnrollResponse)
@limiter.limit(lambda: get_settings().rate_limit_write)
async def enroll_user(
    request: Request,
    device_name: str,
    user_id: str = Form(..., min_length=1, max_length=32),
    name: str = Form(..., max_length=31),
    face: UploadFile = File(..., description="JPG/PNG face photo, ≤200 KB"),
    department: str = Form(""),
    phone: str = Form(""),
    door: int = Form(1, ge=1, le=8),
    valid_years: int = Form(10, ge=1, le=50),
    overwrite: bool = Form(False),
    pool: DevicePool = Depends(get_pool),
):
    photo_bytes = await face.read()
    try:
        user_service.validate_photo(photo_bytes)
    except ValueError as e:
        raise HTTPException(400, str(e))

    def _enroll(c: DahuaClient):
        return user_service.enroll_user(
            c, device=device_name,
            user_id=user_id, name=name, photo_bytes=photo_bytes,
            department=department, phone=phone,
            door=door, valid_years=valid_years, overwrite=overwrite,
        )
    result = await pool.run(device_name, _enroll)
    log.info("user_enrolled", extra={"device": device_name, "user_id": user_id})
    return result
