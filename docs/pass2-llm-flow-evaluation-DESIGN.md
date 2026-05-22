# Pass 2 — LLM-flow evaluation design (judge evaluation) — DESIGN

**Status:** DESIGN v0.2 — 2026-05-22. Design-first (no code yet).
**v0.2 reframe (user correction, 2026-05-22):** the judge-of-the-judge is **us** (human + Claude-as-architect), evaluating **retrospectively** and improving the judge **incrementally as we go** — not a prospective planted-label automaton. v0.1 inverted this: it designed the *automated* fidelity harness before we had run any judge-evaluation experiments, importing "mutation testing for the judge" as a solution ahead of the empirical findings that would justify it (the exact anti-pattern `vafi-requirements-gap-ANALYSIS.md` warns against, and a violation of the *experiments → findings → requirements* strategy). The harness is the **output** of the loop, not its input.
**Architecture:** the Pass-2 slice of `viloforge-platform/docs/agentic-pipeline-ARCHITECTURE.md` §7.7 — *how we evaluate the LLM-driven flow*. Unblocked by R4 (sound evidence) / R5 / R6.
**Governing strategy:** [build evaluation systems, no hand-patches](`project_agentic_pipeline_strategy`) + [architect-simulation harvest](`project_architect_simulation_harvest`) — same run→observe→harvest loop that built R0–R6 and the architect methodology, now applied to the judge.
**Kind:** evaluation substrate (offline; zero prod-path impact).

---

## 1. Why

Per `agentic-pipeline-ARCHITECTURE.md` §4 responsibility table, last row: executor *strong (13/13)*; **judge *unmeasured***. The judge issues `approved`/`changes_requested` verdicts that drive terminal `done` transitions, and nothing tells us whether those verdicts are correct. Per Bet A (§2.2) the judge may reject but may not rescue a failed gate or terminalize `done` on subjective grounds — but a *miscalibrated* judge (wrongly approving gate-green-but-defective work, or wrongly rejecting clean work) still breaks the boundary rule §2.1(i): *delegation is permitted only if the output is verifiable.* Today the judge's output is **trusted, not verified.**

## 2. How "unmeasured" gets fixed — the principle (load-bearing)

**The judge is measured by us doing experiments and adjudicating its verdicts retrospectively, then improving it as we go.** Three properties, all from the user's framing:

1. **We are the ground-truth authority.** For Job B (inherently non-machine-checkable quality), the "correct verdict" *is* a human/architect judgment — it cannot be pre-canned at scale without smuggling in the very subjectivity we're trying to assess. Claude-as-architect + human is the adjudicator (and per Bet A's no-recursion rule, we do **not** build a meta-judge LLM).
2. **Evaluation is retrospective.** We observe the judge's verdicts on **real tasks**, then decide after the fact whether it judged well. We do not (primarily) manufacture prospective test cases and assert expected verdicts.
3. **Improvement is incremental.** Each adjudicated miss → a patch to `vtf-methodologies/judge/*` → re-observe. The judge gets better as the corpus of adjudicated cases grows. This is the identical loop that produced R0–R6 and the architect methodology.

**Corollary — the synthetic harness is an output, not an input.** A planted-label mutation/golden harness is only justified for a failure mode we have **already confirmed retrospectively** and want to *regression-lock*. Building it first automates answers to questions we have not yet asked.

## 3. The two phases

| | **Phase 1 — now** | **Phase 2 — emergent** |
|---|---|---|
| Mechanism | Human+architect **retrospective evaluation loop** | Regression-lock confirmed clear-cut modes |
| Ground truth | Us, case-by-case, after the fact | Planted labels — *only* for modes already confirmed in Phase 1 |
| Output | Adjudicated-case corpus + recurring miss-modes → judge-methodology patches | Synthetic harness preventing known regressions |
| Builds on | cxdb DAG + vfobs event stream (already capture judge sessions — R5) | Phase-1 findings |
| Trigger to start | now | when a clear-cut mode recurs often enough that manual re-checking is wasteful |

## 4. Phase 1 — the retrospective evaluation loop (the actual Pass-2 deliverable now)

