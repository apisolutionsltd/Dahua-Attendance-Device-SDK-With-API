"""
Bulk enrollment service — profile-only (no face photos).

Handles CSV/Excel parsing, per-row validation, and device enrollment.
No photo/face logic — all users are created as profile-only.
"""
import csv
import io
import json
import logging
import os
import traceback
from datetime import datetime
from typing import Optional

from app.core import db
from app.core.dahua_client import DahuaClient
from app.core.device_pool import DevicePool
from app.schemas.bulk_enroll import BulkEnrollResult, BulkEnrollSummary

log = logging.getLogger(__name__)


# ===========================================================================
# File parsing
# ===========================================================================
def _normalize_keys(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        out[str(k).strip().lower()] = "" if v is None else str(v).strip()
    return out


def parse_csv_bytes(raw: bytes) -> list[dict]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            reader = csv.DictReader(io.StringIO(text))
            return [_normalize_keys(r) for r in reader]
        except (UnicodeDecodeError, Exception):
            continue
    raise ValueError("Could not decode CSV. Use UTF-8 or Latin-1 encoding.")


def parse_excel_bytes(raw: bytes) -> list[dict]:
    try:
        import pandas as pd
    except ImportError:
        raise ValueError(
            "Excel support requires pandas + openpyxl.\n"
            "Install with: pip install pandas openpyxl"
        )
    df = pd.read_excel(io.BytesIO(raw), dtype=str).fillna("")
    return [_normalize_keys(r) for r in df.to_dict(orient="records")]


def parse_uploaded_file(raw: bytes, filename: str) -> list[dict]:
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".csv", ".txt"):
        return parse_csv_bytes(raw)
    if ext in (".xlsx", ".xls"):
        return parse_excel_bytes(raw)
    raise ValueError(
        f"Unsupported file type '{ext}'. Upload a .csv, .xlsx, or .xls file."
    )


# ===========================================================================
# Per-row logic
# ===========================================================================
def _enroll_one_row(
    client: DahuaClient,
    row: dict,
    *,
    overwrite: bool,
    device: str,
) -> BulkEnrollResult:
    user_id    = row.get("user_id", "").strip()
    name       = row.get("name", "").strip()
    department = row.get("department", "")
    phone      = row.get("phone", "")
    door       = row.get("door", "1")
    valid_yrs  = row.get("valid_years", "10")
    password   = row.get("password", "")

    def fail(msg):
        return BulkEnrollResult(
            user_id=user_id or "(empty)", name=name,
            status="failed", message=msg)

    # Validation
    if not user_id:
        return fail("user_id is missing")
    if not name:
        return fail("name is missing")
    if len(user_id) > 32 or not user_id.replace("_", "").replace("-", "").isalnum():
        return fail("invalid user_id (max 32, alphanumeric/underscore/hyphen)")
    if len(name) > 31:
        return fail("name too long (max 31 chars)")
    try:
        door_num  = int(door) if door else 1
        valid_num = int(valid_yrs) if valid_yrs else 10
    except ValueError:
        return fail("door / valid_years must be integers")

    # Device operations
    try:
        exists = client.get_user(user_id) is not None
        if exists and not overwrite:
            return BulkEnrollResult(
                user_id=user_id, name=name,
                status="skipped",
                message="user already exists (pass overwrite=true to replace)",
            )

        valid_from = datetime.now()
        valid_to = datetime(
            valid_from.year + valid_num, valid_from.month, valid_from.day)

        ok = client.insert_user(
            user_id=user_id, name=name,
            department=department, phone=phone,
            doors=(door_num,), valid_from=valid_from, valid_to=valid_to,
            password=password,
        )
        if not ok:
            return fail("insert_user failed on device")

        status = "overwritten" if exists else "created"
        return BulkEnrollResult(
            user_id=user_id, name=name, status=status,
            message="profile created",
        )

    except Exception as e:
        return fail(f"unexpected error: {e}")


