"""Independent validators: never construct expected output from its schema."""

import json
import re
from datetime import datetime

from jsonschema import Draft202012Validator, FormatChecker

from spring_runtime_mcp.contracts.definitions import definition, list_tools
from spring_runtime_mcp.transport.responses import serialize_response

NAMES = (
    "springboot_analyze_startup",
    "springboot_check_migrations",
    "springboot_detect_drift",
    "springboot_diagnose_health",
    "springboot_inspect_beans",
    "springboot_inspect_config",
    "springboot_search_openapi",
    "springboot_verify_mappings",
)


FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def rfc3339(value):
    # jsonschema's optional RFC3339 dependency is absent in the offline tool
    # environment. Register explicitly so format keywords never silently skip.
    if not isinstance(value, str):
        return True
    if (
        re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})",
            value,
        )
        is None
    ):
        return False
    normalized = value.upper()
    # RFC3339 admits leap seconds; datetime validates the other calendar fields.
    if normalized[17:19] == "60":
        normalized = normalized[:17] + "59" + normalized[19:]
    datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return True


def validator(name):
    return Draft202012Validator(
        definition(name)["outputSchema"], format_checker=FORMATS
    )


def assert_contract(name, envelope):
    definitions = list_tools()
    assert tuple(d["name"] for d in definitions) == NAMES
    assert len(definitions) == len(set(NAMES)) == 8
    hints = definition(name)["annotations"]
    for key, expected in {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }.items():
        assert hints[key] is expected
    validator(name).validate(envelope)
    frame = serialize_response(envelope, "\0" * 128)
    assert len(frame) <= 16_384
    result = json.loads(frame)["result"]
    assert result["structuredContent"] == envelope
    assert json.loads(result["content"][0]["text"]) == envelope
    assert result["isError"] is (envelope["status"] not in ("ok", "partial"))
