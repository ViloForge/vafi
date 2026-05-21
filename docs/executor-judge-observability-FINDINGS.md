# Executor / Judge Observability — Empirical Findings

**Date:** 2026-05-16 (experiments) / written up 2026-05-17
**Environment:** `vtf.dev.viloforge.com` (vtf-dev) + `vafi-dev` fleet, project `vtf-e2e`
(repo `vilosource/vtf-canary`). Live, post-L4b (vfobs emission deployed).
**Method:** synthetic canary tasks fired into the live fleet; ground truth
reconstructed from the vfobs read API event stream, vtf native event log,
controller pod logs, task `execution_summary`, and the target repo itself.
**Status of each claim:** ✅ = verified against ≥2 independent sources
(live run + source/log); ⚠ = single-source or attribution-nuanced.

## Why this exists

The WG5-min arc delivered a proactive observation channel (vfobs emission +
`vfobs-watch`). With it working, the executor/judge could be *exercised* and
*observed live* rather than inferred from silent timeouts. Three experiments
were run to characterize limitations. They surfaced ten findings; the
headline ones (F7, F10) confirm and mechanize the previously-documented
closed-system ghost-completion weakness.

## Experiments

| # | Setup | Harness | Outcome |
|---|-------|---------|---------|
| 1 | Re-fired proven canary (good spec) | Claude `executor` | done/approved — **healthy baseline** |
| 2 | `is_prime` spec, weak ACs, **no `test_command`** | `executor-pi` | done/approved — **empty repo (ghost)** |
| 3 | Same spec **with externally-grounded `test_command`**, pi scaled to 0 | Claude `executor` | done/approved — **gate passed but repo still empty (ghost)** |

## Findings

| ID | Sev | One-line | Status |
|----|-----|----------|--------|
| F1 | — | vfobs emission is live end-to-end post-L4b (behavior-verified, not just capability) | ✅ |
| F3 | High | `vfobs-watch --crash-seconds` default (120) < controller `heartbeat_interval` (300) ⇒ false-positive CRASHED on healthy >300s tasks | ✅ → **FIXED (R5)** `from_cadence()` |
| F4 | **Critical** | Heartbeat loop sleeps 300s *before* first emit; workdir-change is a sub-step of it ⇒ sub-300s tasks emit zero heartbeat/workdir ⇒ `Stall`+`Crashed` both inert (gated on `last_heartbeat_at`) ⇒ proactive stuck-detection structurally non-functional for the common task class | ✅ → **FIXED (R5)** beat-on-entry |
| F5 | Med | `required_tags=executor` matches *both* `executor` and `executor-pi` (`executor,pi`) pools ⇒ silent claim race, nondeterministic harness per task | ✅ |
| F6 | High | Controller→**Pi**-harness prompt construction did not deliver the persisted task spec; agent self-reported "no task specified" though vtf stored an 795-char spec | ⚠ (Pi-path specific; Claude path delivers fine) |
| F7 | **Critical** | No `test_command` ⇒ `GateRunner` builds zero gates ⇒ task success == "agent process exited 0"; `acceptance_criteria` never machine-checked; judge LLM affirmatively approved an empty workdir | ✅ |
| F8 | — | (Retracted) review record *is* persisted (`POST /v2/tasks/<id>/reviews/ 201`); earlier empty `reviews[]` was a `task show` serialization nuance | ✅ |
| F9 | High | A task with **no milestone** emits zero vfobs events (`workgraph_id ← milestone.id`; empty ⇒ emission skipped) ⇒ milestone-less tasks fully invisible to proactive observability | ✅ → **FIXED (R5)** synth `task-<id>` |
| F10 | **Critical** | Even *with* an externally-grounded `test_command`, the gate runs in the ephemeral pod workdir; an agent that locally-commits but never pushes/opens the required PR still passes, task→done, judge approves — deliverable lost, indistinguishable from a no-op from the repo's perspective | ✅ |

## Detail & mechanism

