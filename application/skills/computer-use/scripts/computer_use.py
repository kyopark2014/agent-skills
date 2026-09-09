#!/usr/bin/env python3
"""
computer_use.py — Desktop GUI control CLI for the computer-use skill.

Screenshot + mouse/keyboard actions for macOS and Linux. Coordinates default
to the last screenshot's image space (auto-scaled to real screen pixels).

Usage:
    python computer_use.py doctor
    python computer_use.py info
    python computer_use.py screenshot [--out PATH] [--max-width N]
    python computer_use.py click X Y [--button left|right|middle] [--clicks N]
    python computer_use.py double-click X Y
    python computer_use.py move X Y
    python computer_use.py drag X1 Y1 X2 Y2
    python computer_use.py type "text"
    python computer_use.py key cmd+c
    python computer_use.py scroll [--x X] [--y Y] [--clicks N] [--direction up|down]
    python computer_use.py wait SECONDS
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_NAME = "computer-use"
DEFAULT_MAX_WIDTH = 1280
META_NAME = ".last_screenshot.json"


def _ensure_deps() -> None:
    try:
        import PIL  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "Pillow", "-q"],
            stdout=subprocess.DEVNULL,
        )
    try:
        import pyautogui  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyautogui", "-q"],
            stdout=subprocess.DEVNULL,
        )
        import pyautogui  # noqa: F401

    import pyautogui as pag

    pag.FAILSAFE = True
    pag.PAUSE = 0.05


def _artifacts_root() -> Path:
    env = os.environ.get("ARTIFACTS_DIR") or os.environ.get("CU_OUT_DIR")
    if env:
        root = Path(env).expanduser().resolve() / SKILL_NAME
    else:
        root = Path.cwd() / "artifacts" / SKILL_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _meta_path() -> Path:
    return _artifacts_root() / META_NAME


def _load_meta() -> dict[str, Any] | None:
    path = _meta_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_meta(meta: dict[str, Any]) -> None:
    path = _meta_path()
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    status = payload.get("ok", True)
    prefix = "OK" if status else "ERROR"
    print(f"[{prefix}] {payload.get('action', 'result')}")
    for key, value in payload.items():
        if key in {"ok", "action"}:
            continue
        print(f"  {key}: {value}")


def _screen_size() -> tuple[int, int]:
    _ensure_deps()
    import pyautogui as pag

    w, h = pag.size()
    return int(w), int(h)


def _cursor_position() -> tuple[int, int]:
    _ensure_deps()
    import pyautogui as pag

    x, y = pag.position()
    return int(x), int(y)


def _to_screen(x: float, y: float, coord_space: str) -> tuple[int, int]:
    if coord_space == "screen":
        return int(round(x)), int(round(y))

    meta = _load_meta()
    if not meta:
        raise RuntimeError(
            "No screenshot metadata found. Run `screenshot` first, or pass --coord-space screen."
        )
    scale = float(meta.get("scale") or 1.0)
    if scale <= 0:
        scale = 1.0
    return int(round(x / scale)), int(round(y / scale))


def _capture_raw_png(path: Path) -> None:
    system = platform.system()
    path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    if system == "Darwin" and shutil.which("screencapture"):
        # -x: no shutter sound
        result = subprocess.run(
            ["screencapture", "-x", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and path.is_file() and path.stat().st_size > 0:
            return
        errors.append(f"screencapture: {result.stderr.strip() or result.stdout.strip() or result.returncode}")
        path.unlink(missing_ok=True)

    if system == "Linux":
        for cmd in (
            ["gnome-screenshot", "-f", str(path)],
            ["scrot", str(path)],
            ["import", "-window", "root", str(path)],
        ):
            if not shutil.which(cmd[0]):
                continue
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and path.is_file() and path.stat().st_size > 0:
                return
            errors.append(f"{cmd[0]}: {result.stderr.strip() or result.returncode}")
            path.unlink(missing_ok=True)

    # Fallback: pyautogui / Pillow grab (also needs Screen Recording on macOS)
    try:
        _ensure_deps()
        import pyautogui as pag

        img = pag.screenshot()
        img.save(str(path))
        if path.is_file() and path.stat().st_size > 0:
            return
        errors.append("pyautogui.screenshot: empty file")
    except Exception as e:
        errors.append(f"pyautogui.screenshot: {e}")

    hint = ""
    if system == "Darwin":
        hint = (
            " Grant Screen Recording (and Accessibility for clicks) to the "
            "terminal/IDE running this script."
        )
    raise RuntimeError("screenshot failed: " + "; ".join(errors) + "." + hint)


def _resize_for_agent(src: Path, dst: Path, max_width: int) -> dict[str, Any]:
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        scale = 1.0
        if max_width > 0 and sw > max_width:
            scale = max_width / float(sw)
            nh = max(1, int(round(sh * scale)))
            im = im.resize((max_width, nh), Image.Resampling.LANCZOS)
        iw, ih = im.size
        im.save(dst, format="PNG", optimize=True)

    screen_w, screen_h = _screen_size()
    # Prefer actual pixel ratio from image if grab matched screen; else use resize scale.
    # When screencapture is retina, image pixels may be 2x logical pyautogui coords.
    if sw > 0 and sh > 0:
        # Map image coords → logical screen (pyautogui) coords.
        # image_x * (screen_w / image_w) after resize:
        #   scale_img = iw/sw; logical = image_x / scale_img * (screen_w/sw)
        # Simplify: factor from saved image to logical screen.
        scale_x = screen_w / float(iw)
        scale_y = screen_h / float(ih)
        # Use uniform scale (average) only if close; else store both.
        scale_out = (scale_x + scale_y) / 2.0
    else:
        scale_out = 1.0 / scale if scale else 1.0
        scale_x = scale_y = scale_out

    return {
        "screen_width": screen_w,
        "screen_height": screen_h,
        "raw_width": sw,
        "raw_height": sh,
        "image_width": iw,
        "image_height": ih,
        "scale": 1.0 / scale_out if scale_out else 1.0,  # image → multiply? see _to_screen
        "scale_x": 1.0 / scale_x if scale_x else 1.0,
        "scale_y": 1.0 / scale_y if scale_y else 1.0,
    }


def cmd_doctor(as_json: bool) -> int:
    system = platform.system()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("os", True, f"{system} {platform.release()}")
    add("python", True, sys.version.split()[0])

    try:
        import PIL

        add("pillow", True, getattr(PIL, "__version__", "ok"))
    except ImportError as e:
        add("pillow", False, str(e))

    try:
        _ensure_deps()
        import pyautogui as pag

        add("pyautogui", True, getattr(pag, "__version__", "ok"))
        w, h = pag.size()
        add("display", True, f"{w}x{h}")
        x, y = pag.position()
        add("cursor", True, f"({x}, {y})")
    except Exception as e:
        add("pyautogui", False, str(e))
        add(
            "accessibility",
            False,
            "On macOS: System Settings → Privacy & Security → Accessibility "
            "— enable the terminal/IDE running this script.",
        )

    if system == "Darwin":
        add("screencapture", bool(shutil.which("screencapture")), shutil.which("screencapture") or "missing")
        tmp = Path(tempfile.gettempdir()) / f"cu-doctor-{os.getpid()}.png"
        try:
            _capture_raw_png(tmp)
            add("screen_capture", tmp.is_file() and tmp.stat().st_size > 0, "ok")
        except Exception as e:
            add(
                "screen_capture",
                False,
                f"{e}",
            )
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
    else:
        grabbers = [c for c in ("gnome-screenshot", "scrot", "import") if shutil.which(c)]
        add("linux_grabber", bool(grabbers), ", ".join(grabbers) or "none (will use pyautogui)")
        tmp = Path(tempfile.gettempdir()) / f"cu-doctor-{os.getpid()}.png"
        try:
            _capture_raw_png(tmp)
            add("screen_capture", tmp.is_file() and tmp.stat().st_size > 0, "ok")
        except Exception as e:
            add("screen_capture", False, str(e))
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
    out_dir = _artifacts_root()
    add("artifacts_dir", out_dir.is_dir(), str(out_dir))

    ok = all(c["ok"] for c in checks if c["name"] in {"pillow", "pyautogui", "display"})
    payload = {"ok": ok, "action": "doctor", "checks": checks}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("[doctor] computer-use environment")
        for c in checks:
            mark = "✓" if c["ok"] else "✗"
            print(f"  {mark} {c['name']}: {c['detail']}")
        if not ok:
            print(
                "\nFix failing checks, then re-run doctor. "
                "macOS needs Accessibility + Screen Recording for the host app."
            )
    return 0 if ok else 1


def cmd_info(as_json: bool) -> int:
    _ensure_deps()
    sw, sh = _screen_size()
    cx, cy = _cursor_position()
    meta = _load_meta()
    payload = {
        "ok": True,
        "action": "info",
        "screen_width": sw,
        "screen_height": sh,
        "cursor_x": cx,
        "cursor_y": cy,
        "platform": platform.system(),
        "last_screenshot": meta,
    }
    _emit(payload, as_json=as_json)
    return 0


def cmd_screenshot(out: str | None, max_width: int, as_json: bool) -> int:
    root = _artifacts_root()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = root / f"raw_{ts}.png"
    if out:
        final = Path(out).expanduser().resolve()
    else:
        final = root / f"shot_{ts}.png"
    final.parent.mkdir(parents=True, exist_ok=True)

    _capture_raw_png(raw)
    dims = _resize_for_agent(raw, final, max_width)
    # Prefer deleting raw to save disk; keep if CU_KEEP_RAW=1
    if os.environ.get("CU_KEEP_RAW") != "1":
        raw.unlink(missing_ok=True)

    # scale in meta: image_coord * (1/scale) was wrong naming earlier.
    # _to_screen for image space: screen = image / scale_factor where
    # scale_factor = image_w/screen_w (how much we shrunk logically).
    # Store scale so: screen = image / scale  with scale = image_w/screen_w
    scale = dims["image_width"] / float(dims["screen_width"]) if dims["screen_width"] else 1.0
    meta = {
        "path": str(final),
        "created_at": ts,
        "max_width": max_width,
        "screen_width": dims["screen_width"],
        "screen_height": dims["screen_height"],
        "image_width": dims["image_width"],
        "image_height": dims["image_height"],
        "scale": scale,
        "coord_space": "image",
        "note": "click/move/drag default to image coords: screen = image / scale",
    }
    _save_meta(meta)
    payload = {"ok": True, "action": "screenshot", **meta}
    _emit(payload, as_json=as_json)
    return 0


def cmd_click(
    x: float,
    y: float,
    button: str,
    clicks: int,
    coord_space: str,
    as_json: bool,
) -> int:
    _ensure_deps()
    import pyautogui as pag

    sx, sy = _to_screen(x, y, coord_space)
    pag.click(sx, sy, clicks=clicks, button=button)
    payload = {
        "ok": True,
        "action": "click",
        "button": button,
        "clicks": clicks,
        "coord_space": coord_space,
        "input_x": x,
        "input_y": y,
        "screen_x": sx,
        "screen_y": sy,
    }
    _emit(payload, as_json=as_json)
    return 0


def cmd_move(x: float, y: float, coord_space: str, as_json: bool) -> int:
    _ensure_deps()
    import pyautogui as pag

    sx, sy = _to_screen(x, y, coord_space)
    pag.moveTo(sx, sy)
    payload = {
        "ok": True,
        "action": "move",
        "coord_space": coord_space,
        "input_x": x,
        "input_y": y,
        "screen_x": sx,
        "screen_y": sy,
    }
    _emit(payload, as_json=as_json)
    return 0


def cmd_drag(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    coord_space: str,
    as_json: bool,
) -> int:
    _ensure_deps()
    import pyautogui as pag

    a, b = _to_screen(x1, y1, coord_space)
    c, d = _to_screen(x2, y2, coord_space)
    pag.moveTo(a, b)
    pag.dragTo(c, d, duration=0.3, button="left")
    payload = {
        "ok": True,
        "action": "drag",
        "coord_space": coord_space,
        "from": [a, b],
        "to": [c, d],
    }
    _emit(payload, as_json=as_json)
    return 0


def cmd_type(text: str, as_json: bool) -> int:
    _ensure_deps()
    import pyautogui as pag

    # write() is ASCII-oriented; use clipboard paste for unicode when possible
    if any(ord(ch) > 127 for ch in text):
        try:
            import pyperclip

            prev = None
            try:
                prev = pyperclip.paste()
            except Exception:
                prev = None
            pyperclip.copy(text)
            modifier = "command" if platform.system() == "Darwin" else "ctrl"
            pag.hotkey(modifier, "v")
            time.sleep(0.05)
            if prev is not None:
                try:
                    pyperclip.copy(prev)
                except Exception:
                    pass
        except ImportError:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "pyperclip", "-q"],
                stdout=subprocess.DEVNULL,
            )
            return cmd_type(text, as_json)
    else:
        pag.write(text, interval=0.02)

    payload = {"ok": True, "action": "type", "length": len(text)}
    _emit(payload, as_json=as_json)
    return 0


def _normalize_keys(spec: str) -> list[str]:
    """Parse 'cmd+shift+s' / 'ctrl+c' / 'enter' into pyautogui key names."""
    aliases = {
        "cmd": "command",
        "command": "command",
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "option": "alt",
        "shift": "shift",
        "enter": "enter",
        "return": "enter",
        "esc": "esc",
        "escape": "esc",
        "space": "space",
        "tab": "tab",
        "backspace": "backspace",
        "delete": "delete",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
    }
    parts = [p.strip().lower() for p in spec.replace("-", "+").split("+") if p.strip()]
    return [aliases.get(p, p) for p in parts]


def cmd_key(spec: str, repeat: int, as_json: bool) -> int:
    _ensure_deps()
    import pyautogui as pag

    keys = _normalize_keys(spec)
    if not keys:
        raise RuntimeError("Empty key spec")
    for _ in range(max(1, repeat)):
        if len(keys) == 1:
            pag.press(keys[0])
        else:
            pag.hotkey(*keys)
    payload = {"ok": True, "action": "key", "keys": keys, "repeat": repeat}
    _emit(payload, as_json=as_json)
    return 0


def cmd_scroll(
    x: float | None,
    y: float | None,
    clicks: int,
    direction: str,
    coord_space: str,
    as_json: bool,
) -> int:
    _ensure_deps()
    import pyautogui as pag

    amount = abs(int(clicks))
    if direction == "down":
        amount = -amount
    if x is not None and y is not None:
        sx, sy = _to_screen(x, y, coord_space)
        pag.moveTo(sx, sy)
    else:
        sx, sy = _cursor_position()
    pag.scroll(amount)
    payload = {
        "ok": True,
        "action": "scroll",
        "amount": amount,
        "direction": direction,
        "screen_x": sx,
        "screen_y": sy,
    }
    _emit(payload, as_json=as_json)
    return 0


def cmd_wait(seconds: float, as_json: bool) -> int:
    time.sleep(max(0.0, seconds))
    _emit({"ok": True, "action": "wait", "seconds": seconds}, as_json=as_json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="computer_use.py",
        description="Desktop GUI control for the computer-use skill",
    )
    # Accept --json before or after the subcommand.
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    sub = p.add_subparsers(dest="command", required=True)

    def add_json(sp_: argparse.ArgumentParser) -> None:
        sp_.add_argument(
            "--json",
            action="store_true",
            default=False,
            help="Emit machine-readable JSON",
        )

    def add_coord(sp_: argparse.ArgumentParser) -> None:
        sp_.add_argument(
            "--coord-space",
            choices=("image", "screen"),
            default=os.environ.get("CU_COORD_SPACE", "image"),
            help="image=coords in last screenshot pixels (default); screen=real pixels",
        )

    sp = sub.add_parser("doctor", help="Check OS permissions and dependencies")
    add_json(sp)

    sp = sub.add_parser("info", help="Show screen size and cursor position")
    add_json(sp)

    sp = sub.add_parser("screenshot", help="Capture screen and save PNG")
    add_json(sp)
    sp.add_argument("--out", help="Output PNG path")
    sp.add_argument(
        "--max-width",
        type=int,
        default=int(os.environ.get("CU_MAX_WIDTH", DEFAULT_MAX_WIDTH)),
        help=f"Max image width for agent view (default {DEFAULT_MAX_WIDTH})",
    )

    sp = sub.add_parser("click", help="Click at coordinates")
    add_json(sp)
    sp.add_argument("x", type=float)
    sp.add_argument("y", type=float)
    sp.add_argument("--button", choices=("left", "right", "middle"), default="left")
    sp.add_argument("--clicks", type=int, default=1)
    add_coord(sp)

    sp = sub.add_parser("double-click", help="Double-click at coordinates")
    add_json(sp)
    sp.add_argument("x", type=float)
    sp.add_argument("y", type=float)
    add_coord(sp)

    sp = sub.add_parser("move", help="Move mouse")
    add_json(sp)
    sp.add_argument("x", type=float)
    sp.add_argument("y", type=float)
    add_coord(sp)

    sp = sub.add_parser("drag", help="Drag from (x1,y1) to (x2,y2)")
    add_json(sp)
    sp.add_argument("x1", type=float)
    sp.add_argument("y1", type=float)
    sp.add_argument("x2", type=float)
    sp.add_argument("y2", type=float)
    add_coord(sp)

    sp = sub.add_parser("type", help="Type text (unicode via clipboard paste)")
    add_json(sp)
    sp.add_argument("text")

    sp = sub.add_parser("key", help="Press key or hotkey (e.g. enter, cmd+c)")
    add_json(sp)
    sp.add_argument("spec")
    sp.add_argument("--repeat", type=int, default=1)

    sp = sub.add_parser("scroll", help="Scroll at cursor or position")
    add_json(sp)
    sp.add_argument("--x", type=float, default=None)
    sp.add_argument("--y", type=float, default=None)
    sp.add_argument("--clicks", type=int, default=3)
    sp.add_argument("--direction", choices=("up", "down"), default="down")
    add_coord(sp)

    sp = sub.add_parser("wait", help="Sleep N seconds")
    add_json(sp)
    sp.add_argument("seconds", type=float)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Parent and/or subcommand --json
    as_json = bool(getattr(args, "json", False))

    try:
        if args.command == "doctor":
            return cmd_doctor(as_json)
        if args.command == "info":
            return cmd_info(as_json)
        if args.command == "screenshot":
            return cmd_screenshot(args.out, args.max_width, as_json)
        if args.command == "click":
            return cmd_click(args.x, args.y, args.button, args.clicks, args.coord_space, as_json)
        if args.command == "double-click":
            return cmd_click(args.x, args.y, "left", 2, args.coord_space, as_json)
        if args.command == "move":
            return cmd_move(args.x, args.y, args.coord_space, as_json)
        if args.command == "drag":
            return cmd_drag(args.x1, args.y1, args.x2, args.y2, args.coord_space, as_json)
        if args.command == "type":
            return cmd_type(args.text, as_json)
        if args.command == "key":
            return cmd_key(args.spec, args.repeat, as_json)
        if args.command == "scroll":
            return cmd_scroll(args.x, args.y, args.clicks, args.direction, args.coord_space, as_json)
        if args.command == "wait":
            return cmd_wait(args.seconds, as_json)
        parser.error(f"unknown command: {args.command}")
        return 2
    except Exception as e:
        payload = {"ok": False, "action": args.command, "error": str(e)}
        if as_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"[ERROR] {args.command}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