```
 real tasks run through the pipeline
        │  (judge issues verdicts as normal — prod path untouched)
        ▼
 1. PULL      list judge sessions + their verdicts        ◄─ vfobs events + cxdb sessions
 2. REVIEW    read the judge's reasoning trace            ◄─ cxdb_get_turns / breadcrumbs
              + the deliverable it judged
 3. ADJUDICATE  we decide: did the judge get it right?    ◄─ human + Claude-as-architect
              record verdict-on-the-verdict + rationale   ─► adjudicated-case corpus
 4. HARVEST   recurring miss-mode → patch judge/*.md       ─► vtf-methodologies/judge/*
 5. RE-OBSERVE  next runs reflect the patch; corpus grows  ─► loop
```

**Substrate already exists (verified 2026-05-22) — Phase 1 builds almost no new infra:**
- **vfobs** event stream: which task got which verdict and when (R5 gives a complete monotonic stream).
- **cxdb** conversation DAG: the judge's full reasoning per session — `cxdb_list_sessions`, `cxdb_get_turns`, `cxdb_session_breadcrumbs`.

**What Phase 1 actually needs (minimal):**
- **(a) A pull/filter affordance** to surface *judge* sessions — **DONE (P2-1, vafi#30 merged 2026-05-22).** O1 resolved: the invoker labelled cxdb sessions only `task:<id>`, so judge vs executor sessions were indistinguishable; fixed by adding `--label role:<agent_role>` (both claude+pi cxtx paths) and a `role` filter on `cxdb_list_sessions`. **Forward-looking caveat (verified against real cxdb data 2026-05-22):** pre-#30 sessions carry only `cxtx`/harness/`interactive`/`task:` labels — no role — and a task often has several same-harness sessions, so the judge cannot be isolated historically. The loop evaluates tasks judged **after** the #30 deploy.
- **(b) An adjudication record** — **DONE (P2-2): O2 resolved → `vtf-methodologies/judge/adjudications/<task-id>.md`** (one file per task, format in `judge/adjudications/TEMPLATE.md`). Co-located with `judge/*.md` so the empirical case sits next to the SOP it patches (lab-notebook→SOP). Fields grounded in real cxdb context + vfobs verdict data.
- **(c) A light protocol/SOP** — **DONE (P2-2): `vtf-methodologies/judge/EVALUATION-LOOP.md`** (principle, prerequisites, the 5-step loop, sampling policy, miss-mode taxonomy, Phase-2 graduation bar).
- **No new prod-path code** beyond the added session label (P2-1). The judge keeps running as-is; we observe it.

**Definition of done for Phase 1:** the loop runs at least once end-to-end on real judge sessions, produces ≥1 adjudicated miss, and that miss lands as a `vtf-methodologies/judge/*` patch — proving the empirical→harvest path works for the judge dimension (mirrors how R5/R6 dogfooded their experiments).

## 5. Phase 2 — regression-lock (emergent, designed-later)

Once Phase 1 surfaces a **recurring, clear-cut, machine-statable** failure mode, codify it as a synthetic regression so it cannot return. The natural mechanism is a **gate-green, quality-red** fixture: a known-good deliverable transformed so the deterministic gate still passes but a faithful judge must reject — otherwise the fixture re-tests the gate (Job A), not the judge (Job B). Scoring is deterministic against the planted label (no meta-judge).

**Seed candidates already evidenced (NOT yet a backlog — listed so Phase 1 can confirm/deny them):**
- *Ghost external artifact* — notes claim "pushed/PR opened" but the ref/PR doesn't exist on the remote. **Already observed:** 2026-05-14 canary spike-1 judge approved a "PR diff is one line" AC with no PR. (judge `bugfix.md` R1.)
- *Stub-games-test* — tests pass against a hardcoded constant, not a real impl.
- *Silent pattern substitution / SOLID violation* — gate-green code violating a spec-mandated pattern with no rationale (judge `bugfix.md` SOLID/pattern grading).
- *Notes-vs-AC contradiction* — executor notes admit an AC is unmet (judge `bugfix.md` R2).

The full operator catalog, metrics (catch-rate / false-rejection-rate), thresholds, and the harness architecture (Strategy-per-operator, Decorator-style affordance à la `fault-injection-DESIGN.md`) are deferred to the Phase-2 DESIGN, written **after** Phase 1 has produced confirmed modes and baseline behavior. Pre-baking thresholds or operators now would be unverified.

## 6. Executor rubric (named in §7.7; lower priority)

Executor is strong (13/13), so lower priority and a **separate DESIGN**. It follows the *same* principle: retrospective review of real executor submissions against rubric dimensions (spec-adherence, test quality, SOLID), harvested incrementally — not a prospective automaton. Split because its ground truth (submission quality) differs from the judge's (verdict correctness).

## 7. Non-goals

- A meta-judge LLM (recurses the axiom — forbidden by Bet A).
- The independent-validation oracle (`ViloForge-PRD/software-factory-orchestration-RESEARCH.md`, deferred). Phase-2's planted-defect fixtures are the *measurement-scoped, regression-only* image of that idea, not production adjudication.
- Building the Phase-2 harness before Phase 1 yields confirmed modes.
- Planner/architect-flow evaluation (input substrate, separate concern).

## 8. Open questions

- **O1 — RESOLVED (P2-1, vafi#30).** Judge sessions filterable via the new `role:judge` cxdb label + `cxdb_list_sessions(role=...)`. Forward-looking only (pre-#30 sessions lack the label).
- **O2 — RESOLVED (P2-2).** Adjudication record home = `vtf-methodologies/judge/adjudications/<task-id>.md` (co-located with `judge/*.md`; lab-notebook→SOP).
- **O3 — PROVISIONALLY SET (P2-2 SOP v0.1):** all `changes_requested` + all `approved→done` with external-artifact ACs + a random sample of remaining approvals. Tune as the corpus reveals where misses concentrate.
- **O4 — PROVISIONALLY SET (P2-2 SOP v0.1):** a miss-mode graduates to Phase 2 at ≥3 independent corpus occurrences (clear-cut + machine-statable).
- **O5** — executor rubric: share substrate with the judge loop or fully split (lean split). *Open.*

## 9. Sequencing

1. **P2-1 (pull/filter affordance) — DONE (vafi#30 merged 2026-05-22).** `role:judge` label + `cxdb_list_sessions(role=...)`. O1 resolved.
2. **P2-2 (adjudication-record format + loop SOP) — DONE (2026-05-22).** `vtf-methodologies/judge/EVALUATION-LOOP.md` + `judge/adjudications/TEMPLATE.md`. O2/O3/O4 set.
3. **P2-3 (Phase-1 DoD — the first real run)** — **gated on the #30 deploy**: deploy vafi to vafi-dev so judge sessions carry `role:judge`, run a judged task, then run the loop once end-to-end → first adjudicated record → ≥1 harvested `judge/*.md` patch (or a clean "judge correct" record if no miss). Mirrors how R5/R6 dogfooded their experiments.
4. **P2-4** — only if a clear-cut mode recurs (≥3): write the Phase-2 regression-lock DESIGN for *that* mode.
5. Executor rubric → its own DESIGN.

Each slice: design-first → north-star TDD (for any code) → harvest into methodology. No slice is patched.

## 10. References

- `viloforge-platform/docs/agentic-pipeline-ARCHITECTURE.md` §1, §2.2 (Bet A), §4, §7.7
- `vtf-methodologies/judge/bugfix.md` (judge Job-B duties; the canary ghost-completion evidence)
- `vafi/docs/fault-injection-DESIGN.md` (evaluation-affordance precedent; relevant to Phase 2)
- cxdb_mcp query surface: `cxdb_list_sessions`, `cxdb_get_turns`, `cxdb_session_breadcrumbs`; vfobs verdict events (R5)
- `ViloForge-PRD/software-factory-orchestration-RESEARCH.md` (deferred oracle)
- kb: `project_agentic_pipeline_strategy`, `project_architect_simulation_harvest`
