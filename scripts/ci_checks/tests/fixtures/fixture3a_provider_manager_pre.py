# Provenance: amplifier-app-cli amplifier_app_cli/provider_manager.py
# ProviderManager.get_provider_config(), at commit 3f3f425^ (before PR #214).
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
        for provider in providers:
            module = provider.get("module")
            logger.debug(
                f"get_provider_config: checking module '{module}' against '{provider_id}'"
            )
            if module == provider_id:
                config = provider.get("config", {})
                logger.debug(
                    f"get_provider_config: found matching config with keys: {list(config.keys())}"
                )
                return config
        logger.debug(
            f"get_provider_config: no matching provider found for '{provider_id}'"
        )
        return None
