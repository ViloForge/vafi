"""Gate execution for vafi task verification.

Gates are shell commands that run after the harness completes to verify
task implementation. They use exit codes to determine success/failure,
following the design principle that controllers never parse LLM output.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import TaskInfo, RepoInfo, GateResult

logger = logging.getLogger(__name__)


def deliverable_branch(task_id: str) -> str:
    """The deterministic branch the executor must push and the delivery gate
    verifies on origin. Single source of truth for the F7/F10 convention
    (docs/f7-f10-delivery-gate-DESIGN.md) — also consumed by the context
    builder so producer and verifier sides cannot drift.
    """
    return f"vafi/task-{task_id}"


def _delivery_gate_command(task_id: str, base_branch: str) -> str:
    """Shell command asserting the deliverable was *durably pushed* to origin:
    a branch ``vafi/task-<id>`` exists on origin and carries commits the base
    branch does not (tip SHA differs). Uses ``git ls-remote`` so it queries
    the remote directly — unaffected by the shallow/detached pod clone, and
    NOT satisfiable by a workdir-only local commit (closes F10). Always
    present, so a no-``test_command`` task is no longer a vacuous pass (F7).
    """
    branch = deliverable_branch(task_id)
    return (
        "set -e; "
        f"remote_sha=$(git ls-remote --heads origin '{branch}' | cut -f1); "
        '[ -n "$remote_sha" ] || { echo "deliverable branch '
        f"'{branch}' not found on origin (nothing pushed)\"; exit 1; }}; "
        f"base_sha=$(git ls-remote --heads origin '{base_branch}' | cut -f1); "
        '[ "$remote_sha" != "$base_sha" ] || { echo "deliverable branch '
        f"'{branch}' is identical to base '{base_branch}' "
        '(no new commits pushed)"; exit 1; }; '
        f"echo \"deliverable verified on origin: {branch} @ $remote_sha\""
    )


def _efficacy_gate_command(task_id: str, base_branch: str, test_command: str) -> str:
    """The 'tests-were-red' check (docs/held-out-tamper-proof-gate-DESIGN.md slice 1).

    A genuine TDD test is RED before the implementation exists; a vacuous or
    executor-weakened test never was. So reconstruct ``base + ONLY the
    deliverable's test files`` (the implementation absent) and run the
    acceptance command: it MUST fail. If it PASSES without the implementation,
    the tests do not depend on it ⇒ vacuous/tampered ⇒ reject. This closes the
    proven L1 gate-tamper crack (ViloForge/vafi#32) deterministically — the
    executor cannot ship broken code green by weakening its own test.

    Refs are fetched from origin (not the possibly-mutated workdir — JQ1=yes).
    Test files are identified by Python test-naming convention; a deliverable
    that changes no test files is skipped (delivery + test_command still gate).
    """
    branch = deliverable_branch(task_id)
    return rf"""
bbase=refs/efficacy/base; bdeliv=refs/efficacy/deliv
if ! git fetch -q origin "+{base_branch}:$bbase" "+{branch}:$bdeliv"; then echo "efficacy: cannot fetch refs from origin"; exit 1; fi
recon=$(mktemp -d)
trap 'git worktree remove --force "$recon" 2>/dev/null; git update-ref -d "$bbase" 2>/dev/null; git update-ref -d "$bdeliv" 2>/dev/null' EXIT
if ! git worktree add -q --detach "$recon" "$bbase"; then echo "efficacy: cannot materialise base tree"; exit 1; fi
tests=$(git -C "$recon" diff --name-only "$bbase".."$bdeliv" | grep -E '(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.py$|(^|/)tests?/' || true)
if [ -z "$tests" ]; then echo "efficacy: deliverable changed no test files; nothing to witness - skipping"; exit 0; fi
if ! git -C "$recon" checkout -q "$bdeliv" -- $tests; then echo "efficacy: cannot overlay deliverable test files onto base"; exit 1; fi
echo "efficacy: running acceptance command against base+tests with NO implementation"
if ( cd "$recon" && {test_command} ) >/dev/null 2>&1; then
  echo "efficacy FAIL: acceptance tests PASS without the implementation (vacuous or tampered tests) [files: $tests]"
  exit 1
fi
echo "efficacy OK: acceptance tests fail without the implementation (were genuinely red) [files: $tests]"
exit 0
"""


@dataclass
class GateConfig:
    """Configuration for a single verification gate."""
    name: str
    command: str
    required: bool = True


class GateRunner:
    """Executes verification gates after harness completion.

    Gates are shell commands run in the task workdir that verify the
    implementation is correct. Exit code 0 = pass, non-zero = fail.
    """

    def __init__(self, gates: list[GateConfig]):
        """Initialize the gate runner.

        Args:
            gates: List of gate configurations to execute
        """
        self.gates = gates

    async def run_gates(self, workdir: Path, task: TaskInfo) -> list[GateResult]:
        """Run all configured gates sequentially.

        Gates run in the task workdir and receive the working directory
        as their cwd. For MVP, gates are created from the task's test_command
        field if it exists.

        Args:
            workdir: Working directory for gate execution (task workdir)
            task: Task information for context

        Returns:
            List of GateResult objects with execution details
        """
        if not self.gates:
            logger.debug(f"No gates configured for task {task.id}")
            return []

        logger.info(f"Running {len(self.gates)} gates for task {task.id} in {workdir}")
        results = []

        for gate in self.gates:
            logger.info(f"Running gate '{gate.name}': {gate.command}")
            result = await self._run_single_gate(gate, workdir, task)
            results.append(result)

            if gate.required and not result.passed:
                logger.warning(f"Required gate '{gate.name}' failed for task {task.id}")
                # Continue running remaining gates for complete reporting

        passed_count = sum(1 for r in results if r.passed)
        logger.info(f"Gates complete for task {task.id}: {passed_count}/{len(results)} passed")

        return results

    async def _run_single_gate(self, gate: GateConfig, workdir: Path, task: TaskInfo) -> GateResult:
        """Execute a single gate as a subprocess.

        Runs the gate command in the workdir and captures exit code, stdout, stderr.
        Following the lesson from M2.5, we decode bytes manually rather than using
        text=True on asyncio.create_subprocess_exec.

        Args:
            gate: Gate configuration
            workdir: Working directory for execution
            task: Task info for logging

        Returns:
            GateResult with execution details
        """
        try:
            # Run gate command as subprocess
            # Use shell=True since commands might contain pipes, redirects, etc.
            process = await asyncio.create_subprocess_shell(
                gate.command,
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Combine stderr with stdout
            )

            # Wait for completion and capture output
            stdout_bytes, _ = await process.communicate()

            # Decode bytes manually (learned from M2.5)
            stdout = stdout_bytes.decode('utf-8') if stdout_bytes else ''

            # Determine pass/fail based on exit code
            passed = process.returncode == 0

            logger.debug(f"Gate '{gate.name}' for task {task.id}: exit_code={process.returncode}, passed={passed}")

            return GateResult(
                name=gate.name,
                command=gate.command,
                exit_code=process.returncode,
                stdout=stdout,
                passed=passed
            )

        except Exception as e:
            logger.error(f"Gate '{gate.name}' execution failed for task {task.id}: {e}", exc_info=True)
            return GateResult(
                name=gate.name,
                command=gate.command,
                exit_code=-1,
                stdout=f"Gate execution error: {str(e)}",
                passed=False
            )

    @classmethod
    def from_task_command(cls, test_command: dict[str, Any]) -> "GateRunner":
        """Create a GateRunner from task test_command field.

        For MVP, create a single gate from the task spec's test_command.
        If test_command exists and has a "command" field, use it as a gate.

        Args:
            test_command: Test command dictionary from task spec

        Returns:
            GateRunner with gates based on test_command
        """
        gates = []

        if test_command and "command" in test_command:
            gate = GateConfig(
                name="task-test",
                command=test_command["command"],
                required=True
            )
            gates.append(gate)
            logger.debug(f"Created gate from test_command: {gate.command}")

        return cls(gates)

    @classmethod
    def from_task(cls, task: TaskInfo, repo_info: RepoInfo) -> "GateRunner":
        """Create a GateRunner for a task: an always-present, required
        delivery gate (verifies the deliverable was durably pushed to
        origin) followed by the optional ``test_command`` gate.

        This is the F7/F10 fix seam. We do NOT special-case the controller's
        success computation; the delivery requirement is just another
        required gate, so the existing exit-code machinery is reused
        unchanged (Open/Closed). ``from_task_command`` is kept verbatim for
        existing direct callers/tests (V16).
        """
        gates = [
            GateConfig(
                name="deliverable-pushed",
                command=_delivery_gate_command(task.id, repo_info.branch),
                required=True,
            )
        ]
        # Reuse the existing optional test_command gate, ordered AFTER the
        # delivery gate (delivery is the floor; test_command refines it).
        tc_gates = cls.from_task_command(task.test_command).gates
        gates.extend(tc_gates)
        # The 'tests-were-red' efficacy gate (ViloForge/vafi#32): only meaningful
        # when there IS a test_command to witness. Ordered last — delivery and
        # test_command are the floor; efficacy proves the test_command isn't
        # vacuous/tampered. Same exit-code machinery (Open/Closed).
        if tc_gates:
            gates.append(GateConfig(
                name="tests-were-red",
                command=_efficacy_gate_command(
                    task.id, repo_info.branch, task.test_command["command"]
                ),
                required=True,
            ))
        return cls(gates)