"""Reject oversized/ambiguous inputs before parsing or invoking a reader."""

import json

import pytest
from jsonschema import Draft202012Validator

from spring_runtime_mcp.contracts.definitions import (
    TOOL,
    definition,
    glob_matches,
    list_tools,
    validate_arguments,
)
from spring_runtime_mcp.models.common import NormalizationError
from spring_runtime_mcp.transport.bounded_json import loads_bounded, validate_tree


@pytest.mark.parametrize("payload", [b'{"x":1,"x":2}', b'{"x":{"y":1,"y":2}}'])
def test_duplicate_json_keys_are_rejected(payload):
    with pytest.raises(NormalizationError, match="DUPLICATE_JSON_KEY"):
        loads_bounded(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b'{"x":' + b"9" * 5000 + b"}",
        b'{"x":"\\ud800"}',
    ],
)
def test_nonfinite_huge_numbers_and_surrogates_are_safe_errors(payload):
    with pytest.raises(NormalizationError):
        loads_bounded(payload)


def test_scanner_understands_brackets_inside_strings_and_escapes():
    payload = {"value": "[{" * 1000 + '"\\'}
    assert loads_bounded(json.dumps(payload).encode(), max_depth=3) == payload
    with pytest.raises(NormalizationError, match="NORMALIZATION_LIMIT"):
        loads_bounded(b'{"v":' + b"[" * 65 + b"0" + b"]" * 65 + b"}")


def test_preparse_byte_and_node_limits():
    with pytest.raises(NormalizationError, match="RESPONSE_TOO_LARGE"):
        loads_bounded(b'{"x":"large"}', max_bytes=5)
    with pytest.raises(NormalizationError, match="NORMALIZATION_LIMIT"):
        loads_bounded(b'{"x":[1,2,3]}', max_nodes=4)
    for value in (["not an object"], None):
        with pytest.raises(NormalizationError, match="UNSUPPORTED_SHAPE"):
            loads_bounded(json.dumps(value).encode())


def test_tree_counts_large_integers_and_escape_expansion():
    with pytest.raises(NormalizationError, match="RESPONSE_TOO_LARGE"):
        validate_tree({"value": 10**100}, max_bytes=50)
    with pytest.raises(NormalizationError, match="INVALID_JSON_NUMBER"):
        validate_tree({"value": 10**5000})
    with pytest.raises(NormalizationError, match="RESPONSE_TOO_LARGE"):
        validate_tree({"value": "\x00" * 100}, max_bytes=200)


@pytest.mark.parametrize(
    "arguments",
    [
        {"beanNamePattern": "a", "maxResults": True},
        {"beanNamePattern": "a", "maxResults": 51},
        {"beanNamePattern": "a", "maxDepth": -1},
        {"beanNamePattern": "a", "conditionDepth": 17},
        {"beanNamePattern": "a", "targetId": "localhost\n"},
        {"beanNamePattern": "a", "targetId": "https://example.test"},
        {"beanNamePattern": "a", "headers": {"Authorization": "x"}},
        {"beanNamePattern": "a", "url": "http://example.test"},
        {"beanNamePattern": "[a-z]+"},
        {"beanNamePattern": "a", "contextId": "\ud800"},
        {"beanNamePattern": "a", "maxResults": 10**5000},
        {"beanNamePattern": "a", "maxResults": float("inf")},
        {},
    ],
)
def test_invalid_arguments_fail_closed(arguments):
    with pytest.raises(NormalizationError, match="INVALID_ARGUMENT"):
        validate_arguments(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {"beanNamePattern": "order*"},
        {
            "typePattern": "example.?Service",
            "maxResults": 1,
            "maxDepth": 16,
            "conditionDepth": 0,
        },
        {
            "packagePrefix": "example",
            "contextId": "application:1",
            "direction": "dependencies",
        },
    ],
)
def test_runtime_input_validation_agrees_with_public_schema(arguments):
    Draft202012Validator(definition(TOOL)["inputSchema"]).validate(arguments)
    parsed = validate_arguments(arguments)
    assert parsed.target_id == "local"
    assert parsed.max_results == arguments.get("maxResults", 50)


def test_stable_defensive_registration_and_literal_glob_grammar():
    original = list_tools()
    assert len(original) == 8
    assert [row["name"] for row in original] == sorted(row["name"] for row in original)
    original[0]["name"] = "modified"
    assert list_tools()[0]["name"] != "modified"
    assert glob_matches("example.*Service", "example.OrderService")
    assert glob_matches("example.?ervice", "example.Service")
    assert not glob_matches("example.[OS]ervice", "example.Service")
