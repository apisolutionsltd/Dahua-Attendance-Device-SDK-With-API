"""FastAPI dependencies: auth + pool injection."""
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.config import get_settings
from app.core.device_pool import DevicePool
from app.core.security import decode_token

# tokenUrl is for OpenAPI docs only — it lets the Swagger UI's "Authorize" button
# do a login flow. Set auto_error=False so we can fall through to API-key auth.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def get_pool(request: Request) -> DevicePool:
    return request.app.state.pool


def require_auth(
    token: Optional[str] = Depends(oauth2_scheme),
    x_api_key: Optional[str] = Header(None),
) -> str:
    """
    Accept either:
      - Bearer JWT (Authorization: Bearer <token>)  → returns the username
      - API key   (X-API-Key: <key>)                → returns "api-key"
    """
    settings = get_settings()

    if token:
        username = decode_token(token)
        if username:
            return username

    if x_api_key and x_api_key == settings.api_key:
        return "api-key"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: send Bearer JWT or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )
