"""Safe-mode flag — a process-wide read-only fallback.

When `enabled == True`, every mutating HTTP route MUST refuse with
503. Read paths (/health, /retrieve, /mcp/tools/get_*) stay open.
The flag is set explicitly by the boot orchestrator OR by the
health gate when a critical signal degrades.

Phase-10 expansion — discrete SafeMode *modes*
==============================================

A boolean isn't enough to describe partial degradation. Operators
need to distinguish, at a glance, between:

    - the whole API is read-only because Postgres is down
    - the API is fully up but MCP is disabled (incident response
      after a tool-execution exploit)
    - everything except retrieval is throttled (cost spike on the
      ingestion pipeline)

So `SafeModeStatus` now carries an explicit `mode` field. The
existing `enabled` boolean remains as a backward-compatibility
shorthand: `enabled == True` iff `mode != "off"`. Existing routers
that only consult `.enabled` keep working unchanged.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal

# Discrete safe-mode states. Operators read these; routers can
# branch on them when more granular gating is needed in the future.
SafeModeMode = Literal[
    "off",            # default — fully operational
    "read_only",      # mutating writes refused; reads + retrieval OK
    "mcp_disabled",   # MCP tool execution refused; HTTP ingest + retrieve OK
    "retrieval_only", # only /retrieve and /health serve; everything else 503
]

VALID_MODES: frozenset[str] = frozenset(
    {"off", "read_only", "mcp_disabled", "retrieval_only"}
)


@dataclass(frozen=True, slots=True)
class SafeModeStatus:
    """Snapshot of the safe-mode controller's state.

    `enabled`, `reason`, `triggered_by` are unchanged from Phase 9 —
    callers and tests that pre-date Phase 10 see exactly the same
    fields. `mode` is additive: defaults to "off" when disabled and
    to "read_only" when enabled without an explicit mode (so the
    historical "any safe-mode == read-only" semantics are preserved).
    """

    enabled: bool
    reason: str
    triggered_by: str  # "config" | "boot_failure" | "runtime_health" | "manual"
    mode: SafeModeMode = "off"


class SafeModeController:
    """Thread-safe accessor for the live safe-mode state.

    The controller is intentionally *not* a state machine — there is
    no allowed-transitions table. Operators decide what mode to enter
    based on the incident; we just record their decision atomically.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        reason: str = "",
        triggered_by: str = "config",
        mode: SafeModeMode | None = None,
    ) -> None:
        self._lock = threading.Lock()
        # Honor the historical semantics: enabled-without-mode means
        # "treat as read_only" so downstream gates that only look at
        # `enabled` keep refusing writes.
        resolved: SafeModeMode = self._coerce_mode(mode, enabled)
        self._status = SafeModeStatus(
            enabled=enabled,
            reason=reason,
            triggered_by=triggered_by,
            mode=resolved,
        )

    # ----- read --------------------------------------------------------
    @property
    def status(self) -> SafeModeStatus:
        with self._lock:
            return self._status

    # ----- mutation primitives -----------------------------------------
    def enable(
        self,
        *,
        reason: str,
        triggered_by: str,
        mode: SafeModeMode = "read_only",
    ) -> None:
        """Enter safe mode in the requested `mode`.

        Backward-compatible: callers who don't pass `mode` get the
        Phase-9 default of read-only, which matches the historical
        boolean-only semantics.
        """
        if mode == "off":
            raise ValueError(
                "SafeModeController.enable() refuses mode='off'. "
                "Call .disable() to leave safe mode."
            )
        if mode not in VALID_MODES:
            raise ValueError(f"unknown safe-mode mode: {mode!r}")
        with self._lock:
            self._status = SafeModeStatus(
                enabled=True,
                reason=reason,
                triggered_by=triggered_by,
                mode=mode,
            )

    def disable(self) -> None:
        with self._lock:
            self._status = SafeModeStatus(
                enabled=False,
                reason="",
                triggered_by="manual",
                mode="off",
            )

    # ----- semantic helpers --------------------------------------------
    def enable_read_only(self, *, reason: str, triggered_by: str) -> None:
        """Mutating writes refused; reads + retrieval continue."""
        self.enable(reason=reason, triggered_by=triggered_by, mode="read_only")

    def enable_mcp_disabled(self, *, reason: str, triggered_by: str) -> None:
        """MCP tool execution refused; everything else operates normally."""
        self.enable(reason=reason, triggered_by=triggered_by, mode="mcp_disabled")

    def enable_retrieval_only(self, *, reason: str, triggered_by: str) -> None:
        """Most endpoints refuse; only /retrieve and /health stay open."""
        self.enable(reason=reason, triggered_by=triggered_by, mode="retrieval_only")

    # ----- query helpers (used by routers + status surface) ------------
    def writes_blocked(self) -> bool:
        """True iff mutating writes (ingest, snapshot/build, etc.) MUST 503."""
        with self._lock:
            return self._status.mode in ("read_only", "retrieval_only")

    def mcp_blocked(self) -> bool:
        """True iff MCP tool execution MUST refuse with 503."""
        with self._lock:
            return self._status.mode in ("mcp_disabled", "retrieval_only")

    def retrieval_allowed(self) -> bool:
        """True iff /retrieve is allowed to serve.

        Retrieval is the last surface to die in any degradation mode —
        agents need read-only context even when ingestion is throttled.
        """
        with self._lock:
            # All current modes leave retrieval open; retrieval-only is
            # explicitly designed around it. We expose this as a method
            # so future modes (e.g. "fully closed") can flip it without
            # touching every call site.
            return True

    # ----- internals ---------------------------------------------------
    @staticmethod
    def _coerce_mode(
        mode: SafeModeMode | None, enabled: bool,
    ) -> SafeModeMode:
        if mode is not None:
            if mode not in VALID_MODES:
                raise ValueError(f"unknown safe-mode mode: {mode!r}")
            # Refuse contradictory inputs so misuse fails loudly.
            if enabled and mode == "off":
                raise ValueError(
                    "SafeModeController(enabled=True, mode='off') is "
                    "contradictory; pick a non-'off' mode or enabled=False",
                )
            if not enabled and mode != "off":
                raise ValueError(
                    "SafeModeController(enabled=False, mode='read_only'/etc.) "
                    "is contradictory; pass enabled=True for non-'off' modes",
                )
            return mode
        return "read_only" if enabled else "off"


__all__ = ["VALID_MODES", "SafeModeController", "SafeModeMode", "SafeModeStatus"]
