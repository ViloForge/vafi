# Fault-injection harness for fail-loud verification (vafi) — DESIGN

**Status:** DESIGN v0.1 — 2026-05-20. Design-first.
**Architecture:** evaluation-system affordance for the deterministic
substrate (`agentic-pipeline-ARCHITECTURE.md` §7 "dogfood the experiment
as regression"; GOVERNING strategy: *build evaluation systems, no
hand-patches, long-term solutions only*). First consumer: R3b
(`r3b-verdict-write-fail-loud-DESIGN.md`).
**Kind:** feature (north-star TDD; SOLID Decorator; zero prod impact when off).

## Why

The controller's I2 fail-loud guarantees (driving a task to a vtf-defined
terminal/`needs_attention` state rather than silently abandoning it) are
proven by unit tests, but several only fire on a **work-source call
raising** — e.g. R3b escalates only when `submit_review()` raises (a
verdict-write failure). In the deployed system that failure is real
(transient vtf 5xx / connection drop during a rolling restart) but **not
triggerable on demand**: authz-403 is closed by R2, and clone/harness
errors are caught and turned into ordinary verdicts. So the fail-loud
paths cannot be dogfooded as live regression without a way to
deterministically induce a work-source failure.

This harness provides that: a controlled, repeatable failure of any
work-source seam, so every fail-loud path becomes an observable,
re-runnable scenario — not a one-off racy outage.

## Design

A **Decorator** over the existing `WorkSource` protocol
(`controller/worksources/protocol.py`).

- `FaultInjectingWorkSource(inner, policy)` delegates everything to
  `inner` (via `__getattr__`), but for the **fail-loud write seams** —
  `submit_review`, `complete`, `fail`, `report_integration_result`,
  `add_note` — it consults `policy` first and raises `FaultInjected`
  (a dedicated exception) for the first *N* calls, then passes through.
- `policy: dict[str, int]` = method-name → remaining-raise-count.
- `parse_fault_spec(env) -> dict[str,int]` parses
  `VF_FAULT_INJECT="submit_review:1,complete:2"` (method:count, comma-
  separated); malformed entries are ignored with a warning.
- `maybe_wrap_faults(work_source) -> WorkSource` reads `VF_FAULT_INJECT`;
  if set+non-empty it returns the decorator and logs a **WARNING**
  ("FAULT INJECTION ACTIVE: …") so the harness is never silently live;
  otherwise returns `work_source` unchanged (V16 byte-identical — prod
  is unaffected).

Wiring: one line in `controller/__main__.py` right after the
`VtfWorkSource` is constructed. The `Controller` is untouched
(Open/Closed) — it depends only on the `WorkSource` protocol.

## Safety / prod isolation

- Off unless `VF_FAULT_INJECT` is set and non-empty. The env is **not**
  set in any prod/standard deployment values — it is supplied ad-hoc
  (kubectl patch) or in a dedicated test scenario only.
- When active it logs a loud WARNING at startup. It only ever *raises*
  on the listed seams — it never fabricates success, never mutates
  state, never hides a real error.

## Tests (TDD red→green)

`tests/test_fault_injection.py`:
1. `parse_fault_spec`: valid single/multi, empty, malformed → expected dict.
2. decorator raises `FaultInjected` for the configured count then passes
   through to inner (call-through verified).
3. non-configured methods (e.g. `poll`, `register`) delegate unchanged.
4. `maybe_wrap_faults`: env unset → returns the same object; env set →
   returns a `FaultInjectingWorkSource`.

## Dogfood (R3b live escalation, repeatable)

Deploy with the harness in the image. Set `VF_FAULT_INJECT=submit_review:1`
on the judge (kubectl patch; selfHeal reverts after ~one sync, ample
window). Drive a `judge=true` task to `pending_completion_review`; the
judge produces a verdict and calls `submit_review`, which raises
`FaultInjected` → R3b's `except` → `work_source.fail()` →
`needs_attention`. Assert the task lands in `needs_attention` with the
R3b note. Re-runnable any time; the same harness later verifies the
executor and integration fail-loud paths.
