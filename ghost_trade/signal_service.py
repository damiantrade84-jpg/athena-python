"""Signal lifecycle facade kept independent from scanning and execution."""

from __future__ import annotations

from .models import GhostSignal
from .persistence import GhostRepository


class SignalService:
    def __init__(self, repository: GhostRepository):
        self._repository = repository

    def save(self, signal: GhostSignal) -> GhostSignal:
        self._repository.upsert_signal(signal)
        return signal

    def get(self, signal_id: str) -> GhostSignal | None:
        return self._repository.get_signal(signal_id)

    def dismiss(self, signal_id: str) -> bool:
        return self._repository.dismiss_signal(signal_id)
