"""Job service: background tasks with SQLite-persisted state."""
import json
import logging
import traceback
import uuid
from datetime import datetime, timedelta

from app.core import db
from app.core.dahua_client import DahuaClient
from app.core.device_pool import DevicePool
from app.services import migration_service, user_service
from app.services.migration_service import fetch_attendance

log = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def create_job(kind: str, params: dict) -> str:
    job_id = _new_id()
    db.insert_job(job_id, kind, params)
    log.info("job_created", extra={"job_id": job_id, "kind": kind})
    return job_id


def _start(job_id: str):
    db.update_job(job_id, status="running")


def _finish(job_id: str, result: dict):
    db.update_job(job_id, status="done", result_json=json.dumps(result), progress=None)


def _fail(job_id: str, error: str):
    db.update_job(job_id, status="failed", error=error[:1000])


# -------- Job runners (called from BackgroundTasks) --------
def run_export(job_id: str, pool: DevicePool, device: str, days: int):
    """Export users + attendance for a device. Long-running."""
    try:
        _start(job_id)

        # Users
        db.update_job(job_id, progress="listing users")
        def _list_users(c: DahuaClient):
            return user_service.list_users(c)
        users = pool.run_sync(device, _list_users)
        users_data = [u.model_dump() for u in users]

        # Attendance
        db.update_job(job_id, progress=f"fetched {len(users_data)} users; pulling attendance")
        def _att(c: DahuaClient):
            return fetch_attendance(c, days=days)
        records = pool.run_sync(device, _att)
        att_data = [r.model_dump() for r in records]

        result = {
            "device": device,
            "user_count": len(users_data),
            "attendance_count": len(att_data),
            "users": users_data,
            "attendance": att_data,
            "exported_at": datetime.utcnow().isoformat(),
        }
        _finish(job_id, result)
    except Exception as e:
        log.exception("export_job_failed", extra={"job_id": job_id})
        _fail(job_id, f"{type(e).__name__}: {e}")


def run_bulk_migrate(job_id: str, pool: DevicePool, params: dict):
    """Migrate many users; tolerate per-user failures, report per-user status."""
    try:
        _start(job_id)
        user_ids = params["user_ids"]
        results = []
        for i, uid in enumerate(user_ids, 1):
            db.update_job(job_id, progress=f"{i}/{len(user_ids)} — {uid}")
            try:
                r = migration_service.migrate_user_sync(
                    pool, user_id=uid,
                    from_device=params["from_device"],
                    to_device=params["to_device"],
                    keep_source=params.get("keep_source", False),
                    overwrite_target=params.get("overwrite_target", False),
                )
                results.append({"user_id": uid, "ok": True, **r})
            except Exception as e:
                results.append({"user_id": uid, "ok": False, "error": str(e)})

        ok_count = sum(1 for r in results if r["ok"])
        _finish(job_id, {
            "total": len(user_ids),
            "succeeded": ok_count,
            "failed": len(user_ids) - ok_count,
            "results": results,
        })
    except Exception as e:
        log.exception("bulk_migrate_failed", extra={"job_id": job_id})
        _fail(job_id, f"{type(e).__name__}: {e}")
