"""User business logic."""
from datetime import datetime
from typing import Optional

from app.core.dahua_client import DahuaClient
from app.exceptions import (DeviceError, UserAlreadyExistsError,
                            UserNotFoundError)
from app.schemas.user import EnrollResponse, UserDetail, UserSummary

MAX_PHOTO_BYTES = 200 * 1024


def list_users(client: DahuaClient) -> list[UserSummary]:
    out = []
    seen = set()
    for cr in client.list_all_card_records():
        uid = cr.szUserID.decode(errors="ignore").strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        u = client.get_user(uid)
        if not u:
            continue
        fp = (cr.stuFingerPrintInfoEx.nCount if cr.bEnableExtended
              else cr.stuFingerPrintInfo.nCount)
        card_no = cr.szCardNo.decode(errors="ignore").strip()
        out.append(UserSummary(
            user_id=uid,
            name=u.szName.decode(errors="ignore").strip(),
            department=u.szDepartment.decode(errors="ignore").strip(),
            has_card=bool(card_no),
            has_fingerprint=fp > 0,
            fingerprint_count=fp,
        ))
    return out


def get_user_detail(client: DahuaClient, user_id: str, device: str) -> UserDetail:
    u = client.get_user(user_id)
    if not u:
        raise UserNotFoundError(user_id, device)
    photos = client.get_face_bytes(user_id)
    return UserDetail.from_sdk(u, has_face=bool(photos))


def get_user_face(client: DahuaClient, user_id: str, device: str) -> Optional[bytes]:
    if not client.get_user(user_id):
        raise UserNotFoundError(user_id, device)
    photos = client.get_face_bytes(user_id)
    return photos[0] if photos else None


def delete_user(client: DahuaClient, user_id: str, device: str) -> None:
    if not client.get_user(user_id):
        raise UserNotFoundError(user_id, device)
    client.remove_face(user_id)
    if not client.remove_user(user_id):
        raise DeviceError(device, f"remove_user failed for {user_id}")


def validate_photo(photo_bytes: bytes) -> None:
    if not photo_bytes:
        raise ValueError("Photo file is empty")
    if len(photo_bytes) > MAX_PHOTO_BYTES:
        raise ValueError(f"Photo is {len(photo_bytes)} bytes; max is {MAX_PHOTO_BYTES} (200 KB)")
    if not (photo_bytes[:2] == b"\xff\xd8" or photo_bytes[:2] == b"\x89P"):
        raise ValueError("Photo doesn't look like JPG or PNG")


def enroll_user(
    client: DahuaClient, *, device: str,
    user_id: str, name: str, photo_bytes: bytes,
    department: str = "", phone: str = "",
    door: int = 1, valid_years: int = 10,
    overwrite: bool = False,
) -> EnrollResponse:
    exists = client.get_user(user_id) is not None
    if exists and not overwrite:
        raise UserAlreadyExistsError(user_id, device)

    valid_from = datetime.now()
    valid_to = datetime(valid_from.year + valid_years, valid_from.month, valid_from.day)

    if not client.insert_user(
        user_id=user_id, name=name,
        department=department, phone=phone,
        doors=(door,), valid_from=valid_from, valid_to=valid_to,
    ):
        raise DeviceError(device, "insert_user failed")

    if exists:
        client.remove_face(user_id)
    if not client.insert_face(user_id, [photo_bytes]):
        raise DeviceError(
            device,
            "insert_face failed — likely a photo quality issue. "
            "Try a clearer, well-lit, front-facing JPG.")

    return EnrollResponse(
        user_id=user_id, name=name, device=device,
        valid_until=valid_to.date().isoformat(),
        overwrote=exists,
    )
