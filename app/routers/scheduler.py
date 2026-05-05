"""Scheduler control endpoints."""
import logging

from fastapi import APIRouter, Depends, Request

from app.core.device_pool import DevicePool
from app.dependencies import get_pool, require_auth
from app.services import scheduler_service

router = APIRouter(
    prefix="/scheduler",
    tags=["scheduler"],
    # dependencies=[Depends(require_auth)],
)
log = logging.getLogger(__name__)


@router.get("/status")
async def status():
    """Show scheduler state, next run time, and last run results per device."""
    return scheduler_service.get_status()


@router.post("/trigger")
async def trigger(pool: DevicePool = Depends(get_pool)):
    """Trigger a DGHS push right now without waiting for the next interval."""
    msg = scheduler_service.trigger_now(pool)
    log.info("scheduler_manual_trigger")
    return {"message": msg}


@router.post("/pause")
async def pause():
    """Pause the scheduler (stops future automatic runs until resumed)."""
    msg = scheduler_service.pause()
    return {"message": msg}


@router.post("/resume")
async def resume():
    """Resume the scheduler after a pause."""
    msg = scheduler_service.resume()
    return {"message": msg}