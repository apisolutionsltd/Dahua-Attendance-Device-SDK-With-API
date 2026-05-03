"""Auth endpoints: username/password login → JWT."""
import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import create_access_token, verify_password
from app.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(lambda: get_settings().rate_limit_auth)
async def login(request: Request, body: LoginRequest):
    settings = get_settings()
    if body.username != settings.admin_username or not verify_password(
        body.password, settings.admin_password_hash
    ):
        log.warning("login_failed", extra={"username": body.username})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token, expires_in = create_access_token(subject=body.username)
    log.info("login_ok", extra={"username": body.username})
    return TokenResponse(access_token=token, expires_in=expires_in)