# ===========================================================================
# Dry-run validation
# ===========================================================================
def validate_rows(rows: list[dict]) -> list[BulkEnrollResult]:
    results = []
    for row in rows:
        user_id = row.get("user_id", "").strip()
        name    = row.get("name", "").strip()
        door    = row.get("door", "1")
        valid_y = row.get("valid_years", "10")

        def fail(msg):
            results.append(BulkEnrollResult(
                user_id=user_id or "(empty)", name=name,
                status="failed", message=msg))

        def ok_msg(msg):
            results.append(BulkEnrollResult(
                user_id=user_id, name=name,
                status="skipped", message=msg))

        if not user_id:
            fail("user_id is missing"); continue
        if not name:
            fail("name is missing"); continue
        if len(user_id) > 32 or not user_id.replace("_", "").replace("-", "").isalnum():
            fail("invalid user_id (max 32, alphanumeric/underscore/hyphen)"); continue
        if len(name) > 31:
            fail("name too long (max 31 chars)"); continue
        try:
            int(door) if door else 1
            int(valid_y) if valid_y else 10
        except ValueError:
            fail("door / valid_years must be integers"); continue

        ok_msg("OK: ready to enroll")

    return results


# ===========================================================================
# Sync runner
# ===========================================================================
def run_bulk_enroll_sync(
    pool: DevicePool,
    device_name: str,
    rows: list[dict],
    overwrite: bool,
) -> BulkEnrollSummary:
    results: list[BulkEnrollResult] = []

    def _do(client: DahuaClient):
        for row in rows:
            r = _enroll_one_row(client, row,
                                overwrite=overwrite, device=device_name)
            results.append(r)
        return results

    pool.run_sync(device_name, _do)

    counts = {s: sum(1 for r in results if r.status == s)
              for s in ("created", "overwritten", "skipped", "failed")}
    return BulkEnrollSummary(
        device=device_name, total=len(rows), **counts, results=results)


# ===========================================================================
# Background job runner
# ===========================================================================
def run_bulk_enroll_job(
    job_id: str,
    pool: DevicePool,
    device_name: str,
    rows: list[dict],
    overwrite: bool,
) -> None:
    db.update_job(job_id, status="running")
    try:
        results: list[BulkEnrollResult] = []
        counts = {"created": 0, "overwritten": 0, "skipped": 0, "failed": 0}

        for i, row in enumerate(rows, 1):
            if i % 5 == 1:
                db.update_job(
                    job_id,
                    progress=(f"{i}/{len(rows)} — "
                              f"ok={counts['created']+counts['overwritten']}, "
                              f"fail={counts['failed']}"),
                )

            def _do(client: DahuaClient, _row=row):
                return _enroll_one_row(client, _row,
                                       overwrite=overwrite, device=device_name)
            try:
                r = pool.run_sync(device_name, _do)
            except Exception as e:
                uid = row.get("user_id", "(empty)")
                r = BulkEnrollResult(
                    user_id=uid, name=row.get("name", ""),
                    status="failed", message=f"pool error: {e}")

            results.append(r)
            counts[r.status] = counts.get(r.status, 0) + 1

        db.update_job(
            job_id, status="done", progress=None,
            result_json=json.dumps({
                "device": device_name,
                "total": len(rows),
                **counts,
                "results": [r.model_dump() for r in results],
            }),
        )
        log.info("bulk_enroll_job_done", extra={"job_id": job_id, **counts})

    except Exception as e:
        log.exception("bulk_enroll_job_failed", extra={"job_id": job_id})
        db.update_job(
            job_id, status="failed",
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1000]}")






























# """
# Bulk enrollment service.

# Business logic extracted from dahua_bulk_enroll.py v2.
# Called from the router (sync, in thread pool) and background jobs.

