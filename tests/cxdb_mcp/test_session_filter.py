"""P2-1: cxdb session filtering by task and role labels.

The retrospective judge-evaluation loop (pass2-llm-flow-evaluation-DESIGN)
pulls *judge* sessions specifically. Sessions are labelled
``task:<id>`` and ``role:<role>`` (by the controller invoker); this
pure filter is what cxdb_list_sessions uses to select them.
"""
from cxdb_mcp.formatters import filter_sessions


def _ctx(cid, labels):
    return {"context_id": cid, "title": f"ctx{cid}", "labels": labels}


CONTEXTS = [
    _ctx(1, ["cxtx", "task:T1", "role:executor"]),
    _ctx(2, ["cxtx", "task:T1", "role:judge"]),
    _ctx(3, ["cxtx", "task:T2", "role:executor"]),
    _ctx(4, ["cxtx", "task:T2", "role:judge"]),
    _ctx(5, ["cxtx"]),  # unlabelled-by-task/role
]


def test_no_filters_returns_all():
    assert filter_sessions(CONTEXTS) == CONTEXTS


def test_filter_by_role_judge():
    got = [c["context_id"] for c in filter_sessions(CONTEXTS, role="judge")]
    assert got == [2, 4]


def test_filter_by_task():
    got = [c["context_id"] for c in filter_sessions(CONTEXTS, task_id="T1")]
    assert got == [1, 2]


def test_filter_by_task_and_role_is_conjunctive():
    """The loop's core query: the judge session for one task."""
    got = [c["context_id"] for c in filter_sessions(CONTEXTS, task_id="T2", role="judge")]
    assert got == [4]


def test_unmatched_role_returns_empty():
    assert filter_sessions(CONTEXTS, role="architect") == []


def test_context_missing_labels_key_is_excluded_when_filtering():
    contexts = CONTEXTS + [{"context_id": 6, "title": "no-labels"}]
    got = [c["context_id"] for c in filter_sessions(contexts, role="judge")]
    assert got == [2, 4]
