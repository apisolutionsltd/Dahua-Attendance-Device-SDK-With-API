"""Device pool: persistent SDK clients with per-device locks."""
import asyncio
import logging
import threading
from typing import Callable, TypeVar

from app.core.dahua_client import DahuaClient
from app.exceptions import DeviceError

log = logging.getLogger(__name__)
T = TypeVar("T")


class DevicePool:
    def __init__(self):
        self._clients: dict[str, DahuaClient] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._init_lock = threading.Lock()
        self._creds: dict[str, tuple] = {}  # for re-login

    def register(self, name, ip, port, username, password):
        with self._init_lock:
            if name in self._clients:
                return
            self._creds[name] = (ip, port, username, password)
            client = DahuaClient(ip, port, username, password, label=name)
            client.login()
            self._clients[name] = client
            self._locks[name] = threading.Lock()
            log.info("device_registered", extra={"device": name, "ip": ip})

    def is_connected(self, name: str) -> bool:
        return name in self._clients and self._clients[name].login_id != 0

    def _ensure_login(self, name: str):
        """Re-login if a previous call dropped the session."""
        client = self._clients.get(name)
        if not client:
            raise DeviceError(name, "Unknown device — not in config")
        if client.login_id == 0:
            log.warning("device_relogin", extra={"device": name})
            client.login()
        return client

    async def run(self, device_name: str, fn: Callable[[DahuaClient], T]) -> T:
        if device_name not in self._clients:
            raise DeviceError(device_name, "Unknown device")
        lock = self._locks[device_name]

        def _call():
            with lock:
                client = self._ensure_login(device_name)
                return fn(client)

        return await asyncio.get_running_loop().run_in_executor(None, _call)

    def run_sync(self, device_name: str, fn: Callable[[DahuaClient], T]) -> T:
        """Sync version for background tasks running outside the event loop."""
        if device_name not in self._clients:
            raise DeviceError(device_name, "Unknown device")
        with self._locks[device_name]:
            client = self._ensure_login(device_name)
            return fn(client)

    def shutdown(self):
        for name, client in self._clients.items():
            try:
                client.logout()
            except Exception as e:
                log.warning("logout_error", extra={"device": name, "error": str(e)})
        self._clients.clear()
        self._locks.clear()
