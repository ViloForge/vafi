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

## Sequencing
1 → 2 → 3 → 4 → 5. Slices 1–2 are pure (mergeable immediately). 3 grounds the
real Vault I/O against live dev Vault (uses `httpx`, already a dep — see Slice 3
note); 5 adds `kubernetes`. None changes behaviour for tasks without a
`variables:` block.
