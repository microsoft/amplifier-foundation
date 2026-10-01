"""Reuse a completed install only inside one isolated preparation attempt.

Every new attempt refreshes each selected source at least once. A duplicate
may skip resolution only when the source, explicit policy and complete installed
package metadata still match. Any intervening graph change forces a fresh install.
This is not a standing installed-version shortcut or a dependency pin.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import sys
from pathlib import Path


def graph_signature():
    rows = []
    for dist in importlib.metadata.distributions():
        rows.append(
            [
                dist.metadata["Name"],
                dist.version,
                dist.read_text("METADATA"),
                dist.read_text("direct_url.json"),
            ]
        )
    return hashlib.sha256(json.dumps(sorted(rows), sort_keys=True).encode()).hexdigest()


def source_signature(root):
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(
            part in {".git", ".venv", "node_modules", "__pycache__", "build", "dist"}
            or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()):
                # External build inputs can change independently. Unknown
                # content must force installation, not a reusable receipt.
                raise ValueError("External source symlink cannot be qualified")
            digest.update(str(relative).encode() + b"\0" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(str(relative).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


class PreparationReceipt:
    def __init__(self, activator, source):
        self.source = Path(source)
        token = os.environ.get("AMPLIFIER_INSTALL_PREPARATION", "")
        # An external install target cannot be qualified by this process's graph.
        self.path = None
        if (
            activator.refresh_dependencies
            and re.fullmatch("[a-f0-9]{32}", token)
            and os.path.abspath(activator.install_python)
            == os.path.abspath(sys.executable)
        ):
            policy = [
                Path(value).read_bytes().hex() if value else None
                for value in (
                    activator.install_constraints,
                    activator.install_overrides,
                )
            ]
            self.policy = hashlib.sha256(json.dumps(policy).encode()).hexdigest()
            key = hashlib.sha256(str(self.source.absolute()).encode()).hexdigest()
            self.path = (
                activator.cache_dir / "install-preparations" / token / (key + ".json")
            )

    def evidence(self):
        return {
            "source": source_signature(self.source),
            "policy": self.policy,
            "graph": graph_signature(),
        }

    def reusable(self):
        if not self.path:
            return False
        try:
            return json.loads(self.path.read_text()) == self.evidence()
        except (OSError, ValueError, TypeError):
            return False

    def record(self):
        if self.path:
            from amplifier_foundation.settings import atomic_write

            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self.path, json.dumps(self.evidence()), private=True)
            except (OSError, ValueError, TypeError):
                pass  # Reuse is optional; later qualification remains authoritative.
