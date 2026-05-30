"""L1 unit tests for the stream Redactor (C.3 Slice 2).

Design rules: mask vault-sourced values only, length >= 8, single-pass in-order
(longest first), replace with [MASKED:<name>]. Defense in depth, not a hard boundary.
"""
from variables.redactor import Redactor
from variables.types import FetchResult, VarRef


class TestRedactor:
    def test_masks_vault_value_over_8(self):
        refs = VarRef.list_from_spec([{"name": "GH_TOKEN"}])
        results = {"GH_TOKEN": FetchResult(value=b"ghp_supersecret", result="success")}
        red = Redactor.from_results(refs, results)
        assert red.redact("token is ghp_supersecret here") == "token is [MASKED:GH_TOKEN] here"

    def test_does_not_mask_short_value(self):
        refs = VarRef.list_from_spec([{"name": "X"}])
        results = {"X": FetchResult(value=b"abc", result="success")}  # len 3 < 8
        red = Redactor.from_results(refs, results)
        assert red.redact("value abc stays") == "value abc stays"

    def test_does_not_mask_literal_source(self):
        refs = VarRef.list_from_spec([{"name": "LOG", "source": {"kind": "literal", "value": "infolevel"}}])
        results = {"LOG": FetchResult(value=b"infolevel", result="success")}
        red = Redactor.from_results(refs, results)
        assert red.redact("level infolevel shown") == "level infolevel shown"  # literal not masked

    def test_longest_first(self):
        refs = VarRef.list_from_spec([{"name": "SHORT"}, {"name": "LONG"}])
        results = {
            "SHORT": FetchResult(value=b"secretval", result="success"),
            "LONG": FetchResult(value=b"secretvalue_long", result="success"),
        }
        red = Redactor.from_results(refs, results)
        out = red.redact("x secretvalue_long y")
        assert out == "x [MASKED:LONG] y"  # long masked whole, not partially by SHORT

    def test_only_successful_values_masked(self):
        refs = VarRef.list_from_spec([{"name": "GONE"}])
        results = {"GONE": FetchResult(result="not_found")}  # no value
        red = Redactor.from_results(refs, results)
        assert red.redact("nothing to mask") == "nothing to mask"

    def test_multiple_occurrences(self):
        refs = VarRef.list_from_spec([{"name": "T"}])
        results = {"T": FetchResult(value=b"repeatedsecret", result="success")}
        red = Redactor.from_results(refs, results)
        assert red.redact("repeatedsecret and repeatedsecret") == "[MASKED:T] and [MASKED:T]"
