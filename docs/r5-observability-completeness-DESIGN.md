# R5 — Observability completeness (F3 / F4 / F9) — DESIGN

**Status:** DRAFT v0.1 — design-first, pre-implementation.
**Owner:** lonvilo@pm.me
**Slice:** R5 of the deterministic-substrate roadmap
(`viloforge-platform/docs/agentic-pipeline-ARCHITECTURE.md` §7).
**Upstream findings:** `docs/executor-judge-observability-FINDINGS.md`
(F3 #—, F4 #10, F9 #12).
**North star:** `viloforge-platform/docs/engineering-principles.md`
(SOLID + design patterns + TDD red/green + full pyramid).

---

## 1. Problem (source-grounded, verified 2026-05-20)

R5 closes "complete monotonic event stream" — three findings, all
verified against current `main`. The vfobs *service* (WG1/WG2/WG5-min)
is deployed + verified on vafi-dev; these defects are in the **vafi
controller emission cadence** and the **`vfobs-watch` rule defaults**,
i.e. the producer side of the stream, not vfobs itself.

### F4 — first progress signal delayed by a full heartbeat interval (Critical)

`heartbeat_loop` (`src/controller/heartbeat.py:92-93`) sleeps **before**
its first emit:

```python
while True:
    await asyncio.sleep(interval_seconds)   # <-- top of loop
    ...                                      # heartbeat + workdir emit AFTER
```

With `heartbeat_interval = 300` (`config.py:25`; chart values 49/97/136,
all three pools), the **first** `task.heartbeat` / `task.workdir_changed`
lands at t≈300s. A task that finishes in <300s emits *zero* heartbeat /
workdir events — the loop is cancelled at task end before it ever
completes one sleep. vfobs then sees only `task.claimed → task.state_
changed(terminal)` with nothing in between. The watcher's `Stall` +
`Crashed` rules are both gated on `last_heartbeat_at`, which never gets
set ⇒ **proactive stuck-detection is structurally inert for the common
(<300s) task class.** This is the 2026-05-14 pi-hang failure mode.

### F3 — watcher crash threshold below the emit cadence (High)

`vfobs-watch` defaults to `crash_s = 120` (`sdk/python/.../watch`
rules; encoded in `test_watch_rules.py:59-60`). The controller heartbeats
every 300s. A perfectly healthy task that hasn't beat for 200s is flagged
`CRASHED` — a false positive *by construction*, because the alarm
threshold is shorter than the signal period. F3 and F4 are the same
cadence-coherence defect seen from the two ends of the stream.

### F9 — milestone-less tasks are wholly invisible (High)

`safe_emit` (`src/controller/emission.py:126-132`) drops **every** event
when `workgraph_id` is empty:

```python
if not kwargs.get("workgraph_id"):
    logger.debug("vfobs: skipping %s — task has no workgraph_id ...")
    return
```

`workgraph_id ← milestone.id`. A standalone vtaskforge task (no
milestone) is a legitimate run — a one-off spike, a bugfix, an ad-hoc
experiment — but emits nothing, so it cannot be watched at all.

---

## 2. Why these are one slice

All three are facets of one invariant: **the event stream must be
complete and monotonic from claim to terminal, for every task, and the
consumer's alarm cadence must be coherent with the producer's emit
cadence.** F4 = stream incomplete at the head (no early signal); F3 =
consumer mis-calibrated against the producer; F9 = stream absent for a
whole task class. Fixing one without the others leaves the stream
incomplete.

---

## 3. Design

### D-F4 — emit on entry, sleep at the tail (controller)

Restructure `heartbeat_loop` so the **first** heartbeat (+ workdir
signature capture) fires immediately on entry, and the interval sleep
moves to the tail of the loop body. The claim-keepalive `work_source.
heartbeat()` call retains its current cadence semantics (vtf claim
timeout is unchanged), but the **vfobs liveness/progress emit** is no
longer hostage to the first sleep.

- The emit cadence is now: t≈0 (entry), then every `interval_seconds`.
- A <300s task emits at least one heartbeat + one workdir signal.
- **No new pattern** — this is a loop-ordering correction within the
  existing fail-safe emit path. The keepalive call ordering vs. vtf is
  preserved (design note in the doc + a test asserting first-tick emit).

Open sub-decision **OQ-F4a:** should the *vtf claim heartbeat* also fire
on entry, or only the vfobs emit? Keeping vtf cadence unchanged is the
conservative, minimal-blast-radius choice (claim timeout math is
unaffected); the fix is scoped to the vfobs emit. **Proposed: vfobs emit
on entry; vtf keepalive unchanged.**

### D-F3 — derive watcher thresholds from the emit cadence (watcher/SDK)

The watcher's `crash_s` default must be **strictly greater** than the
controller's heartbeat interval, with margin for one missed beat.
Proposed: `crash_s` default = `2 × heartbeat_interval` (≈600s) and
`stall_s` re-based likewise. The thresholds should not be free-floating
literals divorced from the producer cadence — express the relationship
(a `from_cadence(heartbeat_interval)` constructor on the rule config),
so F3 cannot silently regress when the interval changes (Specification /
config-object pattern, already the v1 affordance in the DESIGN doc V15).

### D-F9 — synthesize a stable workgraph_id for milestone-less tasks

Replace the "drop if no workgraph_id" guard with a **deterministic
fallback identity**: when a task has no milestone, derive a stable,
collision-free synthetic workgraph_id from the task id
(proposed: `"task-" + task_id`). Properties required:

- **Stable** across every event of the same run (so the watcher /
  read API can correlate the run's events).
- **Namespaced** so it never collides with a real milestone id
  (the `task-` prefix; real milestone ids are vtaskforge nanoids).
- **Self-describing** so an operator/retro reading the event log can
  tell "this was a standalone task, not a milestone DAG."

This honors G9 ("every event carries workgraph_id") while making the
invariant *total* instead of *partial*. The synthesis happens once, at
the controller boundary where `workgraph_id` is resolved from the task,
not scattered through `safe_emit`.

Open sub-decision **OQ-F9a:** prefix scheme (`task-<id>` vs a dedicated
`standalone-<id>` namespace) and whether the vfobs read API needs to
*know* the convention (e.g. to render "standalone run" vs "milestone").
**Proposed: `task-<id>`, convention documented in the event-schema
notes; vfobs treats it as an opaque workgraph_id (no read-side change
required for R5).**

---

## 4. Test plan (TDD red/green, full pyramid)

| Finding | Unit (red→green) | Integration | Scenario / dogfood |
|---|---|---|---|
| F4 | `heartbeat_loop` emits a `task.heartbeat` on the **first** tick before any interval sleep (fake clock / RecordingEmitter); a task cancelled at t < interval still produced ≥1 emit | emit path with a real bounded-queue emitter, assert first event timestamp ≪ interval | dogfood: claim a real <300s task on vafi-dev, confirm vfobs has heartbeat+workdir events for it |
| F3 | watcher rule: healthy task heartbeating every 300s is **not** `CRASHED` under the new default; `from_cadence(300)` yields `crash_s>300` | watcher against a synthetic event stream that beats every 300s ⇒ verdict OK | dogfood: `vfobs-watch` a real >300s run ⇒ stays OK, no false CRASHED |
| F9 | `safe_emit` / id-resolution: milestone-less task yields `workgraph_id="task-<id>"` and the event is **emitted** (not dropped); milestone task is unchanged | emit a milestone-less run end-to-end ⇒ events land under the synthetic id | dogfood: standalone task on vafi-dev ⇒ queryable via `GET /workgraphs/task-<id>/events` |

Regression dogfood ties back to the empirical failures the FINDINGS doc
records — a <300s task and a milestone-less task, the two classes that
were invisible, are now observable end-to-end.

## 5. Blast radius / safety

Emission stays **degradable by design** (D-T1-impl-2): every change is
inside the existing fail-safe path (`safe_emit` swallows all; emitter is
optional, default OFF). No change makes emission able to raise, block, or
slow the controller. The vtf claim-keepalive cadence is unchanged
(D-F4 scopes the entry-emit to vfobs only). The watcher default change
is consumer-side only.

## 6. Out of scope (later WG)

Live mid-run `harness.turn_*` streaming (consuming the harness JSONL as
it is produced, instead of `_parse_*_output` post-hoc at
`invoker.py:441`) is a richer fix for *granular* progress but is **not**
required to close F3/F4/F9 — the heartbeat+workdir cadence fix restores a
complete monotonic stream. Streaming harness turns is tracked separately
(WG3/WG5 full controller instrumentation in the observability plan).
</content>
