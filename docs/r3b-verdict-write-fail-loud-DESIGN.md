# R3b — Controller verdict-write fail-loud (vafi) — DESIGN

**Status:** IMPLEMENTED v0.1 — 2026-05-20. Code + unit tests green
locally; deploy + experiment-regression dogfood pending cluster
availability (vafi-dev path degraded this session).
**Architecture:** R-slice **R3b** of `agentic-pipeline-ARCHITECTURE.md`
§7 — split out of R3's faithful-reporting correction ("controller
verdict-write fail-loud" was *not* delivered by vtaskforge#9; it is a
small vafi-side slice). Realises the controller half of I2.
**Kind:** bugfix (executor methodology — TDD red/green, fail-loud, no
silent stall).

## The defect (source-verified)

`controller/controller.py` `_poll_and_review` wraps the **entire** judge
path — harness `execute`, verdict parse, and `submit_review` (the
verdict write) — in one `except Exception` whose only action was a
best-effort `add_note` followed by `return`. Consequences:

- A `submit_review` failure (the precise #18 "judge-write failure
  swallowed" case) is caught and discarded. The task stays in
  `pending_completion_review`; the judge polls the next task.
- The task is then recoverable **only** by R3's server-side
  `expire_stale_reviews` reaper, after the full `review_expires_at`
  timeout (default 30 min). The controller carries no fail-loud
  obligation of its own — exactly the I2 gap the architecture calls
  out (responsibility table: "Controller fail-loud obligation (I2) …
  judge-write failure swallowed", #18).

This is asymmetric with the rest of the controller, which already fails
loud: `_process_task` calls `work_source.fail(...)` on error;
`_poll_and_integrate` reports `report_integration_result(success=False)`
→ `needs_attention`.

## Grounded transition legality

- `tasks/state_machine.py` `VALID_TRANSITIONS["pending_completion_review"]`
  includes `needs_attention` (the same edge R3's reaper uses).
- vtaskforge `tasks/views.py:315 fail()` performs
  `perform_transition(task, "needs_attention", trigger_source="fail")`
  and clears stale claim fields. So `work_source.fail()` on a task in
  `pending_completion_review` is legal and lands it in `needs_attention`.
- `worksources/vtf.py:113 fail()` = `add_note("Task failed: <reason>")`
  then `tasks.fail()` — it both annotates and transitions, subsuming the
  old best-effort note.

## The fix

In `_poll_and_review`'s `except`, replace the swallowing `add_note` with
an explicit escalation: `await work_source.fail(task.id, reason)`,
driving `pending_completion_review → needs_attention` so the
human-escalation terminal (the needs-attention/reviews queue, the
architecture's defined fail-loud consumer) sees it immediately rather
than after the timeout. This covers both failure phases — a harness
failure (no verdict producible) and a verdict-write failure (verdict
produced, not recordable); both strand the task identically.

If `fail()` itself also fails (vtf unreachable — often the very reason
`submit_review` failed), log `CRITICAL` and fall through. R3's
`expire_stale_reviews` is the server-side backstop of last resort:
client-driven liveness is structurally the F4 mistake, so the durable
guarantee stays server-side; R3b only removes the *silent* swallow and
escalates promptly when vtf is reachable.

## Tests (TDD)

`tests/test_judge.py::TestJudgeVerdictWriteFailLoud` (red → green):
1. verdict-write failure → `work_source.fail(task_id, …)` awaited once.
2. judge-harness failure → `fail()` awaited; `submit_review` not called.
3. escalation also failing → logged, never raised (reaper backstop).
4. happy path → `submit_review` awaited; `fail()` never called.

## Deploy + dogfood (2026-05-20)

Deployed: vafi#24 merged (main `59abe10`), built (`vafi-build-9zx5m`),
tag-bumped (viloforge-platform `6c0d19b`), ArgoCD rolled vafi-judge to
`vafi-agent:59abe10`. R3b escalation source confirmed present in the
running image (`controller.py:378`).

**Live happy-path regression — PASS.** The deployed judge polls
`/v2/reviews/pending/` cleanly and ran a full review cycle (pick up →
harness → verdict → `submit_review` 201).

**Escalation trigger surface — empirically refined (key finding).**
Attempted injection: a `judge=true` task in `pending_completion_review`
pointed at an unreachable repo. Result: the judge's `execute()` **caught**
the clone failure and **returned a failure result** — it did NOT raise —
so `_parse_verdict` defaulted to `changes_requested` and `submit_review`
**succeeded (201)**. R3b's `except` was therefore never entered. The task
still reached `needs_attention` (no silent stall) — but via the
*executor's* pre-existing fail-loud (`_process_task`) after re-attempt,
not R3b.

Conclusion: R3b's `except` fires **only** when `submit_review()` itself
raises (the true #18 verdict-write failure) or an exception escapes
`execute()` (rare — the invoker catches clone/exec errors). In the
deployed system `submit_review` does not fail on demand: authz-403 is
closed by R2 (judge has fleet authority — 201 even on a non-member
project), clone/harness failures are handled gracefully, and the
remaining triggers (vtf 5xx / connection drop mid-write, e.g. a rolling
restart) require fault injection or a racy outage to reproduce. **R3b
thus guards a real but not-on-demand-triggerable failure mode (transient
vtf unavailability during the verdict write).**

**Live escalation — PROVEN (2026-05-20).** The fault-injection harness
(`docs/fault-injection-DESIGN.md`, vafi `ea42ddd`) made the trigger
reproducible. With `VF_FAULT_INJECT=submit_review:1` on the judge, a
`judge=true` task in `pending_completion_review` exercised the exact R3b
path end-to-end:

```
controller.invoker  ERROR  Git clone failed (execute() returns a result, not raise)
fault_injection     WARNING VF_FAULT_INJECT: raising on submit_review
controller          ERROR  Error reviewing task …: injected fault on submit_review
httpx               POST …/notes/  201
httpx               POST …/fail/   200
```
Task event: `status_changed | fail | {"to":"needs_attention","from":
"pending_completion_review"}` — driven directly from review state by the
judge's `fail()` (R3b), not the executor. Reproducible any time via the
harness.

**Status: DELIVERED.** Deployed (vafi-judge `ea42ddd`), happy-path
regression verified live, escalation proven both by the unit suite (4
cases) and a deterministic live dogfood. The original "induce a
verdict-write failure → needs_attention" bar is met.
