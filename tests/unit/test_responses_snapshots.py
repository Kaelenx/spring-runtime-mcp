"""Actual frame bytes and immutable, expiring opaque cursor regressions."""

import json
from dataclasses import replace

import pytest

from spring_runtime_mcp.models.common import NormalizationError
from spring_runtime_mcp.models.queries import InspectBeansQuery
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.transport.responses import serialize_response


def test_actual_frame_duplicate_and_error_status():
    for status, error in (("ok", False), ("partial", False), ("denied", True)):
        envelope = {"status": status, "data": {"name": "中文😀"}}
        frame = json.loads(serialize_response(envelope, "\0" * 128))
        assert frame["result"]["structuredContent"] == envelope
        assert json.loads(frame["result"]["content"][0]["text"]) == envelope
        assert frame["result"]["resultType"] == "complete"
        assert frame["result"]["isError"] is error
        assert serialize_response(envelope, 1) == serialize_response(
            dict(reversed(list(envelope.items()))), 1
        )


@pytest.mark.parametrize("request_id", [True, 2**53, -(2**53), "😀" * 33])
def test_request_id_rejected(request_id):
    with pytest.raises(NormalizationError, match="INVALID_REQUEST_ID"):
        serialize_response({"status": "ok"}, request_id)


def test_complete_frame_budget_and_invalid_json():
    with pytest.raises(NormalizationError, match="RESPONSE_TOO_LARGE"):
        serialize_response({"status": "ok", "data": "😀" * 2500})
    with pytest.raises(NormalizationError, match="INVALID_RESPONSE"):
        serialize_response({"status": "ok", "data": float("nan")})


def test_cursor_binding_immutable_copy_and_expiration():
    now = [0.0]
    store = SnapshotStore(clock=lambda: now[0])
    query = InspectBeansQuery(bean_name_pattern="bean")
    source = {"data": {"items": ["one"]}}
    token = store.put(source, query)
    source["data"]["items"].append("mutation")
    assert "bean" not in token
    resumed = store.get(token, replace(query, cursor=token))
    assert resumed == {"data": {"items": ["one"]}}
    resumed["data"]["items"].clear()
    assert store.get(token, query)["data"]["items"] == ["one"]
    with pytest.raises(NormalizationError, match="CURSOR_QUERY_MISMATCH"):
        store.get(token, replace(query, max_results=1))
    now[0] = 60.0
    with pytest.raises(NormalizationError, match="CURSOR_EXPIRED"):
        store.get(token, query)


def test_cache_lru_and_absolute_ttl():
    now = [0.0]
    store = SnapshotStore(clock=lambda: now[0], max_bytes=24)
    query = InspectBeansQuery()
    first = store.put({"x": "one"}, query)
    second = store.put({"x": "two"}, query)
    store.get(first, query)
    third = store.put({"x": "tri"}, query)
    with pytest.raises(NormalizationError, match="CURSOR_EXPIRED"):
        store.get(second, query)
    assert store.get(first, query) == {"x": "one"}
    now[0] = 59
    store.get(first, query)
    now[0] = 60
    with pytest.raises(NormalizationError, match="CURSOR_EXPIRED"):
        store.get(first, query)
    with pytest.raises(NormalizationError, match="CURSOR_EXPIRED"):
        store.get(third, query)
    with pytest.raises(NormalizationError, match="SNAPSHOT_TOO_LARGE"):
        store.put({"x": "a" * 100}, query)
