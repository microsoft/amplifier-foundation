"""YAML file reading and writing utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .files import read_with_retry
from .files import write_with_retry


async def read_yaml(path: Path) -> dict[str, Any] | None:
    """Read YAML file and return parsed content.

    Args:
        path: Path to YAML file.

    Returns:
        Parsed YAML content as dict, or None if file doesn't exist.

    Raises:
        yaml.YAMLError: If file contains invalid YAML.
        OSError: If file can't be read.
    """
    if not path.exists():
        return None

    content = await read_with_retry(path)
    return yaml.safe_load(content) or {}


async def write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write dict to YAML file.

    Args:
        path: Path to YAML file.
        data: Dict to write.

    Raises:
        OSError: If file can't be written.
    """
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    await write_with_retry(path, content)
