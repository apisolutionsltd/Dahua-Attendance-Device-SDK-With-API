"""DGHS push endpoints — async-first."""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.config import get_settings
from app.core.device_pool import DevicePool
from app.core.rate_limit import limiter
from app.dependencies import get_pool, require_auth
from app.schemas.dghs import (DGHSJobResponse, DGHSPushRequest,
                              DGHSStateResponse, DGHSTestResponse)
from app.services import dghs_service, job_service

router = APIRouter(prefix="/dghs", tags=["dghs"])
log = logging.getLogger(__name__)


@router.post("/push-async/{device_name}", response_model=DGHSJobResponse, status_code=202)
@limiter.limit(lambda: get_settings().rate_limit_write)
async def push_async(
    request: Request,
    device_name: str,
    body: DGHSPushRequest = DGHSPushRequest(),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    pool: DevicePool = Depends(get_pool),
):
    """
    Schedule a DGHS push as a background job.

    Returns immediately with a job_id; poll GET /jobs/{job_id} for progress
    and final result. The job:
      1. Reads attendance records from the device for the time window
      2. Fetches each unique user's enrolled face photo (cached)
      3. POSTs each record + its face to the DGHS API as multipart form-data
      4. Updates the dghs_state table on full success so the next run resumes
    """
    settings = get_settings()

    # Validate the Dahua device name exists in DEVICES config
    known_devices = {d.name for d in settings.devices}
    if device_name not in known_devices:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown Dahua device '{device_name}'. "
                   f"Known devices: {sorted(known_devices)}",
        )

    # Validate device has a DGHS mapping
    try:
        dghs_device_id = dghs_service.get_dghs_device_id(device_name)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Device '{device_name}' has no DGHS mapping. "
                   f"Add it to DGHS_DEVICE_ID_MAP in app/services/dghs_service.py",
        )

    params = body.model_dump()
    params["device"] = device_name
    job_id = job_service.create_job(kind="dghs_push", params=params)

    background_tasks.add_task(
        dghs_service.run_dghs_push,
        job_id, pool, device_name,
        days=body.days, since=body.since,
        successful_only=body.successful_only,
        user_ids_filter=body.user_ids,
        ignore_state=body.ignore_state,
    )
    log.info("dghs_push_queued", extra={
        "job_id": job_id, "device": device_name})
    return DGHSJobResponse(
        job_id=job_id, status="queued",
        device=device_name, dghs_device_id=dghs_device_id,
    )


@router.get("/state/{device_name}", response_model=DGHSStateResponse)
async def get_state(device_name: str):
    """Read the saved 'last pushed' marker for a device."""
    row = dghs_service.get_state(device_name) or {}
    return DGHSStateResponse(
        device=device_name,
        dghs_device_id=row.get("dghs_device_id"),
        last_pushed=row.get("last_pushed"),
        last_pushed_count=row.get("last_pushed_count"),
        last_run_at=row.get("last_run_at"),
    )


@router.post("/state/{device_name}/reset")
async def reset_state(device_name: str):
    """Clear the saved 'last pushed' marker so the next run starts fresh."""
    from app.core import db as _db
    with _db.get_conn() as c:
        c.execute("DELETE FROM dghs_state WHERE device = ?", (device_name,))
    log.info("dghs_state_reset", extra={"device": device_name})
    return {"device": device_name, "reset": True}


@router.post("/test-connection", response_model=DGHSTestResponse)
async def test_connection():
    """Send one tiny no-face request to verify the API URL + key are valid."""
    return DGHSTestResponse(**dghs_service.test_connection())