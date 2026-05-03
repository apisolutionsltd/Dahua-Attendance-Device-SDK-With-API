"""Domain exceptions + handler registration."""
import logging

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class DeviceError(Exception):
    def __init__(self, device: str, message: str, sdk_code: int = 0):
        self.device, self.message, self.sdk_code = device, message, sdk_code
        super().__init__(f"[{device}] {message}")


class UserNotFoundError(Exception):
    def __init__(self, user_id: str, device: str):
        self.user_id, self.device = user_id, device
        super().__init__(f"User '{user_id}' not found on '{device}'")


class UserAlreadyExistsError(Exception):
    def __init__(self, user_id: str, device: str):
        self.user_id, self.device = user_id, device
        super().__init__(f"User '{user_id}' already exists on '{device}'")


class JobNotFoundError(Exception):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Job '{job_id}' not found")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(UserNotFoundError)
    async def _u_nf(req, exc):
        return JSONResponse(status_code=404, content={
            "error": "user_not_found", "user_id": exc.user_id, "device": exc.device})

    @app.exception_handler(UserAlreadyExistsError)
    async def _u_ae(req, exc):
        return JSONResponse(status_code=409, content={
            "error": "user_already_exists", "user_id": exc.user_id,
            "device": exc.device, "hint": "Pass overwrite=true to replace."})

    @app.exception_handler(DeviceError)
    async def _d_err(req, exc):
        log.error("device_error", extra={"device": exc.device, "message": exc.message})
        return JSONResponse(status_code=502, content={
            "error": "device_error", "device": exc.device,
            "message": exc.message, "sdk_code": exc.sdk_code})

    @app.exception_handler(JobNotFoundError)
    async def _j_nf(req, exc):
        return JSONResponse(status_code=404, content={
            "error": "job_not_found", "job_id": exc.job_id})
