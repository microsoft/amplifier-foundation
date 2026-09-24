"""Opt-in provider preparation outcomes for application-owned failure policy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderPreparationFailure:
    """Private diagnostic input. The spec and exception may contain credentials."""

    provider_spec: dict[str, Any]
    agent_name: str | None
    phase: str
    error: Exception


ProviderFailurePolicy = Callable[[ProviderPreparationFailure], Awaitable[None]]


class ProviderPreparation:
    """Retain source identity while handling only explicitly opted-in providers."""

    def __init__(self, policy: ProviderFailurePolicy):
        self.policy = policy
        self.specs: list[tuple[dict, str | None]] = []
        self.resolved: list[tuple[dict, dict, str | None]] = []
        self.failures: list[ProviderPreparationFailure] = []
        self.failed_sources: dict[tuple[str, str], Exception] = {}
        self.source_paths: dict[tuple[str, str], Path] = {}

    async def failed(self, spec, agent_name, phase, error, resolved=None):
        failure = ProviderPreparationFailure(deepcopy(spec), agent_name, phase, error)
        # Raising from host policy aborts; returning accepts this preparation
        # failure, without silently removing the configured entry.
        await self.policy(
            ProviderPreparationFailure(deepcopy(spec), agent_name, phase, error)
        )
        self.failures.append(failure)
        for source in (spec, resolved or spec):
            self.failed_sources[(source["module"], source["source"])] = error

    async def resolve(self, source_resolver):
        for spec, agent_name in self.specs:
            if not spec.get("module") or not spec.get("source"):
                continue  # Match activate_all's handling of incomplete rows.
            try:
                resolved = source_resolver(deepcopy(spec))
            except Exception as error:  # noqa: BLE001 - host policy owns module failures
                await self.failed(spec, agent_name, "source_resolution", error)
            else:
                self.resolved.append((spec, resolved, agent_name))
        return [resolved for _, resolved, _ in self.resolved]

    async def activate(self, activator, progress_callback=None):
        keys = list(
            dict.fromkeys((row["module"], row["source"]) for _, row, _ in self.resolved)
        )
        results = await asyncio.gather(
            *[
                activator.activate(module, source, progress_callback=progress_callback)
                for module, source in keys
            ],
            return_exceptions=True,
        )
        by_source = dict(zip(keys, results))
        paths = {}
        for spec, resolved, agent_name in self.resolved:
            result = by_source[(resolved["module"], resolved["source"])]
            if isinstance(result, Exception):
                await self.failed(spec, agent_name, "activation", result, resolved)
            elif isinstance(result, BaseException):
                raise result  # Never accept cancellation or process termination.
            else:
                paths[spec["module"]] = result
                for source in (spec, resolved):
                    self.source_paths[(source["module"], source["source"])] = result
        return paths
