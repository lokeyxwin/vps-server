"""VPS proxy MCP server entrypoint.

This module is intentionally thin:
- configure project logs away from stdout so stdio MCP packets stay clean
- expose Tool metadata from tools.ALL_TOOLS
- dispatch tools/call requests to the matching tool handler
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import mcp.server.stdio
from mcp.server import NotificationOptions, Server


def _configure_logs_for_stdio() -> None:
    """Keep application logs on stderr; stdout belongs to MCP stdio."""
    import log as project_log

    root = logging.getLogger()
    root.setLevel(project_log.LOG_LEVEL)

    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.setStream(sys.stderr)

    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(project_log.LOG_LEVEL)
        handler.setFormatter(project_log.LayeredFormatter())
        root.addHandler(handler)

    project_log._configured = True


_configure_logs_for_stdio()

from tools import ALL_TOOLS  # noqa: E402  (logs must be configured first)


server = Server(
    "vps-proxy-user",
    version="0.1.0",
    instructions=(
        "MCP server for VPS asset management and proxy egress automation. "
        "Tools cover three groups: registering a VPS or an upstream proxy IP "
        "(write: enqueues a background task and returns immediately), "
        "querying registration progress and available proxy nodes (read-only), "
        "and ops bootstrap init_db / init_probe_vps (admin). "
        "After a write call, follow up with the matching status query tool."
    ),
)

_TOOL_HANDLERS = {tool.name: handler for tool, handler in ALL_TOOLS}


@server.list_tools()
async def list_tools():
    """Return all tools exposed by this MCP server."""
    return [tool for tool, _ in ALL_TOOLS]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]):
    """Dispatch an MCP tool call to the matching tools/<name>.py handler."""
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    return await handler(arguments)


async def run() -> None:
    """Run the MCP server over stdio."""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )


if __name__ == "__main__":
    asyncio.run(run())
