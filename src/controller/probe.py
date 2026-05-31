"""Onboarding probe — operator pre-flight that resolves a project's declared
variables and reports per-variable status (C.3 Slice 5 #5).

Reuses the **exact** `VariableMaterializer.fetch()` path the controller runs at
spawn time (never a parallel implementation): list a project's `ProjectVariable`
declarations from vtaskforge, resolve each through the registry's `VaultBackend`
(authenticating as the executor SA), and report `result` + byte length per
variable — **never the value**.

Caller auth is the caller's own vtaskforge token: we validate it against the vtf
API (which both proves the caller may see the project and yields the slug used
for the Vault path). The Vault read itself uses the executor SA (the pod's
identity), not the caller — so an operator never needs cluster credentials.

α scope: only **executor-role** variables are truly probed (the executor SA's
policy boundary). Judge-role variables are reported as `not_probed` — probing the
judge SA separately is a β capability (per-role auth), per the design.
"""
from __future__ import annotations

EXECUTOR_ROLE = "executor"


class ProbeError(Exception):
    """Maps to an HTTP status for the probe endpoint."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def run_probe(
    project_id: str,
    caller_token: str,
    *,
    vtf_url: str,
    materializer,
    controller_env: str,
    controller_id: str,
    now,
    http,
) -> dict:
    """Resolve `project_id`'s declared variables; return a no-values report.

    `http` is a sync httpx-style client (injected for tests). `materializer` is a
    `VariableMaterializer` whose registry holds the executor `VaultBackend`.
    """
    base = vtf_url.rstrip("/")
    headers = {"authorization": f"Token {caller_token}"}

    # 1. Authorize the caller for this project + get the slug (Vault-path identity).
    pr = http.get(f"{base}/v2/projects/{project_id}/", headers=headers)
    if pr.status_code in (401, 403):
        raise ProbeError(403, "not authorized for this project")
    if pr.status_code == 404:
        raise ProbeError(404, f"project {project_id} not found")
    pr.raise_for_status()
    project = pr.json()
    slug = project.get("slug") or project_id

    # 2. List the project's declared variables (caller token; paginated API).
    vr = http.get(f"{base}/v2/projects/{project_id}/variables/", headers=headers)
    vr.raise_for_status()
    body = vr.json()
    rows = body.get("results", body) if isinstance(body, dict) else body

    executor_spec = [
        {"name": v["name"], "required": bool(v.get("required", True))}
        for v in rows
        if v.get("role", EXECUTOR_ROLE) == EXECUTOR_ROLE
    ]
    judge_rows = [v for v in rows if v.get("role") and v["role"] != EXECUTOR_ROLE]

    # 3. Resolve executor-role variables via the SAME fetch path as spawn.
    report_vars: list[dict] = []
    overall_ok = True
    if executor_spec:
        plan = materializer.materialize(
            variables_spec=executor_spec,
            project_slug=slug,
            project_pk=project_id,
            role=EXECUTOR_ROLE,
            controller_env=controller_env,
            controller_id=controller_id,
            timestamp=now(),
        )
        required_by_name = {s["name"]: s["required"] for s in executor_spec}
        for ar in plan.audit_records:
            report_vars.append(
                {
                    "name": ar.variable_name,
                    "role": EXECUTOR_ROLE,
                    "result": ar.result,
                    "size_bytes": ar.size_bytes,  # byte length on success; never the value
                    "required": required_by_name.get(ar.variable_name, True),
                }
            )
        overall_ok = plan.ok  # validator fails only on required-variable failures

    # Judge-role: not probed in α (needs the judge SA — β).
    for v in judge_rows:
        report_vars.append(
            {
                "name": v["name"],
                "role": v["role"],
                "result": "not_probed",
                "size_bytes": None,
                "required": bool(v.get("required", True)),
            }
        )

    return {
        "project": project_id,
        "slug": slug,
        "environment": controller_env,
        "variables": report_vars,
        "result": "PASS" if overall_ok else "FAIL",
    }