### F4 — proactive stuck-detection is structurally inert (Critical)
`vafi/src/controller/heartbeat.py`: the loop is
`while True: await asyncio.sleep(interval_seconds); emit task_heartbeat; if sig_changed: emit task_workdir_changed`.
`interval_seconds = heartbeat_interval`, default **300s** (`VF_HEARTBEAT_INTERVAL`,
`controller/config.py`). The sleep precedes the first emit, and
`task.workdir_changed` is nested inside the heartbeat tick — so neither can
appear before t=300s, sampled only at 300s granularity thereafter.
`vfobs_sdk/watch.py`: both `Stall.evaluate` and `Crashed.evaluate` early-return
`None` when `last_heartbeat_at is None`. Therefore any task finishing in
<300s (Exp#1 ran 167s; most real tasks finish well under 5 min) produces
**zero** of the two signals the detectors consume → `vfobs-watch` prints
`OK: progressing` from claim to terminal and **never alerts**. Empirically:
Exp#1 captured 6 events (`task.claimed`, `harness.turn_started/completed` ×2,
`task.state_changed`) — *none* of type `task.heartbeat`/`task.workdir_changed`.
The unit/scenario suites pass only because they synthesize fine-cadence
heartbeat/workdir events the real controller never emits — a grounding gap at
the watcher↔controller event-contract boundary.

### F7 — closed-system rubber-stamp, no gate (Critical)
`vafi/src/controller/controller.py` (~416–450): after harness `exit_code==0`,
`GateRunner.from_task_command(task.test_command)`. `gates.py:from_task_command`
builds a gate **only** `if test_command and "command" in test_command`;
otherwise `gates=[]`. `all_required_gates_passed` initialises `True` and is
only falsified by a *failing* gate, so an empty gate list ⇒ vacuous pass ⇒
`success=True` **purely because the agent process exited 0**.
`task.acceptance_criteria` is stored in vtf but **never read** on this path
(`GateRunner` consumes only `test_command`). Judge side
(`controller.py:_poll_and_review`): `verdict = self._parse_verdict(judge_llm_output)`;
`_parse_verdict` **fails safe** (unparseable ⇒ `changes_requested`, *not*
approved). Exp#2: executor (Pi) produced nothing — its own
`execution_summary` said *"No execution occurred … no actions were taken"* —
yet task→`done` and the judge LLM affirmatively emitted
`{"decision":"approved"}` for an empty shared workdir. Empty repo confirmed
against `vilosource/vtf-canary` ground truth.

### F10 — delivery-dimension ghost survives the documented mitigation (Critical)
Exp#3 added an externally-grounded gate
(`python3 -c "import primes; assert is_prime(7) and not is_prime(9) …"`) and
forced the Claude executor. Controller logs: `Running gate 'task-test' … 1/1
passed`. But `vtf-canary` had **no `primes.py`, no branch, no PR**. The
executor's `execution_summary` shows it created `primes.py` and *locally*
committed it (`480214c`) in `/sessions/task-<id>/` — the **ephemeral pod
workdir** — and never pushed or opened the PR the task explicitly required.
`gates.py` runs the gate via subprocess with the pod workdir as cwd, so the
gate validated the throwaway local state and passed legitimately *for code
correctness*. Pod discarded ⇒ work lost ⇒ from the durable repo's
perspective identical to Exp#2's no-op. The `test_command` mitigation only
narrows the hole from *"any no-op passes"* to *"code correct in the
discarded workdir passes"*; it does **not** verify the deliverable shipped,
and the "open a PR" requirement / `acceptance_criteria` are never enforced.

### F6 — Pi-harness task delivery (High, attribution-nuanced)
Exp#2's `executor-pi` agent self-reported *"received a structured data
object but no task was specified"* (`turn_count: 0`, `duration: 0`) while the
vtf task record held a correct 795-char `spec` + `description` +
`acceptance_criteria`. So the loss is downstream of vtf, in the
controller→Pi-harness prompt construction. Exp#3's Claude `executor` read
`.vafi/context.md` + `README.md` and did the work — so the defect is
**Pi-path-specific**, not universal. Root cause not yet isolated.

