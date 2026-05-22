# Held-out / tamper-proof gate — DESIGN

**Status:** DRAFT v0.1 — design-first, pre-implementation. **One load-bearing fork (§4) needs a decision before code.**
**Issue:** ViloForge/vafi#32 (proven gate-tamper crack). Related: #33 (cross-provider judge), vtaskforge#15 (R6 v2).
**North star:** `viloforge-platform/docs/engineering-principles.md`. **Governing axiom:** correctness-load-bearing outcomes belong to the deterministic layer, never an LLM.
**Strategic context:** `ViloForge-PRD/vafi-verification-direction-RESEARCH.md`.

---

## 1. Problem (empirically proven)

The executor authors, sees (`context.py:53-60`), and can edit the very tests it is graded on; `gates.py` runs `test_command` in the agent's own branch checkout with no held-out comparison. Proven 2026-05-22 vs `vafi@8d8c442`: a broken impl + an agent-weakened test (`assert True`) ships **green**, while the same broken impl + the real test **fails** — the only delta is the agent editing its own gate. (Repro: `ViloForge-PRD/vafi-gate-crack-experiment.py`.)

Backstops that do **not** catch it: the delivery gate (push-exists only), R6 (AC-label word-boundary match only), and — the sole remaining line — the LLM judge, the field's *weakest* layer (JudgeBench: near-random on objective correctness), reading a *possibly-mutated workdir* (the judge SOP's own open question **JQ1**).

## 2. Goal

Make a broken implementation **impossible to ship green by editing its own tests** — deterministically, in the gate (strong layer), not by relying on the judge.

## 3. The hardest unknown (resolve first)

> A tamper-proof gate needs an **oracle the executor does not control**. But vafi's SDD/TDD methodology has the **executor write its own test bodies** (the spec-author pins the `test_command` and the AC-label *names*; the executor writes the *assertions*). So closing the crack requires changing **where the authoritative acceptance test comes from.**

Re-cloning the pushed branch (resolving JQ1) is necessary hygiene — it defeats workdir mutation — but it is **not sufficient**: a fresh clone still contains the agent's weakened test. The crack closes only when acceptance is judged against a test the executor never wrote or saw.

## 4. Options for the oracle (THE FORK)

| Opt | Mechanism | Closes crack? | Cost |
|---|---|---|---|
| **A1 — spec-author held-out acceptance test** | Spec-author authors the acceptance test **body**, stored in vtf, never shown to the executor, injected into a fresh clone at gate time and run against the agent's non-test code. Executor still writes its own TDD tests for its dev loop; *acceptance* is the held-out test. | **Yes — true held-out oracle** | Spec-author writes real tests; vtf `held_out_test` field; gate injection; R6 requires it for AC'd tasks. Methodology change. |
| A4 — independent test-gen | A separate agent / the judge generates acceptance tests from the ACs; executor never sees them. | Mostly (oracle is independent) | No spec-author burden, but agent-generated tests can be weak/circular (verification-research caveat). Good as an *augmentation* to A1, weak alone. |
| A3 — red-phase test freeze | Capture executor tests at "red" (pre-impl), freeze, require the same tests pass at "green". | Partial — executor still controls the red-phase tests (can write them weak up front) | Two-phase harness protocol change. |
| A2 — tamper-detection heuristic | Detect post-impl assertion removal / test weakening. | No — gameable, no handling of net-new tests | Low, but low value. **Reject as primary.** |

**Recommendation:** **A1** as the architecture — it is the per-task, measurement-scoped image of the deferred independent-validation oracle (`software-factory-orchestration-RESEARCH.md`), and it is fully deterministic. **A4** as an optional later augmentation for ACs the spec-author can't easily test. **A2/A3 rejected** as primary mechanisms.

A1 changes the spec-author methodology contract (specs with ACs must ship a held-out acceptance test body, not just a `test_command` string) and adds a vtf field. **That is the decision to confirm before slice 2 code.**

## 5. Sliced plan

- **Slice 1 — deterministic floor (no schema/methodology change; ships independently).**
  Resolve **JQ1 = yes**: the gate (and judge) run against a **fresh clone of the pushed branch**, not the mutated workdir. Add a `test-file-diff` signal: surface, into the judge context + as a structured gate field, which test files the agent's branch changed vs. the base ref. *Honest scope:* this is hygiene + improved judge input; **it does not by itself close the proven crack** (a fresh clone still has the weakened test). Land it because it's correct regardless of the fork and it's the substrate slice 2 builds on.
- **Slice 2 — closes the crack (depends on the §4 fork = A1).**
  vtf gains `held_out_test` (spec-author-authored, never emitted into `.vafi/context.md`); R6 requires it for tasks with ≥1 AC; the gate injects it into the fresh clone and runs it as the **authoritative acceptance gate**, ranked above the executor's own `test_command`. Re-running `vafi-gate-crack-experiment.py` after slice 2 must flip `tamper` to **FAIL**.

## 6. TDD test plan (red→green, north-star pyramid)

**Slice 1 (unit + integration, in vafi):**
- Gate runs against a fresh clone, not the passed workdir (unit: clone invoked; the workdir's mutations are not read).
- `test-file-diff` correctly lists changed test paths vs base; empty when none changed.
- V16: a task with no test changes behaves byte-identically to today.

**Slice 2 (the crack-closing scenario test — the regression lock):**
- Port `vafi-gate-crack-experiment.py` into the suite as a scenario test: honest→PASS, control→FAIL, **tamper→FAIL** (currently PASS). This test is RED today and turns GREEN only when the held-out oracle lands. It is the executable definition-of-done for #32.
- R6 (vtaskforge#15 coupling): a spec with ACs but no `held_out_test` is inadmissible.

## 7. Blast radius / safety

- Slice 1: gate reads from a fresh clone instead of the workdir — pure-deterministic, no model fields. Exempts no-AC bare tasks (they have no test_command). V16 for tasks that don't mutate tests.
- Slice 2: additive vtf field + an R6 requirement gated behind "task has ≥1 AC"; bare/operator tasks unaffected. Coordinated change across vtaskforge (#15) + vafi.

## 8. Open (for the fork-owner)
- **F1 (THE fork):** A1 (spec-author held-out test) vs A4 (independent test-gen) as the slice-2 oracle. Recommendation: A1, A4 later.
- **F2:** does slice 1 (re-clone hygiene) ship now as its own PR, or wait and ship together with slice 2?
- **F3:** for A1, where does the held-out test live in vtf — a new `held_out_test` JSON field on Task, parallel to `test_command`? (Likely yes; mirrors the existing `test_command` shape.)
