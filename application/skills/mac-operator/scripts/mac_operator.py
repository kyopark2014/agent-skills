#!/usr/bin/env python3
"""
mac_operator.py — macOS AppleScript/JXA CLI for the mac-operator skill.

Usage:
    python skills/mac-operator/scripts/mac_operator.py doctor
    python skills/mac-operator/scripts/mac_operator.py categories
    python skills/mac-operator/scripts/mac_operator.py tips [--search TERM] [--category ID] [--limit N]
    python skills/mac-operator/scripts/mac_operator.py execute --kb ID [--input key=value ...]
    python skills/mac-operator/scripts/mac_operator.py execute --script 'return "hi"'
    python skills/mac-operator/scripts/mac_operator.py execute --file /abs/path.scpt
    python skills/mac-operator/scripts/mac_operator.py open "Freeform"
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from knowledge_base import (  # noqa: E402
    KnowledgeBase,
    default_kb_roots,
    format_categories_markdown,
    format_tips_markdown,
    substitute_placeholders,
)
from script_executor import execute_script  # noqa: E402


def _kb() -> KnowledgeBase:
    return KnowledgeBase(default_kb_roots(SKILL_DIR))


def _parse_inputs(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"Invalid --input {item!r}; expected key=value")
        key, value = item.split("=", 1)
        out[key.strip()] = value
    return out


def cmd_doctor(_: argparse.Namespace) -> int:
    info: dict[str, Any] = {
        "platform": platform.system(),
        "macos": platform.system() == "Darwin",
        "skill_dir": str(SKILL_DIR),
        "kb_roots": [str(p) for p in default_kb_roots(SKILL_DIR)],
    }
    if platform.system() != "Darwin":
        info["ok"] = False
        info["message"] = "mac-operator requires macOS"
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 1

    kb = _kb()
    kb.load()
    info["tip_count"] = len(kb.tips)
    info["categories"] = [c.id for c in kb.list_categories()]

    probe = execute_script(content='return "mac-operator-ok"')
    info["osascript_ok"] = probe.ok
    info["osascript_stdout"] = probe.stdout
    info["osascript_stderr"] = probe.stderr
    info["ok"] = bool(probe.ok)
    info["message"] = (
        "Ready. Grant Automation/Accessibility to the host app when controlling other apps."
        if probe.ok
        else (probe.message or probe.stderr or "osascript failed")
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0 if probe.ok else 1


def cmd_categories(_: argparse.Namespace) -> int:
    print(format_categories_markdown(_kb().list_categories()))
    return 0


def cmd_tips(args: argparse.Namespace) -> int:
    kb = _kb()
    if args.refresh:
        kb.load(force=True)
    if args.list_categories and not args.search and not args.category:
        print(format_categories_markdown(kb.list_categories()))
        return 0
    tips = kb.search(
        search_term=args.search,
        category=args.category,
        limit=args.limit,
    )
    print(format_tips_markdown(tips))
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    sources = [bool(args.kb), bool(args.script), bool(args.file)]
    if sum(sources) != 1:
        raise SystemExit("Provide exactly one of --kb, --script, or --file")

    language = (args.language or "applescript").lower()
    if language in {"js", "jxa"}:
        language = "javascript"
    input_data = _parse_inputs(args.input)
    arguments = list(args.arg or [])
    content = args.script
    path = args.file
    logs: list[str] = []

    if args.kb:
        tip = _kb().get_tip(args.kb)
        if not tip or not tip.script:
            raise SystemExit(f"Unknown kb id: {args.kb}")
        language = tip.language
        content, logs = substitute_placeholders(
            tip.script, input_data=input_data, arguments=arguments
        )
        path = None
    elif content:
        content, logs = substitute_placeholders(
            content, input_data=input_data, arguments=arguments
        )

    result = execute_script(
        content=content,
        path=path,
        language=language,  # type: ignore[arg-type]
        arguments=arguments,
        timeout_seconds=args.timeout,
        output_format_mode=args.output_format,  # type: ignore[arg-type]
    )

    payload: dict[str, Any] = {
        "ok": result.ok,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "execution_time_seconds": result.execution_time_seconds,
        "message": result.message,
    }
    if args.verbose:
        payload["substitution_logs"] = logs
        if content is not None:
            payload["executed_script"] = content
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if result.ok:
            print(result.stdout or "(no output)")
        else:
            print(result.message or "execution failed", file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return 1
    return 0 if result.ok else 1


def cmd_open(args: argparse.Namespace) -> int:
    """Shortcut: activate/open an application by name."""
    ns = argparse.Namespace(
        kb="app_open",
        script=None,
        file=None,
        language="applescript",
        input=[f"app_name={args.app_name}"],
        arg=[],
        timeout=60,
        output_format="auto",
        json=args.json,
        verbose=False,
    )
    return cmd_execute(ns)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="mac-operator skill CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Check macOS/osascript readiness")
    p_doc.set_defaults(func=cmd_doctor)

    p_cat = sub.add_parser("categories", help="List knowledge-base categories")
    p_cat.set_defaults(func=cmd_categories)

    p_tips = sub.add_parser("tips", help="Search scripting tips")
    p_tips.add_argument("--search", "-s", default=None)
    p_tips.add_argument("--category", "-c", default=None)
    p_tips.add_argument("--limit", "-n", type=int, default=10)
    p_tips.add_argument("--list-categories", action="store_true")
    p_tips.add_argument("--refresh", action="store_true")
    p_tips.set_defaults(func=cmd_tips)

    p_ex = sub.add_parser("execute", help="Run kb tip / inline / file script")
    p_ex.add_argument("--kb", help="Knowledge-base tip id")
    p_ex.add_argument("--script", help="Inline AppleScript or JXA")
    p_ex.add_argument("--file", help="Absolute script path")
    p_ex.add_argument("--language", default="applescript")
    p_ex.add_argument("--input", action="append", help="key=value for placeholders")
    p_ex.add_argument("--arg", action="append", help="Positional argv / arguments[N]")
    p_ex.add_argument("--timeout", type=int, default=60)
    p_ex.add_argument(
        "--output-format",
        default="auto",
        choices=[
            "auto",
            "human_readable",
            "structured_error",
            "structured_output_and_error",
            "direct",
        ],
    )
    p_ex.add_argument("--json", action="store_true")
    p_ex.add_argument("--verbose", action="store_true")
    p_ex.set_defaults(func=cmd_execute)

    p_open = sub.add_parser("open", help="Open/activate an application")
    p_open.add_argument("app_name")
    p_open.add_argument("--json", action="store_true")
    p_open.set_defaults(func=cmd_open)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
