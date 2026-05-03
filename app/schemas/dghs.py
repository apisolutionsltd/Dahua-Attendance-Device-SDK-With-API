"""DGHS push request/response models."""
from typing import Optional

from pydantic import BaseModel, Field


class DGHSPushRequest(BaseModel):
    """Body for POST /dghs/push-async/{device_name}."""
    days: Optional[int] = Field(
        None, ge=1, le=365,
        description="Look back this many days. Ignored if 'since' is set or "
                    "if a state row exists for this device.",
    )
    since: Optional[str] = Field(
        None,
        description="Override start date as ISO datetime "
                    "(e.g. '2024-12-01' or '2024-12-01T08:00:00'). "
                    "Overrides 'days' and any saved state.",
    )
    successful_only: bool = Field(
        False,
        description="Skip records where bStatus is false (denied/failed access).",
    )
    user_ids: Optional[list[str]] = Field(
        None,
        description="Restrict push to these user_ids only. None = all users.",
    )
    ignore_state: bool = Field(
        False,
        description="Don't read or update the saved 'last pushed' marker.",
    )


class DGHSJobResponse(BaseModel):
    job_id: str
    status: str = "queued"
    device: str
    dghs_device_id: str


class DGHSStateResponse(BaseModel):
    device: str
    dghs_device_id: Optional[str] = None
    last_pushed: Optional[str] = None
    last_pushed_count: Optional[int] = None
    last_run_at: Optional[str] = None


class DGHSTestResponse(BaseModel):
    api_url: str
    api_reachable: bool
    http_status: Optional[int] = None
    response_excerpt: Optional[str] = None
    error: Optional[str] = None
