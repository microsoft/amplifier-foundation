"""DOT diagram generators for bundles and agents.

Public API:

- :func:`bundle_to_dot` — Single bundle/behavior → composition DAG
- :func:`bundle_overview_dot` — All bundles/behaviors → overview graph
- :func:`agent_to_dot` — Single agent → composition card
- :func:`agents_topology_dot` — All agents → delegation topology
- :func:`estimate_tokens` — Token count estimation
- :func:`color_tier` — Token count → hex color classification
- :func:`parse_frontmatter` — YAML frontmatter parsing
- :func:`extract_mentions` — @mention extraction from text
- :func:`extract_delegation_targets` — Delegation pattern extraction
- :func:`resolve_local_mention` — @mention file resolution
"""

from .agent_to_dot import agent_to_dot, agents_topology_dot
from .bundle_to_dot import bundle_to_dot, bundle_overview_dot
from .frontmatter import (
    extract_delegation_targets,
    extract_mentions,
    parse_frontmatter,
    resolve_local_mention,
)
from .token_cost import color_tier, estimate_tokens

__all__ = [
    "agent_to_dot",
    "agents_topology_dot",
    "bundle_overview_dot",
    "bundle_to_dot",
    "color_tier",
    "estimate_tokens",
    "extract_delegation_targets",
    "extract_mentions",
    "parse_frontmatter",
    "resolve_local_mention",
]