# Three modes:
#   1. Sync small file  → run_bulk_enroll_sync() → returns BulkEnrollSummary
#   2. Async large file → run_bulk_enroll_job()  → background task, updates job row
#   3. Dry-run          → validate_only=True, no device connection needed
# """
# import csv
# import io
# import json
# import logging
# import os
# import traceback
# from datetime import datetime
# from typing import Optional

# from app.core import db
# from app.core.dahua_client import DahuaClient
# from app.core.device_pool import DevicePool
# from app.exceptions import DeviceError
# from app.schemas.bulk_enroll import BulkEnrollResult, BulkEnrollSummary

# log = logging.getLogger(__name__)

# MAX_PHOTO_BYTES = 200 * 1024   # 200 KB — Dahua device hard limit


# # ===========================================================================
# # File parsing
# # ===========================================================================
# def _normalize_keys(row: dict) -> dict:
#     """Lowercase + strip whitespace from keys; strip values."""
#     out = {}
#     for k, v in row.items():
#         if k is None:
#             continue
#         nk = str(k).strip().lower()
#         nv = "" if v is None else str(v).strip()
#         out[nk] = nv
#     return out


# def parse_csv_bytes(raw: bytes) -> list[dict]:
#     """Parse CSV from raw bytes. Handles UTF-8-BOM, UTF-8, Latin-1."""
#     for enc in ("utf-8-sig", "utf-8", "latin-1"):
#         try:
#             text = raw.decode(enc)
#             reader = csv.DictReader(io.StringIO(text))
#             return [_normalize_keys(r) for r in reader]
#         except (UnicodeDecodeError, Exception):
#             continue
#     raise ValueError("Could not decode CSV file. Use UTF-8 or Latin-1 encoding.")


# def parse_excel_bytes(raw: bytes, filename: str) -> list[dict]:
#     """Parse Excel from raw bytes. Requires pandas + openpyxl."""
#     try:
#         import pandas as pd
#     except ImportError:
#         raise ValueError(
#             "Excel support requires pandas + openpyxl.\n"
#             "Install with: pip install pandas openpyxl"
#         )
#     buf = io.BytesIO(raw)
#     df = pd.read_excel(buf, dtype=str).fillna("")
#     return [_normalize_keys(r) for r in df.to_dict(orient="records")]


# def parse_uploaded_file(raw: bytes, filename: str) -> list[dict]:
#     """Dispatch to CSV or Excel parser based on filename extension."""
#     ext = os.path.splitext(filename)[1].lower()
#     if ext in (".csv", ".txt"):
#         return parse_csv_bytes(raw)
#     if ext in (".xlsx", ".xls"):
#         return parse_excel_bytes(raw, filename)
#     raise ValueError(
#         f"Unsupported file type '{ext}'. Upload a .csv, .xlsx, or .xls file."
#     )


# # ===========================================================================
# # Photo validation (no file path — works on in-memory bytes)
# # ===========================================================================
# def validate_photo_bytes(data: bytes, label: str = "") -> None:
#     """Raise ValueError if photo is bad."""
#     if not data:
#         raise ValueError(f"{label}: photo is empty")
#     if len(data) > MAX_PHOTO_BYTES:
#         raise ValueError(
#             f"{label}: photo is {len(data):,} bytes; max is {MAX_PHOTO_BYTES:,} (200 KB). "
#             "Resize the image and re-upload."
#         )
#     if not (data[:2] == b"\xff\xd8" or data[:2] == b"\x89P"):
#         raise ValueError(f"{label}: file is not a valid JPG or PNG")


# # ===========================================================================
# # Per-row enrollment logic (uses DahuaClient directly — blocking/sync)
# # ===========================================================================
# def _enroll_one_row(
#     client: DahuaClient,
#     row: dict,
#     *,
#     photo_map: dict[str, bytes],   # filename → bytes, pre-loaded from uploaded photos
#     overwrite: bool,
#     device: str,
# ) -> BulkEnrollResult:
#     """Process one CSV row. Returns a BulkEnrollResult."""
#     user_id    = row.get("user_id", "").strip()
#     name       = row.get("name", "").strip()
#     face_file  = row.get("face_path", "").strip()
#     department = row.get("department", "")
#     phone      = row.get("phone", "")
#     door       = row.get("door", "1")
#     valid_yrs  = row.get("valid_years", "10")
#     password   = row.get("password", "")

