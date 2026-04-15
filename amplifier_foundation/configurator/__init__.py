"""SessionConfigurator — per-session bundle configuration manager."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from amplifier_foundation.dicts.navigation import get_nested, set_nested

_log = logging.getLogger(__name__)

# Maps singular provenance category keys (as stored in Bundle._provenance) to the
# plural contribution dict keys used in behaviors_list() output.
_PROV_CATEGORY_MAP: dict[str, str] = {
    "tool": "tools",
    "hook": "hooks",
    "provider": "providers",
    "agent": "agents",
    "context": "context",  # "context" is already the plural form used in contributions
}


def _normalize_module_name(name: str) -> str:
    """Normalize a name for fuzzy matching: lowercase and hyphens to underscores.

    Examples::

        >>> _normalize_module_name("python-check")
        'python_check'
        >>> _normalize_module_name("LSP")
        'lsp'
    """
    return name.lower().replace("-", "_")


def _build_normalized_prov_lookup(
    category: str, provenance: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Build a map from normalized short module names to behavior value lists.

    For each provenance key of the form ``"{category}:{module_id}"``
    (e.g. ``"tool:tool-python-check"`` or ``"hook:hooks-logging"``), this
    extracts the short suffix by stripping the leading category prefix from the
    module ID, normalizes the result (lowercase, hyphens→underscores), and
    stores the mapping ``"python_check" → [behavior_name, ...]``.

    Two prefix forms are tried in order:

    1. **Singular prefix** — ``"{category}-"`` (e.g. ``"tool-"`` for
       ``"tool:tool-bash"``).
    2. **Plural prefix** — ``"{category}s-"`` (e.g. ``"hooks-"`` for
       ``"hook:hooks-logging"``).  Hook modules follow the plural convention
       (``hooks-logging``, ``hooks-python-check``) while tool/provider/agent
       modules use singular.

    This allows ``tools_list()`` and ``hooks_list()`` to match mounted names
    against provenance entries even when they differ in case or separators
    (e.g. ``LSP`` from ``tool-lsp``, ``apply_patch`` from
    ``tool-apply-patch``, ``python-check`` from ``hooks-python-check``).

    Args:
        category: The provenance category prefix, e.g. ``"tool"`` or ``"hook"``.
        provenance: The full bundle provenance dict (dict[str, list[str]]).

    Returns:
        Dict mapping normalized short name → list of behavior names.
    """
    result: dict[str, list[str]] = {}
    cat_key_prefix = f"{category}:"  # "tool:" or "hook:"
    singular_prefix = f"{category}-"  # "tool-"
    plural_prefix = f"{category}s-"  # "hooks-" (hook modules use plural)

    for key, behavior_names in provenance.items():
        if not key.startswith(cat_key_prefix):
            continue
        module_id = key[
            len(cat_key_prefix) :
        ]  # e.g. "tool-python-check", "hooks-logging"
        # Strip the redundant category prefix from the module ID.
        # Try singular first (e.g. "tool-bash" → "bash"), then plural
        # (e.g. "hooks-logging" → "logging").
        if module_id.startswith(singular_prefix):
            short = module_id[len(singular_prefix) :]  # e.g. "python-check"
        elif module_id.startswith(plural_prefix):
            short = module_id[len(plural_prefix) :]  # e.g. "logging"
        else:
            short = module_id
        norm_short = _normalize_module_name(short)  # e.g. "python_check"
        result[norm_short] = behavior_names

    return result


def _lookup_prov_behavior(
    name: str,
    category: str,
    provenance: dict[str, list[str]],
    norm_prov_map: dict[str, list[str]],
) -> list[str] | None:
    """Resolve the behavior provenance for a mounted item using progressive matching.

    Tries four strategies in order, returning the first match:

    1. **Exact key** — ``"{category}:{name}"`` (e.g. ``"tool:bash"``)
    2. **Module-prefixed key** — ``"{category}:{category}-{name}"``
       (e.g. ``"tool:tool-bash"``)
    3. **Normalized exact** — lowercase + hyphens→underscores on both sides
       (e.g. ``"LSP"`` → ``"lsp"`` matching ``"tool:tool-lsp"``)
    4. **Normalized prefix containment** — the module's normalized short name
       is a word-boundary prefix of the normalized mounted name
       (e.g. ``"web"`` from ``"tool:tool-web"`` matches ``"web_search"`` /
       ``"web_fetch"``)

    Semantically unrelated names (e.g. ``"load_skill"`` from ``tool-skills``,
    ``"grep"`` / ``"glob"`` from ``tool-search``, or ``"read_file"`` from
    ``tool-filesystem``) will not match any strategy and return ``None``.
    This is correct and expected — those mappings require out-of-band metadata
    that is not available at runtime.

    Args:
        name: The mounted item name (e.g. ``"python_check"``, ``"LSP"``).
        category: The provenance category (e.g. ``"tool"``, ``"hook"``).
        provenance: The full bundle ``_provenance`` dict (dict[str, list[str]]).
        norm_prov_map: Pre-built normalized lookup from
            :func:`_build_normalized_prov_lookup`.

    Returns:
        List of behavior name strings, or ``None`` if no match is found.
    """
    # Strategy 1: exact raw key match
    behavior = provenance.get(f"{category}:{name}")
    if behavior:
        return behavior

    # Strategy 2: exact key with category-prefix on module ID
    behavior = provenance.get(f"{category}:{category}-{name}")
    if behavior:
        return behavior

    # Strategy 3: normalized exact match (case + hyphen/underscore insensitive)
    norm_name = _normalize_module_name(name)
    behavior = norm_prov_map.get(norm_name)
    if behavior:
        return behavior

    # Strategy 4: normalized prefix containment — module name is a word-boundary
    # prefix of the mounted name.  E.g. "web" is a prefix of "web_search".
    # Require an underscore boundary so "bash" doesn't match "bash_extras".
    for module_norm, beh in norm_prov_map.items():
        if norm_name.startswith(module_norm + "_"):
            return beh

    return None


