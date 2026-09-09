"""
macOS Operator MCP Server

AppleScript / JXA execution + scripting tips knowledge base.
Inspired by https://github.com/steipete/macos-automator-mcp
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from knowledge_base import (  # noqa: E402
    KnowledgeBase,
    default_kb_roots,
    format_categories_markdown,
    format_tips_markdown,
    substitute_placeholders,
)
from script_executor import execute_script as run_osascript  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mcp-server-mac-operator")

try:
    mcp = MCPServer(
        name="mac-operator",
        instructions=(
            "You control macOS via AppleScript and JXA (JavaScript for Automation). "
            "Before writing scripts from scratch, call get_scripting_tips to search the knowledge base. "
            "Then run a tip with execute_script(kb_script_id=...) or run trusted inline script_content. "
            "Confirm destructive actions with the user. Automation/Accessibility permissions may be required."
        ),
    )
    logger.info("MCP server (mac-operator) initialized successfully")
except Exception as e:  # noqa: BLE001
    logger.error(f"MCP server init error: {e}")
    raise

_kb = KnowledgeBase(default_kb_roots(SKILL_DIR))


def _clean_osa_stderr(stderr: str) -> str:
    if not stderr:
        return ""
    noise = (
        "Connection Invalid error for service com.apple.hiservices-xpcservice",
        "Error received in message reply handler: Connection invalid",
    )
    lines = [
        line
        for line in stderr.splitlines()
        if not any(n in line for n in noise)
    ]
    return "\n".join(lines).strip()


def _format_execution_result(
    result,
    *,
    executed_script: Optional[str] = None,
    substitution_logs: Optional[List[str]] = None,
    report_execution_time: bool = False,
    include_executed_script_in_output: bool = False,
    include_substitution_logs: bool = False,
) -> str:
    parts: list[str] = []
    stderr = _clean_osa_stderr(result.stderr)
    if result.ok:
        parts.append(result.stdout or "(no output)")
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
    else:
        parts.append(result.message or "Script execution failed")
        if result.stdout:
            parts.append(f"[stdout]\n{result.stdout}")
        if stderr:
            parts.append(f"[stderr]\n{stderr}")

    if include_executed_script_in_output and executed_script:
        parts.append(f"[executed_script]\n{executed_script}")
    if include_substitution_logs and substitution_logs:
        parts.append("[substitution_logs]\n" + "\n".join(substitution_logs))
    if report_execution_time:
        parts.append(f"[execution_time] {result.execution_time_seconds}s")
    return "\n\n".join(parts)


@mcp.tool()
def get_scripting_tips(
    list_categories: bool = False,
    category: Optional[str] = None,
    search_term: Optional[str] = None,
    limit: int = 10,
    refresh_database: bool = False,
) -> str:
    """
    List knowledge-base categories or search AppleScript/JXA tips for macOS automation.

    Prefer this before writing scripts from scratch. Runnable tip IDs can be passed to
    execute_script as kb_script_id. Tips with placeholders accept input_data or arguments.

    Args:
        list_categories: If true, return category IDs, titles, and tip counts
        category: Limit results to a category ID (e.g. "01_system", "03_files")
        search_term: Fuzzy search across titles, IDs, keywords, descriptions, scripts
        limit: Maximum number of tip results (default 10)
        refresh_database: Reload bundled and local knowledge bases before querying

    Returns:
        Markdown with categories or matching tips (including script bodies and IDs)
    """
    logger.info(
        "get_scripting_tips list_categories=%s category=%s search_term=%s limit=%s refresh=%s",
        list_categories,
        category,
        search_term,
        limit,
        refresh_database,
    )
    if refresh_database:
        _kb.load(force=True)

    if list_categories and not search_term and not category:
        return format_categories_markdown(_kb.list_categories())

    tips = _kb.search(search_term=search_term, category=category, limit=limit)
    header = ""
    if list_categories:
        header = format_categories_markdown(_kb.list_categories()) + "\n\n"
    if not search_term and not category:
        # Default: show categories + a few starter tips
        starters = _kb.search(search_term="clipboard notification finder safari", limit=min(limit, 5))
        body = format_tips_markdown(starters) if starters else format_tips_markdown(tips)
        return header + "## Starter tips\n\n" + body if not header else header + body
    return header + format_tips_markdown(tips)


@mcp.tool()
def execute_script(
    kb_script_id: Optional[str] = None,
    script_content: Optional[str] = None,
    script_path: Optional[str] = None,
    language: str = "applescript",
    arguments: Optional[List[str]] = None,
    input_data: Optional[Dict[str, Any]] = None,
    timeout_seconds: int = 60,
    output_format_mode: str = "auto",
    include_executed_script_in_output: bool = False,
    include_substitution_logs: bool = False,
    report_execution_time: bool = False,
) -> str:
    """
    Run one AppleScript or JXA (JavaScript for Automation) script on macOS.

    Provide exactly one source: kb_script_id, script_content, or script_path.
    Prefer kb_script_id from get_scripting_tips when available.

    Args:
        kb_script_id: Knowledge-base tip ID (language inferred from tip)
        script_content: Inline AppleScript or JXA source
        script_path: Absolute POSIX path to a readable script file
        language: "applescript" or "javascript" (for inline/file sources; default applescript)
        arguments: Positional argv / ${arguments[N]} / --MCP_ARG_N values
        input_data: Named values for ${inputData.key} / --MCP_INPUT:key placeholders
        timeout_seconds: Kill the script after this many seconds (default 60)
        output_format_mode: auto | human_readable | structured_error |
            structured_output_and_error | direct
        include_executed_script_in_output: Include final substituted source in the response
        include_substitution_logs: Include placeholder substitution diagnostics
        report_execution_time: Append execution duration

    Returns:
        Script stdout on success, or an error message with stderr/permission hints
    """
    sources = [bool(kb_script_id), bool(script_content), bool(script_path)]
    if sum(sources) != 1:
        return (
            "Error: provide exactly one of kb_script_id, script_content, or script_path."
        )

    lang = (language or "applescript").lower()
    if lang in {"js", "jxa"}:
        lang = "javascript"
    if lang not in {"applescript", "javascript"}:
        return "Error: language must be 'applescript' or 'javascript'."

    mode = output_format_mode or "auto"
    valid_modes = {
        "auto",
        "human_readable",
        "structured_error",
        "structured_output_and_error",
        "direct",
    }
    if mode not in valid_modes:
        return f"Error: output_format_mode must be one of {sorted(valid_modes)}"

    content: Optional[str] = script_content
    path: Optional[str] = script_path
    substitution_logs: list[str] = []

    if kb_script_id:
        tip = _kb.get_tip(kb_script_id)
        if not tip or not tip.script:
            return (
                f"Error: unknown kb_script_id '{kb_script_id}'. "
                "Use get_scripting_tips to find valid IDs."
            )
        lang = tip.language
        content, substitution_logs = substitute_placeholders(
            tip.script,
            input_data=input_data,
            arguments=arguments,
        )
        # KB scripts already consumed named/positional placeholders; still pass argv for on run argv
        path = None
    elif content:
        content, substitution_logs = substitute_placeholders(
            content,
            input_data=input_data,
            arguments=arguments,
        )

    logger.info(
        "execute_script source=%s language=%s timeout=%s",
        kb_script_id or ("inline" if content else path),
        lang,
        timeout_seconds,
    )

    started = time.perf_counter()
    result = run_osascript(
        content=content,
        path=path,
        language=lang,  # type: ignore[arg-type]
        arguments=arguments if not kb_script_id else arguments,
        timeout_seconds=timeout_seconds,
        output_format_mode=mode,  # type: ignore[arg-type]
    )
    # Prefer executor timing; fall back if needed
    if result.execution_time_seconds <= 0:
        result.execution_time_seconds = round(time.perf_counter() - started, 3)

    return _format_execution_result(
        result,
        executed_script=content if content is not None else path,
        substitution_logs=substitution_logs,
        report_execution_time=report_execution_time,
        include_executed_script_in_output=include_executed_script_in_output,
        include_substitution_logs=include_substitution_logs,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
