"""Single-user migration endpoint."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import get_settings
from app.core.device_pool import DevicePool
from app.core.rate_limit import limiter
from app.dependencies import get_pool, require_auth
from app.schemas import MigrationRequest, MigrationResponse
from app.services import migration_service

# router = APIRouter(tags=["migration"], dependencies=[Depends(require_auth)])
router = APIRouter(tags=["migration"])

log = logging.getLogger(__name__)


@router.post("/migrate", response_model=MigrationResponse)
@limiter.limit(lambda: get_settings().rate_limit_write)
async def migrate(
    request: Request,
    body: MigrationRequest,
    pool: DevicePool = Depends(get_pool),
):
    """Atomically move a user (profile + face) from one device to another."""
    try:
        result = await migration_service.migrate_user(
            pool,
            user_id=body.user_id,
            from_device=body.from_device,
            to_device=body.to_device,
            keep_source=body.keep_source,
            overwrite_target=body.overwrite_target,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    log.info("migration_done", extra={
        "user_id": body.user_id,
        "from": body.from_device, "to": body.to_device,
    })
    return result
