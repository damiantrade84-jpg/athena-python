"""Independent Ghost scan scheduler; it never imports Athena's auto-trader."""

from __future__ import annotations

import threading

from .service import GhostService, ScanAlreadyRunning


class GhostScheduler:
    def __init__(self, service: GhostService):
        self._service = service
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def trigger_scan(self):
        return self._service.start_scan()

    def start(self, interval_seconds: int, *, run_immediately: bool = True) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            if run_immediately:
                try:
                    self.trigger_scan()
                except ScanAlreadyRunning:
                    pass
            while not self._stop.wait(interval_seconds):
                try:
                    self.trigger_scan()
                except ScanAlreadyRunning:
                    continue

        self._thread = threading.Thread(target=run, name="ghost-trade-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
