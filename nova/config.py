"""Loads config.yaml and .env into one object."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    """Tiny .env loader (no extra dependency)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Config(dict):
    """dict with attribute access: cfg.llm.ollama.model"""

    def __getattr__(self, item):
        try:
            value = self[item]
        except KeyError as e:
            raise AttributeError(item) from e
        return Config(value) if isinstance(value, dict) else value


def load_config(path: str | Path | None = None) -> Config:
    _load_env(ROOT / ".env")
    if path:
        path = Path(path)
    else:
        path = ROOT / "config.yaml"
        if not path.exists():                 # fresh clone: fall back to the template
            path = ROOT / "config.example.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(data)


def resolve(p: str) -> Path:
    """Resolve a config path: ~ expands, relative paths are relative to the project root."""
    path = Path(os.path.expanduser(p))
    return path if path.is_absolute() else (ROOT / path).resolve()
