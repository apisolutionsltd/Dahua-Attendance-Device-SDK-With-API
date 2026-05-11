"""
DGHS attendance pusher.

Reads attendance records from a Dahua device, attaches each user's enrolled
face photo, and POSTs to the DGHS biometric API as multipart/form-data.

ALL CONFIG IS HARDCODED IN THIS FILE — edit the constants below if anything
changes. We deliberately avoided .env for this module per requirements.
"""
import json
import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Optional

import requests

from app.core import db
from app.core.dahua_client import DahuaClient
from app.core.device_pool import DevicePool
from app.exceptions import DeviceError

log = logging.getLogger(__name__)

# ===========================================================================
# === HARDCODED CONFIG — edit here ==========================================
# ===========================================================================

DGHS_API_URL   = "http://attendance.dghs.gov.bd/biometricapi/logapi/log_push"
DGHS_API_KEY   = "9c50edcc3fcf2ec003901b196b27f9d7"
DGHS_OFFICE_ID = "10000033"

# Maps the Dahua device 'name' (from your DEVICES config in .env) → DGHS device_id.
# The KEY must exactly match a device 'name' in your DEVICES env var.
# If a device isn't in this map, push will be refused with a clear error.
DGHS_DEVICE_ID_MAP: dict[str, str] = {
    "24001002": "24001002",   # 10.10.20.78 (DHI-ASI6214S-PW)
    "24001003": "24001003", # 10.10.20.82 — uncomment & set real DGHS ID when known
}

# Field name the API expects for the attached photo (multipart). Most likely
# 'face_image'. Change ONLY if the API rejects with a "missing field" error.
DGHS_FACE_FIELD = "face_image"

# Default look-back if no state row exists and caller didn't pass days/since
DGHS_DEFAULT_DAYS = 1

# How long to wait for each HTTP call to the DGHS API
DGHS_HTTP_TIMEOUT_SEC = 30

# How many records to push before pausing briefly (be polite to the API)
DGHS_PAUSE_EVERY = 100
DGHS_PAUSE_SECONDS = 1.0

DGHS_BATCH_SIZE = 20


# ===========================================================================
# Verify-mode mapping (Dahua emMethod → DGHS verify_mode string)
# ===========================================================================
def map_verify_mode(method_code: int) -> str:
    face_codes = {16, 18, 19, 23, 25, 26, 27, 30, 32, 33, 35, 36, 37, 38,
                  39, 40, 41, 42, 45, 51, 54, 56, 57, 59, 60, 62, 63}
    fp_codes   = {7, 8, 10, 11, 24, 28, 29, 31, 34}
    card_codes = {2, 3, 4}
    pwd_codes  = {1, 14, 21, 22, 47}

    if method_code in face_codes: return "FACEDMCH"
    if method_code in fp_codes:   return "FPDMCH"
    if method_code in card_codes: return "CARDMCH"
    if method_code in pwd_codes:  return "PWDMCH"
    return "FACEDMCH"


# ===========================================================================
# State table: stores the last successfully-pushed timestamp per device
# ===========================================================================
def init_state_table():
    """No-op: dghs_state table is created by db.init_db() at startup."""
    pass


# def get_state(device: str) -> Optional[dict]:
#     with db.get_conn() as c:
#         c.row_factory = sqlite3.Row
#         row = c.execute("SELECT * FROM dghs_state WHERE device = ?", (device,)).fetchone()
#         return dict(row) if row else None


def get_state(device: str) -> Optional[dict]:
    return db.get_dghs_state(device)


def upsert_state(device: str, dghs_device_id: str,
                 last_pushed: Optional[str], count: int):
    db.upsert_dghs_state(device, dghs_device_id, last_pushed, count)

# def upsert_state(device: str, dghs_device_id: str,
#                  last_pushed: Optional[str], count: int):
#     now = datetime.utcnow().isoformat(timespec="seconds")
#     with db.get_conn() as c:
#         c.execute("""
#             INSERT INTO dghs_state (device, dghs_device_id, last_pushed, last_pushed_count, last_run_at)
#             VALUES (?, ?, ?, ?, ?)
#             ON CONFLICT(device) DO UPDATE SET
#                 dghs_device_id = excluded.dghs_device_id,
#                 last_pushed = COALESCE(excluded.last_pushed, dghs_state.last_pushed),
#                 last_pushed_count = excluded.last_pushed_count,
#                 last_run_at = excluded.last_run_at
#         """, (device, dghs_device_id, last_pushed, count, now))


