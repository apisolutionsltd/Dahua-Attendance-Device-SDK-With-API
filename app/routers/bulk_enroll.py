"""
Bulk enrollment router — profile-only (no face photos).

POST /devices/{device_name}/enroll/validate   Dry-run: validate CSV, no device touch
POST /devices/{device_name}/enroll/sync       Enroll ≤50 users, blocks, returns results
POST /devices/{device_name}/enroll/async      Any size, returns job_id immediately

All endpoints accept multipart/form-data with:
    file        — required: CSV or Excel file
    overwrite   — optional bool (default false)

CSV format:
    user_id,name,department,phone,door,valid_years
    EMP1001,John Doe,Engineering,01712345678,1,10
    EMP1002,Jane Smith,HR,01712345679,1,10
"""
import asyncio
import logging

from fastapi import (APIRouter, BackgroundTasks, Depends, File,
                     Form, HTTPException, Request, UploadFile)

from app.config import get_settings
from app.core.device_pool import DevicePool
from app.core.rate_limit import limiter
from app.dependencies import get_pool, require_auth
from app.schemas.bulk_enroll import BulkEnrollJobResponse, BulkEnrollSummary
from app.services import bulk_enroll_service as svc
from app.services import job_service

router = APIRouter(
    prefix="/devices/{device_name}/enroll",
    tags=["bulk-enrollment"],
)
log = logging.getLogger(__name__)

SYNC_ROW_LIMIT = 50


def _validate_device(device_name: str):
    settings = get_settings()
    known = {d.name for d in settings.devices}
    if device_name not in known:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown device '{device_name}'. Known: {sorted(known)}",
        )


# ---------------------------------------------------------------------------
# POST /validate  — dry-run, no device connection
# ---------------------------------------------------------------------------
@router.post("/validate", response_model=BulkEnrollSummary)
async def validate_bulk(
    device_name: str,
    file: UploadFile = File(..., description="CSV or Excel file"),
):
    """
    Validate CSV without touching the device.
    Returns per-row validation results.
    """
    _validate_device(device_name)
    raw = await file.read()
    try:
        rows = svc.parse_uploaded_file(raw, file.filename or "upload.csv")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "File is empty or has no data rows")

    results = svc.validate_rows(rows)
    counts = {s: sum(1 for r in results if r.status == s)
              for s in ("created", "overwritten", "skipped", "failed")}
    return BulkEnrollSummary(device=device_name, total=len(rows),
                             **counts, results=results)


# ---------------------------------------------------------------------------
# POST /sync  — small files, blocks until done
# ---------------------------------------------------------------------------
# @router.post("/sync", response_model=BulkEnrollSummary)
# @limiter.limit(lambda: get_settings().rate_limit_write)
# async def enroll_sync(
#     request: Request,
#     device_name: str,
#     file: UploadFile = File(..., description="CSV or Excel file"),
#     overwrite: bool = Form(False, description="Replace existing users"),
#     pool: DevicePool = Depends(get_pool),
# ):
#     """
#     Enroll users synchronously. Blocks until all rows are processed.
#     Recommended for ≤ 50 users. For larger imports use /async.
#     """
#     _validate_device(device_name)
#     raw = await file.read()
#     try:
#         rows = svc.parse_uploaded_file(raw, file.filename or "upload.csv")
#     except ValueError as e:
#         raise HTTPException(400, str(e))
#     if not rows:
#         raise HTTPException(400, "File is empty or has no data rows")
#     if len(rows) > SYNC_ROW_LIMIT:
#         raise HTTPException(
#             413,
#             f"File has {len(rows)} rows; sync limit is {SYNC_ROW_LIMIT}. "
#             "Use POST /enroll/async for larger imports.",
#         )

#     loop = asyncio.get_running_loop()
#     summary = await loop.run_in_executor(
#         None,
#         lambda: svc.run_bulk_enroll_sync(pool, device_name, rows, overwrite),
#     )
#     log.info("bulk_enroll_sync_done", extra={
#         "device": device_name,
#         "total": summary.total,
#         "enrolled": summary.created,
#         "failed": summary.failed,
#     })
#     return summary


