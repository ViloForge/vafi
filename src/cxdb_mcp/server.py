"""cxdb MCP server — provides agent-facing context query tools.

Reuses src/cxdb/ (client, parser, extractor) from Phase A unchanged.
Adds formatters for breadcrumbs and turn display.
"""

from __future__ import annotations

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

from cxdb.client import CxdbClient
from cxdb.extractor import extract_structured
from cxdb.parser import extract_tool_events, parse_turns

from .formatters import apply_filters, filter_sessions, format_breadcrumbs, format_turn

logger = logging.getLogger("cxdb_mcp")

# Configuration from environment
CXDB_URL = os.environ.get("CXDB_URL", "http://vafi-cxdb:80")
CXDB_PUBLIC_URL = os.environ.get("CXDB_PUBLIC_URL", CXDB_URL)

mcp = FastMCP(
    "cxdb-context",
    instructions="Query agent execution traces from cxdb.",
    host="0.0.0.0",
    port=8090,
)
_client = CxdbClient(base_url=CXDB_URL)


@mcp.tool()
async def cxdb_session_summary(context_id: int) -> str:
    """Get a structured summary of an agent execution session.

    Returns duration, files modified, tools used, test results, and commits.
    Use this to quickly understand what happened in a session.
    """
    raw_turns = await _client.get_turns(context_id)
    parsed = parse_turns(raw_turns)
    events = extract_tool_events(parsed)
    structured = extract_structured(events=events, parsed_turns=parsed)

    lines = [
        f"Session {context_id} ({CXDB_PUBLIC_URL}/c/{context_id})",
        f"Duration: {structured.duration_seconds}s, {structured.turn_count} turns, model: {structured.model}",
    ]

    if structured.tools_used:
        lines.append(f"Tools: {', '.join(structured.tools_used)}")
    if structured.files_modified:
        lines.append(f"Files modified: {', '.join(structured.files_modified)}")
    if structured.files_read:
        lines.append(f"Files read: {', '.join(structured.files_read)}")
    if structured.tests:
        lines.append(f"Tests: {structured.tests.passed} passed, {structured.tests.failed} failed")
    if structured.commits:
        lines.append(f"Commits: {', '.join(structured.commits)}")

    return "\n".join(lines)


@mcp.tool()
async def cxdb_session_breadcrumbs(context_id: int) -> str:
    """Get a step-by-step timeline of what an agent did in a session.

    Returns a collapsed tool-use timeline: reads grouped, bash with outcomes,
    edits with file names. Use this to trace the agent's approach.
    """
    raw_turns = await _client.get_turns(context_id)
    parsed = parse_turns(raw_turns)
    events = extract_tool_events(parsed)
    breadcrumbs = format_breadcrumbs(events)

    if not breadcrumbs:
        return f"No tool events found in session {context_id}."

    return f"## Session {context_id} Breadcrumbs\n\n{breadcrumbs}"


@mcp.tool()
async def cxdb_get_turns(
    context_id: int,
    tool_name: str = "",
    content_contains: str = "",
    from_depth: int = 0,
    to_depth: int = 0,
) -> str:
    """Fetch and filter specific turns from a session.

    Filters: tool_name (e.g. "Bash"), content_contains (e.g. "FAILED"),
    from_depth/to_depth (turn range). Returns matching tool results.
    """
    raw_turns = await _client.get_turns(context_id)
    parsed = parse_turns(raw_turns)

    filtered = apply_filters(
        parsed,
        tool_name=tool_name or None,
        content_contains=content_contains or None,
        from_depth=from_depth or None,
        to_depth=to_depth or None,
    )

    if not filtered:
        return "No matching turns found."

    # Format tool_result turns for display
    events = extract_tool_events(filtered)
    results = []
    for e in events:
        if hasattr(e, "content"):
            results.append(format_turn(e))

    if not results:
        return f"Found {len(filtered)} matching turns but no tool results."

    return json.dumps(results, indent=2)


@mcp.tool()
async def cxdb_list_sessions(
    task_id: str = "",
    role: str = "",
    limit: int = 20,
) -> str:
    """List recent cxdb sessions, optionally filtered by task ID and/or role.

    ``role`` selects sessions by the agent role label (e.g. "judge" or
    "executor") — the retrospective judge-evaluation loop uses
    ``role="judge"`` (optionally with ``task_id``) to pull judge sessions.
    Returns session metadata: context_id, title, turn count, creation time.
    """
    contexts = await _client.list_contexts(limit=limit)
    contexts = filter_sessions(contexts, task_id=task_id or None, role=role or None)

    if not contexts:
        return "No sessions found."

    lines = []
    for ctx in contexts:
        lines.append(
            f"- Context {ctx['context_id']}: {ctx.get('title', 'untitled')} "
            f"({ctx.get('head_depth', 0)} turns, "
            f"live={ctx.get('is_live', False)})"
        )

    return "\n".join(lines)


def run():
    """Entry point for the MCP server."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    run()
