import base64
import json
from urllib.parse import quote

import pytest

from spring_runtime_mcp.models.common import NormalizationError
from spring_runtime_mcp.models.queries import InspectBeansQuery
from spring_runtime_mcp.policy.sanitization import MARKER, SanitizerPipeline
from spring_runtime_mcp.replay import services_for
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.transport.response_gate import EndpointReadError
from spring_runtime_mcp.transport.responses import serialize_response

from .helpers import SECRET, run_handler

PAYLOADS = [
    f"jdbc:postgresql://user:{SECRET}@db/x",
    f"redis://user:{SECRET}@db/0",
    f"mongodb+srv://user:{SECRET}@db/x",
    f"amqp://user:{SECRET}@db/vhost",
    f"Bearer {SECRET}",
    f"Basic {base64.b64encode(('user:' + SECRET).encode()).decode()}",
    f"-----BEGIN PRIVATE KEY-----\n{SECRET}\n-----END PRIVATE KEY-----",
]


@pytest.mark.parametrize("value", PAYLOADS)
@pytest.mark.parametrize(
    "field", ["value", "resource", "origin", "bindingValue", "effectiveValue"]
)
def test_leakage_nested_and_secondary_serialization_is_sanitized(value, field):
    pipeline = SanitizerPipeline((SECRET,))
    value = {
        "data": {"items": [{field: value, "inputs": {"value": value, "origin": value}}]}
    }
    clean = pipeline.sanitize(value)
    assert SECRET not in json.dumps(clean)
    assert pipeline.sanitize(clean) == clean
    embedded = pipeline.sanitize({"value": json.dumps(value)})
    assert SECRET not in json.dumps(embedded)


@pytest.mark.parametrize(
    "key", ["password", "clientSecret", "ｔｏｋｅｎ", "access-key", "private.key"]
)
def test_leakage_descriptor_taint_reaches_inputs_and_origin(key):
    clean = SanitizerPipeline().sanitize(
        {
            "propertyKey": key,
            "inputs": {"value": SECRET},
            "origin": SECRET,
            "value": SECRET,
        }
    )
    assert SECRET not in json.dumps(clean)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_leakage_serializer_is_a_mandatory_guard(payload):
    with pytest.raises(NormalizationError):
        serialize_response({"status": "ok", "data": {"value": payload}})


@pytest.mark.parametrize("payload", PAYLOADS)
def test_leakage_cache_is_a_mandatory_guard(payload):
    store = SnapshotStore()
    with pytest.raises(NormalizationError):
        store.put({"data": {"items": [{"resource": payload}]}}, InspectBeansQuery())
    assert store._size == 0


def test_leakage_endpoint_error_metadata_is_not_echoed():
    services = services_for("parent-child")

    class Reader:
        async def read(self, *args, **kwargs):
            raise EndpointReadError("ENDPOINT_UNAUTHORIZED", f"Bearer {SECRET}", 401)

    services.reader = Reader()
    result = run_handler({"beanNamePattern": "*"}, services)
    assert SECRET not in json.dumps(result)


def test_leakage_unknown_exception_is_structured_and_quiet(capsys):
    services = services_for("parent-child")

    class Reader:
        async def read(self, *args, **kwargs):
            raise RuntimeError(f"upstream failed with password={SECRET}")

    services.reader = Reader()
    result = run_handler({"beanNamePattern": "*"}, services)
    assert result["status"] in ("unavailable", "invalid")
    captured = capsys.readouterr()
    assert SECRET not in json.dumps(result) + captured.out + captured.err


@pytest.mark.parametrize(
    "value",
    [
        quote(quote(quote(SECRET, safe=""), safe=""), safe=""),
        base64.urlsafe_b64encode(SECRET.encode()).decode(),
    ],
)
def test_leakage_known_credentials_remain_hidden_across_transforms(value):
    assert SanitizerPipeline((SECRET,)).sanitize({"value": value})["value"] == MARKER


@pytest.mark.parametrize(
    "value",
    [
        "Bearer\u00a0synthetic-redteam-credential-29",
        "Bea\u200brer synthetic-redteam-credential-29",
        "-----begin private key-----fake",
        "file:///home/user/private.conf",
    ],
)
def test_leakage_obfuscated_credential_patterns_are_detected(value):
    assert SanitizerPipeline().sanitize({"value": value})["value"] == MARKER


def test_leakage_deep_percent_unicode_and_base64_wrapping():
    secret = "opaque+value/with=delimiters?29"
    pipeline = SanitizerPipeline((secret,))
    encoded = secret
    for _ in range(6):
        encoded = quote(encoded, safe="")
    for value in (
        encoded,
        "".join(f"\\u{ord(c):04x}" for c in secret),
        base64.b64encode(("Bearer " + secret).encode()).decode(),
    ):
        assert pipeline.sanitize({"value": value})["value"] == MARKER


def test_leakage_redacted_alias_cannot_be_recovered_through_filter_counts():
    secret = "OpaqueAlphanumericCredential29"
    services = services_for("parent-child")
    services.sanitizer = SanitizerPipeline((secret,))
    raw = services.reader.bodies["beans"]["contexts"]["application"]["beans"]
    raw["orderService"]["aliases"] = [secret]
    result = run_handler({"beanNamePattern": "OpaqueAlphanumeric*"}, services)
    assert not result["data"]["items"]
    assert result["totalMatched"] is None
    assert result["coverage"]["state"] == "partial"


def test_leakage_source_catalog_preflights_invalid_trees():
    from spring_runtime_mcp.adapters.source_catalog import load_source_catalog
    from spring_runtime_mcp.models.common import AnalysisBudget

    payload = {"targetId": "local", "types": [], "producers": []}
    payload["sourceRevision"] = payload
    with pytest.raises(NormalizationError):
        load_source_catalog(payload, target_id="local", budget=AnalysisBudget())


def test_leakage_sinks_use_registered_opaque_credentials():
    opaque = "Qz7Jm9Xw4Vb2Lp6R"
    pipeline = SanitizerPipeline((opaque,))
    with pytest.raises(NormalizationError):
        serialize_response(
            {"status": "ok", "data": {"name": opaque}}, sanitizer=pipeline
        )
    store = SnapshotStore()
    with pytest.raises(NormalizationError):
        store.put(
            {"data": {"items": [{"name": opaque}]}},
            InspectBeansQuery(),
            sanitizer=pipeline,
        )


def test_leakage_cache_policy_change_evicts_newly_recognized_credentials():
    opaque = "Vz4Lk7Gw1Zp8Jq2N"
    store = SnapshotStore()
    query = InspectBeansQuery()
    token = store.put({"data": {"items": [{"name": opaque}]}}, query)
    with pytest.raises(NormalizationError):
        store.get(token, query, sanitizer=SanitizerPipeline((opaque,)))
    assert store._size == 0
    with pytest.raises(NormalizationError, match="CURSOR_EXPIRED"):
        store.get(token, query)