#     def fail(msg): return BulkEnrollResult(user_id=user_id or "(empty)", name=name, status="failed", message=msg)

#     # --- Validation ---
#     if not user_id:
#         return fail("user_id is missing")
#     if not name:
#         return fail("name is missing")
#     uid_clean = user_id.replace("_", "").replace("-", "")
#     if len(user_id) > 32 or not uid_clean.isalnum():
#         return fail(f"invalid user_id (max 32, alphanumeric/underscore/hyphen)")
#     if len(name) > 31:
#         return fail("name too long (max 31 chars)")
#     try:
#         door_num  = int(door) if door else 1
#         valid_num = int(valid_yrs) if valid_yrs else 10
#     except ValueError:
#         return fail("door / valid_years must be integers")

#     # --- Photo lookup (optional) ---
#     jpg_bytes: Optional[bytes] = None
#     if face_file:
#         # Support bare filename OR full path — use just the basename for map lookup
#         key = os.path.basename(face_file)
#         jpg_bytes = photo_map.get(key) or photo_map.get(face_file)
#         if jpg_bytes is None:
#             return fail(f"photo '{face_file}' not found in uploaded files")
#         try:
#             validate_photo_bytes(jpg_bytes, label=face_file)
#         except ValueError as e:
#             return fail(str(e))

#     # --- Device operations ---
#     try:
#         exists = client.user_exists(user_id)
#         if exists and not overwrite:
#             return BulkEnrollResult(
#                 user_id=user_id, name=name,
#                 status="skipped",
#                 message="user already exists (pass overwrite=true to replace)",
#             )

#         valid_from = datetime.now()
#         valid_to = datetime(valid_from.year + valid_num, valid_from.month, valid_from.day)

#         ok = client.insert_user(
#             user_id=user_id, name=name,
#             department=department, phone=phone,
#             doors=(door_num,), valid_from=valid_from, valid_to=valid_to,
#             password=password,
#         )
#         if not ok:
#             return fail("insert_user failed on device")

#         if jpg_bytes is None:
#             status = "overwritten" if exists else "created"
#             return BulkEnrollResult(user_id=user_id, name=name, status=status,
#                                     message=f"profile created (no face)")

#         if exists:
#             client.remove_face(user_id)

#         ok = client.insert_face(user_id, [jpg_bytes])
#         if not ok:
#             return fail("profile created but face upload failed — try a clearer photo")

#         status = "overwritten" if exists else "created"
#         return BulkEnrollResult(user_id=user_id, name=name, status=status,
#                                 message=f"enrolled with face ({len(jpg_bytes):,} bytes)")

#     except Exception as e:
#         return fail(f"unexpected error: {e}")


# # ===========================================================================
# # Dry-run validation (no device needed)
# # ===========================================================================
# def validate_rows(rows: list[dict], photo_map: dict[str, bytes]) -> list[BulkEnrollResult]:
#     """Validate rows without touching any device. Returns a result per row."""
#     results = []
#     for row in rows:
#         user_id   = row.get("user_id", "").strip()
#         name      = row.get("name", "").strip()
#         face_file = row.get("face_path", "").strip()

#         def fail(msg):
#             results.append(BulkEnrollResult(user_id=user_id or "(empty)",
#                                             name=name, status="failed", message=msg))
#         def ok_msg(msg):
#             results.append(BulkEnrollResult(user_id=user_id, name=name,
#                                             status="skipped", message=msg))

