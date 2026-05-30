"""L1 unit tests for PreSpawnValidator — the fail-loud failure-mode matrix (C.3 Slice 2).

Design: viloforge-platform/docs/vtaskforge-variables-DESIGN.md §"Fail-loud taxonomy".
Invariants:
  - unreachable + permission_denied are ALWAYS fatal (availability/policy, not value-shape).
  - required not_found / empty → fatal. optional not_found → omitted; optional empty → injected "".
"""
import pytest

from variables.types import FetchResult, VarRef
from variables.validator import PreSpawnValidator


def _refs(*specs):
    return VarRef.list_from_spec(list(specs))


def _ok(value=b"x"):
    return FetchResult(value=value, version=1, result="success")


V = PreSpawnValidator()


class TestPreSpawnValidator:
    def test_all_success(self):
        refs = _refs({"name": "GH_TOKEN"})
        out = V.validate(refs, {"GH_TOKEN": _ok(b"ghp_xxx")}, {"GH_TOKEN": "p/GH_TOKEN"})
        assert out.ok is True
        assert "GH_TOKEN" in out.injectable

    def test_required_not_found_fatal(self):
        refs = _refs({"name": "GH_TOKEN"})
        out = V.validate(refs, {"GH_TOKEN": FetchResult(result="not_found")}, {"GH_TOKEN": "p/GH"})
        assert out.ok is False
        assert out.failure_reason == "missing_required_variables"
        assert out.failure_detail["missing"] == ["GH_TOKEN"]
        assert out.injectable == {}

    def test_optional_not_found_omitted(self):
        refs = _refs({"name": "SENTRY_DSN", "optional": True})
        out = V.validate(refs, {"SENTRY_DSN": FetchResult(result="not_found")}, {})
        assert out.ok is True
        assert "SENTRY_DSN" not in out.injectable

    def test_required_empty_fatal(self):
        refs = _refs({"name": "TOK"})
        out = V.validate(refs, {"TOK": FetchResult(value=b"", result="empty")}, {"TOK": "p/TOK"})
        assert out.ok is False
        assert out.failure_detail["empty"] == ["TOK"]

    def test_optional_empty_injected(self):
        refs = _refs({"name": "TOK", "optional": True})
        out = V.validate(refs, {"TOK": FetchResult(value=b"", result="empty")}, {})
        assert out.ok is True
        assert "TOK" in out.injectable

    def test_unreachable_always_fatal_even_optional(self):
        refs = _refs({"name": "X", "optional": True})
        out = V.validate(refs, {"X": FetchResult(result="unreachable")}, {"X": "p/X"})
        assert out.ok is False
        assert out.failure_reason == "vault_unreachable"
        assert out.failure_detail["vault_unreachable"] is True

    def test_permission_denied_always_fatal_even_optional(self):
        refs = _refs({"name": "X", "optional": True})
        out = V.validate(refs, {"X": FetchResult(result="permission_denied")}, {"X": "p/X"})
        assert out.ok is False
        assert out.failure_reason == "permission_denied"
        assert out.failure_detail["permission_denied"] == ["X"]

    def test_missing_result_treated_as_unreachable(self):
        refs = _refs({"name": "X"})
        out = V.validate(refs, {}, {"X": "p/X"})  # backend returned nothing for X
        assert out.ok is False
        assert out.failure_detail["vault_unreachable"] is True

    def test_mixed_reports_all_and_no_inject(self):
        refs = _refs({"name": "A"}, {"name": "B"}, {"name": "C", "optional": True})
        results = {
            "A": _ok(b"good_val"),
            "B": FetchResult(result="not_found"),
            "C": FetchResult(result="not_found"),
        }
        out = V.validate(refs, results, {"A": "p/A", "B": "p/B", "C": "p/C"})
        assert out.ok is False
        assert out.failure_detail["missing"] == ["B"]
        assert out.injectable == {}  # no pod → nothing injected
        assert out.failure_detail["vault_paths"]["B"] == "p/B"
