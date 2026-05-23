"""Tests for controller.context — task context file generation."""

import pytest
from pathlib import Path
from src.controller.context import build_context, write_context


class TestBuildContext:
    def test_basic_context_has_title_and_spec(self):
        task_data = {"id": "t1", "title": "Add feature", "spec": "do stuff", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[])
        assert "# Task: Add feature (t1)" in result
        assert "do stuff" in result

    def test_includes_test_commands(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {"unit": "pytest -v"}}
        result = build_context(task_data, notes=[], reviews=[])
        assert "pytest -v" in result

    def test_executor_gets_deliverable_branch_contract(self):
        """F7/F10 producer side: the executor must be told the deterministic
        branch the delivery gate checks (docs/f7-f10-delivery-gate-DESIGN.md)."""
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[], role="executor")
        assert "vafi/task-t1" in result
        assert "push" in result.lower()

    def test_judge_not_given_deliverable_branch_instruction(self):
        """The branch-push contract is an executor instruction, not a judge one."""
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[], role="judge")
        assert "vafi/task-t1" not in result

    def test_no_history_when_empty(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[])
        assert "## History" not in result

    def test_includes_review_in_history(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        reviews = [{"decision": "changes_requested", "reason": "Fix the bug", "reviewer_id": "judge-1", "created_at": "2026-03-28T10:00:00Z"}]
        result = build_context(task_data, notes=[], reviews=reviews)
        assert "## History" in result
        assert "changes_requested" in result
        assert "Fix the bug" in result

    def test_includes_notes_in_history(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        notes = [{"text": "Completed task", "actor_id": "executor-1", "created_at": "2026-03-28T09:00:00Z"}]
        result = build_context(task_data, notes=notes, reviews=[])
        assert "Completed task" in result

    def test_filters_vafi_metadata_notes(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        notes = [
            {"text": "vafi:session_id=abc123", "actor_id": "controller", "created_at": "2026-03-28T09:00:00Z"},
            {"text": "Real note", "actor_id": "executor", "created_at": "2026-03-28T09:01:00Z"},
        ]
        result = build_context(task_data, notes=notes, reviews=[])
        assert "vafi:session_id" not in result
        assert "Real note" in result

    def test_executor_rework_instruction_when_rejection_exists(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        reviews = [{"decision": "changes_requested", "reason": "Fix it", "reviewer_id": "j1", "created_at": "2026-03-28T10:00:00Z"}]
        result = build_context(task_data, notes=[], reviews=reviews, role="executor")
        assert "rework" in result.lower()
        assert "fix the issues" in result.lower() or "previous review" in result.lower()

    def test_executor_new_work_instruction_when_no_rejection(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[], role="executor")
        assert "Implement" in result

    def test_judge_instruction(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[], role="judge")
        assert "judge" in result.lower()
        assert "verify" in result.lower() or "verdict" in result.lower()

    def test_judge_re_review_instruction(self):
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        reviews = [{"decision": "changes_requested", "reason": "Fix it", "reviewer_id": "j1", "created_at": "2026-03-28T10:00:00Z"}]
        result = build_context(task_data, notes=[], reviews=reviews, role="judge")
        assert "re-review" in result.lower() or "previous rejection" in result.lower()

    def test_judge_instruction_forbids_writes_to_origin(self):
        """vafi#37: judges are verifiers, not authors. The judge prompt must
        explicitly forbid every git write operation (push, commit, tag,
        branch, gh pr create) so a prompt-following judge cannot silently
        fabricate or rewrite the delivery — the load-bearing case where a
        write-capable judge becomes a ghost-completion enabler.
        """
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[], role="judge")
        lowered = result.lower()
        # Each forbidden write op must be explicitly named so a literal-
        # minded prompt-follower cannot infer "git push" wasn't on the list.
        for forbidden in [
            "git push",
            "git commit",
            "git tag",
            "git branch",
            "gh pr create",
        ]:
            assert forbidden in lowered, (
                f"judge instruction missing explicit deny of {forbidden!r}"
            )
        # And a positive framing of the read-only intent.
        assert "verification" in lowered or "read-only" in lowered

    def test_judge_instruction_allows_read_only_verification_ops(self):
        """The deny-list is not enough on its own — the prompt also needs
        an allow-list naming the read-only operations that ARE expected,
        so the judge knows what tools it can use to do its job.
        """
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[], role="judge")
        lowered = result.lower()
        for allowed in ["git log", "git diff", "git ls-remote"]:
            assert allowed in lowered, (
                f"judge instruction missing allow-list entry {allowed!r}"
            )

    def test_executor_instruction_does_not_contain_judge_deny_list(self):
        """Regression guard: the executor's instruction must NOT carry the
        judge's deny-list — the executor's whole purpose is to push the
        deliverable branch.
        """
        task_data = {"id": "t1", "title": "T", "spec": "", "test_command": {}}
        result = build_context(task_data, notes=[], reviews=[], role="executor")
        lowered = result.lower()
        # The executor instruction obviously mentions "push" (deliverable
        # contract). It must NOT carry a 'never git push' / 'forbidden'
        # framing aimed at the judge.
        assert "never git push" not in lowered
        assert "forbidden" not in lowered or "deliverable" in lowered


class TestWriteContext:
    def test_creates_vafi_directory(self, tmp_path):
        workdir = tmp_path / "task-123"
        workdir.mkdir()
        write_context(workdir, "test content")
        assert (workdir / ".vafi" / "context.md").exists()

    def test_writes_content(self, tmp_path):
        workdir = tmp_path / "task-123"
        workdir.mkdir()
        write_context(workdir, "hello world")
        content = (workdir / ".vafi" / "context.md").read_text()
        assert content == "hello world"

    def test_overwrites_existing(self, tmp_path):
        workdir = tmp_path / "task-123"
        workdir.mkdir()
        write_context(workdir, "first")
        write_context(workdir, "second")
        content = (workdir / ".vafi" / "context.md").read_text()
        assert content == "second"