# ===========================================================================
# Helpers
# ===========================================================================
def get_dghs_device_id(device_name: str) -> str:
    """Resolve a Dahua device name to its DGHS device_id. Raises if missing."""
    dghs_id = DGHS_DEVICE_ID_MAP.get(device_name)
    if not dghs_id:
        raise DeviceError(
            device_name,
            f"No DGHS device_id configured for '{device_name}'. "
            f"Add it to DGHS_DEVICE_ID_MAP in app/services/dghs_service.py")
    return dghs_id


def _format_record_time(rec) -> str:
    t = rec.stuTime
    if t.dwYear == 0:
        return ""
    return f"{t.dwYear:04d}-{t.dwMonth:02d}-{t.dwDay:02d}T" \
           f"{t.dwHour:02d}:{t.dwMinute:02d}:{t.dwSecond:02d}"


def _push_one(log_entry: dict, jpg_bytes: bytes,
              session: requests.Session) -> tuple[bool, str]:
    """POST a single record + photo. Returns (ok, message)."""
    files = {
        "Logs": (None, json.dumps([log_entry])),
        DGHS_FACE_FIELD: (
            f"{log_entry['user_id']}.jpg",
            jpg_bytes,
            "image/jpeg",
        ),
    }
    headers = {"Api-Key": DGHS_API_KEY}
    try:
        resp = session.post(
            DGHS_API_URL, headers=headers, files=files,
            timeout=DGHS_HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        return False, f"http_error: {e}"

    if 200 <= resp.status_code < 300:
        return True, f"http_{resp.status_code}: {resp.text[:200]}"
    return False, f"http_{resp.status_code}: {resp.text[:200]}"


def _push_log_only(log_entry: dict, session: requests.Session) -> tuple[bool, str]:
    """Push without face — used as fallback when user has no enrolled photo."""
    files = {"Logs": (None, json.dumps([log_entry]))}
    headers = {"Api-Key": DGHS_API_KEY}
    try:
        resp = session.post(
            DGHS_API_URL, headers=headers, files=files,
            timeout=DGHS_HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        return False, f"http_error: {e}"
    if 200 <= resp.status_code < 300:
        return True, f"http_{resp.status_code}: {resp.text[:200]}"
    return False, f"http_{resp.status_code}: {resp.text[:200]}"


def _push_batch(
    entries: list[dict],
    face_cache: dict[str, Optional[bytes]],
    session: requests.Session,
) -> tuple[bool, str]:
    """
    Push up to DGHS_BATCH_SIZE records in ONE multipart request.

    Returns:
        (ok, message)
    """

    logs_json = json.dumps(entries)

    files = {
        "Logs": (None, logs_json),
    }

    image_index = 1

    for entry in entries:
        uid = entry["user_id"]
        jpg = face_cache.get(uid)

        if not jpg:
            continue

        files[f"{DGHS_FACE_FIELD}_{image_index}"] = (
            f"{uid}.jpg",
            jpg,
            "image/jpeg",
        )

        image_index += 1

    headers = {"Api-Key": DGHS_API_KEY}

    try:
        resp = session.post(
            DGHS_API_URL,
            headers=headers,
            files=files,
            timeout=DGHS_HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        return False, f"http_error: {e}"

    if 200 <= resp.status_code < 300:
        return True, f"http_{resp.status_code}: {resp.text[:500]}"

    return False, f"http_{resp.status_code}: {resp.text[:500]}"


# ===========================================================================
# The main push routine — invoked by the background job runner
# ===========================================================================
def run_dghs_push(
    job_id: str,
    pool: DevicePool,
    device_name: str,
    days: Optional[int] = None,
    since: Optional[str] = None,
    successful_only: bool = False,
    user_ids_filter: Optional[list[str]] = None,
    ignore_state: bool = False,
) -> None:
    """Background-task entrypoint. Updates the job row in SQLite as it runs."""
    db.update_job(job_id, status="running")

    try:
        dghs_device_id = get_dghs_device_id(device_name)

        # Resolve time window
        if since:
            start = datetime.fromisoformat(since)
        elif not ignore_state:
            state = get_state(device_name)
            if state and state.get("last_pushed"):
                try:
                    start = datetime.fromisoformat(state["last_pushed"])
                    log.info("dghs_resume", extra={
                        "device": device_name, "from": state["last_pushed"]})
                except ValueError:
                    start = datetime.now() - timedelta(days=days or DGHS_DEFAULT_DAYS)
            else:
                start = datetime.now() - timedelta(days=days or DGHS_DEFAULT_DAYS)
        else:
            start = datetime.now() - timedelta(days=days or DGHS_DEFAULT_DAYS)

        end = datetime.now()
        log.info("dghs_window", extra={
            "device": device_name, "start": start.isoformat(),
            "end": end.isoformat()})

        # Phase 1: read records from device (under device lock)
        def _read(c: DahuaClient):
            recs = []
            for r in c.list_access_records(start, end):
                uid = r.szUserID.decode(errors="ignore").strip()
                if not uid:
                    continue
                if user_ids_filter and uid not in user_ids_filter:
                    continue
                if successful_only and not r.bStatus:
                    continue
                ts = _format_record_time(r)
                if not ts:
                    continue
                recs.append({
                    "user_id": uid,
                    "device_id": dghs_device_id,
                    "office_id": DGHS_OFFICE_ID,
                    "verify_mode": map_verify_mode(int(r.emMethod)),
                    "timestamp": ts,
                })
            return recs

        db.update_job(job_id, progress="reading attendance from device")
        records = pool.run_sync(device_name, _read)
        log.info("dghs_records_read", extra={
            "device": device_name, "count": len(records)})

        if not records:
            db.update_job(
                job_id, status="done",
                result_json=json.dumps({
                    "device": device_name,
                    "window": [start.isoformat(), end.isoformat()],
                    "total": 0,
                    "pushed": 0,
                    "failed": 0,
                    "skipped_no_face": 0,
                    "message": "No records in window",
                }),
            )
            return

        # Phase 2: build face cache from Dahua (one fetch per unique user)
        unique_users = sorted({r["user_id"] for r in records})
        face_cache: dict[str, Optional[bytes]] = {}

        def _fetch_face(uid: str):
            def _f(c: DahuaClient):
                photos = c.get_face_bytes(uid)
                return photos[0] if photos else None
            return pool.run_sync(device_name, _f)

        for i, uid in enumerate(unique_users, 1):
            db.update_job(job_id, progress=f"fetching face {i}/{len(unique_users)}: {uid}")
            try:
                face_cache[uid] = _fetch_face(uid)
            except Exception as e:
                log.warning("dghs_face_fetch_failed", extra={
                    "device": device_name, "user_id": uid, "error": str(e)})
                face_cache[uid] = None

        face_count = sum(1 for v in face_cache.values() if v)
        log.info("dghs_face_cache_built", extra={
            "device": device_name,
            "users": len(unique_users), "with_face": face_count})

        # Phase 3: push records, one HTTP call each
        # session = requests.Session()
        # pushed = 0
        # failed = 0
        # skipped_no_face = 0
        # latest_ts = None
        # first_failure: Optional[str] = None

        # for i, entry in enumerate(records, 1):
        #     if i % 10 == 0 or i == 1:
        #         db.update_job(
        #             job_id,
        #             progress=f"pushing {i}/{len(records)} (ok={pushed}, fail={failed})",
        #         )

        #     uid = entry["user_id"]
        #     jpg = face_cache.get(uid)

        #     if jpg is None:
        #         ok, msg = _push_log_only(entry, session)
        #         if ok:
        #             pushed += 1
        #             skipped_no_face += 1
        #         else:
        #             failed += 1
        #             if first_failure is None:
        #                 first_failure = f"[{uid}] {msg}"
        #     else:
        #         ok, msg = _push_one(entry, jpg, session)
        #         if ok:
        #             pushed += 1
        #         else:
        #             failed += 1
        #             if first_failure is None:
        #                 first_failure = f"[{uid}] {msg}"

        #     if ok and (latest_ts is None or entry["timestamp"] > latest_ts):
        #         latest_ts = entry["timestamp"]

        #     if i % DGHS_PAUSE_EVERY == 0:
        #         time.sleep(DGHS_PAUSE_SECONDS)

        # # Phase 4: update state row only if everything succeeded
        # if not ignore_state and latest_ts and failed == 0:
        #     upsert_state(device_name, dghs_device_id, latest_ts, pushed)
        #     log.info("dghs_state_saved", extra={
        #         "device": device_name, "last_pushed": latest_ts})
        # elif failed > 0:
        #     log.warning("dghs_state_not_saved", extra={
        #         "device": device_name,
                # "reason": f"{failed} push(es) failed; same window will retry next run"})

        

        # New
                # ------------------------------------------------------------------
        # Phase 3: push records in batches of 20
        # ------------------------------------------------------------------
        records.sort(key=lambda r: (r["timestamp"], r["user_id"]))

        session = requests.Session()

        pushed = 0
        failed = 0
        skipped_no_face = 0
        latest_ts = None
        first_failure: Optional[str] = None

        total_batches = (
            len(records) + DGHS_BATCH_SIZE - 1
        ) // DGHS_BATCH_SIZE

        for batch_index in range(total_batches):

            start_idx = batch_index * DGHS_BATCH_SIZE
            end_idx = start_idx + DGHS_BATCH_SIZE

            batch = records[start_idx:end_idx]

            batch_no = batch_index + 1

            db.update_job(
                job_id,
                progress=(
                    f"batch {batch_no}/{total_batches} "
                    f"(ok={pushed}, fail={failed})"
                ),
            )

            batch_users = [x["user_id"] for x in batch]

            log.info(
                "dghs_batch_start",
                extra={
                    "device": device_name,
                    "batch": batch_no,
                    "batch_size": len(batch),
                    "users": batch_users,
                },
            )

            ok, msg = _push_batch(batch, face_cache, session)

            # --------------------------------------------------------------
            # SUCCESS
            # --------------------------------------------------------------
            if ok:

                pushed += len(batch)

                skipped_no_face += sum(
                    1
                    for x in batch
                    if face_cache.get(x["user_id"]) is None
                )

                batch_latest_ts = max(x["timestamp"] for x in batch)

                latest_ts = batch_latest_ts

                # IMPORTANT:
                # Save progress IMMEDIATELY after successful batch
                if not ignore_state:
                    upsert_state(
                        device_name,
                        dghs_device_id,
                        latest_ts,
                        pushed,
                    )

                log.info(
                    "dghs_batch_success",
                    extra={
                        "device": device_name,
                        "batch": batch_no,
                        "latest_ts": latest_ts,
                        "pushed_total": pushed,
                    },
                )

                continue

            # --------------------------------------------------------------
            # FAILURE
            # --------------------------------------------------------------
            failed += len(batch)

            if first_failure is None:
                first_failure = (
                    f"batch={batch_no} "
                    f"users={batch_users} "
                    f"error={msg}"
                )

            log.error(
                "dghs_batch_failed",
                extra={
                    "device": device_name,
                    "batch": batch_no,
                    "users": batch_users,
                    "error": msg,
                },
            )

            # STOP IMMEDIATELY
            break

        result = {
            "device": device_name,
            "dghs_device_id": dghs_device_id,
            "window": [start.isoformat(), end.isoformat()],
            "total": len(records),
            "pushed": pushed,
            "failed": failed,
            "skipped_no_face": skipped_no_face,
            "latest_timestamp": latest_ts,
            "first_failure": first_failure,
            # "state_advanced": (failed == 0 and latest_ts is not None and not ignore_state),
            "state_advanced": (latest_ts is not None and not ignore_state),
        }
        db.update_job(job_id, status="done", result_json=json.dumps(result),
                      progress=None)

    except Exception as e:
        log.exception("dghs_push_failed", extra={"job_id": job_id})
        db.update_job(job_id, status="failed",
                      error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1000]}")


# ===========================================================================
# Quick connectivity check
# ===========================================================================
def test_connection() -> dict:
    """POST a tiny throwaway record (no face) just to verify the API key works."""
    fake = [{
        "user_id": "TEST_CONNECTIVITY",
        "device_id": "TEST_CONNECTIVITY",
        "office_id": DGHS_OFFICE_ID,
        "verify_mode": "FACEDMCH",
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }]
    files = {"Logs": (None, json.dumps(fake))}
    headers = {"Api-Key": DGHS_API_KEY}
    try:
        resp = requests.post(DGHS_API_URL, headers=headers, files=files,
                             timeout=DGHS_HTTP_TIMEOUT_SEC)
        return {
            "api_url": DGHS_API_URL,
            "api_reachable": True,
            "http_status": resp.status_code,
            "response_excerpt": resp.text[:300],
            "error": None,
        }
    except requests.RequestException as e:
        return {
            "api_url": DGHS_API_URL,
            "api_reachable": False,
            "http_status": None,
            "response_excerpt": None,
            "error": str(e),
        }