# ---------------------------------------------------------------------------
# POST /async  — any size, returns job_id immediately
# ---------------------------------------------------------------------------
@router.post("/async", response_model=BulkEnrollJobResponse, status_code=202)
@limiter.limit(lambda: get_settings().rate_limit_write)
async def enroll_async(
    request: Request,
    device_name: str,
    file: UploadFile = File(..., description="CSV or Excel file"),
    overwrite: bool = Form(False, description="Replace existing users"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    pool: DevicePool = Depends(get_pool),
):
    """
    Enroll users as a background job. Returns job_id immediately.
    Poll GET /jobs/{job_id} for progress and final results.
    """
    _validate_device(device_name)
    raw = await file.read()
    try:
        rows = svc.parse_uploaded_file(raw, file.filename or "upload.csv")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "File is empty or has no data rows")

    params = {
        "device": device_name,
        "filename": file.filename,
        "row_count": len(rows),
        "overwrite": overwrite,
    }
    job_id = job_service.create_job(kind="bulk_enroll", params=params)
    background_tasks.add_task(
        svc.run_bulk_enroll_job,
        job_id, pool, device_name, rows, overwrite,
    )
    log.info("bulk_enroll_job_queued", extra={
        "job_id": job_id, "device": device_name, "rows": len(rows),
    })
    return BulkEnrollJobResponse(
        job_id=job_id,
        device=device_name,
        filename=file.filename or "upload.csv",
        row_count=len(rows),
        overwrite=overwrite,
    )






































# """
# Bulk enrollment router.

# POST /devices/{device_name}/enroll/sync
#     Small files (≤ SYNC_ROW_LIMIT rows). Blocks until done, returns full results.
#     Use for quick imports (< 50 users).

# POST /devices/{device_name}/enroll/async
#     Any file size. Returns job_id immediately. Poll GET /jobs/{job_id} for progress.
#     Use for large imports (50+ users).

# POST /devices/{device_name}/enroll/validate
#     Dry-run: validates CSV rows + photos without touching the device.
#     Always synchronous. Use before any real import.

# All endpoints accept multipart/form-data with:
#     file        — required, the CSV or Excel file
#     photos      — optional, one or more face photo files (JPG/PNG)
#     overwrite   — optional bool (default false)

# The face_path column in the CSV must match the filename (not full path)
# of one of the uploaded photo files.

# Example CSV:
#     user_id,name,face_path,department
#     EMP1001,John Doe,john.jpg,Engineering
#     EMP1002,Jane Smith,,HR          ← no face, profile-only
# """
# import logging
# from typing import List, Optional

# from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
# from fastapi.responses import JSONResponse

# from app.config import get_settings
# from app.core.device_pool import DevicePool
# from app.core.rate_limit import limiter
# from app.dependencies import get_pool, require_auth
# from app.schemas.bulk_enroll import BulkEnrollJobResponse, BulkEnrollSummary
# from app.services import bulk_enroll_service as svc
# from app.services import job_service

# router = APIRouter(
#     prefix="/devices/{device_name}/enroll",
#     tags=["bulk-enrollment"],
# )
# log = logging.getLogger(__name__)

# # Files below this row count run synchronously; above → recommend async
# SYNC_ROW_LIMIT = 50


# def _validate_device(device_name: str):
#     """Raise 400 if device_name is not in config."""
#     settings = get_settings()
#     known = {d.name for d in settings.devices}
#     if device_name not in known:
#         raise HTTPException(
#             status_code=400,
#             detail=f"Unknown device '{device_name}'. Known: {sorted(known)}",
#         )


# def _build_photo_map(photos: list[UploadFile]) -> dict[str, bytes]:
#     """
#     Read all uploaded photo files into memory.
#     Returns {filename: bytes}.
#     """
#     photo_map: dict[str, bytes] = {}
#     for photo in photos:
#         data = photo.file.read()
#         if photo.filename:
#             photo_map[photo.filename] = data
#             # Also index by basename in case face_path has a directory prefix
#             import os
#             photo_map[os.path.basename(photo.filename)] = data
#     return photo_map


# # ---------------------------------------------------------------------------
# # POST /validate  — dry-run, no device connection
# # ---------------------------------------------------------------------------
# @router.post("/validate", response_model=BulkEnrollSummary)
# async def validate_bulk(
#     device_name: str,
#     file: UploadFile = File(..., description="CSV or Excel file"),
#     photos: Optional[List[UploadFile]] = File(
#         default=None,
#         description="Face photo files (JPG/PNG)"
#     ),
# ):
#     """
#     Validate a CSV/Excel + photos without touching the device.
#     Returns per-row validation results. No auth-side effects.
#     """
#     _validate_device(device_name)

#     raw = await file.read()
#     try:
#         rows = svc.parse_uploaded_file(raw, file.filename or "upload.csv")
#     except ValueError as e:
#         raise HTTPException(400, str(e))

