"""Fault-injection harness for fail-loud verification.

A Decorator over the WorkSource protocol that deterministically raises on
configured write/fail-loud seams, so the controller's I2 fail-loud paths
(e.g. R3b's verdict-write escalation) can be dogfooded as repeatable
regression instead of relying on a racy real outage.

OFF unless ``VF_FAULT_INJECT`` is set and non-empty. When active it logs a
loud WARNING. It only ever *raises* on the listed seams — it never
fabricates success, mutates state, or hides a real error. See
docs/fault-injection-DESIGN.md.
"""

import logging
import os

logger = logging.getLogger(__name__)

# The work-source seams whose failure exercises a controller fail-loud path.
INJECTABLE_SEAMS = (
    "submit_review",
    "complete",
    "fail",
    "report_integration_result",
    "add_note",
)


class FaultInjected(Exception):
    """Raised by the harness in place of a real work-source call."""


def parse_fault_spec(spec: str | None) -> dict[str, int]:
    """Parse ``VF_FAULT_INJECT`` into {method: raise_count}.

    Format: comma-separated ``method[:count]`` (count defaults to 1).
    Malformed entries (no method, non-int count, count<=0) are skipped
    with a warning; valid entries are kept.
    """
    policy: dict[str, int] = {}
    if not spec:
        return policy
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        method, sep, raw = entry.partition(":")
        method = method.strip()
        if not method:
            continue
        if method not in INJECTABLE_SEAMS:
            logger.warning(
                "VF_FAULT_INJECT: ignoring unknown seam %r (injectable: %s)",
                method, ", ".join(INJECTABLE_SEAMS),
            )
            continue
        if not sep:
            count = 1
        else:
            try:
                count = int(raw.strip())
            except ValueError:
                logger.warning("VF_FAULT_INJECT: ignoring malformed entry %r", entry)
                continue
        if count <= 0:
            logger.warning("VF_FAULT_INJECT: ignoring non-positive count %r", entry)
            continue
        policy[method] = count
    return policy


class FaultInjectingWorkSource:
    """Decorator over a WorkSource that raises FaultInjected on configured
    seams for the first N calls, then passes through. Everything else
    delegates unchanged."""

    def __init__(self, inner, policy: dict[str, int]):
        self._inner = inner
        self._remaining = dict(policy)

    def __getattr__(self, name):
        # Delegate any attribute not explicitly overridden to the inner
        # work source (poll, register, heartbeat, get_repo_info, ...).
        return getattr(self._inner, name)

    def _maybe_raise(self, method: str) -> None:
        remaining = self._remaining.get(method, 0)
        if remaining > 0:
            self._remaining[method] = remaining - 1
            logger.warning(
                "VF_FAULT_INJECT: raising on %s (remaining after this: %d)",
                method, remaining - 1,
            )
            raise FaultInjected(f"injected fault on {method}")

    async def submit_review(self, *args, **kwargs):
        self._maybe_raise("submit_review")
        return await self._inner.submit_review(*args, **kwargs)

    async def complete(self, *args, **kwargs):
        self._maybe_raise("complete")
        return await self._inner.complete(*args, **kwargs)

    async def fail(self, *args, **kwargs):
        self._maybe_raise("fail")
        return await self._inner.fail(*args, **kwargs)

    async def report_integration_result(self, *args, **kwargs):
        self._maybe_raise("report_integration_result")
        return await self._inner.report_integration_result(*args, **kwargs)

    async def add_note(self, *args, **kwargs):
        self._maybe_raise("add_note")
        return await self._inner.add_note(*args, **kwargs)


def maybe_wrap_faults(work_source):
    """Wrap ``work_source`` in the fault harness iff ``VF_FAULT_INJECT`` is
    set and non-empty; otherwise return it unchanged (prod is unaffected)."""
    spec = os.environ.get("VF_FAULT_INJECT", "")
    policy = parse_fault_spec(spec)
    if not policy:
        return work_source
    logger.warning(
        "FAULT INJECTION ACTIVE (VF_FAULT_INJECT=%r) — policy=%s. "
        "This is a test affordance and must never be set in production.",
        spec, policy,
    )
    return FaultInjectingWorkSource(work_source, policy)
