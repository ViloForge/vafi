"""vafi controller implementation.

The Controller class implements the core poll-claim-execute loop for vafi agents.
It polls for work, claims tasks, executes them via harness invocation, and reports results.
"""

import asyncio
import logging
import signal
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .config import AgentConfig
from .context import build_context, write_context
from .emission import build_emitter, make_execution_summary, safe_emit
from .gates import GateRunner, deliverable_branch
from .integration import integrate
from .heartbeat import agent_heartbeat_loop, heartbeat_loop
from .invoker import HarnessInvoker
from .types import ExecutionResult

if TYPE_CHECKING:
    from .worksources.protocol import WorkSource

logger = logging.getLogger(__name__)


class Controller:
    """Core controller that implements the vtf task execution loop.

    The controller depends only on the WorkSource protocol, not on VtfClient
    directly. This allows different work sources to be swapped in without
    changing controller logic.
    """

    def __init__(self, work_source: "WorkSource", config: AgentConfig):
        """Initialize the controller.

        Args:
            work_source: WorkSource implementation for task operations
            config: Agent configuration including tags and intervals
        """
        self.work_source = work_source
        self.config = config
        self._shutdown = asyncio.Event()
        self._agent_info = None
        self._invoker = HarnessInvoker(config)
        self._summarizer = None  # Set via set_summarizer() after construction
        # vfobs emission — no-op unless the optional SDK is present
        # AND emission is enabled+configured (degradable; never on
        # the critical path). Constructed once, injected by DIP.
        self._emitter = build_emitter(config)

    def set_summarizer(self, summarizer) -> None:
        """Inject an optional summarizer for execution trace summarization."""
        self._summarizer = summarizer

    async def run(self) -> None:
        """Main controller loop.

        Registers with the work source, then starts the agent heartbeat loop
        and polls for work. The agent heartbeat runs continuously (every 30s)
        to signal liveness to vtf. On shutdown, marks the agent offline.
        """
        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()

        agent_hb_task = None

        try:
            # Register with work source
            logger.info("Registering agent with work source...")
            # vtf upserts agents by `name`. Default-naming purely by role
            # (e.g. `executor-default`) produces collisions across
            # deployments that share a role but differ in capability tags
            # (e.g. claude-only `executor` vs pi `executor,pi` — both end
            # up as a single vtf agent with the last-writer's tags).
            # Derive the default from the sorted tag set when available so
            # distinct capability profiles get distinct identities; fall
            # back to role only if tags are empty. Operators can still
            # pin an explicit identity via VF_AGENT_ID.
            default_name = (
                "-".join(sorted(self.config.agent_tags))
                if self.config.agent_tags
                else self.config.agent_role
            )
            self._agent_info = await self.work_source.register(
                name=self.config.agent_id or default_name,
                tags=self.config.agent_tags
            )
            logger.info(f"Registered as agent {self._agent_info.id}")

            # Start agent-level heartbeat loop (runs always, independent of tasks)
            agent_hb_task = asyncio.create_task(
                agent_heartbeat_loop(
                    self.work_source,
                    self._agent_info.id,
                    self.config.poll_interval,
                )
            )
            logger.info("Started agent heartbeat loop")

            # Main polling loop
            logger.info("Starting poll loop...")
            while not self._shutdown.is_set():
                try:
                    await self._poll_and_process()
                except Exception as e:
                    logger.error(f"Error in poll loop: {e}", exc_info=True)
                    # Continue loop on errors - don't crash on transient failures

                # Wait for poll interval or shutdown signal
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=self.config.poll_interval
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    continue  # Normal polling interval

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Fatal error in controller: {e}", exc_info=True)
        finally:
            # Stop agent heartbeat loop
            if agent_hb_task is not None:
                agent_hb_task.cancel()
                try:
                    await agent_hb_task
                except asyncio.CancelledError:
                    pass

            # Signal offline to vtf
            if self._agent_info:
                try:
                    await self.work_source.set_agent_offline(self._agent_info.id)
                    logger.info(f"Agent {self._agent_info.id} marked offline")
                except Exception as e:
                    logger.warning(f"Failed to mark agent offline: {e}")

            # Flush any queued vfobs events (D9). Best-effort —
            # aclose never raises on the no-op/real emitter.
            try:
                await self._emitter.aclose()
            except Exception as e:
                logger.debug(f"emitter aclose suppressed: {e}")

            logger.info("Shutting down controller")

    async def _poll_and_process(self) -> None:
        """Poll for work and process a single task if available."""
        if not self._agent_info:
            logger.warning("No agent registration - skipping poll")
            return

        if self.config.agent_role == "judge":
            await self._poll_and_review()
        else:
            # WC-2/D2: drain the milestone merge queue first (frees the
            # WC-1 integration slot promptly), then poll for new work.
            await self._poll_and_integrate()
            await self._poll_and_execute()

    async def _poll_and_execute(self) -> None:
        """Executor: poll for tasks, execute, and report."""
        logger.debug(f"Polling for work (agent_id={self._agent_info.id}, tags={self.config.agent_tags})")
        task = await self.work_source.poll(self._agent_info.id, self.config.agent_tags)

        if task is None:
            logger.debug("No work available")
            return

        logger.info(f"Found task {task.id}: {task.title}")

        try:
            # Claim the task
            logger.info(f"Claiming task {task.id}")
            claimed_task = await self.work_source.claim(task.id, self._agent_info.id)
            logger.info(f"Claimed task {claimed_task.id}: {claimed_task.title}")

            safe_emit(
                self._emitter, "task_claimed",
                workgraph_id=claimed_task.workgraph_id,
                task_id=claimed_task.id,
                source=f"vafi-controller/{self._agent_info.id}",
                claimed_by_agent_id=self._agent_info.id,
            )

            # Enforce VF_MAX_REWORK before invoking the harness (contract §13).
            # count_rework_attempts returns 0 on first-attempt tasks, so the
            # guard is inert outside rework. Failure to count is treated as 0
            # to avoid stalling tasks on a transient metadata error.
            try:
                rework_count = await self.work_source.count_rework_attempts(task.id)
            except Exception as e:
                logger.warning(f"Failed to count rework attempts for task {task.id}: {e}")
                rework_count = 0
            if rework_count >= self.config.max_rework:
                reason = (
                    f"Rework limit exceeded: {rework_count} prior rejections "
                    f"(max {self.config.max_rework}). Human triage required."
                )
                logger.warning(f"Task {task.id}: {reason}")
                await self.work_source.fail(task.id, reason)
                return

            # Log task details
            self._log_task_details(claimed_task)

            # Execute the task
            result = await self.execute(claimed_task)

            # Terminal state_changed (the gated final result — the
            # single executor-scoped site that knows it; D8
            # success→to_status mapping).
            safe_emit(
                self._emitter, "task_state_changed",
                workgraph_id=claimed_task.workgraph_id,
                task_id=claimed_task.id,
                source=f"vafi-controller/{self._agent_info.id}",
                from_status="doing",
                to_status="done" if result.success else "failed",
                execution_summary=make_execution_summary(
                    result.num_turns, result.cost_usd
                ),
            )

            # Summarize execution trace from cxdb (best-effort, non-blocking)
            if self._summarizer:
                asyncio.create_task(self._summarize_best_effort(task.id))
            else:
                await self._post_trace_note(task.id)

            # Report result
            if result.success:
                # vafi#39: surface the harness exit-code anomaly on the
                # task before accepting the delivery, so adjudicators see
                # why we overrode the harness's verdict.
                if result.harness_anomaly:
                    try:
                        await self.work_source.add_note(
                            task.id,
                            f"vafi:harness_exit_anomaly\n{result.harness_anomaly}",
                            self._agent_info.id,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to post harness_exit_anomaly note for "
                            f"task {task.id}: {e}"
                        )
                await self.work_source.complete(task.id, result)
                logger.info(f"Completed task {task.id}")
            else:
                # Prepare failure reason with gate output if gates failed
                failure_reason = result.completion_report
                if result.gate_results:
                    failed_gates = [g for g in result.gate_results if not g.passed]
                    if failed_gates:
                        gate_output = "\n\nGate failures:\n"
                        for gate in failed_gates:
                            gate_output += f"Gate '{gate.name}' failed (exit_code={gate.exit_code}):\n{gate.stdout}\n"
                        failure_reason += gate_output

                await self.work_source.fail(task.id, failure_reason)
                logger.info(f"Failed task {task.id}: {failure_reason}")

        except Exception as e:
            logger.error(f"Error processing task {task.id}: {e}", exc_info=True)
            try:
                await self.work_source.fail(task.id, f"error during processing: {str(e)}")
            except Exception:
                logger.error(f"Failed to fail task {task.id}", exc_info=True)

    async def _poll_and_integrate(self) -> None:
        """WC-2/D2: service the milestone merge queue.

        WC-1 routes an approved workgraph task to 'integrating' (the
        slot is held SoR-side, one in-flight per milestone). Here the
        controller performs the deterministic merge of the task's
        delivered branch (``vafi/task-<id>`` — the F7/F10 deliverable
        ref) into the milestone integration branch and reports the
        outcome. Conflict → fail-loud → needs_attention (WC-1/C3 + the
        C4 reaper own recovery). Idempotent / re-entrant.
        """
        try:
            integrations = await self.work_source.list_integrations()
        except Exception as e:
            logger.error(f"list_integrations failed: {e}", exc_info=True)
            return
        if not integrations:
            return

        task = integrations[0]
        logger.info(f"Integrating task {task.id}: {task.title}")
        try:
            repo = await self.work_source.get_task_repo_info(task)
            proj = await self.work_source.get_repo_info(task.project_id)
            task_branch = deliverable_branch(task.id)
            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp) / "repo"
                outcome = await asyncio.to_thread(
                    integrate,
                    # WC-1.2: clone/push via the same #17 SSH credential
                    # as the executor path — integrate() pushes the
                    # integration branch, so an unauthenticated HTTPS
                    # origin fails ("could not read Username for
                    # https://github.com"). No-op when no key / non-
                    # GitHub (V16 byte-identical).
                    self._invoker._clone_url(repo.url),
                    repo.branch,            # integration branch (= base_ref)
                    proj.branch,            # project default (branch creation)
                    task_branch,
                    workdir,
                )
            await self.work_source.report_integration_result(
                task.id, outcome.success, outcome.detail
            )
            safe_emit(
                self._emitter, "task_state_changed",
                workgraph_id=task.workgraph_id, task_id=task.id,
                source=f"vafi-controller/{self._agent_info.id}",
                from_status="integrating",
                to_status="done" if outcome.success else "needs_attention",
            )
            logger.info(
                f"Integration {'OK' if outcome.success else 'FAILED'} for "
                f"task {task.id}: {outcome.detail}"
            )
        except Exception as e:
            logger.error(f"Integration error for task {task.id}: {e}",
                         exc_info=True)
            try:
                await self.work_source.report_integration_result(
                    task.id, False, f"controller integration error: {e}"
                )
            except Exception:
                logger.error("Failed to report integration error",
                             exc_info=True)

    async def _poll_and_review(self) -> None:
        """Judge: poll for tasks pending review, verify, and submit verdict.

        Unlike executors, judges do not claim tasks. They find a task in
        pending_completion_review, run verification in the shared workdir,
        and submit a review. The review submission handles state transitions.
        """
        logger.debug(f"Polling for reviews (agent_id={self._agent_info.id})")
        task = await self.work_source.poll_reviews(self._agent_info.id)

        if task is None:
            logger.debug("No reviews pending")
            return

        logger.info(f"Found task to review {task.id}: {task.title}")

        try:
            self._log_task_details(task)

            # Execute judge harness (no claim — judge runs in shared workdir)
            result = await self.execute(task)

            # Link execution trace from cxdb (best-effort)
            await self._post_trace_note(task.id)

            # Parse verdict from harness output
            verdict = self._parse_verdict(result.completion_report)

            # Submit review
            await self.work_source.submit_review(
                task.id,
                verdict["decision"],
                verdict["reason"],
                self._agent_info.id,
            )
            logger.info(f"Submitted review for task {task.id}: {verdict['decision']}")

        except Exception as e:
            # R3b: fail loud (I2). Any unrecoverable error in the review path
            # — judge-harness failure OR a verdict-write that could not be
            # recorded (#18) — leaves the task stranded in
            # pending_completion_review. Do not swallow it: drive it to
            # needs_attention so a human (the escalation terminal) sees it,
            # mirroring _process_task. fail() both notes and transitions
            # (pending_completion_review -> needs_attention is legal). If
            # fail() also fails (vtf unreachable), R3's expire_stale_reviews
            # reaper is the server-side backstop — log loudly, never raise.
            logger.error(f"Error reviewing task {task.id}: {e}", exc_info=True)
            try:
                await self.work_source.fail(
                    task.id, f"judge review failed (verdict not recorded): {e}"
                )
            except Exception:
                logger.critical(
                    f"Could not escalate stranded review for task {task.id} to "
                    f"needs_attention; relying on server-side review reaper",
                    exc_info=True,
                )

    def _parse_verdict(self, completion_report: str) -> dict:
        """Parse judge verdict from harness output.

        Extracts JSON verdict from the completion report. Falls back to
        changes_requested if parsing fails.
        """
        import json

        # Try to find JSON in the output
        try:
            # The harness may wrap the result — try direct parse first
            verdict = json.loads(completion_report)
            if "decision" in verdict:
                return verdict
        except (json.JSONDecodeError, TypeError):
            pass

        # Try to find JSON block in the text
        import re
        json_match = re.search(r'\{[^{}]*"decision"[^{}]*\}', completion_report, re.DOTALL)
        if json_match:
            try:
                verdict = json.loads(json_match.group())
                if "decision" in verdict:
                    return verdict
            except json.JSONDecodeError:
                pass

        # Fallback: could not parse verdict
        logger.warning("Could not parse judge verdict from output, defaulting to changes_requested")
        return {
            "decision": "changes_requested",
            "reason": f"Judge output could not be parsed as verdict. Raw output: {completion_report[:500]}",
        }

    async def execute(self, task) -> ExecutionResult:
        """Execute a task using the harness invoker and run verification gates.

        Implementation follows the M2.6 specification with M2.7 heartbeat:
        1. Start heartbeat coroutine concurrently with execution
        2. Invoke harness (already done in M2.5)
        3. If harness succeeded (is_error=false, exit code 0):
           a. Run gates
           b. All gates pass → success=True
           c. Any gate fails → success=False
        4. If harness failed:
           a. Return failure without running gates
        5. Cancel heartbeat task and await clean shutdown

        Args:
            task: TaskInfo object with task details

        Returns:
            ExecutionResult with success status, execution details, and gate results
        """
        # workdir is deterministic from task id — compute it before
        # the heartbeat loop so the loop can emit task.workdir_changed
        # (the progress signal, plan §D7).
        workdir = Path(self.config.sessions_dir) / f"task-{task.id}"

        # Start heartbeat coroutine before beginning execution. New
        # kw args are optional + defaulted so existing direct callers
        # / tests of heartbeat_loop are unaffected (V16 discipline).
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(
                self.work_source, task.id, self.config.heartbeat_interval,
                workgraph_id=getattr(task, "workgraph_id", ""),
                workdir=workdir,
                emitter=self._emitter,
                source=f"vafi-controller/{getattr(self._agent_info, 'id', '?')}",
            )
        )
        logger.debug(f"Started heartbeat task for task {task.id}")

        try:
            logger.info(f"Creating workdir for task {task.id}: {workdir}")

            # WC-2/D1: per-task clone ref — the server-derived base_ref
            # (the milestone integration branch for a workgraph task;
            # project default otherwise — V16 byte-identical).
            repo_info = await self.work_source.get_task_repo_info(task)

            # Clone repo first (must happen before writing context file)
            await self._invoker._ensure_repo_cloned(repo_info, workdir)

            # Write context file into the cloned workdir
            await self._write_task_context(task, workdir)

            # Build prompt — points agent to the context file
            if self.config.agent_role == "judge":
                prompt = f"Verify task {task.title} ({task.id}). Read .vafi/context.md for the full specification and history."
            else:
                prompt = f"Work on task {task.title} ({task.id}). Read .vafi/context.md for the full specification and history."

            # COARSE phase markers bracketing the opaque harness
            # subprocess (G4 — emitted here, NOT inside the pure
            # invoker; G2 — these are NOT the progress signal, the
            # harness yields no mid-run events).
            _src = f"vafi-controller/{getattr(self._agent_info, 'id', '?')}"
            safe_emit(
                self._emitter, "harness_turn_started",
                workgraph_id=getattr(task, "workgraph_id", ""),
                task_id=task.id, source=_src,
                turn_number=0, model=self.config.harness,
            )
            # Invoke harness (clone is no-op since repo already cloned above)
            result = await self._invoker.invoke(task, repo_info, workdir, prompt)
            safe_emit(
                self._emitter, "harness_turn_completed",
                workgraph_id=getattr(task, "workgraph_id", ""),
                task_id=task.id, source=_src,
                turn_number=result.num_turns,
            )

            # vafi#39: always run gates, even on harness-reported failure.
            # The gate is the load-bearing acceptance signal; the harness
            # exit code is a hint. If the F7/F10 delivery gate verifies
            # the branch on origin, the work is real regardless of what
            # the harness exit code said. An honest failure (no push, no
            # branch) is still caught: gates fail → success=False below.
            # Same hardening direction as vafi#36 (D1+D2).
            if not result.success:
                logger.warning(
                    f"Harness reported failure for task {task.id}; "
                    f"running gates anyway to check whether delivery is real"
                )
            else:
                logger.info(f"Harness succeeded for task {task.id}, running gates")
            # F7/F10: always synthesize a required delivery gate (verifies
            # the deliverable was durably pushed to origin) + the optional
            # test_command gate. A no-test_command task is no longer a
            # vacuous pass, and an ephemeral-workdir-only commit no longer
            # satisfies success. See docs/f7-f10-delivery-gate-DESIGN.md.
            gate_runner = GateRunner.from_task(task, repo_info)
            gate_results = await gate_runner.run_gates(workdir, task)

            # Determine overall success based on gate results
            # All required gates must pass for task success
            all_required_gates_passed = True
            for gate_result in gate_results:
                # For MVP, all gates created from test_command are required
                if not gate_result.passed:
                    all_required_gates_passed = False
                    logger.warning(f"Gate '{gate_result.name}' failed for task {task.id}")

            # vafi#39: harness-failure-but-gates-pass → accept delivery and
            # carry the anomaly forward so the poll layer can surface it on
            # the task as a note.
            harness_anomaly: str | None = None
            if not result.success and all_required_gates_passed:
                harness_anomaly = (
                    f"harness reported success=False but gates passed; "
                    f"completion_report={result.completion_report!r}"
                )
                logger.warning(
                    f"Task {task.id}: {harness_anomaly} — accepting delivery"
                )

            # Create final result with gate information
            final_result = ExecutionResult(
                success=all_required_gates_passed,
                session_id=result.session_id,
                completion_report=result.completion_report,
                cost_usd=result.cost_usd,
                num_turns=result.num_turns,
                gate_results=gate_results,
                harness_anomaly=harness_anomaly,
            )

            if final_result.success:
                logger.info(f"Task {task.id} completed successfully with all gates passing")
            else:
                logger.info(f"Task {task.id} failed due to gate failures")

            return final_result

        except Exception as e:
            logger.error(f"Task execution failed for {task.id}: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                session_id=None,
                completion_report=f"Execution failed: {str(e)}",
                cost_usd=0.0,
                num_turns=0,
                gate_results=[]
            )

        finally:
            # Always cancel heartbeat task and wait for clean shutdown
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                # Expected when we cancelled the task
                logger.debug(f"Heartbeat task cancelled cleanly for task {task.id}")
            except Exception as e:
                # Unexpected error in heartbeat cleanup
                logger.warning(f"Error during heartbeat cleanup for task {task.id}: {e}")

    async def _write_task_context(self, task, workdir: Path) -> None:
        """Fetch task history from vtf and write context file to workdir."""
        try:
            ctx = await self.work_source.get_task_context(task.id)
            task_data = ctx["task"]
            notes = ctx["notes"]
            reviews = task_data.get("reviews", []) or []

            # Collect prior summaries and workplan context (Phase B)
            prior_summaries = self._extract_prior_summaries(task_data)
            workplan_context = await self._build_workplan_context(task_data)

            content = build_context(
                task_data=task_data,
                notes=notes,
                reviews=reviews,
                role=self.config.agent_role,
                prior_summaries=prior_summaries,
                workplan_context=workplan_context,
            )
            write_context(workdir, content)

        except Exception as e:
            logger.warning(f"Failed to write context file for task {task.id}: {e}")
            # Non-fatal — agent can still work with just the prompt

    def _extract_prior_summaries(self, task_data: dict) -> list[dict]:
        """Extract execution summaries from prior attempts of this task.

        For now, reads execution_summary from the task (single attempt).
        Future: track multiple attempts via events.
        """
        summary = task_data.get("execution_summary")
        if summary:
            return [summary]
        return []

    async def _build_workplan_context(self, task_data: dict) -> str:
        """Build workplan-level context if workplan is set."""
        workplan_id = task_data.get("workplan")
        if not workplan_id:
            return ""

        try:
            from cxdb.workplan_context import build_workplan_context

            class _VtfTaskSource:
                def __init__(self, work_source):
                    self._ws = work_source
                async def list_tasks_by_workplan(self, wp_id):
                    tasks = await self._ws._client.list_tasks(workplan=wp_id, status="done")
                    return tasks

            return await build_workplan_context(_VtfTaskSource(self.work_source), workplan_id)
        except Exception as e:
            logger.debug(f"Failed to build workplan context: {e}")
            return ""

    def _log_task_details(self, task) -> None:
        """Log detailed information about a claimed task."""
        logger.info("=== Task Details ===")
        logger.info(f"ID: {task.id}")
        logger.info(f"Title: {task.title}")
        logger.info(f"Project: {task.project_id}")
        logger.info(f"Needs Review: {task.needs_review}")
        logger.info(f"Assigned To: {task.assigned_to}")
        if task.test_command:
            logger.info(f"Test Command: {task.test_command}")
        logger.info("--- Spec ---")
        for i, line in enumerate(task.spec.split('\n'), 1):
            logger.info(f"{i:3}: {line}")
        logger.info("=== End Task Details ===")

    async def _summarize_best_effort(self, task_id: str) -> None:
        """Run the summarizer in the background. Best-effort — never fails the task."""
        try:
            summary = await self._summarizer.summarize_task(task_id)
            if summary:
                logger.info(f"Stored execution summary for task {task_id}")
            else:
                logger.debug(f"No summary generated for task {task_id}")
        except Exception as e:
            logger.warning(f"Summarization failed for task {task_id}: {e}")

    async def _post_trace_note(self, task_id: str) -> None:
        """Look up the cxdb context for a task and post its URL as a vtf note."""
        if not self.config.cxdb_url:
            return

        try:
            context_id = await self._lookup_cxdb_context(task_id)
            if context_id is not None:
                base = self.config.cxdb_public_url or self.config.cxdb_url
                trace_url = f"{base}/c/{context_id}"
                await self.work_source.add_note(
                    task_id, f"vafi:trace_url={trace_url}", "controller"
                )
                logger.info(f"Posted trace link for task {task_id}: {trace_url}")
            else:
                logger.debug(f"No cxdb context found for task {task_id}")
        except Exception as e:
            logger.warning(f"Failed to post trace note for task {task_id}: {e}")

    async def _lookup_cxdb_context(self, task_id: str) -> int | None:
        """Query cxdb for a context matching task:<task_id> label."""
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.config.cxdb_url}/v1/contexts",
                params={"limit": "50"},
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()
            label = f"task:{task_id}"
            for ctx in data.get("contexts", []):
                if label in ctx.get("labels", []):
                    return ctx["context_id"]
        return None

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown")
            self._shutdown.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)