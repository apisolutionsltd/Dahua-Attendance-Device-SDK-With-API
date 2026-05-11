"""
DGHS Push Scheduler.

Runs every 60 minutes inside the FastAPI process using APScheduler.
Pushes attendance + faces for host then recv (sequential).
Uses the same state-file resume logic as the manual API push.

Every run is written to logs/scheduler.log (plain text, daily rotation, 30 days).

Log file location: logs/scheduler.log  (next to where you run uvicorn)
"""
import threading
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core import db
from app.core.device_pool import DevicePool
from app.logging_setup import get_scheduler_logger
from app.services import dghs_service, job_service

# Use the dedicated scheduler logger → writes to logs/scheduler.log
log = get_scheduler_logger()

# Devices to push, in order
SCHEDULER_DEVICES = ["host", "recv"]

# Interval in minutes
SCHEDULER_INTERVAL_MINUTES = 30

# In-memory record of the last scheduled run (for /scheduler/status endpoint)
_last_run: dict = {}
_last_run_lock = threading.Lock()


def _record_run(device: str, job_id: str, status: str, detail: str = ""):
    with _last_run_lock:
        _last_run[device] = {
            "job_id": job_id,
            "status": status,
            "detail": detail,
            "ran_at": datetime.utcnow().isoformat(timespec="seconds"),
        }


def scheduled_push(pool: DevicePool) -> None:
    """
    Called by APScheduler every 60 minutes.
    Pushes host first, then recv. Sequential.
    """
    run_start = datetime.utcnow()
    log.info("=" * 60)
    log.info(f"SCHEDULER RUN STARTED  —  {run_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"Devices: {', '.join(SCHEDULER_DEVICES)}")
    log.info("=" * 60)

    for device_name in SCHEDULER_DEVICES:
        log.info(f"[{device_name}] ── Starting push ──")

        # Skip devices with no DGHS mapping
        try:
            dghs_id = dghs_service.get_dghs_device_id(device_name)
            log.info(f"[{device_name}] DGHS device_id = {dghs_id}")
        except Exception as e:
            log.warning(f"[{device_name}] SKIPPED — no DGHS mapping: {e}")
            _record_run(device_name, "", "skipped", "no DGHS mapping")
            continue

        # Skip devices not connected
        if not pool.is_connected(device_name):
            log.warning(f"[{device_name}] SKIPPED — device not connected")
            _record_run(device_name, "", "skipped", "device not connected")
            continue

        # Create a job record in PostgreSQL
        job_id = job_service.create_job(
            kind="dghs_push_scheduled",
            params={"device": device_name, "trigger": "scheduler"},
        )
        log.info(f"[{device_name}] Job created: {job_id}")
        _record_run(device_name, job_id, "running")

        device_start = datetime.utcnow()

        # Run the push synchronously — recv waits for host to finish
        dghs_service.run_dghs_push(
            job_id=job_id,
            pool=pool,
            device_name=device_name,
            days=None,
            since=None,
            successful_only=False,
            user_ids_filter=None,
            ignore_state=False,
        )

        # Read final result from DB
        row = db.get_job(job_id)
        status  = row.get("status", "unknown") if row else "unknown"
        error   = row.get("error", "") if row else ""
        result  = {}
        if row and row.get("result_json"):
            import json
            try:
                result = json.loads(row["result_json"])
            except Exception:
                pass

        elapsed = (datetime.utcnow() - device_start).total_seconds()
        _record_run(device_name, job_id, status, error[:200] if error else "")

        if status == "done":
            log.info(
                f"[{device_name}] DONE  "
                f"total={result.get('total', '?')}  "
                f"pushed={result.get('pushed', '?')}  "
                f"failed={result.get('failed', '?')}  "
                f"no_face={result.get('skipped_no_face', '?')}  "
                f"elapsed={elapsed:.1f}s  "
                f"latest={result.get('latest_timestamp', '?')}"
            )
        else:
            log.error(
                f"[{device_name}] FAILED  "
                f"status={status}  "
                f"elapsed={elapsed:.1f}s  "
                f"error={error[:300]}"
            )

    total_elapsed = (datetime.utcnow() - run_start).total_seconds()
    log.info("=" * 60)
    log.info(f"SCHEDULER RUN COMPLETE  —  elapsed={total_elapsed:.1f}s")
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------
_scheduler: Optional[BackgroundScheduler] = None


def start(pool: DevicePool) -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        log.warning("Scheduler already running — skipping start")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        func=scheduled_push,
        trigger=IntervalTrigger(minutes=SCHEDULER_INTERVAL_MINUTES),
        kwargs={"pool": pool},
        id="dghs_push",
        name="DGHS attendance push",
        replace_existing=True,
    )
    _scheduler.start()
    job = _scheduler.get_job("dghs_push")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else "unknown"
    log.info(f"Scheduler started  —  interval={SCHEDULER_INTERVAL_MINUTES}min  next_run={next_run}")


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
    _scheduler = None


def trigger_now(pool: DevicePool) -> str:
    """Manually trigger a push right now."""
    if _scheduler and _scheduler.running:
        _scheduler.modify_job("dghs_push", next_run_time=datetime.utcnow())
        return "Triggered — push will start within seconds."
    t = threading.Thread(target=scheduled_push, args=(pool,), daemon=True)
    t.start()
    return "Scheduler not running — triggered directly in background thread."


def get_status() -> dict:
    running = bool(_scheduler and _scheduler.running)
    next_run = None
    if running:
        job = _scheduler.get_job("dghs_push")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    with _last_run_lock:
        last = dict(_last_run)
    return {
        "running": running,
        "interval_minutes": SCHEDULER_INTERVAL_MINUTES,
        "devices": SCHEDULER_DEVICES,
        "next_run_utc": next_run,
        "log_file": "logs/scheduler.log",
        "last_runs": last,
    }


def pause() -> str:
    if _scheduler and _scheduler.running:
        _scheduler.pause_job("dghs_push")
        log.info("Scheduler paused by user")
        return "Scheduler paused."
    return "Scheduler is not running."


def resume() -> str:
    if _scheduler and _scheduler.running:
        _scheduler.resume_job("dghs_push")
        job = _scheduler.get_job("dghs_push")
        next_run = job.next_run_time.isoformat() if job and job.next_run_time else "unknown"
        log.info(f"Scheduler resumed  —  next_run={next_run}")
        return f"Scheduler resumed. Next run: {next_run}"
    return "Scheduler is not running."
