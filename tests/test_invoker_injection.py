"""L1 tests for HarnessInvoker variable injection (C.3 Slice 5).

_apply_injection is the pure boundary between the materializer's InjectionPlan
and the harness subprocess: it writes file-bound secrets and returns the env to
hand create_subprocess_exec. The load-bearing invariant is V16: no injection ⇒
env=None ⇒ the spawn inherits the parent env, byte-identical to today.
"""
from pathlib import Path

import pytest

from controller.config import AgentConfig
from controller.invoker import HarnessInvoker
from variables.materializer import FileInjection, InjectionPlan


@pytest.fixture
def invoker():
    return HarnessInvoker(AgentConfig(
        agent_id="t", task_timeout=30, max_turns=10, sessions_dir="/tmp/x",
    ))


def test_no_injection_returns_none(invoker, tmp_path):
    assert invoker._apply_injection(tmp_path, None) is None


def test_empty_plan_returns_none(invoker, tmp_path):
    # ok plan with no env/files ⇒ still byte-identical (env=None).
    assert invoker._apply_injection(tmp_path, InjectionPlan(ok=True)) is None


def test_env_overlays_parent_environment(invoker, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")  # an inherited var
    plan = InjectionPlan(ok=True, env={"GH_TOKEN": "ghp_x", "NPM_TOKEN": "npm_y"})
    env = invoker._apply_injection(tmp_path, plan)
    assert env["GH_TOKEN"] == "ghp_x"
    assert env["NPM_TOKEN"] == "npm_y"
    assert env["PATH"] == "/usr/bin"  # parent env preserved


def test_relative_file_written_under_workdir_0600(invoker, tmp_path):
    plan = InjectionPlan(ok=True, files=[FileInjection(path="creds/sa.json", content=b'{"k":1}')])
    invoker._apply_injection(tmp_path, plan)
    f = tmp_path / "creds" / "sa.json"
    assert f.read_bytes() == b'{"k":1}'
    assert (f.stat().st_mode & 0o777) == 0o600


def test_absolute_file_written_as_given(invoker, tmp_path):
    abs_target = tmp_path / "home" / "agent" / "token"
    plan = InjectionPlan(ok=True, files=[FileInjection(path=str(abs_target), content=b"secret")])
    invoker._apply_injection(tmp_path, plan)
    assert abs_target.read_bytes() == b"secret"


def test_files_only_still_returns_none_env(invoker, tmp_path):
    # File bindings without env vars must not force a non-None env (V16).
    plan = InjectionPlan(ok=True, files=[FileInjection(path="f", content=b"x")])
    assert invoker._apply_injection(tmp_path, plan) is None
    assert (tmp_path / "f").read_bytes() == b"x"
