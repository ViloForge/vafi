"""Redactor — masks known secret values in stdout/stderr streams.

Design rules (vtaskforge-variables-DESIGN.md §"Log redaction"):
  - Mask `kind: vault` values only (literals are author-declared inline).
  - Mask values of length >= 8 (shorter → too many false positives).
  - Single-pass, in-order string replacement (longest value first so a short
    secret can't partially clobber a longer one); replace with [MASKED:<name>].
Defense in depth, NOT a hard boundary — a malicious task can trivially bypass.
"""
from __future__ import annotations

from .types import RESULT_SUCCESS, FetchResult, VarName, VarRef

MIN_MASK_LEN = 8


class Redactor:
    def __init__(self, subs: list[tuple[str, str]]):
        # subs: (value, mask) sorted longest-value first.
        self._subs = sorted(subs, key=lambda s: len(s[0]), reverse=True)

    @classmethod
    def from_results(
        cls, refs: list[VarRef], results: dict[VarName, FetchResult]
    ) -> "Redactor":
        by_name = {r.name: r for r in refs}
        subs: list[tuple[str, str]] = []
        for name, res in results.items():
            ref = by_name.get(name)
            if ref is None or ref.kind != "vault":
                continue  # only vault-sourced values are masked
            if res.result != RESULT_SUCCESS or not res.value:
                continue
            if len(res.value) < MIN_MASK_LEN:
                continue
            subs.append((res.value.decode("utf-8", "replace"), f"[MASKED:{name}]"))
        return cls(subs)

    def redact(self, text: str) -> str:
        for value, mask in self._subs:
            text = text.replace(value, mask)
        return text
