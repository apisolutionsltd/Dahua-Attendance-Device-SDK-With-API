"""Background jobs: schedule slow operations, poll status."""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from app.config import get_settings
from app.core import db
from app.core.device_pool import DevicePool
from app.core.rate_limit import limiter
from app.dependencies import get_pool, require_auth
from app.exceptions import JobNotFoundError
from app.schemas import BulkMigrateRequest, JobResponse
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])
log = logging.getLogger(__name__)


@router.post("/export/{device_name}")
@limiter.limit(lambda: get_settings().rate_limit_write)
async def schedule_export(
    request: Request,
    device_name: str,
    background_tasks: BackgroundTasks,
    days: int = Query(30, ge=1, le=365),
    pool: DevicePool = Depends(get_pool),
):
    """Schedule a full export (users + attendance) for a device. Returns job_id immediately."""
    job_id = job_service.create_job(
        kind="export", params={"device": device_name, "days": days}
    )
    background_tasks.add_task(job_service.run_export, job_id, pool, device_name, days)
    return {"job_id": job_id, "status": "queued"}


@router.post("/migrate-bulk")
@limiter.limit(lambda: get_settings().rate_limit_write)
async def schedule_bulk_migrate(
    request: Request,
    body: BulkMigrateRequest,
    background_tasks: BackgroundTasks,
    pool: DevicePool = Depends(get_pool),
):
    """Schedule a bulk migration. Per-user errors are reported in the result, not fatal."""
    params = body.model_dump()
    job_id = job_service.create_job(kind="bulk_migrate", params=params)
    background_tasks.add_task(job_service.run_bulk_migrate, job_id, pool, params)
    return {"job_id": job_id, "status": "queued", "user_count": len(body.user_ids)}


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    row = db.get_job(job_id)
    if not row:
        raise JobNotFoundError(job_id)
    return JobResponse.from_row(row)


@router.get("", response_model=list[JobResponse])
async def list_jobs(limit: int = Query(50, ge=1, le=500)):
    rows = db.list_jobs(limit=limit)
    return [JobResponse.from_row(r) for r in rows]
