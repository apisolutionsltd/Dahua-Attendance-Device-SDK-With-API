"""
DGHS Push Scheduler.

Runs every 60 minutes inside the FastAPI process using APScheduler.
Pushes attendance + faces for host then recv (sequential).
Uses the same state-file resume logic as the manual API push.

Started/stopped via the FastAPI lifespan in main.py.
Controlled via GET/POST /scheduler/* endpoints.
"""
import logging
import threading
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core import db
from app.core.device_pool import DevicePool
from app.services import dghs_service, job_service

log = logging.getLogger(__name__)

# Devices to push, in order
SCHEDULER_DEVICES = ["host", "recv"]

# Interval in minutes
SCHEDULER_INTERVAL_MINUTES = 60

# In-memory record of the last scheduled run
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
    Pushes host first, then recv. Sequential — recv waits for host to finish.
    Uses state-file resume: only pushes records newer than last_pushed.
    """
    log.info("scheduler_run_start", extra={"devices": SCHEDULER_DEVICES})

    for device_name in SCHEDULER_DEVICES:
        # Skip devices that have no DGHS mapping
        try:
            dghs_service.get_dghs_device_id(device_name)
        except Exception:
            log.warning("scheduler_skip_no_mapping",
                        extra={"device": device_name})
            continue

        # Skip devices not registered in the pool
        if not pool.is_connected(device_name):
            log.warning("scheduler_skip_not_connected",
                        extra={"device": device_name})
            _record_run(device_name, "", "skipped", "device not connected")
            continue

        log.info("scheduler_push_start", extra={"device": device_name})
        job_id = job_service.create_job(
            kind="dghs_push_scheduled",
            params={"device": device_name, "trigger": "scheduler"},
        )
        _record_run(device_name, job_id, "running")

        # run_dghs_push is synchronous (blocking) — runs in this thread.
        # This is intentional: host finishes before recv starts.
        dghs_service.run_dghs_push(
            job_id=job_id,
            pool=pool,
            device_name=device_name,
            days=None,       # use state-file resume
            since=None,
            successful_only=False,
            user_ids_filter=None,
            ignore_state=False,
        )

        # Read back the final job status for our record
        row = db.get_job(job_id)
        status = row.get("status", "unknown") if row else "unknown"
        error  = row.get("error", "") if row else ""
        _record_run(device_name, job_id, status, error[:200] if error else "")
        log.info("scheduler_push_done",
                 extra={"device": device_name, "job_id": job_id, "status": status})

    log.info("scheduler_run_complete", extra={"devices": SCHEDULER_DEVICES})


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------
_scheduler: Optional[BackgroundScheduler] = None


def start(pool: DevicePool) -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        log.warning("scheduler already running — skipping start")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        func=scheduled_push,
        trigger=IntervalTrigger(minutes=SCHEDULER_INTERVAL_MINUTES),
        kwargs={"pool": pool},
        id="dghs_push",
        name="DGHS attendance push",
        replace_existing=True,
        # Run first time after one interval (not immediately at startup).
        # Change to next_run_time=datetime.utcnow() to run at startup too.
    )
    _scheduler.start()
    job = _scheduler.get_job("dghs_push")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else "unknown"
    log.info("scheduler_started",
             extra={"interval_min": SCHEDULER_INTERVAL_MINUTES,
                    "next_run": next_run})


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
    _scheduler = None


def trigger_now(pool: DevicePool) -> str:
    """Manually trigger a push right now. Returns a status message."""
    if _scheduler and _scheduler.running:
        _scheduler.modify_job("dghs_push", next_run_time=datetime.utcnow())
        return "Triggered — push will start within seconds."
    # Scheduler not running — run directly in a thread so we don't block
    t = threading.Thread(target=scheduled_push, args=(pool,), daemon=True)
    t.start()
    return "Scheduler not running — triggered directly in background thread."


def get_status() -> dict:
    """Return scheduler status for the /scheduler/status endpoint."""
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
        "last_runs": last,
    }


def pause() -> str:
    if _scheduler and _scheduler.running:
        _scheduler.pause_job("dghs_push")
        return "Scheduler paused."
    return "Scheduler is not running."


def resume() -> str:
    if _scheduler and _scheduler.running:
        _scheduler.resume_job("dghs_push")
        job = _scheduler.get_job("dghs_push")
        next_run = job.next_run_time.isoformat() if job and job.next_run_time else "unknown"
        return f"Scheduler resumed. Next run: {next_run}"
    return "Scheduler is not running."