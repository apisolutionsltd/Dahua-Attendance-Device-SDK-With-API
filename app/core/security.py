"""JWT tokens + password hashing.

Uses bcrypt directly instead of passlib (which has a known incompatibility
with bcrypt >= 4.1).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings


def hash_password(password: str) -> str:
    # bcrypt has a 72-byte hard limit on the password input
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        pw = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(subject: str, expires_minutes: Optional[int] = None) -> tuple[str, int]:
    s = get_settings()
    exp_min = expires_minutes or s.jwt_expire_minutes
    expire = datetime.now(timezone.utc) + timedelta(minutes=exp_min)
    payload = {"sub": subject, "exp": expire, "iat": datetime.now(timezone.utc)}
    token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
    return token, exp_min * 60


def decode_token(token: str) -> Optional[str]:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None
