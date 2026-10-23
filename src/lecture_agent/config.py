from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

PROVIDERS = ("gemini", "ollama", "anthropic")
DEFAULT_MODELS = {
    "gemini": "gemini-flash-latest",   # alias for the current Flash model; has a free tier
    "ollama": "llama3.1:8b",           # any model you have pulled with `ollama pull`
    "anthropic": "claude-sonnet-5-5",  # paid
}
DEFAULT_CONDENSE = {"anthropic": "claude-haiku-5-5"}
# input size limits in characters, tuned per provider (local models have small context windows)
DEFAULT_LIMITS = {
    "gemini": (400_000, 60_000),
    "anthropic": (400_000, 60_000),
    "ollama": (24_000, 8_000),
}


@dataclass
class Config:
    inbox: Path
    state_file: Path
    debounce_seconds: int
    provider: str
    model: str
    condense_model: str
    vision: bool
    llm_base_url: str | None
    max_tokens: int
    max_input_chars: int
    chunk_chars: int
    obsidian_enabled: bool
    obsidian_subfolder: str
    link_existing_notes: bool
    vault_path: Path | None
    notion_enabled: bool
    figures_enabled: bool = True
    fig_libreoffice: bool = True
    fig_max_candidates: int = 30
    fig_max_in_notes: int = 10
    fig_zoom: float = 1.6
    figures_dir: Path = Path("state/figures")
    references_enabled: bool = True
    ref_method: str = "ddgs"
    ref_from_material: bool = True
    ref_max: int = 6
    ref_max_searches: int = 5
    ref_model: str = ""
    notion_props: dict = field(default_factory=dict)
    notion_token: str | None = None
    notion_database_id: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None


def _path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


def load_config(config_file: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    raw = yaml.safe_load((config_file or ROOT / "config.yaml").read_text()) or {}
    llm = raw.get("llm", {})
    obs = raw.get("obsidian", {})
    notion = raw.get("notion", {})
    figs = raw.get("figures", {})
    refs = raw.get("references", {})

    provider = str(llm.get("provider") or "gemini").lower()
    model = llm.get("model") or DEFAULT_MODELS.get(provider, DEFAULT_MODELS["gemini"])
    default_in, default_chunk = DEFAULT_LIMITS.get(provider, DEFAULT_LIMITS["gemini"])
    vision = llm.get("vision")
    vision = (provider != "ollama") if vision is None else bool(vision)

    vault = os.getenv("OBSIDIAN_VAULT_PATH")
    return Config(
        inbox=_path(raw.get("inbox", "inbox")),
        state_file=_path(raw.get("state_file", "state/state.json")),
        debounce_seconds=int(raw.get("debounce_seconds", 20)),
        provider=provider,
        model=model,
        condense_model=llm.get("condense_model") or DEFAULT_CONDENSE.get(provider) or model,
        vision=vision,
        llm_base_url=llm.get("base_url"),
        max_tokens=int(llm.get("max_tokens", 16000)),
        max_input_chars=int(llm.get("max_input_chars") or default_in),
        chunk_chars=int(llm.get("chunk_chars") or default_chunk),
        obsidian_enabled=bool(obs.get("enabled", True)),
        obsidian_subfolder=obs.get("subfolder", "Uni"),
        link_existing_notes=bool(obs.get("link_existing_notes", True)),
        vault_path=Path(vault).expanduser() if vault else None,
        notion_enabled=bool(notion.get("enabled", True)),
        figures_enabled=bool(figs.get("enabled", True)),
        fig_libreoffice=bool(figs.get("render_with_libreoffice", True)),
        fig_max_candidates=int(figs.get("max_candidates", 30)),
        fig_max_in_notes=int(figs.get("max_in_notes", 10)),
        fig_zoom=float(figs.get("render_zoom", 1.6)),
        figures_dir=_path(figs.get("dir", "state/figures")),
        references_enabled=bool(refs.get("enabled", True)),
        ref_method=str(refs.get("method") or "ddgs").lower(),
        ref_from_material=bool(refs.get("from_material", True)),
        ref_max=int(refs.get("max_references", 6)),
        ref_max_searches=int(refs.get("max_searches", 5)),
        ref_model=refs.get("model") or model,
        notion_props=notion.get("properties", {}),
        notion_token=os.getenv("NOTION_TOKEN"),
        notion_database_id=os.getenv("NOTION_DATABASE_ID"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
    )


def validate(cfg: Config) -> list[str]:
    """Return a list of human readable problems (empty list means OK)."""
    problems = []
    if cfg.provider not in PROVIDERS:
        problems.append(f"llm.provider must be one of {', '.join(PROVIDERS)} (got '{cfg.provider}')")
    if cfg.provider == "gemini" and not cfg.gemini_api_key:
        problems.append("GEMINI_API_KEY is not set (free key: https://aistudio.google.com/apikey)")
    if cfg.provider == "anthropic" and not cfg.anthropic_api_key:
        problems.append("ANTHROPIC_API_KEY is not set")
    if cfg.references_enabled and cfg.ref_method == "anthropic" and cfg.provider != "anthropic":
        problems.append("references.method 'anthropic' needs llm.provider 'anthropic' (use 'ddgs' for free search)")
    if cfg.obsidian_enabled:
        if not cfg.vault_path:
            problems.append("OBSIDIAN_VAULT_PATH is not set (or set obsidian.enabled: false)")
        elif not cfg.vault_path.exists():
            problems.append(f"Obsidian vault path does not exist: {cfg.vault_path}")
    if cfg.notion_enabled:
        if not cfg.notion_token:
            problems.append("NOTION_TOKEN is not set (or set notion.enabled: false)")
        if not cfg.notion_database_id:
            problems.append("NOTION_DATABASE_ID is not set (run `lecture-agent init-notion <page_id>`)")
    if not cfg.obsidian_enabled and not cfg.notion_enabled:
        problems.append("Both outputs are disabled, nothing to do")
    return problems
