"""JSON-formatted logging."""
import logging
import sys
import uuid
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    fmt = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    h.setFormatter(fmt)
    h.addFilter(RequestIdFilter())
    root.addHandler(h)
    root.setLevel(level)
    # Tame noisy libs
    for noisy in ("uvicorn.access", "passlib"):
        logging.getLogger(noisy).setLevel("WARNING")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid
