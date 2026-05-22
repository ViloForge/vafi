"""Format cxdb events for agent consumption.

Breadcrumbs collapse sequences, turns are truncated, filters applied server-side.
"""

from __future__ import annotations

from cxdb.models import ParsedTurn, ToolResultEvent, ToolUseEvent


def format_breadcrumbs(events: list[ToolUseEvent | ToolResultEvent]) -> str:
    """Convert tool events into a collapsed markdown timeline.

    Sequential reads are collapsed. Bash shows command + outcome.
    Edit/Write shows file name.
    """
    if not events:
        return ""

    lines: list[str] = []
    pending_reads: list[str] = []
    step = 0

    def flush_reads():
        nonlocal step
        if pending_reads:
            step += 1
            names = [p.rsplit("/", 1)[-1] for p in pending_reads]
            if len(pending_reads) == 1:
                lines.append(f"{step}. Read {names[0]}")
            else:
                lines.append(f"{step}. Read {len(pending_reads)} files: {', '.join(names)}")
            pending_reads.clear()

    for event in events:
        if isinstance(event, ToolUseEvent):
            if event.tool_name == "Read" and event.file_path:
                pending_reads.append(event.file_path)
                continue

            flush_reads()
            step += 1

            if event.tool_name == "Bash":
                cmd = event.description or event.command or "shell command"
                lines.append(f"{step}. Bash: {cmd}")
            elif event.tool_name in ("Edit", "Write") and event.file_path:
                name = event.file_path.rsplit("/", 1)[-1]
                lines.append(f"{step}. {event.tool_name} {name}")
            else:
                lines.append(f"{step}. {event.tool_name}")

        elif isinstance(event, ToolResultEvent):
            # Attach outcome to the last line if it's a test result
            if event.tool_name == "Bash" and ("passed" in event.content or "failed" in event.content):
                # Extract the relevant part
                for part in event.content.split("\n"):
                    if "passed" in part or "failed" in part:
                        lines.append(f"   → {part.strip()}")
                        break

    flush_reads()
    return "\n".join(lines)


def format_turn(
    event: ToolResultEvent,
    max_content: int = 2000,
) -> dict:
    """Format a single tool result for agent display."""
    content = event.content
    if len(content) > max_content:
        content = content[:max_content] + "..."

    return {
        "turn_id": event.turn_id,
        "call_id": event.call_id,
        "tool_name": event.tool_name,
        "content": content,
        "is_error": event.is_error,
    }


def filter_sessions(
    contexts: list[dict],
    task_id: str | None = None,
    role: str | None = None,
) -> list[dict]:
    """Filter cxdb session contexts by their ``task:`` / ``role:`` labels.

    Sessions are labelled by the controller invoker; the retrospective
    judge-evaluation loop (P2-1) uses this to pull e.g. the judge session
    for a given task. Filters are conjunctive; a context whose ``labels``
    do not contain a required label (including a context missing the key
    entirely) is excluded.
    """
    result = contexts
    if task_id:
        label = f"task:{task_id}"
        result = [c for c in result if label in c.get("labels", [])]
    if role:
        label = f"role:{role}"
        result = [c for c in result if label in c.get("labels", [])]
    return result


def apply_filters(
    turns: list[ParsedTurn],
    tool_name: str | None = None,
    content_contains: str | None = None,
    from_depth: int | None = None,
    to_depth: int | None = None,
) -> list[ParsedTurn]:
    """Filter parsed turns by criteria."""
    result = turns

    if from_depth is not None:
        result = [t for t in result if t.depth >= from_depth]
    if to_depth is not None:
        result = [t for t in result if t.depth <= to_depth]

    if tool_name:
        result = [
            t for t in result
            if t.item_type == "assistant_turn"
            and any(
                tc.get("name") == tool_name
                for tc in t.content.get("turn", {}).get("tool_calls", [])
            )
        ]

    if content_contains:
        result = [
            t for t in result
            if t.item_type == "tool_result"
            and content_contains in t.content.get("tool_result", {}).get("content", "")
        ]

    return result
