"""L1 tests for HttpSnapshotWriter — the secrets-snapshot persist adapter (C.3 #3).

Injects a fake async client to assert the exact request the (separately tested)
vtaskforge POST /v1/tasks/<id>/secrets-snapshot/ endpoint expects, and that a
non-2xx response raises (so VariablesStage.record can log-and-continue).
"""
import httpx
import pytest

from variables.snapshot_writer import HttpSnapshotWriter


class _FakeResp:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _FakeClient:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.calls = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResp(self.status_code)


async def test_posts_snapshot_to_task_endpoint():
    client = _FakeClient()
    writer = HttpSnapshotWriter("http://vtf:8000/", "tok123", http_client=client)
    await writer.write("tk-7", {"GH_TOKEN": 4, "SMOKE_TOKEN": 1})
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "http://vtf:8000/v1/tasks/tk-7/secrets-snapshot/"
    assert call["json"] == {"snapshot": {"GH_TOKEN": 4, "SMOKE_TOKEN": 1}}
    assert call["headers"] == {"authorization": "Token tok123"}


async def test_raises_on_non_2xx():
    writer = HttpSnapshotWriter("http://vtf:8000", "tok", http_client=_FakeClient(status_code=400))
    with pytest.raises(httpx.HTTPStatusError):
        await writer.write("tk-1", {"X": 1})
