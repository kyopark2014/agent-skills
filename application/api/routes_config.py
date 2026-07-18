import json
import logging
import os

from fastapi import APIRouter
from pydantic import BaseModel

try:
    from application import utils
except ImportError:
    import utils

logger = logging.getLogger("routes_config")

router = APIRouter(prefix="/api/config", tags=["config"])

_APPLICATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODELS = [
    "Claude 5.0 Sonnet",
    "Claude 4.6 Sonnet",
    "Claude Fable 5",
    "Claude 4.8 Opus",
    "Claude 4.7 Opus",
    "Claude 4.6 Opus",
    "Claude 4.5 Opus",
    "Claude 4.5 Sonnet",
    "Claude 4.5 Haiku",
    "OpenAI GPT 5.4",
    "OpenAI GPT 5.5",
    "OpenAI GPT 5.6 Sol",
    "OpenAI GPT 5.6 Terra",
    "OpenAI GPT 5.6 Luna",
    "OpenAI OSS 120B",
    "OpenAI OSS 20B",
]

DEFAULT_MODEL = "Claude 4.6 Sonnet"


def load_capability_list(filename: str) -> list[str]:
    path = os.path.join(_APPLICATION_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except FileNotFoundError:
        logger.warning("Capability list not found: %s", path)
        return []


class DefaultsPatch(BaseModel):
    default_skills: list[str] | None = None
    default_mcp_servers: list[str] | None = None


@router.get("")
def get_config():
    config = utils.load_config()
    skill_options = load_capability_list("skills.list")
    mcp_options = load_capability_list("mcp.list")
    default_skills = config.get("default_skills") or []
    default_mcp = config.get("default_mcp_servers") or [
        "web_fetch",
        "websearch",
        "aws documentation",
        "korea_weather",
    ]
    default_skills = [s for s in default_skills if s in skill_options]
    default_mcp = [m for m in default_mcp if m in mcp_options]
    if not default_skills and "skill-creator" in skill_options:
        default_skills = ["skill-creator"]
    return {
        "projectName": config.get("projectName", "agent"),
        "skills": skill_options,
        "mcp_servers": mcp_options,
        "models": MODELS,
        "default_model": DEFAULT_MODEL,
        "default_skills": default_skills,
        "default_mcp_servers": default_mcp,
    }


@router.patch("/defaults")
def patch_defaults(body: DefaultsPatch):
    config = utils.load_config()
    if body.default_skills is not None:
        config["default_skills"] = body.default_skills
    if body.default_mcp_servers is not None:
        config["default_mcp_servers"] = body.default_mcp_servers
    with open(utils.config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)
    return {"ok": True}
