"""Attendance / access record fetching."""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.dahua_client import DahuaClient
from app.core.device_pool import DevicePool
from app.dependencies import get_pool, require_auth
from app.schemas.attendance import AttendanceRecord
from app.services.migration_service import fetch_attendance

router = APIRouter(
    prefix="/devices/{device_name}/attendance",
    tags=["attendance"]
)


@router.get("", response_model=list[AttendanceRecord])
async def get_attendance(
    device_name: str,
    days: int = Query(7, ge=1, le=365),
    user_id: Optional[str] = None,
    pool: DevicePool = Depends(get_pool),
):
    def _fetch(c: DahuaClient):
        return fetch_attendance(c, days=days, user_id=user_id)
    return await pool.run(device_name, _fetch)