#         if not user_id:
#             fail("user_id is missing"); continue
#         if not name:
#             fail("name is missing"); continue
#         uid_clean = user_id.replace("_", "").replace("-", "")
#         if len(user_id) > 32 or not uid_clean.isalnum():
#             fail("invalid user_id"); continue
#         if len(name) > 31:
#             fail("name too long (max 31 chars)"); continue

#         if not face_file:
#             ok_msg("OK: profile only (no face_path)"); continue

#         key = os.path.basename(face_file)
#         data = photo_map.get(key) or photo_map.get(face_file)
#         if data is None:
#             fail(f"photo '{face_file}' not uploaded"); continue
#         try:
#             validate_photo_bytes(data, label=face_file)
#         except ValueError as e:
#             fail(str(e)); continue
#         ok_msg(f"OK: would enroll with {key} ({len(data):,} bytes)")

#     return results


# # ===========================================================================
# # Main sync runner — called directly for small files (sync endpoint)
# # ===========================================================================
# def run_bulk_enroll_sync(
#     pool: DevicePool,
#     device_name: str,
#     rows: list[dict],
#     photo_map: dict[str, bytes],
#     overwrite: bool,
# ) -> BulkEnrollSummary:
#     """Run enrollment synchronously. Blocks until all rows are processed."""
#     results: list[BulkEnrollResult] = []

#     def _do(client: DahuaClient):
#         for row in rows:
#             r = _enroll_one_row(client, row, photo_map=photo_map,
#                                 overwrite=overwrite, device=device_name)
#             results.append(r)
#         return results

#     # run_sync holds the per-device lock for the whole batch
#     pool.run_sync(device_name, _do)

#     counts = {s: sum(1 for r in results if r.status == s)
#               for s in ("created", "overwritten", "skipped", "failed")}
#     return BulkEnrollSummary(
#         device=device_name,
#         total=len(rows),
#         **counts,
#         results=results,
#     )


# # ===========================================================================
# # Background job runner — called from BackgroundTasks for large files
# # ===========================================================================
# def run_bulk_enroll_job(
#     job_id: str,
#     pool: DevicePool,
#     device_name: str,
#     rows: list[dict],
#     photo_map: dict[str, bytes],
#     overwrite: bool,
# ) -> None:
#     """Background-task entrypoint. Updates job row in PostgreSQL as it runs."""
#     db.update_job(job_id, status="running")

#     try:
#         results: list[BulkEnrollResult] = []
#         counts = {"created": 0, "overwritten": 0, "skipped": 0, "failed": 0}

#         for i, row in enumerate(rows, 1):
#             # Update progress every 5 rows
#             if i % 5 == 1:
#                 db.update_job(
#                     job_id,
#                     progress=f"{i}/{len(rows)} — "
#                              f"ok={counts['created']+counts['overwritten']}, "
#                              f"fail={counts['failed']}",
#                 )

#             def _do(client: DahuaClient, _row=row):
#                 return _enroll_one_row(
#                     client, _row, photo_map=photo_map,
#                     overwrite=overwrite, device=device_name,
#                 )
#             try:
#                 r = pool.run_sync(device_name, _do)
#             except Exception as e:
#                 uid = row.get("user_id", "(empty)")
#                 r = BulkEnrollResult(user_id=uid, name=row.get("name", ""),
#                                      status="failed", message=f"pool error: {e}")

#             results.append(r)
#             counts[r.status] = counts.get(r.status, 0) + 1

#         result_data = {
#             "device": device_name,
#             "total": len(rows),
#             **counts,
#             "results": [r.model_dump() for r in results],
#         }
#         db.update_job(job_id, status="done",
#                       result_json=json.dumps(result_data), progress=None)
#         log.info("bulk_enroll_job_done", extra={"job_id": job_id, **counts})

#     except Exception as e:
#         log.exception("bulk_enroll_job_failed", extra={"job_id": job_id})
#         db.update_job(job_id, status="failed",
#                       error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1000]}")