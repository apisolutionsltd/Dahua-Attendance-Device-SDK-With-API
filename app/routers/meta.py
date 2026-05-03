"""Health + device list."""
from fastapi import APIRouter, Depends

from app.config import get_settings
from app.core.device_pool import DevicePool
from app.dependencies import get_pool, require_auth

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health():
    return {"status": "ok", "app": get_settings().app_name}


@router.get("/devices", dependencies=[Depends(require_auth)])
async def list_devices(pool: DevicePool = Depends(get_pool)):
    settings = get_settings()
    return [
        {"name": d.name, "ip": d.ip, "connected": pool.is_connected(d.name)}
        for d in settings.devices
    ]
