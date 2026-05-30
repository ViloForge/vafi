# C.3 Implementation Plan — vafi controller variables runtime

> Status: **PLAN — building** (2026-05-30).
> Parent design: `viloforge-platform/docs/vtaskforge-variables-DESIGN.md`.
> Predecessors: **C.1** (Vault policies + K8s auth roles + chart SAs) — live in vafi-dev.
> **C.2** (vtaskforge schema + admission — `ProjectVariable`, `Task.variables`,
> admission validation) — merged to vtaskforge `develop`.
> Test governance: `viloforge-platform/docs/engineering-principles.md §3.2` + **§3.2.5
> (applicability gate)** — each slice below records its required test levels.

## Scope

C.3 is the **runtime** that turns a task's declared `variables:` into values the
agent process can actually read. It is phasing stage 3 ("vafi controller code —
tasks without `variables:` see no behavior change; tasks with `variables:`
exercise the new path").

### Execution model — DECISION (2026-05-30): **α (current k8s), behind a pluggable seam**

The current vafi runtime runs the harness as a **subprocess** via `HarnessInvoker`
(`src/controller/invoker.py`), under a single `vtaskforge-executor` SA with a
project-agnostic (wildcard) Vault policy; isolation is controller-discipline on
`project_id`/`slug`. C.3 ships against this (the design's "ship α first").

The design's per-task **K8s Job + ephemeral Secret + per-project SA** spawn flow is
the **β target**, preserved as a documented migration — NOT built here.

Why α now, and why it is **not throwaway** (re: no-throwaway-becomes-permanent):
- It is the only thing **deployable on the existing vafi-dev now** — and §3.2.5
  requires C.3 to reach L4 (e2e + scenario), which needs a real deployed system.
- The durable core — `SecretBackend` protocol + fetch / validate / redact / audit /
  snapshot — is **execution-model-agnostic**. Only the *auth strategy* (k8s
  TokenRequest → Vault) and *injection* (subprocess env/file) are α-specific, and
  both sit behind the seam. The cloud-native redesign (`project_vafi_cloud_native_redesign`:
  bare-VM / AWS `ExecutionBackend`) plugs in a new backend later and reuses the core
  untouched. **The seam is exactly what that redesign wants.**

### Out of C.3 scope
- The β per-task-Job materializer + per-project SA reconcile (future, behind the seam).
- The bare-VM/AWS `ExecutionBackend` (future backend; this plan keeps the seam for it).
- `ProjectConfig`, anything already delivered in C.1/C.2.

## Test-level rulings per slice (§3.2.5 applied)

| Slice | What | Required levels | Why |
|---|---|---|---|
| 1 — agnostic core | `SecretBackend` protocol, `FetchResult`, `VarRef` (+ parse), `LiteralBackend`, `BackendRegistry` | **L1** | pure in-process; no boundary, no contract, nothing deployable |
| 2 — validator + redactor | `PreSpawnValidator` (fail-loud matrix), stream `redactor` (≥8-byte mask) | **L1** | pure logic |
| 3 — VaultBackend | k8s `TokenRequest` → Vault login → KV v2 read; slug-derived path | **L1 + L2** | L2: real Vault + real K8s TokenRequest on dev (first boundary) |
| 4 — audit + snapshot emit | controller → vtaskforge `VariableAudit` + `secrets_snapshot`; add the deferred vtaskforge write endpoint | **L1 + L2 + L3** | L3: emitted record ↔ vtaskforge API schema, both sides |
| 5 — controller injection + probe | inject into HarnessInvoker subprocess; pre-spawn fail-loud; snapshot; `vtf project var probe` (`/admin/probe`) | **L1 + L2 + L4 e2e + L4 scenario** | first deployable vertical + first user-observable, security-sensitive behaviour |

L4 detail (Slice 5):
- **e2e (scripted):** vafi-dev — declare a var → submit a task → assert the agent
  process has the value (env/file); required-missing → no run.
- **scenario (QA agent):** black-box agent, no code access, given a *goal* needing
  the secret (e.g. "clone private repo X, open a PR"), asserts the **external
  effect** (PR exists); probes isolation (A can't read B's), redaction (no log
  leak), fail-loud UX. Findings loop back as new L1/L2 tests.

## Slice 1 — agnostic core (this commit set)

New top-level package `src/variables/`:
- `types.py` — `Role` / `VarName` aliases; `FetchResult` (`value: bytes`,
  `version: int | None`, `audit_metadata: dict`); `VarRef` (frozen dataclass:
  name, kind=vault, scope=project, value, version, target_env, target_file,
  required) + `VarRef.list_from_spec(variables: list[dict])` parser (the task-spec
  `variables:` shape → `list[VarRef]`, applying the design's defaults: source
  vault/project, target env=name, required=true).
- `backend.py` — `SecretBackend` Protocol: `fetch(project_id, role, refs, env) ->
  dict[VarName, FetchResult]`.
- `literal.py` — `LiteralBackend` (kind=`literal` → value bytes, version None).
- `registry.py` — `BackendRegistry`: register backends by `kind`; `fetch()` groups
  refs by kind, dispatches to each backend, merges results (unknown kind → error).

**Tests (`tests/variables/test_core.py`, L1 unit):** VarRef parse defaults +
explicit source/target + optional/required; LiteralBackend returns value bytes;
registry dispatch (literal now; vault registered in Slice 3) + unknown-kind error;
FetchResult shape.

## Slice 3 — VaultBackend (L1 done; **L2 grounded 2026-05-30**)

L1 (`tests/variables/test_vault.py`): `vault_path` derivation + outcome mapping
with an injected fake `VaultReader`. Done in commit `272bae5`.

**L2 — real `KubernetesVaultReader` grounded vs LIVE Vault** (`src/variables/vault_reader.py`,
`tests/variables/test_vault_l2.py`). Vault is ClusterIP-only on the viloforge
cluster, so the reader was exercised **inside the `vafi-executor` pod** (which
runs as the `vtaskforge-executor` SA) against `https://vault.vault.svc:8200`
(HA 3/3). All five outcomes verified by the real reader code, not a mock:

| Outcome | How grounded | Result |
|---|---|---|
| `success` | seeded `…/c3-l2-probe/executor/MY_TOKEN` | value bytes + `version=1` |
| `empty` | seeded blank `…/EMPTY_VAR` | `value=b""` |
| `not_found` | in-policy missing path | 404 |
| `permission_denied` | judge subtree (outside executor policy) | 403 |
| `unreachable` | bogus port, transport error | classified `unreachable` |

Grounded contract (do not re-derive from docs):
- KV v2 read **requires** the `data/` path infix; the raw convention path is 403,
  not 404. The reader strips the mount segment and re-prefixes `<mount>/data/`.
- Login `POST /v1/auth/kubernetes/login {role, jwt}` → `auth.client_token`
  (policies `[default, vtaskforge-executor]`, lease 3600s = the declared TTL).
- Value key convention is `value` (operator: `vault kv put …/NAME value=…`);
  blank or absent → `empty`.

**Implementation note — `httpx`, not `hvac`.** The raw HTTP login+read contract
was grounded directly, and `httpx` is already a vafi dependency used across
`src/`. Using it (vs adding `hvac`) keeps the reader to the exact grounded
contract, adds no new runtime dep, and avoids an image rebuild for Slice 3.

α token source: the pod runs **as** the `vtaskforge-<role>` SA, so the SA JWT is
the pod's own projected token. β swaps `_read_sa_jwt` for a `TokenRequest` mint —
the login + read contract is unchanged (the seam's purpose).

## Slice 4 — audit + snapshot emit (L1 done; **L3 grounded 2026-05-30**)

Two pure builders + an I/O emitter (`src/variables/audit.py`,
`src/variables/audit_emitter.py`):
- `build_audit_record` → one forensic `VariableAudit` per Vault read. The
  `AuditRecord` dataclass structurally has **no value field**; `to_body` emits
  exactly `AUDIT_BODY_FIELDS` — the writable subset of the vtaskforge
  `VariableAuditSerializer`. L1 asserts the value/hash/prefix never appears in
  the body under any key spelling, and that the key set is exactly the contract.
- `build_secrets_snapshot` → `{name: vault_version}` for every read that
  resolved to a real KV version (success or empty-but-present); literals and
  availability/value failures (no version) are excluded.
- `HttpAuditEmitter` → append-only POST to `/v1/variable-audits/` with the
  controller's `Token` auth; raises on non-2xx so the caller can log-and-continue
  (emission is best-effort forensics, never a spawn gate — wired in Slice 5).

**L3 — vafi emit ↔ live vtaskforge endpoint** (`tests/variables/test_audit_l3.py`,
env-gated). The real `HttpAuditEmitter` posted vafi-built bodies to a **live**
vtaskforge server (the `/v1/variable-audits/` receiver from vtaskforge PR #18) on
a migrated postgres: 201 accepted, the row round-trips on list with
result/version/scope/size, and **no value/hash/prefix crossed the boundary**
(server logged `POST … 201`). The vtaskforge side (`tests/variables/test_audit_api.py`,
7 passed) covers create/each-result/null-size/no-value-surface/auth/scoping.
Grounded contract: writable field set is exactly
`{timestamp, task, project, variable_name, variable_scope, vault_path,
vault_version, result, size_bytes, duration_ms, controller_id}`; `task`/`project`
are FK PKs (distinct from the slug used in the Vault path); `result`/`scope`
vocabularies match vafi's constants byte-for-byte.

Remaining for Slice 4 at controller-wiring time (Slice 5): supply `task`/`project`
PKs + `controller_id` from the task context and PATCH `Task.secrets_snapshot`.
**Blocker:** vtaskforge PR #18 must merge before the dev cluster has the endpoint.

## Slice 5 — controller injection + probe (**core done 2026-05-30**; wiring + L4 next)

**Done (L1, committed `b3f31a4`):**
- `VariableMaterializer` (`src/variables/materializer.py`) — the spawn-time
  orchestrator: parse `variables:` → `registry.fetch` → `PreSpawnValidator` →
  fail-loud refusal *or* an `InjectionPlan` (env + files + audit rows + snapshot +
  redactor). A task with **no** `variables:` block → empty `ok=True` plan (V16
  no-op). Keeps `project_slug` (Vault-path identity) distinct from `project_pk`
  (vtaskforge audit FK). 8 tests.
- `HarnessInvoker` injection (`src/controller/invoker.py`) — `invoke`/`_run_harness`
  take an optional `InjectionPlan`; `_apply_injection` writes file-bound secrets
  (relative→workdir, absolute as-given, 0600) and returns the subprocess env
  (`os.environ` overlaid). **No injection → `env=None` → inherit parent env,
  byte-identical to today (V16).** 6 tests; existing 56 invoker tests unchanged.

**Remaining (controller wiring + L4) — grounding gaps identified:**
1. Wire the materializer into `controller.execute` *before* `invoker.invoke`:
   on `ok=False` fail the task (no spawn); else pass the `InjectionPlan` to
   `invoke`, emit `audit_records` (best-effort via `HttpAuditEmitter`), PATCH
   `Task.secrets_snapshot`, and pipe stdout/stderr through `plan.redactor`.
2. **`project_slug` source** — `TaskInfo.project_id` is the project **PK**
   (`task.project.id`). Grounded vs live vtf-dev: the task's nested `project` is
   `{id, name}` only (no slug), but `GET /v2/projects/<id>` **does** return `slug`.
   So the controller must *fetch the project* for the slug.
   - **DONE:** `Project.slug` added to the SDK entity (commit `46f091f`),
     grounded + forward-compat-defaulted.
   - **TODO:** a worksource seam method `get_project_slug(project_id) -> str`
     (vtf impl = `(await client.projects.get(pid)).slug`) — resolve lazily in
     `execute` only when the task declares variables (keep it off the hot path).
3. **snapshot write path** — **NOT yet grounded.** Confirm the task-update
   endpoint/SDK accepts writing `secrets_snapshot` (a `Task` JSONField — but it
   must be *writable* in the TaskSerializer, not read-only). Likely a second
   worksource seam method `set_secrets_snapshot(task_id, snapshot)`. Verify the
   serializer field before wiring.
4. `AuditEmitter` wiring — vtf base URL + the controller's agent token from config.
5. `/admin/probe` (vafi) + `vtf project var probe` (vtaskforge CLI).
6. **L4** — needs the vafi feature image redeployed to vafi-dev + a test secret
   seeded under the convention path: e2e (declare→run→agent-has-secret;
   required-missing→no run) + scenario (black-box QA agent, external-effect goal).

vtaskforge PR #18 (the audit receiver) is **merged + deployed + verified live** on
`vtf-dev` (endpoint returns authenticated 200), so wiring #1/#4 can ground against
the running endpoint.

## Sequencing
1 → 2 → 3 → 4 → 5. Slices 1–2 are pure (mergeable immediately). 3 grounds the
real Vault I/O against live dev Vault; 4 grounds the audit emit against a live
vtaskforge endpoint (both use `httpx`, already a dep — see Slice 3 note); 5 adds
`kubernetes`. None changes behaviour for tasks without a `variables:` block.
