"""In-memory store for the four context scopes the judge pushes."""

from dataclasses import dataclass
from threading import Lock
from typing import Any

VALID_SCOPES = ("category", "merchant", "customer", "trigger")


@dataclass
class StoredContext:
    version: int
    payload: dict[str, Any]
    # Kept so a message can name what changed when the judge pushes an update mid-test.
    previous_payload: dict[str, Any] | None


@dataclass
class WriteResult:
    accepted: bool
    current_version: int


class ContextStore:
    def __init__(self) -> None:
        self._contexts: dict[tuple[str, str], StoredContext] = {}
        self._lock = Lock()

    def put(self, scope: str, context_id: str, version: int, payload: dict[str, Any]) -> WriteResult:
        """Store a context. A version equal to or below the stored one is rejected as stale."""
        key = (scope, context_id)
        with self._lock:
            stored = self._contexts.get(key)
            if stored and version <= stored.version:
                return WriteResult(accepted=False, current_version=stored.version)
            self._contexts[key] = StoredContext(
                version=version,
                payload=payload,
                previous_payload=stored.payload if stored else None,
            )
            return WriteResult(accepted=True, current_version=version)

    def get(self, scope: str, context_id: str) -> dict[str, Any] | None:
        stored = self._contexts.get((scope, context_id))
        return stored.payload if stored else None

    def get_stored(self, scope: str, context_id: str) -> StoredContext | None:
        return self._contexts.get((scope, context_id))

    def all_of(self, scope: str) -> list[dict[str, Any]]:
        """Every payload in one scope. Cohort facts are computed across this."""
        return [stored.payload for (kind, _), stored in self._contexts.items() if kind == scope]

    def version_of(self, scope: str, context_id: str) -> int:
        stored = self._contexts.get((scope, context_id))
        return stored.version if stored else 0

    def counts_by_scope(self) -> dict[str, int]:
        counts = {scope: 0 for scope in VALID_SCOPES}
        for scope, _ in self._contexts:
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def clear(self) -> None:
        with self._lock:
            self._contexts.clear()
