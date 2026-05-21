"""WG5-min T1 — vfobs emission hooks + fail-safe.

vfobs_sdk is intentionally NOT a vafi runtime dep, so these tests
drive emission with the SDK simulated absent (the real vafi-env
case) and present (monkeypatched recording factories). The
load-bearing assertion: NOTHING about emission can perturb the
controller — not a missing SDK, not a raising emitter, not a
missing workgraph_id, not a broken workdir.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from controller import emission
from controller.config import AgentConfig
from controller.controller import Controller
from controller.heartbeat import heartbeat_loop
from controller.types import AgentInfo, ExecutionResult, TaskInfo


class RecordingEmitter:
    def __init__(self, raise_on_emit=False):
        self.events = []
        self.closed = False
        self._raise = raise_on_emit

    def emit(self, ev):
        if self._raise:
            raise RuntimeError("emitter exploded")
        self.events.append(ev)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def recording_events(monkeypatch):
    """Simulate the SDK present with recording factories that just
    echo their kwargs as a dict tagged by event name."""
    def f(name):
        return lambda **kw: {"_event": name, **kw}

    fake = {
        n: f(n)
        for n in (
            "task_claimed", "task_heartbeat", "task_state_changed",
            "task_workdir_changed", "harness_turn_started",
            "harness_turn_completed",
        )
    }
    fake["ExecutionSummary"] = lambda **kw: {"_es": kw}
    monkeypatch.setattr(emission, "_SDK_AVAILABLE", True)
    monkeypatch.setattr(emission, "EVENTS", fake)
    return fake


# ---- workdir_signature (real function) -----------------------------------

def test_workdir_signature_changes_on_mutation(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    s1 = emission.workdir_signature(tmp_path)
    (tmp_path / "a.txt").write_text("two-different-length")
    s2 = emission.workdir_signature(tmp_path)
    assert s1 is not None and s2 is not None and s1 != s2


def test_workdir_signature_none_on_bad_path():
    assert emission.workdir_signature(__import__("pathlib").Path("/no/such")) is None


# ---- build_emitter -------------------------------------------------------

def test_build_emitter_noop_when_disabled():
    cfg = AgentConfig(vfobs_emit_enabled=False)
    e = emission.build_emitter(cfg)
    assert isinstance(e, emission._NoopEmitter)


def test_build_emitter_noop_when_sdk_absent(monkeypatch):
    monkeypatch.setattr(emission, "_SDK_AVAILABLE", False)
    cfg = AgentConfig(vfobs_emit_enabled=True, vfobs_emit_url="u",
                      vfobs_emit_token="t")
    assert isinstance(emission.build_emitter(cfg), emission._NoopEmitter)


# ---- safe_emit guarantees ------------------------------------------------

def test_safe_emit_noop_when_sdk_absent(monkeypatch):
    monkeypatch.setattr(emission, "_SDK_AVAILABLE", False)
    emission.safe_emit(RecordingEmitter(), "task_claimed",
                       workgraph_id="wg", task_id="t")  # must not raise


def test_safe_emit_skips_when_workgraph_id_empty(recording_events):
    rec = RecordingEmitter()
    emission.safe_emit(rec, "task_claimed", workgraph_id="", task_id="t")
    assert rec.events == []  # no milestone ⇒ skipped, not crashed


def test_safe_emit_emits_with_workgraph_id(recording_events):
    rec = RecordingEmitter()
    emission.safe_emit(rec, "task_claimed", workgraph_id="wg_1",
                       task_id="t_1", source="s", claimed_by_agent_id="a")
    assert len(rec.events) == 1
    assert rec.events[0]["_event"] == "task_claimed"
    assert rec.events[0]["workgraph_id"] == "wg_1"


def test_safe_emit_swallows_raising_emitter(recording_events):
    # THE load-bearing fail-safe: emitter.emit raising must not
    # propagate out of safe_emit.
    emission.safe_emit(RecordingEmitter(raise_on_emit=True),
                       "task_claimed", workgraph_id="wg", task_id="t")


# ---- controller wiring (claim + terminal) --------------------------------

def _ws():
    ws = AsyncMock()
    ws.count_rework_attempts = AsyncMock(return_value=0)
    return ws


@pytest.mark.asyncio
async def test_poll_and_execute_emits_claimed_and_terminal(
    recording_events, monkeypatch
):
    ws = _ws()
    task = TaskInfo(id="t_1", title="T", spec="s", project_id="p",
                    test_command={}, needs_review=False, assigned_to=None,
                    workgraph_id="wg_42")
    ws.poll.return_value = task
    ws.claim.return_value = task
    cfg = AgentConfig(agent_id="a", agent_role="executor")
    c = Controller(ws, cfg)
    c._agent_info = AgentInfo(id="agent-1", token="x")
    rec = RecordingEmitter()
    c._emitter = rec
    monkeypatch.setattr(
        c, "execute",
        AsyncMock(return_value=ExecutionResult(
            success=True, session_id=None, completion_report="ok",
            cost_usd=0.3, num_turns=5, gate_results=[])),
    )

    await c._poll_and_execute()

    kinds = [e["_event"] for e in rec.events]
    assert "task_claimed" in kinds and "task_state_changed" in kinds
    sc = next(e for e in rec.events if e["_event"] == "task_state_changed")
    assert sc["workgraph_id"] == "wg_42" and sc["to_status"] == "done"
    ws.complete.assert_awaited()  # task still reported normally


@pytest.mark.asyncio
async def test_poll_and_execute_unaffected_by_exploding_emitter(
    recording_events, monkeypatch
):
    """Fail-safe end-to-end: a raising emitter does NOT stop the
    task being executed + reported."""
    ws = _ws()
    task = TaskInfo(id="t_1", title="T", spec="s", project_id="p",
                    test_command={}, needs_review=False, assigned_to=None,
                    workgraph_id="wg_42")
    ws.poll.return_value = task
    ws.claim.return_value = task
    c = Controller(ws, AgentConfig(agent_id="a"))
    c._agent_info = AgentInfo(id="agent-1", token="x")
    c._emitter = RecordingEmitter(raise_on_emit=True)
    monkeypatch.setattr(
        c, "execute",
        AsyncMock(return_value=ExecutionResult(
            success=True, session_id=None, completion_report="ok",
            cost_usd=0.0, num_turns=1, gate_results=[])),
    )

    await c._poll_and_execute()  # must not raise
    ws.complete.assert_awaited()


# ---- heartbeat loop emission --------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_loop_emits_heartbeat_and_workdir(
    recording_events, tmp_path
):
    (tmp_path / "f").write_text("v1")
    ws = AsyncMock()
    rec = RecordingEmitter()
    task = asyncio.create_task(heartbeat_loop(
        ws, "t_1", 0,  # 0s interval — ticks immediately
        workgraph_id="wg_9", workdir=tmp_path, emitter=rec, source="src",
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    kinds = {e["_event"] for e in rec.events}
    assert "task_heartbeat" in kinds
    assert "task_workdir_changed" in kinds  # first sig ≠ None ⇒ emitted
    assert all(e["workgraph_id"] == "wg_9" for e in rec.events)


@pytest.mark.asyncio
async def test_heartbeat_loop_emits_before_first_interval(
    recording_events, tmp_path
):
    """F4 (Critical): the first heartbeat + workdir signal must fire on
    loop ENTRY, before the first interval sleep. Otherwise a task that
    finishes in < heartbeat_interval (the common task class) emits
    nothing, and the watcher's Stall/Crashed rules — both gated on
    last_heartbeat_at — never arm. Uses a large interval and cancels
    well before it elapses: with the old sleep-at-top loop this records
    zero events; with the fix it records the entry tick."""
    (tmp_path / "f").write_text("v1")
    ws = AsyncMock()
    rec = RecordingEmitter()
    task = asyncio.create_task(heartbeat_loop(
        ws, "t_short", 600,  # would-be 600s interval; we cancel at 50ms
        workgraph_id="wg_9", workdir=tmp_path, emitter=rec, source="src",
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    kinds = {e["_event"] for e in rec.events}
    assert "task_heartbeat" in kinds  # entry tick, not after the 600s sleep
    assert "task_workdir_changed" in kinds  # first sig ≠ None ⇒ emitted
    ws.heartbeat.assert_awaited()  # vtf keepalive also fires on entry


@pytest.mark.asyncio
async def test_heartbeat_loop_default_args_unchanged(monkeypatch):
    """V16: existing callers (no kw args) still work — no emit, no
    raise, just the original keepalive behaviour."""
    ws = AsyncMock()
    task = asyncio.create_task(heartbeat_loop(ws, "t_1", 0))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    ws.heartbeat.assert_awaited()  # original behaviour intact
