"""In-memory SDK host used by tests to assert on real wire frames.

Lines are parsed and re-serialized exactly as `mcp.server.stdio` does, so a
frame produced here has the same shape as one the real process writes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import mcp.types as types
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage

from spring_runtime_mcp.server import build_server
from spring_runtime_mcp.tools.inspect_beans import Services

_INIT = {
    "jsonrpc": "2.0",
    "id": "harness-init",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "harness", "version": "0"},
    },
}
_INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
_HANDSHAKE = (_INIT, _INITIALIZED)


async def collect(
    services: Services,
    messages: list[object],
    *,
    expect: int,
    timeout: float = 10.0,
    step: float = 1.0,
    grace: float = 0.0,
) -> list[dict[str, Any]]:
    """Send `messages` one at a time and return the frames the server wrote.

    Each request waits up to `step` for a response before the next is sent, so
    the handshake completes before follow-up traffic. A dropped frame or a
    notification simply produces nothing; `expect` bounds the whole exchange
    and `grace` holds the session open afterwards for negative assertions.
    """
    frames: list[dict[str, Any]] = []
    flush = asyncio.Event()
    crashed: list[BaseException] = []
    loop = asyncio.get_running_loop()

    async def serve() -> None:
        server = build_server(services)
        async with create_client_server_memory_streams() as (client, server_streams):
            read_stream, write_stream = server_streams
            reader = client[0]

            async def run_server() -> None:
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )

            async def drain() -> None:
                async for item in reader:
                    if isinstance(item, Exception):
                        continue
                    frames.append(
                        json.loads(
                            item.message.model_dump_json(
                                by_alias=True, exclude_unset=True
                            )
                        )
                    )

            sender = client[1]
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(run_server())
                    group.create_task(drain())
                    for message in messages:
                        text = (
                            message
                            if isinstance(message, str)
                            else json.dumps(message, separators=(",", ":"))
                        )
                        try:
                            parsed = types.jsonrpc_message_adapter.validate_json(
                                text, by_name=False
                            )
                        except Exception:
                            # The stdio transport drops unparsable frames the same way.
                            continue
                        before = len(frames)
                        await sender.send(SessionMessage(parsed))
                        if isinstance(parsed, types.JSONRPCRequest):
                            step_deadline = loop.time() + step
                            while len(frames) == before:
                                if crashed or loop.time() > step_deadline:
                                    break
                                await asyncio.sleep(0.005)
                    await flush.wait()
                    # EOF on the client side is what ends the server loop.
                    await sender.aclose()
            except BaseException as exc:  # noqa: BLE001 - reported by collect()
                crashed.append(exc)

    task = asyncio.create_task(serve())
    deadline = loop.time() + timeout
    try:
        while len(frames) < expect:
            if crashed:
                raise crashed[0]
            if loop.time() > deadline:
                raise AssertionError(
                    f"expected {expect} responses, collected {len(frames)}"
                )
            await asyncio.sleep(0.01)
        if grace:
            await asyncio.sleep(grace)
        flush.set()
    finally:
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001 - teardown never masks the verdict
            pass
    return frames


async def dispatch(
    message: object,
    services: Services,
    timeout: float = 10.0,
    *,
    expect_reply: bool = True,
) -> bytes | None:
    """Run one message after a full handshake and return its response frame.

    Returns None when the server wrote no response for the message (a
    notification, or a frame the transport refused to parse). Pass
    `expect_reply=False` for those so the wait does not burn the timeout.
    """
    frames = await collect(
        services,
        [*_HANDSHAKE, message],
        expect=2 if expect_reply else 1,
        timeout=timeout,
    )
    init = next((frame for frame in frames if frame.get("id") == "harness-init"), None)
    assert init is not None and "result" in init, frames
    if not isinstance(message, dict) or "id" not in message:
        return None
    reply = next(
        (frame for frame in frames if _same_id(frame.get("id"), message["id"])),
        None,
    )
    return None if reply is None else _wire(reply)


def _same_id(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _wire(frame: dict[str, Any]) -> bytes:
    return json.dumps(frame, separators=(",", ":")).encode("utf-8")
