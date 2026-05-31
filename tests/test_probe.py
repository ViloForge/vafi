"""L1 tests for the onboarding probe logic (C.3 Slice 5 #5).

Fake http client + fake materializer — asserts caller authz, executor-var
resolution via the real fetch path's report shape, judge-var handling (β:
not_probed), PASS/FAIL semantics, and that NO value ever appears (size only).
"""
from types import SimpleNamespace

import pytest

from controller.probe import ProbeError, run_probe


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected {self.status_code}")


class _Http:
    """Maps URL suffix -> queued _Resp."""
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers))
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return _Resp(404, {})


class _Plan:
    def __init__(self, ok, audit_records):
        self.ok = ok
        self.audit_records = audit_records


class _Materializer:
    def __init__(self, plan):
        self._plan = plan
        self.calls = []

    def materialize(self, **kwargs):
        self.calls.append(kwargs)
        return self._plan


def _ar(name, result, size):
    return SimpleNamespace(variable_name=name, result=result, size_bytes=size)


PROJ = {"id": "p1", "slug": "abad", "name": "Abad"}


def _routes(variables):
    return {
        "/v2/projects/p1/": _Resp(200, PROJ),
        "/v2/projects/p1/variables/": _Resp(200, {"results": variables}),
    }


def test_happy_path_pass_reports_size_not_value():
    http = _Http(_routes([{"name": "GH_TOKEN", "role": "executor", "required": True}]))
    plan = _Plan(ok=True, audit_records=[_ar("GH_TOKEN", "success", 47)])
    rep = run_probe("p1", "tok", vtf_url="http://vtf:8000", materializer=_Materializer(plan),
                    controller_env="dev", controller_id="ctl", now=lambda: "T", http=http)
    assert rep["result"] == "PASS"
    assert rep["slug"] == "abad"
    v = rep["variables"][0]
    assert v == {"name": "GH_TOKEN", "role": "executor", "result": "success",
                 "size_bytes": 47, "required": True}
    # no value field anywhere
    assert "value" not in v and "ghp" not in str(rep)


def test_required_failure_is_fail():
    http = _Http(_routes([{"name": "GH_TOKEN", "role": "executor", "required": True}]))
    plan = _Plan(ok=False, audit_records=[_ar("GH_TOKEN", "not_found", None)])
    rep = run_probe("p1", "tok", vtf_url="http://vtf:8000", materializer=_Materializer(plan),
                    controller_env="dev", controller_id="ctl", now=lambda: "T", http=http)
    assert rep["result"] == "FAIL"


def test_unauthorized_caller_raises_403():
    http = _Http({"/v2/projects/p1/": _Resp(403, {})})
    with pytest.raises(ProbeError) as e:
        run_probe("p1", "bad", vtf_url="http://vtf:8000", materializer=_Materializer(_Plan(True, [])),
                  controller_env="dev", controller_id="ctl", now=lambda: "T", http=http)
    assert e.value.status == 403


def test_missing_project_raises_404():
    http = _Http({"/v2/projects/p1/": _Resp(404, {})})
    with pytest.raises(ProbeError) as e:
        run_probe("p1", "tok", vtf_url="http://vtf:8000", materializer=_Materializer(_Plan(True, [])),
                  controller_env="dev", controller_id="ctl", now=lambda: "T", http=http)
    assert e.value.status == 404


def test_judge_vars_reported_not_probed_in_alpha():
    http = _Http(_routes([
        {"name": "GH_TOKEN", "role": "executor", "required": True},
        {"name": "JUDGE_KEY", "role": "judge", "required": True},
    ]))
    plan = _Plan(ok=True, audit_records=[_ar("GH_TOKEN", "success", 47)])
    mat = _Materializer(plan)
    rep = run_probe("p1", "tok", vtf_url="http://vtf:8000", materializer=mat,
                    controller_env="dev", controller_id="ctl", now=lambda: "T", http=http)
    # executor var materialized; judge var only reported, never materialized
    assert all(c["role"] == "executor" for c in mat.calls)
    judge = [v for v in rep["variables"] if v["role"] == "judge"][0]
    assert judge["result"] == "not_probed"
