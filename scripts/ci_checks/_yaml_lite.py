"""Minimal stdlib-only YAML subset loader for resolver_priority_tripwire's
config file: flat scalar/list keys only, no nested mappings. Avoids a
third-party PyYAML dependency for a handful of simple override keys.
"""

from __future__ import annotations

from pathlib import Path


def load_simple_yaml(text: str) -> dict:
    data: dict = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line[:1].isspace() and current and line.strip().startswith("-"):
            data.setdefault(current, []).append(line.strip()[1:].strip().strip("\"'"))
            continue
        if ":" not in line:
            continue
        key, _, value = (p.strip() for p in line.partition(":"))
        if not value:
            current, data[key] = key, data.get(key, [])
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [
                v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()
            ]
            current = None
        else:
            data[key], current = value.strip("\"'"), None
    return data


def load_config(repo_root: Path, config_filename: str, defaults: dict) -> dict:
    cfg = {k: list(v) if isinstance(v, list) else v for k, v in defaults.items()}
    path = repo_root / config_filename
    if path.exists():
        cfg.update(load_simple_yaml(path.read_text(encoding="utf-8")))
    return cfg