### F1 / F3 / F5 / F9 — see findings table; all ✅ verified as stated.

## Synthesis

The pipeline trusts **process exit codes and ephemeral local state**, never
**durable external ground truth**:

- `acceptance_criteria` is decorative — never machine-checked anywhere.
- The judge LLM is the only "verifier" without a gate, and it rubber-stamps
  without an objective anchor (Exp#2).
- A `test_command` anchors only the throwaway pod workdir, not the shipped
  deliverable (Exp#3).

Three independent ghost-completion surfaces — *no gate* (F7), *gate but no
delivery verification* (F10), *task never delivered to the agent* (F6) — all
terminate as `done`/`approved`. Independently, the proactive observation
channel that would have caught a *stalled* such run is itself inert for the
common task class (F4) and entirely absent for milestone-less tasks (F9).

## Recommended remediations (prioritized)

1. **F7/F10 — gate the deliverable, not the workdir.** Mark a task complete
   only after verifying durable ground truth (branch/PR pushed to origin,
   matching the task's stated deliverable). Make `acceptance_criteria`
   machine-enforced or stop advertising it as acceptance.
2. **F4 — fix the emission cadence/contract.** Emit the first heartbeat
   immediately (sleep last, or pre-tick); emit `task.workdir_changed`
   independently of the 300s heartbeat; and/or have `WatchState` derive
   liveness/progress from `harness.turn_started/completed` (which *are*
   emitted). Derive `vfobs-watch` crash/stall thresholds from the controller
   `heartbeat_interval`, not hardcoded 120/60 (F3).
3. **F6 — fix Pi-harness task delivery** so the persisted spec reaches the
   agent prompt; add a controller-side assertion that the agent received a
   non-empty task before accepting `exit_code==0` as success.
4. **F9 — emit even without a milestone** (synthesize a stable workgraph_id
   from project/task), or treat milestone-less tasks as a configuration
   error rather than silently un-observable.
5. **F5 — make harness selection explicit** (distinct required tags per
   pool) so experiments and routing are deterministic.

### Remediation status (R5, 2026-05-21)

- **F4 — DONE** (remediation #2, cadence path): `heartbeat_loop` now
  beats + emits on loop entry and sleeps at the tail, so the first
  heartbeat/workdir signal lands at t≈0 instead of after a full
  interval. The richer "derive liveness from `harness.turn_*`" option is
  deliberately *deferred* — live mid-run harness streaming is a later WG
  (the cadence fix alone restores a complete monotonic stream). See
  `docs/r5-observability-completeness-DESIGN.md` §6.
- **F3 — DONE**: `vfobs-watch` thresholds now derive from the controller
  cadence via `from_cadence(heartbeat_interval)` (crash = 2× interval,
  stall = 1× interval) instead of hardcoded 120/60; CLI `--heartbeat-
  interval`.
- **F9 — DONE**: milestone-less tasks get a stable synthetic
  `workgraph_id = "task-<id>"` at the vtf worksource boundary, so they
  emit instead of being dropped.
- **F5 — DONE** earlier (R4, disjoint claude/pi tags).

## Tracking issues

| Finding | Issue |
|---------|-------|
| F7  | [#8](https://github.com/ViloForge/vafi/issues/8) |
| F10 | [#9](https://github.com/ViloForge/vafi/issues/9) |
| F4  | [#10](https://github.com/ViloForge/vafi/issues/10) |
| F6  | [#11](https://github.com/ViloForge/vafi/issues/11) |
| F9  | [#12](https://github.com/ViloForge/vafi/issues/12) |

## Provenance

All raw timelines, event dumps, controller-log excerpts, and source
citations are recorded in the `viloforge-platform` kb workspace journal
(2026-05-16/17) and kb `vafi`-area gotchas `dlMZ1rZD` (F3/F4), `EMksjx8h`
(F5/F6/F7), `qTUZ3cdT` (F9/F10). Experiments are reproducible: re-fire a
`vtf-e2e` task, capture `/tasks/<id>/events` from the vfobs read API +
`controller` pod logs + the target repo.
