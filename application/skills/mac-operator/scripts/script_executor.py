"""Execute AppleScript / JXA via osascript (macOS only)."""

from __future__ import annotations

import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Sequence


OutputFormatMode = Literal[
    "auto",
    "human_readable",
    "structured_error",
    "structured_output_and_error",
    "direct",
]

Language = Literal["applescript", "javascript"]


@dataclass
class ScriptResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: Optional[int]
    execution_time_seconds: float
    is_timeout: bool = False
    message: str = ""


def _resolve_output_flags(
    language: Language,
    mode: OutputFormatMode,
) -> list[str]:
    resolved = mode
    if resolved == "auto":
        resolved = "direct" if language == "javascript" else "human_readable"

    if resolved == "human_readable":
        return ["-s", "h"]
    if resolved == "structured_error":
        return ["-s", "s"]
    if resolved == "structured_output_and_error":
        return ["-s", "s", "-s", "s"]
    return []


def execute_script(
    *,
    content: Optional[str] = None,
    path: Optional[str] = None,
    language: Language = "applescript",
    arguments: Optional[Sequence[str]] = None,
    timeout_seconds: int = 60,
    output_format_mode: OutputFormatMode = "auto",
) -> ScriptResult:
    """Run one AppleScript or JXA script via osascript."""
    if platform.system() != "Darwin":
        return ScriptResult(
            ok=False,
            stdout="",
            stderr="AppleScript/JXA is only supported on macOS.",
            exit_code=None,
            execution_time_seconds=0.0,
            message="UnsupportedPlatformError",
        )

    if not content and not path:
        return ScriptResult(
            ok=False,
            stdout="",
            stderr="Provide script_content, script_path, or kb_script_id.",
            exit_code=None,
            execution_time_seconds=0.0,
            message="InvalidScriptSourceError",
        )

    osa_args: list[str] = []
    if language == "javascript":
        osa_args.extend(["-l", "JavaScript"])
    osa_args.extend(_resolve_output_flags(language, output_format_mode))

    if content is not None:
        osa_args.extend(["-e", content])
    else:
        script_path = Path(path).expanduser()
        if not script_path.is_file():
            return ScriptResult(
                ok=False,
                stdout="",
                stderr=f"Script file not found or not readable: {script_path}",
                exit_code=None,
                execution_time_seconds=0.0,
                message="ScriptFileAccessError",
            )
        osa_args.append(str(script_path.resolve()))

    if arguments:
        osa_args.extend(list(arguments))

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            ["osascript", *osa_args],
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
        )
        elapsed = round(time.perf_counter() - started, 3)
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        ok = completed.returncode == 0
        hint = ""
        if not ok and any(code in stderr for code in ("-1743", "-10004", "-1712")):
            hint = (
                " Permission hint: grant Automation / Accessibility to the host app "
                "(Terminal, IDE, or MCP client) in System Settings → Privacy & Security."
            )
        return ScriptResult(
            ok=ok,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            execution_time_seconds=elapsed,
            message=("" if ok else f"osascript failed (exit {completed.returncode}).{hint}"),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return ScriptResult(
            ok=False,
            stdout=(exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "").strip() if isinstance(exc.stderr, str) else "Timed out",
            exit_code=None,
            execution_time_seconds=elapsed,
            is_timeout=True,
            message=f"Script timed out after {timeout_seconds}s",
        )
    except FileNotFoundError:
        return ScriptResult(
            ok=False,
            stdout="",
            stderr="osascript not found. This MCP requires macOS.",
            exit_code=None,
            execution_time_seconds=round(time.perf_counter() - started, 3),
            message="osascript missing",
        )
    except Exception as exc:  # noqa: BLE001
        return ScriptResult(
            ok=False,
            stdout="",
            stderr=str(exc),
            exit_code=None,
            execution_time_seconds=round(time.perf_counter() - started, 3),
            message=f"Execution failed: {exc}",
        )
