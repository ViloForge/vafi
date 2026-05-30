"""L3 cross-boundary contract test: vafi audit emit ↔ live vtaskforge endpoint.

Drives the REAL HttpAuditEmitter against a LIVE vtaskforge server (the
/v1/variable-audits/ receiver from vtaskforge PR #18), asserting that the body
vafi emits is accepted (201) and round-trips on list — and that NO value/hash/
prefix of the secret ever crosses the boundary. Per external-contract-grounding,
the vtaskforge contract is verified against the running server, not a mock.

Env-gated (skipped in normal CI). Run against a migrated vtaskforge + a seeded
user token / project / task::

    VAFI_L3_VTF_URL=http://127.0.0.1:18080 \
    VAFI_L3_VTF_TOKEN=<token> \
    VAFI_L3_PROJECT_ID=<project_pk> \
    VAFI_L3_TASK_ID=<task_pk> \
        python -m pytest tests/variables/test_audit_l3.py -q

First grounded 2026-05-30 against vtaskforge feat/variable-audit-endpoint on local postgres.
"""
from __future__ import annotations

import os

import httpx
import pytest

from variables.audit import build_audit_record
from variables.audit_emitter import HttpAuditEmitter
from variables.types import FetchResult, VarRef

URL = os.environ.get("VAFI_L3_VTF_URL")
TOKEN = os.environ.get("VAFI_L3_VTF_TOKEN")
PROJECT_ID = os.environ.get("VAFI_L3_PROJECT_ID")
TASK_ID = os.environ.get("VAFI_L3_TASK_ID")

pytestmark = pytest.mark.skipif(
    not (URL and TOKEN and PROJECT_ID and TASK_ID),
    reason="L3: set VAFI_L3_VTF_URL/TOKEN/PROJECT_ID/TASK_ID against a live vtaskforge",
)

_SECRET = b"ghp_L3_TOPSECRET_value"


def _ref(name):
    return VarRef.list_from_spec([{"name": name}])[0]


def _success(name, version):
    return FetchResult(
        value=_SECRET,
        version=version,
        result="success",
        audit_metadata={
            "kind": "vault",
            "vault_path": f"secret/apps/vtaskforge/dev/projects/l3/executor/{name}",
            "scope": "project",
        },
    )


async def test_emitted_record_is_accepted_and_round_trips_without_value():
    name = "L3_GH_TOKEN"
    record = build_audit_record(
        ref=_ref(name),
        fetch_result=_success(name, version=9),
        task_id=TASK_ID,
        project_id=PROJECT_ID,
        controller_id="vafi-l3-test",
        timestamp="2026-05-30T12:34:56Z",
        duration_ms=17,
    )
    emitter = HttpAuditEmitter(URL, TOKEN)
    # 201 (no raise) proves the real serializer accepts vafi's exact field set.
    await emitter.emit(record)

    # Round-trip: the row is listed, carries the metadata, and leaks no value.
    async with httpx.AsyncClient() as c:
        resp = await c.get(
            f"{URL.rstrip('/')}/v1/variable-audits/",
            headers={"authorization": f"Token {TOKEN}"},
            timeout=10.0,
        )
    resp.raise_for_status()
    payload = resp.json()
    rows = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
    mine = [r for r in rows if r.get("variable_name") == name]
    assert mine, f"emitted audit row not found in list: {rows}"
    row = mine[0]
    assert row["result"] == "success"
    assert row["vault_version"] == 9
    assert row["variable_scope"] == "project"
    assert int(row["size_bytes"]) == len(_SECRET)
    # No value/hash/prefix crossed the boundary.
    assert "TOPSECRET" not in str(row)
    for forbidden in ("value", "hash", "prefix"):
        assert not any(forbidden in k.lower() for k in row), f"leaky key: {list(row)}"


async def test_required_failure_result_is_accepted_with_null_size():
    name = "L3_MISSING"
    record = build_audit_record(
        ref=_ref(name),
        fetch_result=FetchResult(
            result="not_found",
            audit_metadata={"vault_path": f"secret/apps/vtaskforge/dev/projects/l3/executor/{name}"},
        ),
        task_id=TASK_ID,
        project_id=PROJECT_ID,
        controller_id="vafi-l3-test",
        timestamp="2026-05-30T12:35:00Z",
        duration_ms=3,
    )
    await HttpAuditEmitter(URL, TOKEN).emit(record)  # 201, null size/version accepted
