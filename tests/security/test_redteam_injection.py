import json

import pytest

from spring_runtime_mcp.models.common import NormalizationError
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.replay import services_for
from spring_runtime_mcp.transport.responses import serialize_response

from .helpers import INSTRUCTION, run_handler


@pytest.mark.parametrize(
    "field",
    [
        "description",
        "comments",
        "details",
        "stackTrace",
        "message",
        "instructions",
        "role",
        "system",
        "nextAction",
    ],
)
def test_injection_raw_runtime_text_cannot_survive_projection(field):
    result = SanitizerPipeline().sanitize({field: INSTRUCTION})
    assert INSTRUCTION not in json.dumps(result)


@pytest.mark.parametrize("field", ["name", "aliases", "resource", "type", "scope"])
def test_injection_indirect_bean_labels_are_bounded_data(field):
    services = services_for("parent-child")
    beans = services.reader.bodies["beans"]["contexts"]["application"]["beans"]
    record = beans["orderService"]
    record[field] = [INSTRUCTION] if field == "aliases" else INSTRUCTION
    result = run_handler({"beanNamePattern": "orderService"}, services)
    assert INSTRUCTION not in json.dumps(result)
    assert result["nextAction"]["kind"] in ("done", "refine", "page")


@pytest.mark.parametrize(
    "payload",
    [INSTRUCTION, json.dumps({"message": INSTRUCTION}), "[system] " + INSTRUCTION],
)
def test_injection_free_text_and_embedded_json_rejected_by_final_sink(payload):
    with pytest.raises(NormalizationError):
        serialize_response({"status": "ok", "data": {"message": payload}})


def test_injection_conditions_and_source_correlation_never_export_message():
    services = services_for("stripe-missing")
    contexts = services.reader.bodies["conditions"]["contexts"]
    contexts["application"]["negativeMatches"]["example.StripePaymentGateway"][
        "notMatched"
    ][0]["message"] += "; " + INSTRUCTION
    result = run_handler({"typePattern": "example.PaymentGateway"}, services)
    assert INSTRUCTION not in json.dumps(result)
    assert result["data"]["missingBeanAnalysis"]["state"] in (
        "unknown",
        "partial",
        "explained",
    )


@pytest.mark.parametrize(
    "endpoint,body",
    [
        (
            "openapi",
            {
                "openapi": "3.0.3",
                "description": INSTRUCTION,
                "servers": [{"url": "http://169.254.169.254/"}],
                "components": {
                    "schemas": {"Exfil": {"$ref": "http://169.254.169.254/latest"}}
                },
            },
        ),
        ("flyway", {"description": INSTRUCTION, "comments": INSTRUCTION}),
        ("health", {"status": "DOWN", "details": {"message": INSTRUCTION}}),
    ],
)
def test_injection_runtime_links_and_prose_cannot_trigger_a_second_request(
    endpoint, body
):
    import asyncio

    from spring_runtime_mcp.transport.response_gate import (
        HttpResponse,
        ResponseGateReader,
    )

    from .helpers import SpyTransport

    spy = SpyTransport(HttpResponse(200, "application/json", json.dumps(body).encode()))
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({endpoint})
    )
    observation = asyncio.run(reader.read("local", endpoint, snapshot_id="snapshot"))
    projected = SanitizerPipeline().sanitize(observation.body)
    assert INSTRUCTION not in json.dumps(projected)
    assert len(spy.calls) == 1


@pytest.mark.parametrize("field", ["message", "nextAction", "scope", "name"])
def test_injection_long_prose_is_omitted_or_bounded(field):
    clean = SanitizerPipeline().sanitize({field: INSTRUCTION * 2000})
    assert INSTRUCTION not in json.dumps(clean)
    assert len(json.dumps(clean).encode()) < 1024


@pytest.mark.parametrize("field", ["value", "bindingValue", "effectiveValue", "steps"])
def test_injection_generic_scalar_fields_do_not_reintroduce_free_prose(field):
    result = SanitizerPipeline().sanitize({field: INSTRUCTION})
    assert INSTRUCTION not in json.dumps(result)
    with pytest.raises(NormalizationError):
        serialize_response({"status": "ok", "data": {field: INSTRUCTION}})
