import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .helpers import SECRET

ROOT = Path(__file__).resolve().parents[2]


def cli(*args, bootstrap=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    command = (
        [sys.executable, "-m", "spring_runtime_mcp.replay", *args]
        if bootstrap is None
        else [sys.executable, "-c", bootstrap, *args]
    )
    return subprocess.run(
        command, check=False, env=env, capture_output=True, timeout=10
    )


def test_stdio_success_is_exactly_one_json_rpc_line():
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"orderService"}',
    )
    assert result.returncode == 0 and not result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["jsonrpc"] == "2.0"


@pytest.mark.parametrize("argument", ["--scenario", "--tool", "--unknown"])
def test_stdio_argparse_never_echoes_untrusted_values(argument):
    result = cli(argument, f"Bearer {SECRET}")
    assert result.returncode != 0
    assert SECRET.encode() not in result.stdout + result.stderr
    assert b"Traceback" not in result.stderr


def test_stdio_unexpected_dependency_error_never_prints_traceback_or_secret():
    bootstrap = f"""from spring_runtime_mcp import replay
replay.services_for = lambda scenario: (_ for _ in ()).throw(RuntimeError("password={SECRET}"))
replay.main()
"""
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"*"}',
        bootstrap=bootstrap,
    )
    assert SECRET.encode() not in result.stdout + result.stderr
    assert b"Traceback" not in result.stderr


def test_stdio_third_party_prints_are_contained():
    bootstrap = """from spring_runtime_mcp import replay
original = replay.services_for
def noisy(scenario):
    print("THIRD_PARTY_DEBUG_OUTPUT")
    return original(scenario)
replay.services_for = noisy
replay.main()
"""
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"orderService"}',
        bootstrap=bootstrap,
    )
    assert b"THIRD_PARTY_DEBUG_OUTPUT" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["jsonrpc"] == "2.0"


def test_stdio_native_descriptor_writes_are_contained():
    bootstrap = f"""import os
from spring_runtime_mcp import replay
original = replay.services_for
def noisy(scenario):
    os.write(1, b"Bearer {SECRET}")
    os.write(2, b"Bearer {SECRET}")
    return original(scenario)
replay.services_for = noisy
replay.main()
"""
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"orderService"}',
        bootstrap=bootstrap,
    )
    assert SECRET.encode() not in result.stdout + result.stderr
    assert json.loads(result.stdout)["jsonrpc"] == "2.0"


def test_stdio_fixture_symlink_cannot_read_outside_fixture_root(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"contexts":{}}')
    root = tmp_path / "fixtures"
    (root / "parent-child").mkdir(parents=True)
    (root / "parent-child" / "beans.json").symlink_to(outside)
    bootstrap = f"""from pathlib import Path
from spring_runtime_mcp import replay
replay.REPLAY_ROOT = Path({str(root)!r})
replay.main()
"""
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"*"}',
        bootstrap=bootstrap,
    )
    frame = json.loads(result.stdout)
    assert frame["result"]["isError"] is True
    assert b"Traceback" not in result.stderr


def test_stdio_cached_stream_buffer_cannot_flush_after_protocol_frame():
    bootstrap = f"""import sys
cached = sys.stdout
from spring_runtime_mcp import replay
original = replay.services_for
def noisy(scenario):
    cached.write("Bearer {SECRET}")
    return original(scenario)
replay.services_for = noisy
replay.main()
"""
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"orderService"}',
        bootstrap=bootstrap,
    )
    assert SECRET.encode() not in result.stdout + result.stderr
    assert json.loads(result.stdout)["jsonrpc"] == "2.0"


def test_stdio_atexit_library_logging_is_also_contained():
    bootstrap = f"""import atexit
from spring_runtime_mcp import replay
atexit.register(lambda: print("Bearer {SECRET}"))
replay.main()
"""
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"orderService"}',
        bootstrap=bootstrap,
    )
    assert SECRET.encode() not in result.stdout + result.stderr
    assert json.loads(result.stdout)["jsonrpc"] == "2.0"


def test_stdio_cancellation_exception_is_redacted():
    bootstrap = f"""import asyncio
from spring_runtime_mcp import replay
def cancelled(scenario):
    raise asyncio.CancelledError("password={SECRET}")
replay.services_for = cancelled
replay.main()
"""
    result = cli(
        "--scenario",
        "parent-child",
        "--tool",
        "springboot_inspect_beans",
        "--arguments-json",
        '{"beanNamePattern":"*"}',
        bootstrap=bootstrap,
    )
    assert SECRET.encode() not in result.stdout + result.stderr
    assert b"Traceback" not in result.stderr
    assert json.loads(result.stdout)["result"]["isError"] is True


def test_stdio_fixture_reader_rejects_symlink_roots_and_enforces_read_budget(tmp_path):
    from spring_runtime_mcp.models.common import NormalizationError
    from spring_runtime_mcp.transport.replay_files import read_replay_file

    root = tmp_path / "data"
    root.mkdir()
    path = root / "payload.json"
    path.write_bytes(b"x" * 128)
    with pytest.raises(NormalizationError, match="RESPONSE_TOO_LARGE"):
        read_replay_file(root, path, max_bytes=64)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(NormalizationError, match="FIXTURE_PATH_DENIED"):
        read_replay_file(alias, alias / "payload.json")
