"""Misc schemas: migration, auth, jobs."""
from typing import Any, Optional
from pydantic import BaseModel, Field


# ---- Migration ----
class MigrationRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=32)
    from_device: str
    to_device: str
    keep_source: bool = False
    overwrite_target: bool = False


class MigrationResponse(BaseModel):
    user_id: str
    from_device: str
    to_device: str
    deleted_from_source: bool
    overwrote_target: bool


# ---- Auth ----
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ---- Jobs ----
class JobResponse(BaseModel):
    id: str
    kind: str
    status: str  # queued | running | done | failed
    progress: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict) -> "JobResponse":
        import json
        return cls(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            progress=row.get("progress"),
            result=json.loads(row["result_json"]) if row.get("result_json") else None,
            error=row.get("error"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class BulkMigrateRequest(BaseModel):
    user_ids: list[str] = Field(..., min_length=1, max_length=500)
    from_device: str
    to_device: str
    keep_source: bool = False
    overwrite_target: bool = False
