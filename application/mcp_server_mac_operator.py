"""
mac-operator MCP entrypoint.

Launches the skill-local MCP server that runs AppleScript/JXA on macOS.
See skills/mac-operator/SKILL.md and https://github.com/steipete/macos-automator-mcp
"""

from __future__ import annotations

import runpy
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "skills" / "mac-operator" / "scripts" / "mcp_server.py"

if __name__ == "__main__":
    runpy.run_path(str(SERVER), run_name="__main__")
