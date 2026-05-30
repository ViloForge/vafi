"""L2 grounding test for KubernetesVaultReader against a LIVE Vault.

Per external-contract-grounding, the real reader's behaviour (k8s-auth login +
KV v2 read + outcome classification) is asserted against a real Vault, not a
mock. Vault is ClusterIP-only on the viloforge cluster, so this test only runs
**inside** a pod that is bound to the ``vtaskforge-executor`` ServiceAccount
(i.e. the ``vafi-executor`` pod). It is env-gated and skipped everywhere else,
exactly like vtaskforge's live e2e tests.

Run (from a vafi-executor pod, with src on PYTHONPATH)::

    VAFI_L2_VAULT_ADDR=https://vault.vault.svc:8200 \
    VAFI_L2_VAULT_INSECURE=1 \
        python -m pytest tests/variables/test_vault_l2.py -q

Preconditions (seed once via an operator with the platform-operator policy)::

    vault kv put secret/apps/vtaskforge/dev/projects/c3-l2-probe/executor/MY_TOKEN  value=s3cr3t-c3l2-PROBE
    vault kv put secret/apps/vtaskforge/dev/projects/c3-l2-probe/executor/EMPTY_VAR value=

First grounded 2026-05-30 — all five outcomes verified against live Vault HA 3/3.
"""
from __future__ import annotations

import os

import pytest

from variables.types import (
    RESULT_EMPTY,
    RESULT_NOT_FOUND,
    RESULT_PERMISSION_DENIED,
    RESULT_SUCCESS,
    RESULT_UNREACHABLE,
)

ADDR = os.environ.get("VAFI_L2_VAULT_ADDR")

pytestmark = pytest.mark.skipif(
    not ADDR,
    reason="L2: set VAFI_L2_VAULT_ADDR and run inside a vtaskforge-executor pod",
)

_BASE = "secret/apps/vtaskforge/dev/projects/c3-l2-probe"
_EXEC = f"{_BASE}/executor"
_JUDGE = f"{_BASE}/judge"  # outside the executor policy → 403


@pytest.fixture()
def reader():
    from variables.vault_reader import KubernetesVaultReader

    insecure = os.environ.get("VAFI_L2_VAULT_INSECURE") == "1"
    r = KubernetesVaultReader(ADDR, verify=not insecure)
    yield r
    r.close()


def test_success_returns_value_and_version(reader):
    o = reader.read("executor", f"{_EXEC}/MY_TOKEN")
    assert o.result == RESULT_SUCCESS
    assert o.value == b"s3cr3t-c3l2-PROBE"
    assert o.version == 1


def test_blank_value_classifies_empty(reader):
    o = reader.read("executor", f"{_EXEC}/EMPTY_VAR")
    assert o.result == RESULT_EMPTY
    assert o.value == b""


def test_missing_in_policy_path_is_not_found(reader):
    o = reader.read("executor", f"{_EXEC}/NO_SUCH_VAR")
    assert o.result == RESULT_NOT_FOUND


def test_out_of_policy_path_is_permission_denied(reader):
    # The executor SA's policy grants only the executor/ subtree; the judge/
    # subtree returns 403 — a real isolation boundary, not a missing secret.
    o = reader.read("executor", f"{_JUDGE}/MY_TOKEN")
    assert o.result == RESULT_PERMISSION_DENIED


def test_unreachable_vault_classifies_unreachable():
    from variables.vault_reader import KubernetesVaultReader

    r = KubernetesVaultReader(
        "https://vault.vault.svc:18200",
        verify=False,
        timeout=3.0,
    )
    o = r.read("executor", f"{_EXEC}/MY_TOKEN")
    assert o.result == RESULT_UNREACHABLE
    r.close()
