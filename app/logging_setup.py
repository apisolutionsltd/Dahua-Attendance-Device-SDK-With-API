"""
Logging setup.

Two handlers:
  1. Console (stdout)   — all app logs, JSON format
  2. File (logs/scheduler.log) — scheduler-only logs, plain text, daily rotation
     Keeps 30 days of history. Easy to read / tail / grep.
"""
import logging
import logging.handlers
import os
import sys
import uuid
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

SCHEDULER_LOG_FILE = os.path.join("logs", "scheduler.log")
SCHEDULER_LOGGER_NAME = "scheduler"


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    # ---- Console handler (JSON) ----
    root = logging.getLogger()
    root.handlers.clear()
    h_console = logging.StreamHandler(sys.stdout)
    fmt = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    h_console.setFormatter(fmt)
    h_console.addFilter(RequestIdFilter())
    root.addHandler(h_console)
    root.setLevel(level)

    # ---- File handler for scheduler (plain text, rotating daily) ----
    os.makedirs("logs", exist_ok=True)
    h_file = logging.handlers.TimedRotatingFileHandler(
        filename=SCHEDULER_LOG_FILE,
        when="midnight",        # rotate at midnight
        interval=1,
        backupCount=30,         # keep 30 days
        encoding="utf-8",
    )
    h_file.setLevel(logging.INFO)
    h_file.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    # Only attach to the scheduler logger — keeps the file clean
    sched_log = logging.getLogger(SCHEDULER_LOGGER_NAME)
    sched_log.addHandler(h_file)
    sched_log.propagate = True   # still shows in console too

    # Tame noisy libs
    for noisy in ("uvicorn.access", "passlib", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel("WARNING")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid


def get_scheduler_logger() -> logging.Logger:
    """Return the dedicated scheduler logger that writes to logs/scheduler.log."""
    return logging.getLogger(SCHEDULER_LOGGER_NAME)



