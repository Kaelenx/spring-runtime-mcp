"""All eight public contracts; goldens do not impersonate unimplemented handlers."""

from copy import deepcopy

import pytest
from jsonschema import ValidationError

from spring_runtime_mcp.contracts.definitions import definition, list_tools
from spring_runtime_mcp.tools.inspect_beans import failed

from .assertions import NAMES, assert_contract, validator


@pytest.mark.parametrize("name", NAMES)
def test_typed_golden_output_and_risk_annotations(name, load_sample):
    example = load_sample(f"contracts/{name}.json")
    assert_contract(name, example)
    data = example["data"]
    assert data.get("items") or data.get("missingInRuntime")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "mutation",
    (
        "unknown-property",
        "missing-required",
        "bad-time",
        "wrong-total",
        "invalid-record",
    ),
)
def test_contract_rejects_corrupted_output(name, mutation, load_sample):
    result = load_sample(f"contracts/{name}.json")
    if mutation == "unknown-property":
        result["data"]["unregistered"] = True
    elif mutation == "missing-required":
        del result["coverage"]
    elif mutation == "bad-time":
        result["source"]["observedAt"] = "yesterday"
    elif mutation == "wrong-total":
        result["totalMatched"] = -1
    else:
        records = result["data"].get("items", result["data"].get("missingInRuntime"))
        records[0]["unregistered"] = True
    with pytest.raises(ValidationError):
        validator(name).validate(result)


@pytest.mark.parametrize("name", NAMES)
def test_unavailable_contract_does_not_invent_empty_success(name):
    result = failed("CAPABILITY_DISABLED")
    assert_contract(name, result)
    invalid = deepcopy(result)
    invalid["status"] = "ok"
    with pytest.raises(ValidationError):
        validator(name).validate(invalid)


def test_list_tools_is_stable_and_defensive():
    first = list_tools()
    assert first == list_tools()
    first.reverse()
    first[0]["annotations"]["readOnlyHint"] = False
    assert tuple(t["name"] for t in list_tools()) == NAMES
    assert definition(NAMES[-1])["annotations"]["readOnlyHint"] is True


VALID_ARGUMENTS = {
    "springboot_analyze_startup": {},
    "springboot_check_migrations": {"engine": "auto"},
    "springboot_detect_drift": {"expectedBeans": [{"name": "orderService"}]},
    "springboot_diagnose_health": {"componentPath": ["db"]},
    "springboot_inspect_beans": {"beanNamePattern": "order*"},
    "springboot_inspect_config": {"prefix": "spring.datasource"},
    "springboot_search_openapi": {"pathPattern": "/orders/*"},
    "springboot_verify_mappings": {"httpMethod": "POST", "pathPattern": "/orders"},
}


@pytest.mark.parametrize("name", NAMES)
def test_input_schema_valid_arguments(name):
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(definition(name)["inputSchema"])
    validator.validate(VALID_ARGUMENTS[name])
    assert validator.is_valid(VALID_ARGUMENTS[name])


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "extra",
    [
        {"url": "http://169.254.169.254/"},
        {"headers": {"Authorization": "blocked"}},
        {"method": "POST"},
        {"maxResults": 0},
        {"maxResults": 51},
        {"maxResults": True},
        {"targetId": "local\n"},
    ],
)
def test_input_schema_rejects_escalation_and_invalid_limits(name, extra):
    from jsonschema import Draft202012Validator

    with pytest.raises(ValidationError):
        Draft202012Validator(definition(name)["inputSchema"]).validate(
            {**VALID_ARGUMENTS[name], **extra}
        )
