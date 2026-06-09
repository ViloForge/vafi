# Tamper-proof gate — DESIGN

**Status:** DRAFT v0.2 — 2026-05-22. **The v0.1 "fork" (A1 vs A4) is dissolved** (see §4): it was a false choice. The load-bearing primitive is a deterministic test-efficacy check; the held-out oracle is an additive application of it, not a competing branch.
**Issue:** ViloForge/vafi#32 (proven gate-tamper crack). Related: #33 (cross-provider judge), vtaskforge#15 (R6 v2 — the same efficacy primitive at the admission layer).
**North star:** `viloforge-platform/docs/engineering-principles.md`. **Governing axiom:** correctness-load-bearing outcomes belong to the deterministic layer, never an LLM.
**Strategic context:** `ViloForge-PRD/vafi-verification-direction-RESEARCH.md`.

---

## 1. Problem (empirically proven)

The executor authors, sees (`context.py:53-60`), and can edit the very tests it is graded on; `gates.py` runs `test_command` in the agent's own branch checkout with no held-out comparison. Proven 2026-05-22 vs `vafi@8d8c442`: a broken impl + an agent-weakened test (`assert True`) ships **green**, while the same broken impl + the real test **fails** — the only delta is the agent editing its own gate. (Repro: `ViloForge-PRD/vafi-gate-crack-experiment.py`; encoded as `tests/test_efficacy_gate_git.py`.)

## 2. Two threats at different depths

- **L1 — vacuous / weakened tests** (the **proven** crack): the executor's test has no teeth, so broken code passes. Demonstrated.
- **L2 — coherent-but-wrong**: the executor misunderstands the spec and writes *both* impl and tests to the wrong behavior — internally consistent, externally wrong. **Not yet observed**; the deeper threat.

## 3. The insight — enforce the "red" half of TDD, deterministically

A genuine TDD test is **red before the implementation exists**; a vacuous or tampered test was **never red**. So the gate can reconstruct the world *without the implementation* and require the acceptance assertions to **fail**:

> **Tests-were-red check:** take the base ref, apply **only the agent's test files** from the delivered branch (not the implementation), run `test_command`. It MUST fail. If it passes, the tests don't depend on the implementation ⇒ vacuous/tampered ⇒ reject.

Verified against the proven crack: the `tamper` test (`print('AC1 PASS')`) passes with no impl → caught; the `honest` test (`from prime import is_prime`) errors with no impl → survives. **A git diff + an exit code closes L1** — no schema change, no methodology change, TDD-by-executor preserved, no LLM in the load-bearing path.

This is the same primitive as vtaskforge#15 (R6 v2 "does the assertion have teeth"), applied **post-delivery** to the executor's tests instead of at admission to the spec-author's `test_command`.

## 4. Why there is no fork — the regress terminates deterministically

A1 (spec-author held-out test) and A4 (agent-generated test) both aim at **L2** and both only work *with* an efficacy check — a lazy oracle author just moves the gaming up a level ("who verifies the verifier"). So:

- The **efficacy check is the primitive.** It closes L1 directly and is what makes any L2 oracle trustworthy.
- The **held-out oracle (old A1) is an additive application** of the same check: a spec-author-authored acceptance test, server-held, executor-hidden, validated by the efficacy check, that closes **L2**. It is the per-task image of the deferred independent-validation oracle (`software-factory-orchestration-RESEARCH.md`) — so it stays deferred until L2 is actually observed (experiments→findings→requirements).
- **A4 is "let an agent author that oracle"** — an implementation detail under the same architecture, not a competing design.

The regress ends at a **deterministic** check (tests must have been red), not another LLM ⇒ respects Bet A + the governing axiom.

## 5. Sliced plan

- **Slice 1 — the "tests-were-red" efficacy gate (closes L1 / the proven crack). NOW.**
  A new required gate in `gates.py`, ordered after delivery + `test_command`, that: re-derives the delivered branch's test-file diff vs base (re-clone hygiene / JQ1=yes — read from the pushed ref, not the mutated workdir), reconstructs `base + test-files-only`, runs `test_command`, and **requires failure**. Deterministic; Open/Closed (just another `GateConfig`). Net-new tasks: base lacks the new behavior, so a real acceptance test fails on base. Test-file identification: a configurable glob (default `test_*.py`, `*_test.py`, `tests/**`), with an optional spec hint. *Honest scope:* closes L1; does **not** close L2 (a coherent-but-wrong impl+test pair survives — that's slice 2).
- **Slice 2 — held-out spec-author acceptance oracle (closes L2). ADDITIVE, DEFERRED.**
  vtf gains `held_out_test` (spec-author-authored, never emitted into `.vafi/context.md`); R6 requires it for AC'd tasks; the gate injects it into the fresh clone as the authoritative acceptance gate, validated by the **same** efficacy check from slice 1. Introduce when L2 is observed and/or the PM/spec-author role work matures. Opt-in → required, exactly like R6 rolled out.

## 6. TDD test plan (red→green, north-star pyramid)

- **`tests/test_efficacy_gate_git.py` (integration, hermetic git — RED today, the executable DoD for #32):** honest→PASS, control (broken impl + real test)→FAIL, **tamper (broken impl + weakened test)→FAIL**. Tamper is `xfail(strict=True)` until slice 1 lands; when the gate ships it xpasses → forces removal of the marker. Models `test_delivery_gate_git.py`.
- **Unit:** test-file glob selection; base-reconstruction omits impl files; V16 — a task whose tests legitimately fail-on-base is unaffected; no-`test_command` tasks skip the efficacy gate (delivery gate still backstops, F7).

## 7. Blast radius / safety
Slice 1: a new gate reading from the pushed ref + a temp base-reconstruction; pure-deterministic, no model fields, no migration. Skipped when there is no `test_command` (bare/no-AC tasks unaffected — consistent with R6 OQ-R6a). V16 for any task whose acceptance tests are genuinely red-on-base (i.e., all well-formed tasks).
Slice 2: additive vtf field + R6 requirement behind "task has ≥1 AC"; coordinated with vtaskforge#15.

## 8. Open
- **F2:** does slice 1 ship as its own PR ahead of slice 2? (Recommended: yes — it closes the *proven* crack and is self-contained.)
- **F3 (slice 2 only):** `held_out_test` as a new JSON field on Task parallel to `test_command`? (Likely yes.)
- **F4:** rework feedback granularity for slice 2 — show the executor *which AC* the held-out test failed, never the held-out test source (else the oracle re-leaks).
