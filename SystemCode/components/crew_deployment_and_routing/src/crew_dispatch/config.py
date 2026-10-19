"""Configuration loading (YAML + deep-merge overrides) and component-root resolution."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

COMPONENT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = COMPONENT_ROOT / "config" / "default.yaml"


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return deep_merge(cfg, overrides)


def resolve(cfg_path: str | Path) -> Path:
    p = Path(cfg_path)
    return p if p.is_absolute() else COMPONENT_ROOT / p


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]
