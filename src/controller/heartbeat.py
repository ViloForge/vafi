"""Heartbeat mechanisms for vafi controller.

Two separate heartbeat concerns:
- agent_heartbeat_loop: Agent-level liveness signal (am I alive?). Runs always.
- heartbeat_loop: Task-level claim keepalive (am I still working on this task?).
  Runs only during task execution.
"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .emission import safe_emit, workdir_signature

if TYPE_CHECKING:
    from .worksources.protocol import WorkSource

logger = logging.getLogger(__name__)


async def agent_heartbeat_loop(
    work_source: "WorkSource",
    agent_id: str,
    interval_seconds: int,
) -> None:
    """Send periodic agent-level heartbeats to signal liveness.

    Runs as a background asyncio task for the lifetime of the controller.
    Independent of task execution — beats whether idle or busy.

    Args:
        work_source: WorkSource implementation for heartbeat calls
        agent_id: Agent ID to heartbeat for
        interval_seconds: Seconds between heartbeat calls
    """
    logger.info(f"Starting agent heartbeat loop (interval={interval_seconds}s)")

    try:
        while True:
            try:
                await work_source.agent_heartbeat(agent_id)
                logger.debug(f"Agent heartbeat sent for {agent_id}")
            except Exception as e:
                logger.warning(f"Agent heartbeat failed: {e}")

            await asyncio.sleep(interval_seconds)

    except asyncio.CancelledError:
        logger.info("Agent heartbeat loop stopped")
        raise


async def heartbeat_loop(
    work_source: "WorkSource",
    task_id: str,
    interval_seconds: int,
    *,
    workgraph_id: str = "",
    workdir: "Path | None" = None,
    emitter=None,
    source: str = "vafi-controller",
) -> None:
    """Send periodic heartbeats for a claimed task.

    Runs as an asyncio task concurrently with harness execution. Calls
    work_source.heartbeat(task_id) every interval_seconds to extend the
    claim timeout in vtf.

    The keyword-only args are OPTIONAL + defaulted (V16 discipline —
    existing direct callers/tests are unaffected). When an emitter is
    provided: each tick also emits a vfobs `task.heartbeat` (alive
    signal) and, when the workdir signature changed since the prior
    tick, `task.workdir_changed` (the progress signal — plan §D7).
    Both are fail-safe: emission/sig errors never perturb the loop.

    Args:
        work_source: WorkSource implementation for heartbeat calls
        task_id: ID of the claimed task
        interval_seconds: Seconds between heartbeat calls
        workgraph_id: vfobs dimension (empty ⇒ vfobs emits skipped)
        workdir: task workdir for the change signature (optional)
        emitter: vfobs Emitter (None ⇒ no vfobs emission)
        source: event source string

    The coroutine runs until cancelled via asyncio.CancelledError.
    """
    logger.info(f"Starting heartbeat loop for task {task_id} (interval={interval_seconds}s)")
    _prev_sig: str | None = None

    try:
        while True:
            # F4 (Critical): beat + emit on ENTRY, sleep at the TAIL —
            # mirrors agent_heartbeat_loop. The old loop slept first, so
            # the first heartbeat/workdir signal was delayed by a full
            # interval (300s default) and a task finishing in < interval
            # emitted nothing, leaving the watcher's last_heartbeat_at
            # unset and proactive stuck-detection structurally inert.
            try:
                await work_source.heartbeat(task_id)
                logger.debug(f"Heartbeat sent for task {task_id}")
            except Exception as e:
                # Log heartbeat errors but don't crash the loop
                # The task execution should continue even if heartbeats fail
                logger.warning(f"Heartbeat failed for task {task_id}: {e}")

            # vfobs alive + progress signals (fail-safe; safe_emit
            # swallows everything, workdir_signature returns None on
            # any error → the loop is never perturbed).
            if emitter is not None:
                safe_emit(
                    emitter, "task_heartbeat",
                    workgraph_id=workgraph_id, task_id=task_id,
                    source=source,
                )
                if workdir is not None:
                    sig = workdir_signature(workdir)
                    if sig is not None and sig != _prev_sig:
                        _prev_sig = sig
                        safe_emit(
                            emitter, "task_workdir_changed",
                            workgraph_id=workgraph_id, task_id=task_id,
                            source=source, files_changed=0, commits=0,
                        )

            await asyncio.sleep(interval_seconds)

    except asyncio.CancelledError:
        logger.info(f"Heartbeat loop cancelled for task {task_id}")
        # Re-raise to properly handle cancellation
        raise