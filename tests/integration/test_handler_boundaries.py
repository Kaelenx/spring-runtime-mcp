"""Handler failure boundaries must not leak reader exceptions or invent evidence."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator

from spring_runtime_mcp.contracts.definitions import definition, list_tools
from spring_runtime_mcp.models.common import NormalizationError
from spring_runtime_mcp.replay import services_for
from spring_runtime_mcp.tools.inspect_beans import inspect_beans


def run(arguments, services):
    result = asyncio.run(inspect_beans(arguments, services))
    Draft202012Validator(
        definition("springboot_inspect_beans")["outputSchema"]
    ).validate(result)
    return result


def test_reader_error_text_is_never_returned():
    services = services_for("parent-child")

    class Reader:
        async def read(self, *args, **kwargs):
            raise NormalizationError("Bearer secret-from-reader")

    services.reader = Reader()
    result = run({"beanNamePattern": "*"}, services)
    assert result["errors"][0]["code"] == "MALFORMED_EVIDENCE"
    assert "secret-from-reader" not in json.dumps(result)


def test_conflicting_conditions_parent_cannot_explain_missing_bean():
    services = services_for("stripe-missing")
    services.reader.bodies["conditions"]["contexts"]["application"]["parentId"] = (
        "other"
    )
    result = run({"typePattern": "example.PaymentGateway"}, services)
    assert result["data"]["missingBeanAnalysis"]["state"] == "unknown"
    assert "CONTEXT_HIERARCHY_CONFLICT" in result["coverage"]["gaps"]
    assert not result["data"]["missingBeanAnalysis"]["conditions"]


def test_invalid_observation_time_fails_closed():
    services = services_for("parent-child")
    original = services.reader

    class Reader:
        async def read(self, *args, **kwargs):
            return replace(
                await original.read(*args, **kwargs), observed_at="not-a-date"
            )

    services.reader = Reader()
    result = run({"beanNamePattern": "*"}, services)
    assert result["errors"][0]["code"] == "INVALID_OBSERVATION_TIME"


def test_definition_order_and_copies():
    definitions = list_tools()
    names = [d["name"] for d in definitions]
    assert len(names) == 8 and names == sorted(names)
    definitions[0]["name"] = "tampered"
    assert list_tools()[0]["name"] == names[0]


def test_cli_replays_handler_and_emits_only_json_rpc():
    environment = dict(
        os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src")
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "spring_runtime_mcp.replay",
            "--scenario",
            "stripe-missing",
            "--tool",
            "springboot_inspect_beans",
            "--arguments-json",
            '{"typePattern":"example.PaymentGateway","contextId":"application"}',
        ],
        env=environment,
        capture_output=True,
        check=True,
    )
    assert not completed.stderr
    assert len(completed.stdout) <= 16_384
    frame = json.loads(completed.stdout)
    result = frame["result"]["structuredContent"]
    assert result["data"]["missingBeanAnalysis"]["state"] == "explained"
    assert json.loads(frame["result"]["content"][0]["text"]) == result
