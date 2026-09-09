"""Knowledge-base loader/search for mac-operator tips (macos-automator-mcp compatible)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
CODE_BLOCK_RE = re.compile(
    r"```(?:applescript|javascript|js)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
INPUT_DATA_RE = re.compile(r"\$\{inputData\.([A-Za-z0-9_]+)\}|--MCP_INPUT:([A-Za-z0-9_]+)")
ARG_RE = re.compile(r"\$\{arguments\[(\d+)\]\}|--MCP_ARG_(\d+)")


@dataclass
class Tip:
    id: str
    title: str
    category: str
    description: str
    language: str
    keywords: list[str] = field(default_factory=list)
    notes: str = ""
    script: str = ""
    path: str = ""
    arguments_prompt: str = ""

    def searchable_text(self) -> str:
        parts = [
            self.id,
            self.title,
            self.description,
            self.notes,
            " ".join(self.keywords),
            self.script,
            self.category,
        ]
        return " ".join(p for p in parts if p).lower()


@dataclass
class Category:
    id: str
    title: str
    description: str
    tip_count: int = 0


class KnowledgeBase:
    def __init__(self, roots: list[Path]):
        self.roots = roots
        self.tips: dict[str, Tip] = {}
        self.categories: dict[str, Category] = {}
        self._loaded = False

    def load(self, force: bool = False) -> None:
        if self._loaded and not force:
            return
        self.tips.clear()
        self.categories.clear()
        for root in self.roots:
            if not root.is_dir():
                continue
            self._load_root(root)
        self._loaded = True

    def _load_root(self, root: Path) -> None:
        for category_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if category_dir.name.startswith("_"):
                continue
            cat_id = category_dir.name
            cat_title = cat_id
            cat_desc = ""
            info_path = category_dir / "_category_info.md"
            if info_path.is_file():
                meta, _ = _parse_markdown(info_path.read_text(encoding="utf-8"))
                cat_title = str(meta.get("title") or cat_id)
                cat_desc = str(meta.get("description") or "")
                cat_id = str(meta.get("category") or cat_id)

            tip_files = list(category_dir.rglob("*.md"))
            tip_files = [p for p in tip_files if p.name != "_category_info.md"]
            for tip_path in tip_files:
                tip = _parse_tip(tip_path, default_category=cat_id)
                if tip:
                    self.tips[tip.id] = tip

            self.categories[cat_id] = Category(
                id=cat_id,
                title=cat_title,
                description=cat_desc,
                tip_count=0,
            )

        for cat in self.categories.values():
            cat.tip_count = sum(1 for t in self.tips.values() if t.category == cat.id)

    def list_categories(self) -> list[Category]:
        self.load()
        return sorted(self.categories.values(), key=lambda c: c.id)

    def get_tip(self, tip_id: str) -> Optional[Tip]:
        self.load()
        return self.tips.get(tip_id)

    def search(
        self,
        *,
        search_term: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> list[Tip]:
        self.load()
        tips = list(self.tips.values())
        if category:
            tips = [t for t in tips if t.category == category or t.category.endswith(category)]

        if search_term:
            tokens = [t for t in re.split(r"\s+", search_term.strip().lower()) if t]
            scored: list[tuple[int, Tip]] = []
            for tip in tips:
                hay = tip.searchable_text()
                score = 0
                for token in tokens:
                    if token in tip.id.lower():
                        score += 8
                    if token in tip.title.lower():
                        score += 6
                    if any(token in kw.lower() for kw in tip.keywords):
                        score += 5
                    if token in hay:
                        score += 1
                if score > 0:
                    scored.append((score, tip))
            scored.sort(key=lambda x: (-x[0], x[1].id))
            tips = [t for _, t in scored]
        else:
            tips.sort(key=lambda t: t.id)

        return tips[: max(1, int(limit))]


def _parse_markdown(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text.strip())
    if not match:
        return {}, text
    meta_raw, body = match.group(1), match.group(2)
    try:
        meta = yaml.safe_load(meta_raw) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:  # noqa: BLE001
        meta = {}
    return meta, body


def _parse_tip(path: Path, default_category: str) -> Optional[Tip]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_markdown(text)
    tip_id = str(meta.get("id") or path.stem).strip()
    if not tip_id:
        return None

    language = str(meta.get("language") or "applescript").lower()
    if language in {"js", "jxa"}:
        language = "javascript"

    script = ""
    code_match = CODE_BLOCK_RE.search(body)
    if code_match:
        script = code_match.group(1).strip()

    keywords = meta.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [str(k) for k in keywords]

    return Tip(
        id=tip_id,
        title=str(meta.get("title") or tip_id),
        category=str(meta.get("category") or default_category),
        description=str(meta.get("description") or "").strip(),
        language=language if language in {"applescript", "javascript"} else "applescript",
        keywords=keywords,
        notes=str(meta.get("notes") or "").strip(),
        script=script,
        path=str(path),
        arguments_prompt=str(meta.get("argumentsPrompt") or meta.get("arguments_prompt") or ""),
    )


def substitute_placeholders(
    script: str,
    *,
    input_data: Optional[dict[str, Any]] = None,
    arguments: Optional[list[str]] = None,
) -> tuple[str, list[str]]:
    """Replace ${inputData.x} / --MCP_INPUT:x and ${arguments[N]} / --MCP_ARG_N."""
    logs: list[str] = []
    input_data = input_data or {}
    arguments = arguments or []

    # Normalize camelCase keys so folderName matches folder_name
    normalized: dict[str, Any] = {}
    for key, value in input_data.items():
        normalized[key] = value
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
        camel = "".join(
            part.capitalize() if i else part
            for i, part in enumerate(re.split(r"[_\-]+", key))
            if part
        )
        normalized.setdefault(snake, value)
        normalized.setdefault(camel, value)
        # folder_name -> folderName
        parts = re.split(r"[_\-]+", key)
        if len(parts) > 1:
            camel2 = parts[0] + "".join(p.capitalize() for p in parts[1:] if p)
            normalized.setdefault(camel2, value)

    def replace_input(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        if key in normalized:
            logs.append(f"inputData.{key} -> provided")
            return str(normalized[key])
        logs.append(f"inputData.{key} -> MISSING (left empty)")
        return ""

    def replace_arg(match: re.Match[str]) -> str:
        idx = int(match.group(1) or match.group(2))
        if 0 <= idx < len(arguments):
            logs.append(f"arguments[{idx}] -> provided")
            return arguments[idx]
        logs.append(f"arguments[{idx}] -> MISSING (left empty)")
        return ""

    out = INPUT_DATA_RE.sub(replace_input, script)
    out = ARG_RE.sub(replace_arg, out)
    return out, logs


def default_kb_roots(skill_dir: Path) -> list[Path]:
    roots = [skill_dir / "knowledge_base"]
    local = os.environ.get("LOCAL_KB_PATH") or os.path.expanduser("~/.macos-automator/knowledge_base")
    local_path = Path(os.path.expanduser(local))
    if local_path.is_dir() and local_path.resolve() != roots[0].resolve():
        roots.append(local_path)
    return roots


def format_tips_markdown(tips: list[Tip]) -> str:
    if not tips:
        return "No matching tips found."
    blocks: list[str] = []
    for tip in tips:
        lines = [
            f"## {tip.title}",
            f"- **id**: `{tip.id}`",
            f"- **category**: `{tip.category}`",
            f"- **language**: `{tip.language}`",
        ]
        if tip.keywords:
            lines.append(f"- **keywords**: {', '.join(tip.keywords)}")
        if tip.description:
            lines.append(f"- **description**: {tip.description}")
        if tip.notes:
            lines.append(f"- **notes**: {tip.notes}")
        if tip.arguments_prompt:
            lines.append(f"- **inputs**: {tip.arguments_prompt}")
        if tip.script:
            fence = "javascript" if tip.language == "javascript" else "applescript"
            lines.append("")
            lines.append(f"```{fence}")
            lines.append(tip.script)
            lines.append("```")
            lines.append("")
            lines.append(f"Run with `execute_script` using `kb_script_id=\"{tip.id}\"`.")
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)


def format_categories_markdown(categories: list[Category]) -> str:
    if not categories:
        return "No categories found."
    lines = ["# Knowledge base categories", ""]
    for cat in categories:
        lines.append(f"- `{cat.id}` — **{cat.title}** ({cat.tip_count} tips)")
        if cat.description:
            lines.append(f"  {cat.description}")
    lines.append("")
    lines.append("Search with `get_scripting_tips(search_term=..., category=...)`.")
    return "\n".join(lines)