#     if not rows:
#         raise HTTPException(400, "File is empty or has no data rows")

#     # photo_map = _build_photo_map(photos)
#     photo_map = _build_photo_map(photos or [])
#     results = svc.validate_rows(rows, photo_map)

#     counts = {s: sum(1 for r in results if r.status == s)
#               for s in ("created", "overwritten", "skipped", "failed")}
#     return BulkEnrollSummary(
#         device=device_name, total=len(rows), **counts, results=results
#     )


# # ---------------------------------------------------------------------------
# # POST /sync  — small files, blocks until done
# # ---------------------------------------------------------------------------
# @router.post("/sync", response_model=BulkEnrollSummary)
# @limiter.limit(lambda: get_settings().rate_limit_write)
# async def enroll_sync(
#     request: Request,
#     device_name: str,
#     file: UploadFile = File(..., description="CSV or Excel file"),
#     photos: Optional[List[UploadFile]] = File(
#         default=None,
#         description="Face photo files (JPG/PNG)"
#     ),
#     overwrite: bool = Form(False, description="Replace existing users"),
#     pool: DevicePool = Depends(get_pool),
# ):
#     """
#     Enroll users synchronously. Blocks until all rows are processed.
#     Recommended for ≤ 50 users. For larger imports use /async.
#     """
#     _validate_device(device_name)

#     raw = await file.read()
#     try:
#         rows = svc.parse_uploaded_file(raw, file.filename or "upload.csv")
#     except ValueError as e:
#         raise HTTPException(400, str(e))

#     if not rows:
#         raise HTTPException(400, "File is empty or has no data rows")

#     if len(rows) > SYNC_ROW_LIMIT:
#         raise HTTPException(
#             413,
#             f"File has {len(rows)} rows; sync endpoint limit is {SYNC_ROW_LIMIT}. "
#             "Use POST /enroll/async for larger imports.",
#         )

#     # Read photo files into memory before handing to thread pool
#     # photo_map = _build_photo_map(photos)
#     photo_map = _build_photo_map(photos or [])

#     import asyncio
#     loop = asyncio.get_running_loop()
#     summary = await loop.run_in_executor(
#         None,
#         lambda: svc.run_bulk_enroll_sync(pool, device_name, rows, photo_map, overwrite),
#     )
#     log.info("bulk_enroll_sync_done", extra={
#         "device": device_name, "total": summary.total,
#         "created": summary.created, "failed": summary.failed,
#     })
#     return summary


# # ---------------------------------------------------------------------------
# # POST /async  — any size, returns job_id immediately
# # ---------------------------------------------------------------------------
# @router.post("/async", response_model=BulkEnrollJobResponse, status_code=202)
# @limiter.limit(lambda: get_settings().rate_limit_write)
# async def enroll_async(
#     request: Request,
#     device_name: str,
#     file: UploadFile = File(..., description="CSV or Excel file"),
#     photos: list[UploadFile] = File(default=[], description="Face photo files (JPG/PNG)"),
#     overwrite: bool = Form(False, description="Replace existing users"),
#     background_tasks: BackgroundTasks = BackgroundTasks(),
#     pool: DevicePool = Depends(get_pool),
# ):
#     """
#     Enroll users as a background job. Returns job_id immediately.
#     Poll GET /jobs/{job_id} for progress and final results.
#     """
#     _validate_device(device_name)

#     raw = await file.read()
#     try:
#         rows = svc.parse_uploaded_file(raw, file.filename or "upload.csv")
#     except ValueError as e:
#         raise HTTPException(400, str(e))

#     if not rows:
#         raise HTTPException(400, "File is empty or has no data rows")

#     photo_map = _build_photo_map(photos)

#     params = {
#         "device": device_name,
#         "filename": file.filename,
#         "row_count": len(rows),
#         "overwrite": overwrite,
#     }
#     job_id = job_service.create_job(kind="bulk_enroll", params=params)

#     background_tasks.add_task(
#         svc.run_bulk_enroll_job,
#         job_id, pool, device_name, rows, photo_map, overwrite,
#     )
#     log.info("bulk_enroll_job_queued", extra={
#         "job_id": job_id, "device": device_name, "rows": len(rows),
#     })
#     return BulkEnrollJobResponse(
#         job_id=job_id,
#         device=device_name,
#         filename=file.filename or "upload.csv",
#         row_count=len(rows),
#         overwrite=overwrite,
#     )