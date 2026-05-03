"""Migration + attendance business logic."""
from datetime import datetime, timedelta

from app.core.dahua_client import DahuaClient
from app.core.device_pool import DevicePool
from app.exceptions import (DeviceError, UserAlreadyExistsError,
                            UserNotFoundError)
from app.schemas.attendance import AttendanceRecord
from app.schemas import MigrationResponse


# --------- Attendance ---------
def fetch_attendance(client: DahuaClient, days: int, user_id: str | None = None) -> list[AttendanceRecord]:
    end = datetime.now()
    start = end - timedelta(days=days)
    out = []
    for rec in client.list_access_records(start, end):
        uid = rec.szUserID.decode(errors="ignore").strip()
        if user_id and uid != user_id:
            continue
        out.append(AttendanceRecord.from_sdk(rec))
    return out


# --------- Migration ---------
async def migrate_user(
    pool: DevicePool, *, user_id: str,
    from_device: str, to_device: str,
    keep_source: bool = False, overwrite_target: bool = False,
) -> MigrationResponse:
    if from_device == to_device:
        raise ValueError("from_device and to_device must differ")

    # 1. Read from source
    def _read(c: DahuaClient):
        u = c.get_user(user_id)
        if not u:
            raise UserNotFoundError(user_id, from_device)
        return u, c.get_face_bytes(user_id)
    source_user, photos = await pool.run(from_device, _read)

    # 2. Write to target
    def _write(c: DahuaClient):
        existing = c.get_user(user_id) is not None
        if existing and not overwrite_target:
            raise UserAlreadyExistsError(user_id, to_device)
        if not c.insert_user_struct(source_user):
            raise DeviceError(to_device, "insert_user failed on target")
        if photos:
            if existing:
                c.remove_face(user_id)
            if not c.insert_face(user_id, photos):
                raise DeviceError(to_device, "insert_face failed on target")
        if not c.get_user(user_id):
            raise DeviceError(to_device, "post-insert verification failed")
        return existing
    overwrote = await pool.run(to_device, _write)

    # 3. Delete from source if requested
    if not keep_source:
        def _del(c: DahuaClient):
            c.remove_face(user_id)
            if not c.remove_user(user_id):
                raise DeviceError(from_device, "remove from source failed — user now on BOTH devices")
        await pool.run(from_device, _del)

    return MigrationResponse(
        user_id=user_id, from_device=from_device, to_device=to_device,
        deleted_from_source=not keep_source, overwrote_target=overwrote,
    )


# Sync version for background tasks
def migrate_user_sync(
    pool: DevicePool, *, user_id: str,
    from_device: str, to_device: str,
    keep_source: bool = False, overwrite_target: bool = False,
) -> dict:
    if from_device == to_device:
        raise ValueError("from_device and to_device must differ")

    def _read(c: DahuaClient):
        u = c.get_user(user_id)
        if not u:
            raise UserNotFoundError(user_id, from_device)
        return u, c.get_face_bytes(user_id)
    source_user, photos = pool.run_sync(from_device, _read)

    def _write(c: DahuaClient):
        existing = c.get_user(user_id) is not None
        if existing and not overwrite_target:
            raise UserAlreadyExistsError(user_id, to_device)
        if not c.insert_user_struct(source_user):
            raise DeviceError(to_device, "insert_user failed")
        if photos:
            if existing:
                c.remove_face(user_id)
            if not c.insert_face(user_id, photos):
                raise DeviceError(to_device, "insert_face failed")
        if not c.get_user(user_id):
            raise DeviceError(to_device, "verification failed")
        return existing
    overwrote = pool.run_sync(to_device, _write)

    if not keep_source:
        def _del(c: DahuaClient):
            c.remove_face(user_id)
            if not c.remove_user(user_id):
                raise DeviceError(from_device, "remove from source failed")
        pool.run_sync(from_device, _del)

    return {
        "user_id": user_id, "from_device": from_device, "to_device": to_device,
        "deleted_from_source": not keep_source, "overwrote_target": overwrote,
    }