class SessionConfigurator:
    """Manages per-session bundle configuration: stashing, restoring, and overriding
    context, tools, hooks, providers, and agents from a prepared bundle."""

    def __init__(self, session: Any, prepared_bundle: Any) -> None:
        """Initialise the configurator for a session.

        Args:
            session: The active AmplifierSession (or compatible mock).
            prepared_bundle: The PreparedBundle associated with the session.
        """
        self._session = session
        self._coordinator = session.coordinator
        self._bundle = prepared_bundle.bundle
        self._prepared_bundle = prepared_bundle

        # Stash for saved bundle resources — each category maps name → config.
        self._stash: dict[str, dict[str, Any]] = {
            "context": {},
            "tools": {},
            "hooks": {},
            "providers": {},
            "agents": {},
        }

        # Snapshot of hook handlers at construction time.
        self._hook_snapshot: dict[str, Any] = {}
        self._capture_hooks()

        # Module-to-tool and reverse mappings built from the mount-plan tool specs.
        self._module_to_tools: dict[str, list[str]] = {}
        self._tool_to_module: dict[str, str] = {}
        self._build_module_to_tools()

        # Track disabled behaviors and per-session config overrides.
        self._disabled_behaviors: set[str] = set()
        self._config_overrides: dict[str, Any] = {}

        # Capture original session snapshot.
        self.take_snapshot()

    # ------------------------------------------------------------------
    # Hook management
    # ------------------------------------------------------------------

    def _capture_hooks(self) -> None:
        """Populate the hook snapshot from coordinator-provided metadata.

        Tries two sources in order:

        1. The ``hook_metadata`` capability — a ``{name: {"event": event}}`` dict
           registered by ``PreparedBundle.create_session()`` after
           ``session.initialize()`` runs.  This is the authoritative source and
           uses only the public ``list_handlers()`` API, so it contains event
           bindings but **not** handler callables or priorities.

        2. Direct call to ``coordinator.hooks.list_handlers()`` as a fallback for
           sessions not created through ``PreparedBundle`` (e.g. tests, custom
           harnesses).

        Either way the snapshot contains at most ``{name: {"event": event}}``.
        ``hook_disable()`` works in every case — it uses the public
        ``unregister(name)`` API.  ``hook_enable()`` requires a ``"handler"``
        key in the snapshot entry; if one is absent it raises ``RuntimeError``
        with a clear message rather than silently doing nothing.

        Note: accessing ``_handlers`` on a ``RustHookRegistry`` (the real
        coordinator hooks object) raises ``AttributeError`` — it is a PyO3
        object with no ``__dict__``.  This method never touches private
        attributes.
        """
        # --- Primary path: capability registered by PreparedBundle.create_session() ---
        try:
            metadata = self._coordinator.get_capability("hook_metadata")
            if metadata and isinstance(metadata, dict):
                self._hook_snapshot.update(metadata)
                return
        except Exception as exc:  # noqa: BLE001
            _log.debug("hook_metadata capability not available: %s", exc)

        # --- Fallback path: public list_handlers() API ---
        try:
            hook_registry = self._coordinator.hooks
            if hasattr(hook_registry, "list_handlers"):
                for event, names in hook_registry.list_handlers().items():
                    for name in names:
                        if name not in self._hook_snapshot:
                            self._hook_snapshot[name] = {"event": event}
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Could not capture hook metadata from coordinator: %s. "
                "hook_disable() will work; hook_enable() will not be available.",
                exc,
            )

    def _build_module_to_tools(self) -> None:
        """Build _module_to_tools and _tool_to_module from the mount-plan tool specs.

        Iterates tool specs from ``coordinator.config['tools']``, extracts the
        module ID from each spec, then matches it against all currently-mounted
        tool names using four strategies (in order):

        1. **Direct match** — module ID equals the mounted tool name.
        2. **Prefix strip** — strip the ``"tool-"`` prefix from the module ID
           (e.g. ``"tool-bash"`` → ``"bash"``).
        3. **Normalized match** — lowercase + hyphens→underscores on both sides
           (e.g. ``"tool-python-check"`` → ``"python_check"``).
        4. **Prefix containment** — the normalized short name is a word-boundary
           prefix of the normalized mounted name
           (e.g. ``"web"`` from ``"tool-web"`` matches ``"web_search"`` and
           ``"web_fetch"``).

        A ``claimed`` set prevents the same tool name from being assigned to
        more than one module.  The results are stored in:

        - ``self._module_to_tools``: ``{module_id: [tool_name, ...]}``
        - ``self._tool_to_module``: ``{tool_name: module_id}`` (reverse)
        """
        try:
            mounted: dict = self._coordinator.get("tools") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        specs = self._coordinator.config.get("tools", [])
        claimed: set[str] = set()

        for spec in specs:
            if not isinstance(spec, dict):
                continue
            module_id = spec.get("id") or spec.get("module", "")
            if not module_id:
                continue

            matched_tools: list[str] = []

            # Pre-compute normalized forms for matching.
            prefix = "tool-"
            short = (
                module_id[len(prefix) :] if module_id.startswith(prefix) else module_id
            )
            norm_id = _normalize_module_name(module_id)
            norm_short = _normalize_module_name(short)

            for tool_name in mounted:
                if tool_name in claimed:
                    continue

                norm_tool = _normalize_module_name(tool_name)

                # Strategy 1: direct match
                if tool_name == module_id:
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

                # Strategy 2: strip 'tool-' prefix
                if tool_name == short:
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

                # Strategy 3: normalized match (case + hyphen/underscore insensitive)
                if norm_tool == norm_id or norm_tool == norm_short:
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

                # Strategy 4: prefix containment — module short name is a word-boundary
                # prefix of the mounted tool name (e.g. "web" matches "web_search").
                if norm_tool.startswith(norm_short + "_"):
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

            if matched_tools:
                self._module_to_tools[module_id] = matched_tools
                for tool_name in matched_tools:
                    self._tool_to_module[tool_name] = module_id

    def hook_disable(self, name: str) -> None:
        """Hook toggle is not supported in this version.

        Hooks are visible in /config for inspection but cannot be
        disabled/re-enabled at runtime. This requires a core suspend/resume
        API that doesn't exist yet. See the design doc's Future Work section.
        """
        raise NotImplementedError(
            "Hook toggle is not supported in this version. "
            "Hooks are read-only — visible in /config but not toggleable. "
            "A core suspend/resume API is needed for safe hook toggle."
        )

    def hook_enable(self, name: str) -> None:
        """Hook toggle is not supported in this version."""
        raise NotImplementedError(
            "Hook toggle is not supported in this version. "
            "Hooks are read-only — visible in /config but not toggleable. "
            "A core suspend/resume API is needed for safe hook toggle."
        )

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of the current session state.

        Returns a dict with keys: 'context', 'tools', 'hooks', 'providers', 'agents'.
        Each value is {'enabled': [...], 'disabled': [...]}.
        """
        stash = self._stash

        # Context: enabled = live keys in bundle.context, disabled = keys in stash
        context_enabled = list(self._bundle.context.keys())
        context_disabled = list(stash["context"].keys())

        # Agents: enabled = live keys in coordinator.config['agents'], disabled = stash keys
        agents_enabled = list(self._coordinator.config.get("agents", {}).keys())
        agents_disabled = list(stash["agents"].keys())

        # Tools: enabled = module IDs from bundle.tools not in stash, disabled = stash keys
        tools_enabled = [
            mid
            for mod in self._bundle.tools
            if (mid := mod.get("id") or mod.get("module")) and mid not in stash["tools"]
        ]
        tools_disabled = list(stash["tools"].keys())

        # Providers: enabled = module IDs from bundle.providers not in stash, disabled = stash keys
        providers_enabled = [
            mid
            for mod in self._bundle.providers
            if (mid := mod.get("id") or mod.get("module"))
            and mid not in stash["providers"]
        ]
        providers_disabled = list(stash["providers"].keys())

        # Hooks: enabled = names from hook_snapshot not in stash, disabled = stash keys
        hooks_enabled = [
            name for name in self._hook_snapshot if name not in stash["hooks"]
        ]
        hooks_disabled = list(stash["hooks"].keys())

        return {
            "context": {"enabled": context_enabled, "disabled": context_disabled},
            "tools": {"enabled": tools_enabled, "disabled": tools_disabled},
            "hooks": {"enabled": hooks_enabled, "disabled": hooks_disabled},
            "providers": {"enabled": providers_enabled, "disabled": providers_disabled},
            "agents": {"enabled": agents_enabled, "disabled": agents_disabled},
        }

    def diff_from_original(self) -> list[dict[str, Any]]:
        """Return a list of changes compared to the original snapshot.

        Returns list of {'category': str, 'name': str, 'action': str} dicts.
        action is 'disabled' (was enabled, now disabled) or 'enabled' (newly enabled).
        Returns empty list if no original snapshot has been taken.
        """
        if self._original_snapshot is None:
            return []

        current = self.snapshot()
        changes: list[dict[str, Any]] = []

        for category in ("context", "tools", "hooks", "providers", "agents"):
            orig_enabled = set(
                self._original_snapshot.get(category, {}).get("enabled", [])
            )
            curr_enabled = set(current.get(category, {}).get("enabled", []))

            # Items present in original but gone now → disabled
            for name in orig_enabled - curr_enabled:
                changes.append(
                    {"category": category, "name": name, "action": "disabled"}
                )

            # Items present now but not in original → enabled
            for name in curr_enabled - orig_enabled:
                changes.append(
                    {"category": category, "name": name, "action": "enabled"}
                )

        return changes

    def take_snapshot(self) -> None:
        """Capture a snapshot and store it as the original reference."""
        self._original_snapshot = self.snapshot()

    # ------------------------------------------------------------------
    # Context enable / disable
    # ------------------------------------------------------------------

    def context_disable(self, name: str) -> None:
        """Move a context entry from the bundle into the stash (disable it).

        Args:
            name: The context key to disable.

        Raises:
            ValueError: If the name is not found in bundle.context and is not
                already stashed (with a message listing available keys).
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["context"]:
            return

        if name not in self._bundle.context:
            available = list(self._bundle.context.keys())
            raise ValueError(
                f"Context key {name!r} not found. Available keys: {available}"
            )

        self._stash["context"][name] = self._bundle.context.pop(name)

    def context_enable(self, name: str) -> None:
        """Move a context entry from the stash back into the bundle (enable it).

        Args:
            name: The context key to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash'
                message).
        """
        # Idempotent: already enabled (in bundle.context) — nothing to do.
        if name in self._bundle.context:
            return

        if name not in self._stash["context"]:
            raise ValueError(f"Context key {name!r} not in stash. Cannot enable.")

        self._bundle.context[name] = self._stash["context"].pop(name)

    # ------------------------------------------------------------------
    # Agent enable / disable
    # ------------------------------------------------------------------

    def agent_disable(self, name: str) -> None:
        """Move an agent entry from coordinator.config['agents'] into the stash (disable it).

        Args:
            name: The agent name to disable.

        Raises:
            ValueError: If the name is not found in coordinator.config['agents'] (with a
                message containing 'not found').
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["agents"]:
            return

        agents = self._coordinator.config.get("agents", {})
        if name not in agents:
            raise ValueError(f"Agent {name!r} not found.")

        self._stash["agents"][name] = agents.pop(name)

    def agent_enable(self, name: str) -> None:
        """Move an agent entry from the stash back into coordinator.config['agents'] (enable it).

        Args:
            name: The agent name to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash' message).
        """
        agents = self._coordinator.config.get("agents", {})

        # Idempotent: already enabled (in agents dict) — nothing to do.
        if name in agents:
            return

        if name not in self._stash["agents"]:
            raise ValueError(f"Agent {name!r} not in stash. Cannot enable.")

        agents[name] = self._stash["agents"].pop(name)

    # ------------------------------------------------------------------
    # Tool enable / disable (async)
    # ------------------------------------------------------------------

    async def tool_disable(self, name: str) -> None:
        """Retrieve and stash a tool instance, then unmount it from the coordinator (disable it).

        Retrieves the live instance via ``coordinator.get("tools")`` *before* calling
        ``coordinator.unmount()``, because the real Rust binding's unmount always
        returns ``None`` — the module is deleted from the dict and the return value
        is discarded.

        Args:
            name: The tool name to disable.

        Raises:
            ValueError: If the name is not found in the currently mounted tools.
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["tools"]:
            return

        tools = self._coordinator.get("tools")
        if tools is None or name not in tools:
            available = list(tools.keys()) if tools else []
            raise ValueError(f"Tool {name!r} not found. Available: {available}")
        instance = tools[name]
        await self._coordinator.unmount("tools", name=name)
        self._stash["tools"][name] = instance

    async def tool_enable(self, name: str) -> None:
        """Remount a previously disabled tool from the stash (enable it).

        Args:
            name: The tool name to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash' message).
        """
        if name not in self._stash["tools"]:
            raise ValueError(f"Tool {name!r} not in stash. Cannot enable.")

        instance = self._stash["tools"].pop(name)
        await self._coordinator.mount("tools", instance, name=name)

    async def tool_disable_module(self, module_id: str) -> list[str]:
        """Disable all tools belonging to a module by stashing each one.

        Iterates the tool names in ``_module_to_tools[module_id]`` and calls
        ``tool_disable`` for each, silently skipping tools that are already
        disabled (catching ``ValueError`` from idempotent ``tool_disable``).

        Args:
            module_id: The module ID (e.g. ``'tool-filesystem'``).

        Returns:
            List of tool names that were disabled by this call.

        Raises:
            ValueError: If ``module_id`` is not in ``_module_to_tools``, with
                a message listing available module IDs.
        """
        if module_id not in self._module_to_tools:
            available = list(self._module_to_tools.keys())
            raise ValueError(
                f"Module {module_id!r} not found in module-to-tool mapping. "
                f"Available modules: {available}"
            )

        disabled: list[str] = []
        for tool_name in self._module_to_tools[module_id]:
            try:
                await self.tool_disable(tool_name)
                disabled.append(tool_name)
            except ValueError:
                # Tool already disabled — skip silently
                pass

        return disabled

    # ------------------------------------------------------------------
    # Provider enable / disable (async)
    # ------------------------------------------------------------------

    async def provider_disable(self, name: str) -> None:
        """Retrieve and stash a provider instance, then unmount it from the coordinator (disable it).

        Retrieves the live instance via ``coordinator.get("providers")`` *before* calling
        ``coordinator.unmount()``, because the real Rust binding's unmount always
        returns ``None`` — the module is deleted from the dict and the return value
        is discarded.

        Args:
            name: The provider name to disable.

        Raises:
            ValueError: If the name is not found in the currently mounted providers.
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["providers"]:
            return

        providers = self._coordinator.get("providers")
        if providers is None or name not in providers:
            available = list(providers.keys()) if providers else []
            raise ValueError(f"Provider {name!r} not found. Available: {available}")
        instance = providers[name]
        await self._coordinator.unmount("providers", name=name)
        self._stash["providers"][name] = instance

    async def provider_enable(self, name: str) -> None:
        """Remount a previously disabled provider from the stash (enable it).

        Args:
            name: The provider name to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash' message).
        """
        if name not in self._stash["providers"]:
            raise ValueError(f"Provider {name!r} not in stash. Cannot enable.")

        instance = self._stash["providers"].pop(name)
        await self._coordinator.mount("providers", instance, name=name)

    # ------------------------------------------------------------------
    # Behavior group toggle (async)
    # ------------------------------------------------------------------

    def _resolve_module_id_to_mounted_name(
        self, module_id: str, mount_point: str
    ) -> str | None:
        """Resolve a provenance module ID to its coordinator-mounted name.

        Provenance stores module IDs (e.g. ``"tool-bash"``, ``"tool-skills"``)
        but the coordinator mounts tools under potentially different names
        (e.g. ``"bash"``, ``"load_skill"``).  This method performs a
        best-effort three-strategy lookup to find the mounted name:

        1. **Direct match** — module ID equals the mounted name.
        2. **Prefix strip** — strip the category prefix
           (``"tool-"`` / ``"provider-"``) from the module ID.
        3. **Normalized match** — case + hyphen/underscore insensitive
           comparison against every mounted name.

        If no strategy matches (e.g. ``"tool-skills"`` vs ``"load_skill"``),
        ``None`` is returned.  Such semantic mismatches are expected and correct;
        they simply cannot be resolved without out-of-band metadata.

        Args:
            module_id: The module ID extracted from a provenance key
                (e.g. ``"tool-bash"`` from ``"tool:tool-bash"``).
            mount_point: The coordinator mount-point name,
                either ``"tools"`` or ``"providers"``.

        Returns:
            The mounted name string, or ``None`` if no match is found.
        """
        try:
            mounted: dict = self._coordinator.get(mount_point) or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        if not mounted:
            return None

        # Strategy 1: direct match (module ID is exactly the mounted name).
        if module_id in mounted:
            return module_id

        # Strategy 2: strip the category prefix.
        # "tools" → "tool-", "providers" → "provider-".
        prefix = mount_point.rstrip("s") + "-"
        short = module_id[len(prefix) :] if module_id.startswith(prefix) else module_id
        if short in mounted:
            return short

        # Strategy 3: normalized match (lowercase + hyphens→underscores).
        norm_id = _normalize_module_name(module_id)
        short_norm = _normalize_module_name(short)
        for mounted_name in mounted:
            norm_mounted = _normalize_module_name(mounted_name)
            if norm_mounted == norm_id or norm_mounted == short_norm:
                return mounted_name

        return None

    async def behavior_disable(self, name: str) -> dict[str, Any]:
        """Disable all contributions from a named behavior across all categories.

        Reads bundle._provenance to find all items contributed by the behavior,
        then disables each one by calling the appropriate category disable method.

        **Tool and provider items are intentionally skipped** — they are never
        unmounted during a behavior disable because shared ownership cannot be
        determined from provenance alone (provenance is last-writer-wins, so a
        tool contributed by two behaviors is only attributed to one).  A warning
        is emitted for each skipped tool/provider so the caller can surface it
        to the user.  To disable a tool explicitly, call
        ``/config tools disable <name>`` directly.

        Hooks are also skipped (read-only in this version).

        Partial failures (e.g. a context key no longer in the bundle) are
        collected as warnings and do not stop processing.

        Args:
            name: The behavior name to disable.

        Returns:
            dict with keys:
                'disabled': list of provenance keys that were successfully disabled.
                'warnings': list of warning message strings for skipped or
                    failed items.

        Raises:
            ValueError: If the behavior name is not found in provenance.
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})
        matching_keys = [k for k, v in provenance.items() if name in v]

        if not matching_keys:
            raise ValueError(f"Behavior {name!r} not found in provenance")

        disabled: list[str] = []
        warnings: list[str] = []

        for prov_key in matching_keys:
            category, item_name = prov_key.split(":", 1)
            # Hooks are read-only in this version — skip silently.
            if category == "hook":
                _log.debug(
                    "Skipping hook %r in behavior toggle — hook toggle not supported in this version.",
                    item_name,
                )
                continue
            # Tools and providers: skip unmount — shared ownership cannot be
            # determined from provenance alone.  Emit a warning with the
            # correct mounted name (if resolvable) so the user knows what to
            # pass to '/config tools disable' or '/config providers disable'.
            if category == "tool":
                mounted_name = self._resolve_module_id_to_mounted_name(
                    item_name, "tools"
                )
                if mounted_name:
                    warnings.append(
                        f"Skipping tool '{mounted_name}' (module '{item_name}') — "
                        f"shared ownership cannot be determined from provenance alone. "
                        f"Use '/config tools disable {mounted_name}' to explicitly disable it."
                    )
                else:
                    warnings.append(
                        f"Tool module '{item_name}' not found in mounted tools. "
                        f"It may already be disabled or use a custom registered name."
                    )
                continue
            if category == "provider":
                mounted_name = self._resolve_module_id_to_mounted_name(
                    item_name, "providers"
                )
                if mounted_name:
                    warnings.append(
                        f"Skipping provider '{mounted_name}' (module '{item_name}') — "
                        f"shared ownership cannot be determined from provenance alone. "
                        f"Use '/config providers disable {mounted_name}' to explicitly disable it."
                    )
                else:
                    warnings.append(
                        f"Provider module '{item_name}' not found in mounted providers. "
                        f"It may already be disabled or use a custom registered name."
                    )
                continue
            try:
                if category == "context":
                    self.context_disable(item_name)
                elif category == "agent":
                    self.agent_disable(item_name)
                disabled.append(prov_key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(str(exc))

        self._disabled_behaviors.add(name)
        return {"disabled": disabled, "warnings": warnings}

    async def behavior_enable(self, name: str) -> dict[str, Any]:
        """Enable all contributions from a previously disabled named behavior.

        Reads bundle._provenance to find all items contributed by the behavior,
        then enables each one by calling the appropriate category enable method.

        **Tool and provider items are silently skipped** — they were never
        unmounted by :meth:`behavior_disable` (shared ownership cannot be
        determined from provenance alone), so there is nothing to re-enable
        here.  To re-enable a manually-disabled tool, use
        ``/config tools enable <name>`` directly.

        Hooks are also skipped (read-only in this version).

        Partial failures (e.g. a stashed context key no longer valid) are
        collected as warnings and do not stop processing.

        Args:
            name: The behavior name to enable.

        Returns:
            dict with keys:
                'enabled': list of provenance keys that were successfully enabled.
                'warnings': list of error message strings for items that failed.

        Raises:
            ValueError: If the behavior name is not found in provenance.
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})
        matching_keys = [k for k, v in provenance.items() if name in v]

        if not matching_keys:
            raise ValueError(f"Behavior {name!r} not found in provenance")

        enabled: list[str] = []
        warnings: list[str] = []

        for prov_key in matching_keys:
            category, item_name = prov_key.split(":", 1)
            # Hooks are read-only in this version — skip silently.
            if category == "hook":
                _log.debug(
                    "Skipping hook %r in behavior toggle — hook toggle not supported in this version.",
                    item_name,
                )
                continue
            # Tools and providers were not stashed by behavior_disable (shared
            # ownership cannot be determined from provenance alone), so there is
            # nothing to re-enable here.  Skip silently.
            if category in ("tool", "provider"):
                _log.debug(
                    "Skipping %s %r in behavior enable — %ss are not managed by behavior toggle.",
                    category,
                    item_name,
                    category,
                )
                continue
            try:
                if category == "context":
                    self.context_enable(item_name)
                elif category == "agent":
                    self.agent_enable(item_name)
                enabled.append(prov_key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(str(exc))

        self._disabled_behaviors.discard(name)
        return {"enabled": enabled, "warnings": warnings}

    # ------------------------------------------------------------------
    # List methods — dashboard views for each category
    # ------------------------------------------------------------------

    def context_list(self) -> list[dict]:
        """Return a list of all context entries with their enabled/disabled status.

        Returns:
            List of dicts with keys:
                'name': str — context key name.
                'path': str — file path (as string).
                'enabled': bool — True if live in bundle.context, False if stashed.
                'behavior': list[str] | None — provenance behavior names (if any).
                'source': list[str] | None — alias for 'behavior' (used by CLI rendering).
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})
        result: list[dict] = []

        for name, path in self._bundle.context.items():
            behavior = provenance.get(f"context:{name}")
            result.append(
                {
                    "name": name,
                    "path": str(path),
                    "enabled": True,
                    "behavior": behavior,
                    "source": behavior,
                }
            )

        for name, path in self._stash["context"].items():
            behavior = provenance.get(f"context:{name}")
            result.append(
                {
                    "name": name,
                    "path": str(path),
                    "enabled": False,
                    "behavior": behavior,
                    "source": behavior,
                }
            )

        return result

    def tools_list(self) -> list[dict]:
        """Return a list of all tools with their enabled/disabled status.

        Enabled tools are read from coordinator.get("tools") — the live mounted dict.
        Disabled tools are read from the stash.  Config is read from
        coordinator.config["tools"] mount-plan specs (matched by module ID).

        Returns:
            List of dicts with keys:
                'name': str — tool module ID.
                'enabled': bool.
                'config': dict — per-tool config from the mount plan (may be empty).
                'behavior': list[str] | None — provenance behavior names.
                'source': list[str] | None — alias for 'behavior'.
                'module': str — module ID from the mount-plan tool-to-module mapping
                    ('unknown' when not matched).
                'source_uri': str | None — source URI from the mount plan spec
                    (e.g. 'git+https://example.com/tool-bash@main'); None when absent.
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})

        # Pre-build normalized provenance lookup for fuzzy name matching.
        # This handles mismatches between module IDs and registered tool names
        # that differ in case or word separators:
        #   tool-apply-patch  → apply_patch  (hyphen→underscore normalization)
        #   tool-lsp          → LSP          (case normalization)
        #   tool-python-check → python_check (both)
        # One-module-many-tools cases (tool-filesystem → read_file/write_file/edit_file)
        # and semantically renamed tools (tool-skills → load_skill) will not match
        # any strategy and correctly return None.
        norm_prov_map = _build_normalized_prov_lookup("tool", provenance)

        # Build config and source_uri lookups by module ID from the coordinator's mount plan.
        # Index by: full module ID, raw short name, and normalized short name so
        # that tool names like "python_check" find the config for "tool-python-check".
        config_by_id: dict[str, dict] = {}
        source_by_id: dict[str, str | None] = {}
        for spec in self._coordinator.config.get("tools", []):
            if isinstance(spec, dict):
                mid = spec.get("id") or spec.get("module", "")
                cfg = spec.get("config") or {}
                src = spec.get("source")
                if mid:
                    config_by_id[mid] = cfg
                    source_by_id[mid] = src
                    # Short name: strip "tool-" prefix (e.g. "tool-bash" → "bash").
                    short = mid[5:] if mid.startswith("tool-") else mid
                    config_by_id[short] = cfg
                    source_by_id[short] = src
                    # Normalized short name: lowercase + hyphens→underscores
                    # (e.g. "tool-python-check" → "python_check").
                    config_by_id[_normalize_module_name(short)] = cfg
                    source_by_id[_normalize_module_name(short)] = src

        result: list[dict] = []

        # Enabled: live mounted tools.
        try:
            mounted: dict = self._coordinator.get("tools") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        for name in mounted:
            behavior = _lookup_prov_behavior(name, "tool", provenance, norm_prov_map)
            norm_name = _normalize_module_name(name)
            result.append(
                {
                    "name": name,
                    "enabled": True,
                    "config": config_by_id.get(name, config_by_id.get(norm_name, {})),
                    "behavior": behavior,
                    "source": behavior,
                    "module": self._tool_to_module.get(name, "unknown"),
                    "source_uri": source_by_id.get(name, source_by_id.get(norm_name)),
                }
            )

        # Disabled: stashed tools.
        for name in self._stash["tools"]:
            behavior = _lookup_prov_behavior(name, "tool", provenance, norm_prov_map)
            norm_name = _normalize_module_name(name)
            result.append(
                {
                    "name": name,
                    "enabled": False,
                    "config": config_by_id.get(name, config_by_id.get(norm_name, {})),
                    "behavior": behavior,
                    "source": behavior,
                    "module": self._tool_to_module.get(name, "unknown"),
                    "source_uri": source_by_id.get(name, source_by_id.get(norm_name)),
                }
            )

        return result

    def hooks_list(self) -> list[dict]:
        """Return a list of all hooks.

        Hooks are always shown as enabled — hook toggle is not supported in this
        version (read-only).  Data is sourced from the _hook_snapshot captured
        at construction time.

        Returns:
            List of dicts with keys:
                'name': str — hook handler name.
                'event': str — event the hook is bound to.
                'priority': int — priority (0 if not available).
                'enabled': bool — always True (hooks are read-only).
                'behavior': list[str] | None — provenance behavior names.
                'source': list[str] | None — alias for 'behavior'.
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})
        # Pre-build normalized provenance lookup for fuzzy name matching.
        # Hook names registered in the hook registry may differ from module IDs
        # in case or word separators (e.g. "python-check" from "hooks-python-check").
        norm_prov_map = _build_normalized_prov_lookup("hook", provenance)
        result: list[dict] = []

        for name, meta in self._hook_snapshot.items():
            behavior = _lookup_prov_behavior(name, "hook", provenance, norm_prov_map)
            result.append(
                {
                    "name": name,
                    "event": meta.get("event", ""),
                    "priority": meta.get("priority", 0),
                    "enabled": True,
                    "behavior": behavior,
                    "source": behavior,
                }
            )

        return result

    def providers_list(self) -> list[dict]:
        """Return a list of all providers with their enabled/disabled status.

        Same pattern as tools_list but for providers.

        .. note::
            Providers that are **auto-configured by the application layer**
            (e.g. resolved from an ``ANTHROPIC_API_KEY`` environment variable by
            the app-cli's ``resolve_bundle_config()`` function) are **not tracked**
            by this configurator.  They bypass bundle composition entirely and
            therefore have no provenance entry and do not appear in
            ``coordinator.get("providers")`` or ``coordinator.config["providers"]``.
            The list returned here reflects only providers that were explicitly
            declared in the bundle's mount plan.  This is a known limitation: a
            future "provider introspection" API on the coordinator would be needed
            to surface app-level providers here.

        Returns:
            List of dicts with keys:
                'name': str — provider module ID (short name with prefix stripped).
                'enabled': bool.
                'config': dict — per-provider config from the mount plan.
                'behavior': list[str] | None — provenance behavior names.
                'source': list[str] | None — alias for 'behavior'.
                'source_uri': str | None — source URI from the mount plan spec
                    (e.g. 'git+https://example.com/provider-anthropic@main'); None when absent.
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})

        # Pre-build normalized provenance lookup for fuzzy provider name matching.
        norm_prov_map = _build_normalized_prov_lookup("provider", provenance)

        config_by_id: dict[str, dict] = {}
        source_by_id: dict[str, str | None] = {}
        for spec in self._coordinator.config.get("providers", []):
            if isinstance(spec, dict):
                mid = spec.get("id") or spec.get("module", "")
                cfg = spec.get("config") or {}
                src = spec.get("source")
                if mid:
                    config_by_id[mid] = cfg
                    source_by_id[mid] = src
                    # Short name (strip "provider-" prefix).
                    short = mid[9:] if mid.startswith("provider-") else mid
                    config_by_id[short] = cfg
                    source_by_id[short] = src
                    # Normalized short name (lowercase + hyphens→underscores).
                    config_by_id[_normalize_module_name(short)] = cfg
                    source_by_id[_normalize_module_name(short)] = src

        result: list[dict] = []

        try:
            mounted: dict = self._coordinator.get("providers") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        if mounted:
            # Coordinator exposes live provider instances — use them as the source of truth.
            for name in mounted:
                behavior = _lookup_prov_behavior(
                    name, "provider", provenance, norm_prov_map
                )
                norm_name = _normalize_module_name(name)
                result.append(
                    {
                        "name": name,
                        "enabled": True,
                        "config": config_by_id.get(
                            name, config_by_id.get(norm_name, {})
                        ),
                        "behavior": behavior,
                        "source": behavior,
                        "source_uri": source_by_id.get(
                            name, source_by_id.get(norm_name)
                        ),
                    }
                )

            # Disabled providers live in the stash.
            for name in self._stash["providers"]:
                behavior = _lookup_prov_behavior(
                    name, "provider", provenance, norm_prov_map
                )
                norm_name = _normalize_module_name(name)
                result.append(
                    {
                        "name": name,
                        "enabled": False,
                        "config": config_by_id.get(
                            name, config_by_id.get(norm_name, {})
                        ),
                        "behavior": behavior,
                        "source": behavior,
                        "source_uri": source_by_id.get(
                            name, source_by_id.get(norm_name)
                        ),
                    }
                )
        else:
            # Fallback: coordinator.get("providers") is not supported by this coordinator
            # implementation (e.g. the Rust binding does not expose a providers mount-point).
            # Derive the provider list from the mount-plan specs in coordinator.config so
            # the dashboard still shows something meaningful.
            #
            # NOTE: If coordinator.config also has no providers (e.g. when all providers
            # are auto-configured by the app-cli layer from environment variables), the
            # result will be empty.  That is the correct, honest answer — see the docstring.
            added: set[str] = set()
            for spec in self._coordinator.config.get("providers", []):
                if not isinstance(spec, dict):
                    continue
                mid = spec.get("id") or spec.get("module", "")
                if not mid:
                    continue
                # Display with short name (strip "provider-" prefix if present).
                short_name = mid[9:] if mid.startswith("provider-") else mid
                if short_name in added:
                    continue
                added.add(short_name)
                enabled = (
                    short_name not in self._stash["providers"]
                    and mid not in self._stash["providers"]
                )
                behavior = _lookup_prov_behavior(
                    short_name, "provider", provenance, norm_prov_map
                ) or _lookup_prov_behavior(mid, "provider", provenance, norm_prov_map)
                result.append(
                    {
                        "name": short_name,
                        "enabled": enabled,
                        "config": config_by_id.get(
                            short_name, config_by_id.get(mid, {})
                        ),
                        "behavior": behavior,
                        "source": behavior,
                        "source_uri": source_by_id.get(
                            short_name, source_by_id.get(mid)
                        ),
                    }
                )

            # Include any stashed providers not already covered by the mount plan.
            for name in self._stash["providers"]:
                if name not in added:
                    behavior = _lookup_prov_behavior(
                        name, "provider", provenance, norm_prov_map
                    )
                    norm_name = _normalize_module_name(name)
                    result.append(
                        {
                            "name": name,
                            "enabled": False,
                            "config": config_by_id.get(
                                name, config_by_id.get(norm_name, {})
                            ),
                            "behavior": behavior,
                            "source": behavior,
                            "source_uri": source_by_id.get(
                                name, source_by_id.get(norm_name)
                            ),
                        }
                    )

        return result

    def agents_list(self) -> list[dict]:
        """Return a list of all agents with their enabled/disabled status.

        Returns:
            List of dicts with keys:
                'name': str — agent name.
                'enabled': bool.
                'config': dict — agent config dict.
                'behavior': list[str] | None — provenance behavior names.
                'source': list[str] | None — alias for 'behavior'.
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})
        result: list[dict] = []

        # Enabled: live in coordinator.config["agents"].
        for name, cfg in self._coordinator.config.get("agents", {}).items():
            behavior = provenance.get(f"agent:{name}")
            result.append(
                {
                    "name": name,
                    "enabled": True,
                    "config": cfg if isinstance(cfg, dict) else {},
                    "behavior": behavior,
                    "source": behavior,
                }
            )

        # Disabled: stashed.
        for name, cfg in self._stash["agents"].items():
            behavior = provenance.get(f"agent:{name}")
            result.append(
                {
                    "name": name,
                    "enabled": False,
                    "config": cfg if isinstance(cfg, dict) else {},
                    "behavior": behavior,
                    "source": behavior,
                }
            )

        return result

    def behaviors_list(self) -> list[dict]:
        """Return a list of all behaviors derived from bundle provenance.

        Groups all provenance entries by behavior name (value) and lists
        the provenance keys that contribute per category.

        Returns:
            Sorted list of dicts with keys:
                'name': str — behavior name.
                'enabled': bool — False if in _disabled_behaviors, else True.
                'contributions': dict[str, list[str]] — list of provenance keys
                    per category (context, tools, hooks, providers, agents).
                    E.g. {'context': ['context:readme'], 'tools': ['tool:tool-bash'], ...}
        """
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})

        # Group provenance keys by behavior name.
        # Provenance keys use SINGULAR category prefixes (tool:, hook:, provider:,
        # agent:, context:) which must be mapped to the PLURAL keys used in the
        # contributions output dict (tools, hooks, providers, agents, context).
        # Each provenance value is a list of behavior names (multi-claimant).
        behaviors: dict[str, dict[str, list[str]]] = {}
        for prov_key, behavior_names in provenance.items():
            if ":" not in prov_key:
                continue
            # Skip entries with an empty behavior names list (can occur when a bundle
            # with no name is composed into the session bundle).
            if not behavior_names:
                continue
            category, _ = prov_key.split(":", 1)
            plural_cat = _PROV_CATEGORY_MAP.get(category, category)
            for behavior_name in behavior_names:
                if not behavior_name:
                    continue
                if behavior_name not in behaviors:
                    behaviors[behavior_name] = {
                        "context": [],
                        "tools": [],
                        "hooks": [],
                        "providers": [],
                        "agents": [],
                    }
                if plural_cat in behaviors[behavior_name]:
                    behaviors[behavior_name][plural_cat].append(prov_key)

        result: list[dict] = []
        for name, contrib_lists in behaviors.items():
            contributions = {cat: list(items) for cat, items in contrib_lists.items()}
            result.append(
                {
                    "name": name,
                    "enabled": name not in self._disabled_behaviors,
                    "contributions": contributions,
                }
            )

        return sorted(result, key=lambda x: x["name"])

    # ------------------------------------------------------------------
    # Config get / set
    # ------------------------------------------------------------------

    def config_get(self, path: str) -> Any:
        """Return the value at a dot-separated path in coordinator.config.

        Args:
            path: Dot-separated key path (e.g. 'model.name').

        Returns:
            The value at the path, or None if not found.
        """
        keys = path.split(".")
        return get_nested(self._coordinator.config, keys)

    def config_set(self, path: str, value: Any) -> None:
        """Set a value at a dot-separated path in coordinator.config.

        Mutates coordinator.config in-place and records the override for
        later persistence via save().

        Args:
            path: Dot-separated key path (e.g. 'model.name').
            value: Value to set at the path.
        """
        keys = path.split(".")
        set_nested(self._coordinator.config, keys, value)
        self._config_overrides[path] = value

    # ------------------------------------------------------------------
    # Persistence: save and apply
    # ------------------------------------------------------------------

    def save(self, scope: str = "global") -> str:
        """Persist the current configurator state to a settings.yaml file.

        Args:
            scope: 'global' writes to ~/.amplifier/settings.yaml;
                   'project' writes to .amplifier/settings.yaml (relative to cwd).

        Returns:
            The string path of the written settings file.

        Raises:
            ValueError: If scope is not 'global' or 'project'.
        """
        if scope == "global":
            settings_path = Path.home() / ".amplifier" / "settings.yaml"
        elif scope == "project":
            settings_path = Path(".amplifier") / "settings.yaml"
        else:
            raise ValueError(f"Invalid scope: {scope!r}")

        configurator_data: dict[str, Any] = {
            "disabled": {
                "behaviors": sorted(self._disabled_behaviors),
                "context": list(self._stash["context"].keys()),
                "tools": list(self._stash["tools"].keys()),
                "hooks": list(self._stash["hooks"].keys()),
                "providers": list(self._stash["providers"].keys()),
                "agents": list(self._stash["agents"].keys()),
            },
            "config_overrides": dict(self._config_overrides),
        }

        # Read existing settings to preserve non-configurator sections
        existing: dict[str, Any] = {}
        if settings_path.exists():
            with settings_path.open() as f:
                existing = yaml.safe_load(f) or {}

        existing["configurator"] = configurator_data

        # Ensure parent directory exists before writing
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        with settings_path.open("w") as f:
            yaml.dump(existing, f, default_flow_style=False)

        return str(settings_path)

    async def apply_saved_settings(self, settings: dict[str, Any]) -> list[str]:
        """Apply a saved configurator settings dict to the current session.

        Disables behaviors first, then individual items per category, and
        finally applies config_overrides. Stale references (items no longer
        present in the bundle or config) are silently skipped with a debug log.

        Args:
            settings: The 'configurator' section from a settings.yaml file,
                containing 'disabled' and 'config_overrides' keys.

        Returns:
            List of warning messages (e.g. partial failures within behavior_disable).
        """
        warnings: list[str] = []
        disabled = settings.get("disabled", {})

        # Disable whole behaviors first
        for behavior_name in disabled.get("behaviors", []):
            try:
                result = await self.behavior_disable(behavior_name)
                warnings.extend(result.get("warnings", []))
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale behavior %r: %s", behavior_name, exc)

        # Disable individual context items
        for name in disabled.get("context", []):
            try:
                self.context_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale context item %r: %s", name, exc)

        # Disable individual tools (async)
        for name in disabled.get("tools", []):
            try:
                await self.tool_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale tool %r: %s", name, exc)

        # Hooks are read-only — skip any saved hook disable entries
        for name in disabled.get("hooks", []):
            _log.debug(
                "Skipping hook %r in apply_saved_settings — hook toggle not supported in this version.",
                name,
            )

        # Disable individual providers (async)
        for name in disabled.get("providers", []):
            try:
                await self.provider_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale provider %r: %s", name, exc)

        # Disable individual agents
        for name in disabled.get("agents", []):
            try:
                self.agent_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale agent %r: %s", name, exc)

        # Apply config overrides
        for path, value in settings.get("config_overrides", {}).items():
            self.config_set(path, value)

        return warnings
