"""Schemas for bulk enrollment endpoints."""
from typing import Optional
from pydantic import BaseModel, Field


class BulkEnrollResult(BaseModel):
    user_id: str
    name: str
    status: str   # created | overwritten | skipped | failed
    message: str


class BulkEnrollSummary(BaseModel):
    device: str
    total: int
    created: int
    overwritten: int
    skipped: int
    failed: int
    results: list[BulkEnrollResult]


class BulkEnrollJobResponse(BaseModel):
    job_id: str
    status: str = "queued"
    device: str
    filename: str
    row_count: int
    overwrite: bool