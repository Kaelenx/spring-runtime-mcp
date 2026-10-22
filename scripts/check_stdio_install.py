"""Check an installed entrypoint with the official SDK, without target IO."""

from __future__ import annotations

import argparse
import asyncio
from typing import Literal

from mcp import Client, StdioServerParameters


async def verify(command: str) -> None:
    modes: tuple[Literal["legacy", "2026-07-28"], ...] = ("legacy", "2026-07-28")
    for mode in modes:
        async with Client(
            StdioServerParameters(command=command),
            mode=mode,
            read_timeout_seconds=15,
        ) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 8
            assert [tool.name for tool in tools] == sorted(tool.name for tool in tools)
            assert all(tool.output_schema for tool in tools)
            result = await client.call_tool(
                "springboot_diagnose_health", {"maxResults": 0}
            )
            assert (
                result.is_error
                and isinstance(result.structured_content, dict)
                and result.structured_content["errors"][0]["code"] == "INVALID_ARGUMENT"
            )
    print(
        "Installed stdio entrypoint passed legacy and 2026-07-28 discovery and tool-error checks."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.command))


if __name__ == "__main__":
    main()
