"""User-related Pydantic models."""
from typing import Optional

from pydantic import BaseModel, Field


def _decode(b: bytes) -> str:
    if not b:
        return ""
    return b.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


def _format_net_time(t) -> Optional[str]:
    if t is None or t.dwYear == 0:
        return None
    return f"{t.dwYear:04d}-{t.dwMonth:02d}-{t.dwDay:02d} " \
           f"{t.dwHour:02d}:{t.dwMinute:02d}:{t.dwSecond:02d}"


class UserSummary(BaseModel):
    user_id: str
    name: str
    department: str = ""
    has_card: bool = False
    has_fingerprint: bool = False
    fingerprint_count: int = 0


class UserDetail(BaseModel):
    user_id: str
    name: str
    department: str = ""
    phone: str = ""
    door_count: int = 0
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    has_face: bool = False
    has_password: bool = False

    @classmethod
    def from_sdk(cls, u, has_face: bool = False) -> "UserDetail":
        return cls(
            user_id=_decode(u.szUserID),
            name=_decode(u.szName),
            department=_decode(u.szDepartment),
            phone=_decode(u.szPhoneNumber),
            door_count=u.nDoorNum,
            valid_from=_format_net_time(u.stuValidBeginTime),
            valid_to=_format_net_time(u.stuValidEndTime),
            has_face=has_face,
            has_password=bool(_decode(u.szPsw)),
        )


class EnrollResponse(BaseModel):
    user_id: str
    name: str
    device: str
    valid_until: str
    overwrote: bool = False
