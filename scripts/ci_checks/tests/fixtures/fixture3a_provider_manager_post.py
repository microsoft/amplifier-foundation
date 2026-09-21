# Provenance: amplifier-app-cli amplifier_app_cli/provider_manager.py
# ProviderManager.get_provider_config(), at commit 3f3f425 (PR #214 merged, fixed).
# https://github.com/microsoft/amplifier-app-cli/pull/214
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ProviderManager:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def get_provider_config(
        self, provider_id: str, scope: str | None = None
    ) -> dict[str, Any] | None:
        """Get configuration for a specific provider by module ID."""
        if scope is not None:
            providers = self._settings.get_scope_provider_overrides(scope)
        else:
            providers = self._settings.get_provider_overrides()

        logger.debug(f"get_provider_config: found {len(providers)} providers")
        matches: list[dict[str, Any]] = []
        for provider in providers:
            module = provider.get("module")
            logger.debug(
                f"get_provider_config: checking module '{module}' against '{provider_id}'"
            )
            if module == provider_id:
                matches.append(provider)

        if not matches:
            logger.debug(
                f"get_provider_config: no matching provider found for '{provider_id}'"
            )
            return None

        # Multiple instances of the same module can be configured (distinct
        # ids, different priorities). Matching on 'module' alone is
        # ambiguous, so resolve deterministically to the highest-priority
        # (lowest priority number) instance rather than the first in list
        # order.
        def _priority(p: dict[str, Any]) -> int:
            config = p.get("config", {})
            return config.get("priority", 100) if isinstance(config, dict) else 100

        best = min(matches, key=_priority)
        config = best.get("config", {})
        logger.debug(
            f"get_provider_config: found matching config with keys: {list(config.keys())}"
        )
        return config
