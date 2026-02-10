"""Bundle dataclass - the core composable unit."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from amplifier_foundation.modules.activator import ModuleActivator

from amplifier_foundation.dicts.merge import deep_merge
from amplifier_foundation.dicts.merge import merge_module_lists
from amplifier_foundation.exceptions import BundleValidationError
from amplifier_foundation.paths.construction import construct_context_path
from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_foundation.spawn_utils import apply_provider_preferences_with_resolution

logger = logging.getLogger(__name__)


@dataclass
class Bundle:
    """Composable unit containing mount plan config and resources.

    Bundles are the core composable unit in amplifier-foundation. They contain
    mount plan configuration and resources, producing mount plans for AmplifierSession.

    Attributes:
        name: Bundle name (namespace for @mentions).
        version: Bundle version string.
        description: Optional description.
        includes: List of bundle URIs to include.
        session: Session config (orchestrator, context).
        providers: List of provider configs.
        tools: List of tool configs.
        hooks: List of hook configs.
        agents: Dict mapping agent name to definition.
        context: Dict mapping context name to file path.
        instruction: System instruction from markdown body.
        base_path: Path to bundle root directory.
        source_base_paths: Dict mapping namespace to base_path for @mention resolution.
            Tracks original base_path for each bundle during composition, enabling
            @namespace:path references to resolve correctly to source files.
    """

    # Metadata
    name: str
    version: str = "1.0.0"
    description: str = ""
    includes: list[str] = field(default_factory=list)

    # Mount plan sections
    session: dict[str, Any] = field(default_factory=dict)
    providers: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    spawn: dict[str, Any] = field(
        default_factory=dict
    )  # Spawn config (exclude_tools, etc.)

    # Resources
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    context: dict[str, Path] = field(default_factory=dict)
    instruction: str | None = None

    # Internal
    base_path: Path | None = None
    source_base_paths: dict[str, Path] = field(
        default_factory=dict
    )  # Track base_path for each source namespace
    _pending_context: dict[str, str] = field(
        default_factory=dict
    )  # Context refs needing namespace resolution

    def __post_init__(self) -> None:
        """Ensure collection fields are never None.

        Protects against callers passing None explicitly (e.g., via
        dataclasses.replace or direct construction) which bypasses
        default_factory. Without this guard, 'x in self.context' raises
        TypeError: argument of type 'NoneType' is not iterable.
        """
        if self.context is None:
            self.context = {}
        if self.source_base_paths is None:
            self.source_base_paths = {}
        if self._pending_context is None:
            self._pending_context = {}

    def compose(self, *others: Bundle) -> Bundle:
        """Compose this bundle with others (later overrides earlier).

        Creates a new Bundle with merged configuration. For each section:
        - session/spawn: deep merge (nested dicts merged, later wins for scalars)
        - providers/tools/hooks: merge by module ID
        - agents: later overrides earlier (by agent name)
        - context: accumulates with namespace prefix (each bundle contributes)
        - instruction: later replaces earlier

        Args:
            others: Bundles to compose with.

        Returns:
            New Bundle with merged configuration.
        """
        # Initialize source_base_paths: copy self's or create from self's name/base_path
        initial_base_paths = (
            dict(self.source_base_paths) if self.source_base_paths else {}
        )
        if self.name and self.base_path and self.name not in initial_base_paths:
            initial_base_paths[self.name] = self.base_path

        # Prefix self's context keys with bundle name to avoid collisions during compose
        initial_context: dict[str, Path] = {}
        for key, path in self.context.items():
            if self.name and ":" not in key:
                prefixed_key = f"{self.name}:{key}"
            else:
                prefixed_key = key
            initial_context[prefixed_key] = path

        # Copy pending context (already has namespace prefixes from _parse_context)
        initial_pending_context: dict[str, str] = (
            dict(self._pending_context) if self._pending_context else {}
        )

        result = Bundle(
            name=self.name,
            version=self.version,
            description=self.description,
            includes=list(self.includes),
            session=dict(self.session),
            providers=list(self.providers),
            tools=list(self.tools),
            hooks=list(self.hooks),
            spawn=dict(self.spawn),
            agents=dict(self.agents),
            context=initial_context,
            _pending_context=initial_pending_context,
            instruction=self.instruction,
            base_path=self.base_path,
            source_base_paths=initial_base_paths,
        )

        for other in others:
            # Merge other's source_base_paths first (preserves registry-set values like source_root)
            # This is critical for subdirectory bundles where registry sets source_root mapping
            if other.source_base_paths:
                for ns, path in other.source_base_paths.items():
                    if ns not in result.source_base_paths:
                        result.source_base_paths[ns] = path

            # Also track other's own namespace as fallback (if not already set via source_base_paths)
            if (
                other.name
                and other.base_path
                and other.name not in result.source_base_paths
            ):
                result.source_base_paths[other.name] = other.base_path

            # Metadata: later wins
            result.name = other.name or result.name
            result.version = other.version or result.version
            if other.description:
                result.description = other.description

            # Session: deep merge
            result.session = deep_merge(result.session, other.session)

            # Spawn config: deep merge (later overrides)
            result.spawn = deep_merge(result.spawn, other.spawn)

            # Module lists: merge by module ID
            result.providers = merge_module_lists(result.providers, other.providers)
            result.tools = merge_module_lists(result.tools, other.tools)
            result.hooks = merge_module_lists(result.hooks, other.hooks)

            # Agents: later overrides
            result.agents.update(other.agents)

            # Context: accumulate with bundle prefix to avoid collisions
            # This allows multiple bundles to each contribute context files
            for key, path in other.context.items():
                # Add bundle prefix if not already present
                if other.name and ":" not in key:
                    prefixed_key = f"{other.name}:{key}"
                else:
                    prefixed_key = key
                result.context[prefixed_key] = path

            # Pending context: accumulate (already has namespace prefixes)
            if other._pending_context:
                result._pending_context.update(other._pending_context)

            # Instruction: later replaces
            if other.instruction:
                result.instruction = other.instruction

            # Base path: use other's (the bundle being composed in) if set
            # In typical usage: result.compose(user_bundle), so other=user_bundle
            # This ensures @AGENTS.md resolves relative to user's project, not cache
            if other.base_path:
                result.base_path = other.base_path

        return result

    def to_mount_plan(self) -> dict[str, Any]:
        """Compile to mount plan for AmplifierSession.

        Returns:
            Dict suitable for AmplifierSession.create().
        """
        mount_plan: dict[str, Any] = {}

        if self.session:
            mount_plan["session"] = dict(self.session)

        if self.providers:
            mount_plan["providers"] = list(self.providers)

        if self.tools:
            mount_plan["tools"] = list(self.tools)

        if self.hooks:
            mount_plan["hooks"] = list(self.hooks)

        # Agents go in mount plan for sub-session delegation
        if self.agents:
            mount_plan["agents"] = dict(self.agents)

        # Spawn config for tool filtering in spawned agents
        if self.spawn:
            mount_plan["spawn"] = dict(self.spawn)

        return mount_plan

    async def prepare(
        self,
        install_deps: bool = True,
        source_resolver: Callable[[str, str], str] | None = None,
    ) -> PreparedBundle:
        """Prepare bundle for execution by activating all modules.

        Downloads and installs all modules specified in the bundle's mount plan,
        making them importable. Returns a PreparedBundle containing the mount plan
        and a module resolver for use with AmplifierSession.

        This is the turn-key method for apps that want to load a bundle and
        execute it without managing module resolution themselves.

        Args:
            install_deps: Whether to install Python dependencies for modules.
            source_resolver: Optional callback (module_id, original_source) -> resolved_source.
                Allows app-layer source override policy to be applied before activation.
                If provided, each module's source is passed through this resolver,
                enabling settings-based overrides without foundation knowing about settings.

        Returns:
            PreparedBundle with mount_plan and create_session() helper.

        Example:
            bundle = await load_bundle("git+https://...")
            prepared = await bundle.prepare()
            async with prepared.create_session() as session:
                response = await session.execute("Hello!")

            # Or manually:
            session = AmplifierSession(config=prepared.mount_plan)
            await session.coordinator.mount("module-source-resolver", prepared.resolver)
            await session.initialize()

            # With source overrides (app-layer policy):
            def resolve_with_overrides(module_id: str, source: str) -> str:
                return overrides.get(module_id) or source
            prepared = await bundle.prepare(source_resolver=resolve_with_overrides)
        """
        from amplifier_foundation.modules.activator import ModuleActivator

        # Get mount plan
        mount_plan = self.to_mount_plan()

        # Create activator with bundle's base_path so relative module paths
        # like ./modules/foo resolve relative to the bundle, not cwd
        activator = ModuleActivator(install_deps=install_deps, base_path=self.base_path)

        # CRITICAL: Install bundle packages BEFORE activating modules
        # Modules may import from their parent bundle's package (e.g., tool-shadow
        # imports from amplifier_bundle_shadow). These packages must be installed
        # before modules can be activated.
        if install_deps:
            # Install this bundle's package (if it has pyproject.toml)
            if self.base_path:
                await activator.activate_bundle_package(self.base_path)

            # Install packages from all included bundles (from source_base_paths)
            for namespace, bundle_path in self.source_base_paths.items():
                if bundle_path and bundle_path != self.base_path:
                    await activator.activate_bundle_package(bundle_path)

        # Collect all modules that need activation
        modules_to_activate = []

        # Helper to apply source resolver if provided
        def resolve_source(mod_spec: dict) -> dict:
            if source_resolver and "module" in mod_spec and "source" in mod_spec:
                resolved = source_resolver(mod_spec["module"], mod_spec["source"])
                if resolved != mod_spec["source"]:
                    # Copy to avoid mutating original
                    mod_spec = {**mod_spec, "source": resolved}
            return mod_spec

        # Session orchestrator and context
        session_config = mount_plan.get("session", {})
        if isinstance(session_config.get("orchestrator"), dict):
            orch = session_config["orchestrator"]
            if "source" in orch:
                modules_to_activate.append(resolve_source(orch))
        if isinstance(session_config.get("context"), dict):
            ctx = session_config["context"]
            if "source" in ctx:
                modules_to_activate.append(resolve_source(ctx))

        # Providers, tools, hooks
        for section in ["providers", "tools", "hooks"]:
            for mod_spec in mount_plan.get(section, []):
                if isinstance(mod_spec, dict) and "source" in mod_spec:
                    modules_to_activate.append(resolve_source(mod_spec))

        # Activate all modules and get their paths
        module_paths = await activator.activate_all(modules_to_activate)

        # Save install state to disk for fast subsequent startups
        activator.finalize()

        # Create resolver from activated paths with activator for lazy activation
        # This enables child sessions to activate agent-specific modules on-demand
        resolver = BundleModuleResolver(module_paths, activator=activator)

        # Get bundle package paths for inheritance by child sessions
        bundle_package_paths = activator.bundle_package_paths

        return PreparedBundle(
            mount_plan=mount_plan,
            resolver=resolver,
            bundle=self,
            bundle_package_paths=bundle_package_paths,
        )

    def resolve_context_path(self, name: str) -> Path | None:
        """Resolve context file by name.

        Args:
            name: Context name.

        Returns:
            Path to context file, or None if not found.
        """
        # Check registered context
        if name in self.context:
            return self.context[name]

        # Try constructing path from base
        if self.base_path:
            path = construct_context_path(self.base_path, name)
            if path.exists():
                return path

        return None

    def resolve_agent_path(self, name: str) -> Path | None:
        """Resolve agent file by name.

        Handles both namespaced and simple names:
        - "foundation:bug-hunter" -> looks in source_base_paths["foundation"]/agents/
        - "bug-hunter" -> looks in self.base_path/agents/

        For namespaced agents from included bundles, uses source_base_paths
        to find the correct bundle's agents directory.

        Args:
            name: Agent name (may include bundle prefix).

        Returns:
            Path to agent file, or None if not found.
        """
        # Check for namespaced agent (e.g., "foundation:bug-hunter")
        if ":" in name:
            namespace, simple_name = name.split(":", 1)

            # First, try source_base_paths for included bundles
            if namespace in self.source_base_paths:
                agent_path = (
                    self.source_base_paths[namespace] / "agents" / f"{simple_name}.md"
                )
                if agent_path.exists():
                    return agent_path

            # Fall back to self.base_path if namespace matches self.name
            if namespace == self.name and self.base_path:
                agent_path = self.base_path / "agents" / f"{simple_name}.md"
                if agent_path.exists():
                    return agent_path
        else:
            # No namespace - look in self.base_path
            simple_name = name
            if self.base_path:
                agent_path = self.base_path / "agents" / f"{simple_name}.md"
                if agent_path.exists():
                    return agent_path

        return None

    def get_system_instruction(self) -> str | None:
        """Get the system instruction for this bundle.

        Returns:
            Instruction text, or None if not set.
        """
        return self.instruction

    def resolve_pending_context(self) -> None:
        """Resolve any pending namespaced context references using source_base_paths.

        Context includes with namespace prefixes (e.g., "foundation:context/file.md")
        are stored as pending during parsing because source_base_paths isn't available
        yet. This method resolves them after composition when source_base_paths is
        fully populated.

        Call this before accessing self.context to ensure all paths are resolved.
        """
        if not self._pending_context:
            return

        for name, ref in list(self._pending_context.items()):
            # ref format: "namespace:path/to/file.md"
            if ":" not in ref:
                continue

            namespace, path_part = ref.split(":", 1)

            # Try to resolve using source_base_paths
            if namespace in self.source_base_paths:
                base = self.source_base_paths[namespace]
                resolved_path = construct_context_path(base, path_part)
                self.context[name] = resolved_path
                del self._pending_context[name]
            elif self.base_path:
                # Fallback: if namespace matches this bundle's name, use base_path
                # This handles self-referencing context includes
                if namespace == self.name:
                    resolved_path = construct_context_path(self.base_path, path_part)
                    self.context[name] = resolved_path
                    del self._pending_context[name]

    def load_agent_metadata(self) -> None:
        """Load full metadata for all agents from their .md files.

        Updates self.agents in-place with description and other meta fields
        loaded from agent .md files. Uses resolve_agent_path() to find files.

        Call after composition when source_base_paths is fully populated.
        This is similar to resolve_pending_context() which also needs
        source_base_paths for namespace resolution.

        Agents with inline definitions (description already set) are preserved;
        file metadata only fills in missing fields.
        """
        if not self.agents:
            return

        for agent_name, agent_config in self.agents.items():
            path = self.resolve_agent_path(agent_name)
            if path and path.exists():
                try:
                    file_metadata = _load_agent_file_metadata(path, agent_name)
                    # Merge: file metadata fills gaps, doesn't override explicit config
                    for key, value in file_metadata.items():
                        if key not in agent_config or not agent_config.get(key):
                            agent_config[key] = value
                except Exception as e:
                    logger.warning(
                        f"Failed to load metadata for agent '{agent_name}': {e}"
                    )

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_path: Path | None = None) -> Bundle:
        """Create Bundle from parsed dict (from YAML/frontmatter).

        Args:
            data: Dict with bundle configuration.
            base_path: Path to bundle root directory.

        Returns:
            Bundle instance.

        Raises:
            BundleValidationError: If providers, tools, or hooks contain malformed items.
        """
        bundle_meta = data.get("bundle", {})
        bundle_name = bundle_meta.get("name", "")

        # Validate module lists before using them
        providers = _validate_module_list(
            data.get("providers", []), "providers", bundle_name, base_path
        )
        tools = _validate_module_list(
            data.get("tools", []), "tools", bundle_name, base_path
        )
        hooks = _validate_module_list(
            data.get("hooks", []), "hooks", bundle_name, base_path
        )

        # Parse context - returns (resolved, pending) tuple
        resolved_context, pending_context = _parse_context(
            data.get("context", {}), base_path
        )

        return cls(
            name=bundle_name,
            version=bundle_meta.get("version", "1.0.0"),
            description=bundle_meta.get("description", ""),
            includes=data.get("includes", []),
            session=data.get("session", {}),
            providers=providers,
            tools=tools,
            hooks=hooks,
            spawn=data.get("spawn", {}),
            agents=_parse_agents(data.get("agents", {}), base_path),
            context=resolved_context,
            _pending_context=pending_context,
            instruction=None,  # Set separately from markdown body
            base_path=base_path,
        )


def _parse_agents(
    agents_config: dict[str, Any], base_path: Path | None
) -> dict[str, dict[str, Any]]:
    """Parse agents config section.

    Handles both include lists and direct definitions.
    """
    if not agents_config:
        return {}

    result: dict[str, dict[str, Any]] = {}

    # Handle include list
    if "include" in agents_config:
        for name in agents_config["include"]:
            result[name] = {"name": name}

    # Handle direct definitions
    for key, value in agents_config.items():
        if key != "include" and isinstance(value, dict):
            result[key] = value

    return result


def _load_agent_file_metadata(path: Path, fallback_name: str) -> dict[str, Any]:
    """Load agent config from a .md file.

    Extracts both metadata (name, description) from the meta: section AND
    mount plan sections (tools, providers, hooks, session) from top-level
    frontmatter. This allows agents to define their own tools that will be
    used when the agent is spawned.

    Args:
        path: Path to agent .md file
        fallback_name: Name to use if not specified in file

    Returns:
        Dict with name, description, instruction (from markdown body),
        and optionally tools, providers, hooks, session if defined.
    """
    from amplifier_foundation.io.frontmatter import parse_frontmatter

    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)

    # Agents use meta: section (not bundle:)
    meta = frontmatter.get("meta", {})
    if not meta:
        # Some agents might have flat frontmatter without meta wrapper
        if "name" in frontmatter or "description" in frontmatter:
            meta = frontmatter
        else:
            meta = {}

    result = {
        "name": meta.get("name", fallback_name),
        "description": meta.get("description", ""),
        **{k: v for k, v in meta.items() if k not in ("name", "description")},
    }

    # Extract top-level mount plan sections (tools, providers, hooks, session)
    # These are siblings to meta:, not nested inside it
    # This enables agents to define their own tools that get loaded at spawn time
    if "tools" in frontmatter:
        result["tools"] = frontmatter["tools"]
    if "providers" in frontmatter:
        result["providers"] = frontmatter["providers"]
    if "hooks" in frontmatter:
        result["hooks"] = frontmatter["hooks"]
    if "session" in frontmatter:
        result["session"] = frontmatter["session"]

    # Include instruction from markdown body (same as bundle loading does)
    if body and body.strip():
        result["instruction"] = body.strip()

    return result


def _parse_context(
    context_config: dict[str, Any], base_path: Path | None
) -> tuple[dict[str, Path], dict[str, str]]:
    """Parse context config section.

    Handles both include lists and direct path mappings.
    Context names with bundle prefix (e.g., "foundation:file.md") are stored
    as pending for later resolution using source_base_paths.

    Returns:
        Tuple of (resolved_context, pending_context):
        - resolved_context: Dict of name -> Path for immediately resolvable paths
        - pending_context: Dict of name -> original_ref for namespaced refs needing
          deferred resolution via source_base_paths
    """
    if not context_config:
        return {}, {}

    resolved: dict[str, Path] = {}
    pending: dict[str, str] = {}

    # Handle include list
    if "include" in context_config:
        for name in context_config["include"]:
            if ":" in name:
                # Has namespace prefix - needs deferred resolution via source_base_paths
                # Store the original ref for resolution later when source_base_paths is available
                pending[name] = name
            elif base_path:
                # No namespace prefix - resolve immediately using local base_path
                resolved[name] = construct_context_path(base_path, name)

    # Handle direct path mappings (no namespace support for direct mappings)
    for key, value in context_config.items():
        if key != "include" and isinstance(value, str):
            if base_path:
                resolved[key] = base_path / value
            else:
                resolved[key] = Path(value)

    return resolved, pending


def _validate_module_list(
    items: Any,
    field_name: str,
    bundle_name: str,
    base_path: Path | None,
) -> list[dict[str, Any]]:
    """Validate that a module list contains only dicts with required keys.

    Args:
        items: The items to validate (should be a list of dicts).
        field_name: Name of the field being validated (e.g., "tools", "providers").
        bundle_name: Bundle name for error messages.
        base_path: Bundle base path for error messages.

    Returns:
        The validated items list (unchanged if valid).

    Raises:
        BundleValidationError: If items is not a list or contains non-dict items.
    """
    if not items:
        return []

    if not isinstance(items, list):
        bundle_identifier = bundle_name or str(base_path) or "unknown"
        raise BundleValidationError(
            f"Bundle '{bundle_identifier}' has malformed {field_name}: "
            f"expected list, got {type(items).__name__}.\n"
            f"Correct format: {field_name}: [{{module: 'module-id', source: 'git+https://...'}}]"
        )

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            bundle_identifier = bundle_name or str(base_path) or "unknown"
            raise BundleValidationError(
                f"Bundle '{bundle_identifier}' has malformed {field_name}[{i}]: "
                f"expected dict with 'module' and 'source' keys, got {type(item).__name__} {item!r}.\n"
                f"Correct format: {field_name}: [{{module: 'module-id', source: 'git+https://...'}}]"
            )

    # Resolve relative source paths to absolute (before composition can change base_path)
    # This fixes issue #190: relative paths must be resolved at parse time
    if base_path:
        resolved_items = []
        for item in items:
            source = item.get("source", "")
            if isinstance(source, str) and (
                source.startswith("./") or source.startswith("../")
            ):
                # Resolve relative path against bundle's base_path
                resolved_source = str((base_path / source).resolve())
                # Copy dict to avoid mutating original
                item = {**item, "source": resolved_source}
            resolved_items.append(item)
        return resolved_items

    return items


class BundleModuleSource:
    """Simple module source that returns a pre-resolved path."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def resolve(self) -> Path:
        """Return the pre-resolved module path."""
        return self._path


class BundleModuleResolver:
    """Module resolver for prepared bundles with lazy activation support.

    Maps module IDs to their activated paths. Implements the kernel's
    ModuleSourceResolver protocol.

    Supports on-demand module activation for agent-specific modules that
    weren't in the parent bundle's initial activation set.
    """

    def __init__(
        self,
        module_paths: dict[str, Path],
        activator: "ModuleActivator | None" = None,
    ) -> None:
        """Initialize with activated module paths and optional activator.

        Args:
            module_paths: Dict mapping module ID to local path.
            activator: Optional ModuleActivator for lazy activation of missing modules.
                      If provided, modules not in module_paths will be activated on-demand.
        """
        self._paths = module_paths
        self._activator = activator
        self._activation_lock = asyncio.Lock()

    def resolve(
        self, module_id: str, source_hint: Any = None, profile_hint: Any = None
    ) -> BundleModuleSource:
        """Resolve module ID to source.

        Args:
            module_id: Module identifier (e.g., "tool-bash").
            source_hint: Optional source URI hint for lazy activation.
            profile_hint: DEPRECATED - use source_hint instead.

        Returns:
            BundleModuleSource with the module path.

        Raises:
            ModuleNotFoundError: If module not in activated paths and lazy activation fails.

        FIXME: Remove profile_hint parameter after all callers migrate to source_hint (target: v2.0).
        """
        _hint = profile_hint if profile_hint is not None else source_hint  # noqa: F841
        if module_id not in self._paths:
            raise ModuleNotFoundError(
                f"Module '{module_id}' not found in prepared bundle. "
                f"Available modules: {list(self._paths.keys())}. "
                f"Use async_resolve() for lazy activation support."
            )
        return BundleModuleSource(self._paths[module_id])

    async def async_resolve(
        self, module_id: str, source_hint: Any = None, profile_hint: Any = None
    ) -> BundleModuleSource:
        """Async resolve with lazy activation support.

        Args:
            module_id: Module identifier (e.g., "tool-bash").
            source_hint: Optional source URI for lazy activation.
            profile_hint: DEPRECATED - use source_hint instead.

        Returns:
            BundleModuleSource with the module path.

        Raises:
            ModuleNotFoundError: If module not found and activation fails.

        FIXME: Remove profile_hint parameter after all callers migrate to source_hint (target: v2.0).
        """
        hint = profile_hint if profile_hint is not None else source_hint
        # Fast path: already activated
        if module_id in self._paths:
            return BundleModuleSource(self._paths[module_id])

        # Lazy activation path
        if not self._activator:
            raise ModuleNotFoundError(
                f"Module '{module_id}' not found in prepared bundle and no activator available. "
                f"Available modules: {list(self._paths.keys())}"
            )

        if not hint:
            raise ModuleNotFoundError(
                f"Module '{module_id}' not found and no source hint provided for activation. "
                f"Available modules: {list(self._paths.keys())}"
            )

        # Thread-safe activation
        async with self._activation_lock:
            # Double-check after acquiring lock (another task may have activated)
            if module_id in self._paths:
                return BundleModuleSource(self._paths[module_id])

            logger.info(f"Lazy activating module '{module_id}' from '{hint}'")
            try:
                module_path = await self._activator.activate(module_id, hint)
                self._paths[module_id] = module_path
                logger.info(f"Successfully activated '{module_id}' at {module_path}")
                return BundleModuleSource(module_path)
            except Exception as e:
                logger.error(f"Failed to lazy-activate '{module_id}': {e}")
                raise ModuleNotFoundError(
                    f"Module '{module_id}' not found and activation failed: {e}"
                ) from e

    def get_module_source(self, module_id: str) -> str | None:
        """Get module source path as string.

        This method provides compatibility with StandardModuleSourceResolver's
        get_module_source() interface used by some app layers.

        Args:
            module_id: Module identifier.

        Returns:
            String path to module, or None if not found.
        """
        path = self._paths.get(module_id)
        return str(path) if path else None


@dataclass
class PreparedBundle:
    """A bundle that has been prepared for execution.

    Contains the mount plan, module resolver, and original bundle for
    spawning support.

    Attributes:
        mount_plan: Configuration for mounting modules.
        resolver: Resolver for finding module paths.
        bundle: The original Bundle that was prepared.
        bundle_package_paths: Paths to bundle src/ directories added to sys.path.
            These need to be shared with child sessions during spawning to ensure
            bundle packages (like amplifier_bundle_python_dev) remain importable.
    """

    mount_plan: dict[str, Any]
    resolver: BundleModuleResolver
    bundle: Bundle
    bundle_package_paths: list[str] = field(default_factory=list)

    def _build_bundles_for_resolver(self, bundle: "Bundle") -> dict[str, "Bundle"]:
        """Build bundle registry for mention resolution.

        Maps each namespace to a bundle with the correct base_path for that namespace.
        This allows @foundation:context/... to resolve relative to foundation's base_path.
        """
        from dataclasses import replace as dataclass_replace

        bundles_for_resolver: dict[str, Bundle] = {}
        namespaces = (
            list(bundle.source_base_paths.keys()) if bundle.source_base_paths else []
        )
        if bundle.name and bundle.name not in namespaces:
            namespaces.append(bundle.name)

        for ns in namespaces:
            if not ns:
                continue
            ns_base_path = bundle.source_base_paths.get(ns, bundle.base_path)
            if ns_base_path:
                bundles_for_resolver[ns] = dataclass_replace(
                    bundle, base_path=ns_base_path
                )
            else:
                bundles_for_resolver[ns] = bundle

        return bundles_for_resolver

    def _create_system_prompt_factory(
        self,
        bundle: "Bundle",
        session: Any,
        session_cwd: Path | None = None,
    ) -> "Callable[[], Awaitable[str]]":
        """Create a factory that produces fresh system prompt content on each call.

        The factory re-reads context files and re-processes @mentions each time,
        enabling dynamic content like AGENTS.md to be picked up immediately when
        modified during a session.

        Args:
            bundle: Bundle containing instruction, context files, and base paths.
            session: Session for capability access (e.g., extended mention resolver).
            session_cwd: Working directory for resolving local @-mentions like
                @AGENTS.md. If not provided, falls back to bundle.base_path.

        Returns:
            Async callable that returns the system prompt string.
        """

        from amplifier_foundation.mentions import BaseMentionResolver
        from amplifier_foundation.mentions import ContentDeduplicator
        from amplifier_foundation.mentions import format_context_block
        from amplifier_foundation.mentions import load_mentions

        # Capture state for the closure
        captured_bundle = bundle
        captured_self = self
        # Use session_cwd if provided, otherwise fall back to bundle's base_path
        captured_base_path = session_cwd or bundle.base_path or Path.cwd()

        async def factory() -> str:
            # Main instruction stays separate from context files
            main_instruction = captured_bundle.instruction or ""

            # Build bundle registry for resolver (using helper)
            bundles_for_resolver = captured_self._build_bundles_for_resolver(
                captured_bundle
            )

            # For local @-mentions (@AGENTS.md, @.amplifier/...), use session_cwd
            # Bundle-namespaced @-mentions (@foundation:path) use bundles_for_resolver
            resolver = BaseMentionResolver(
                bundles=bundles_for_resolver,
                base_path=captured_base_path,
            )

            # Fresh deduplicator each call (files may have changed)
            deduplicator = ContentDeduplicator()

            # Build mention_to_path map for context block attribution
            # This includes BOTH bundle context files AND @mentions from instruction
            mention_to_path: dict[str, Path] = {}

            # 1. Bundle context files (from context: section)
            # Add to deduplicator and mention_to_path for unified formatting
            for context_name, context_path in captured_bundle.context.items():
                if context_path.exists():
                    content = context_path.read_text(encoding="utf-8")
                    # Add to deduplicator for content-based deduplication
                    deduplicator.add_file(context_path, content)
                    # Add to mention_to_path for attribution (context_name → path)
                    mention_to_path[context_name] = context_path

            # 2. Resolve @mentions from main instruction (re-loads files each call)
            mention_results = await load_mentions(
                main_instruction,
                resolver=resolver,
                deduplicator=deduplicator,
            )

            # Add @mention results to mention_to_path for attribution
            for mr in mention_results:
                if mr.resolved_path:
                    mention_to_path[mr.mention] = mr.resolved_path

            # 3. Format ALL context as XML blocks (bundle context + @mentions)
            # format_context_block uses deduplicator for unique content and
            # mention_to_path for attribution (showing name → resolved path)
            all_context = format_context_block(deduplicator, mention_to_path)

            # Final structure: main instruction FIRST, then all context files
            if all_context:
                return f"{main_instruction}\n\n---\n\n{all_context}"
            else:
                return main_instruction

        return factory

    async def create_session(
        self,
        session_id: str | None = None,
        parent_id: str | None = None,
        approval_system: Any = None,
        display_system: Any = None,
        session_cwd: Path | None = None,
        is_resumed: bool = False,
    ) -> Any:
        """Create an AmplifierSession with the resolver properly mounted.

        This is a convenience method that handles the full setup:
        1. Creates AmplifierSession with mount plan
        2. Mounts the module resolver
        3. Initializes the session

        Note: Session spawning capability registration is APP-LAYER policy.
        Apps should register their own spawn capability that adapts the
        task tool's contract to foundation's spawn mechanism. See the
        end_to_end example for a reference implementation.

        Args:
            session_id: Optional session ID (for resuming existing session).
            parent_id: Optional parent session ID (for lineage tracking).
            approval_system: Optional approval system for hooks.
            display_system: Optional display system for hooks.
            session_cwd: Optional working directory for resolving local @-mentions
                like @AGENTS.md. Apps should pass their project/workspace directory.
                Defaults to bundle.base_path if not provided.
            is_resumed: Whether this session is being resumed (vs newly created).
                Controls whether session:start or session:resume events are emitted.

        Returns:
            Initialized AmplifierSession ready for execute().

        Example:
            prepared = await bundle.prepare()
            async with prepared.create_session() as session:
                response = await session.execute("Hello!")
        """
        from amplifier_core import AmplifierSession

        session = AmplifierSession(
            self.mount_plan,
            session_id=session_id,
            parent_id=parent_id,
            approval_system=approval_system,
            display_system=display_system,
            is_resumed=is_resumed,
        )

        # Mount the resolver before initialization
        await session.coordinator.mount("module-source-resolver", self.resolver)

        # Register bundle package paths for inheritance by child sessions
        # These are src/ directories from bundles like python-dev that need to be
        # on sys.path for their modules to import shared code
        if self.bundle_package_paths:
            session.coordinator.register_capability(
                "bundle_package_paths", list(self.bundle_package_paths)
            )

        # Register session working directory capability
        # This provides a unified way for tools/hooks to discover the working directory
        # instead of using Path.cwd() which returns the wrong value in server deployments.
        # The value can be updated during the session (e.g., if assistant "cd"s to subdir).
        effective_working_dir = session_cwd or self.bundle.base_path or Path.cwd()
        session.coordinator.register_capability(
            "session.working_dir", str(effective_working_dir.resolve())
        )

        # Initialize the session (loads all modules)
        await session.initialize()

        # Resolve any pending namespaced context references now that source_base_paths is available
        self.bundle.resolve_pending_context()

        # Register system prompt factory for dynamic @mention reprocessing
        # The factory is called on EVERY get_messages_for_request(), enabling:
        # - AGENTS.md changes to be picked up immediately
        # - Bundle instruction changes to take effect mid-session
        # - All @mentioned files to be re-read fresh each turn
        if (
            self.bundle.instruction
            or self.bundle.context
            or self.bundle._pending_context
        ):
            from amplifier_foundation.mentions import BaseMentionResolver
            from amplifier_foundation.mentions import ContentDeduplicator

            # Register resolver and deduplicator as capabilities for tools to use
            # (e.g., filesystem tool's read_file can resolve @mention paths)
            # Note: These are created once for capability registration, but the factory
            # creates fresh instances each call for accurate file re-reading
            bundles_for_resolver = self._build_bundles_for_resolver(self.bundle)
            # Use session_cwd for local @-mentions, fall back to bundle.base_path
            resolver_base = session_cwd or self.bundle.base_path or Path.cwd()
            initial_resolver = BaseMentionResolver(
                bundles=bundles_for_resolver,
                base_path=resolver_base,
            )
            initial_deduplicator = ContentDeduplicator()
            session.coordinator.register_capability(
                "mention_resolver", initial_resolver
            )
            session.coordinator.register_capability(
                "mention_deduplicator", initial_deduplicator
            )

            # Create and register the system prompt factory
            factory = self._create_system_prompt_factory(
                self.bundle, session, session_cwd=session_cwd
            )
            context_manager = session.coordinator.get("context")
            if context_manager and hasattr(
                context_manager, "set_system_prompt_factory"
            ):
                # Context manager supports dynamic system prompt - register factory
                await context_manager.set_system_prompt_factory(factory)
            elif context_manager:
                # FALLBACK: Context manager doesn't support dynamic factory.
                # Pre-resolve @mentions now and inject as system message.
                # Trade-off: Files won't be re-read mid-session, but @mentions work.
                resolved_prompt = await factory()
                await context_manager.add_message(
                    {"role": "system", "content": resolved_prompt}
                )

        return session

    async def spawn(
        self,
        child_bundle: Bundle,
        instruction: str,
        *,
        compose: bool = True,
        parent_session: Any = None,
        session_id: str | None = None,
        orchestrator_config: dict[str, Any] | None = None,
        parent_messages: list[dict[str, Any]] | None = None,
        session_cwd: Path | None = None,
        provider_preferences: list[ProviderPreference] | None = None,
        self_delegation_depth: int = 0,
    ) -> dict[str, Any]:
        """Spawn a sub-session with a child bundle.

        This is the library-level spawn method. It creates a child AmplifierSession,
        mounts modules from the bundle, executes the instruction, and returns the result.

        The app layer (CLI, API server) typically wraps this in a "spawn capability"
        function that handles additional concerns:
        - Resolving agent_name to a Bundle (this method takes a pre-resolved Bundle)
        - tool_inheritance / hook_inheritance (filtering which parent tools/hooks
          the child inherits — this is app-layer policy)
        - agent_configs (used by the app to look up agent configuration)

        See amplifier-app-cli/session_spawner.py for the reference production
        implementation of a full spawn capability.

        Args:
            child_bundle: Bundle to spawn (already resolved by app layer).
            instruction: Task instruction for the sub-session.
            compose: Whether to compose child with parent bundle (default True).
            parent_session: Parent session for lineage tracking and UX inheritance.
            session_id: Optional session ID for resuming existing session.
            orchestrator_config: Optional orchestrator config to override/merge into
                the spawned session's orchestrator settings (e.g., min_delay_between_calls_ms).
            parent_messages: Optional list of messages from parent session to inject
                into child's context before execution. Enables context inheritance
                where child can reference parent's conversation history.
            provider_preferences: Optional ordered list of provider/model preferences.
                The system tries each in order until finding an available provider.
                Model names support glob patterns (e.g., "claude-haiku-*").
            self_delegation_depth: Current delegation depth for depth limiting.
                When > 0, registered as a coordinator capability so
                depth-limiting tools can read it via get_capability().

        Returns:
            Dict with "output" (response) and "session_id".

        Example:
            # App layer resolves agent name to Bundle, then calls spawn
            child_bundle = resolve_agent_bundle("bug-hunter", agent_configs)
            result = await prepared.spawn(
                child_bundle,
                "Find the bug in auth.py",
            )

            # Resume existing session
            result = await prepared.spawn(
                child_bundle,
                "Continue investigating",
                session_id=previous_result["session_id"],
            )

            # Spawn without composition (standalone bundle)
            result = await prepared.spawn(
                complete_bundle,
                "Do something",
                compose=False,
            )

            # Spawn with provider preferences (fallback chain)
            result = await prepared.spawn(
                child_bundle,
                "Analyze this code",
                provider_preferences=[
                    ProviderPreference(provider="anthropic", model="claude-haiku-*"),
                    ProviderPreference(provider="openai", model="gpt-4o-mini"),
                ],
            )
        """
        # Compose with parent if requested
        effective_bundle = child_bundle
        if compose:
            effective_bundle = self.bundle.compose(child_bundle)

        # Get mount plan and create session
        child_mount_plan = effective_bundle.to_mount_plan()

        # Merge orchestrator config if provided (recipe-level override)
        if orchestrator_config:
            # Ensure orchestrator section exists
            if "orchestrator" not in child_mount_plan:
                child_mount_plan["orchestrator"] = {}
            if "config" not in child_mount_plan["orchestrator"]:
                child_mount_plan["orchestrator"]["config"] = {}
            # Merge recipe config into mount plan (recipe takes precedence)
            child_mount_plan["orchestrator"]["config"].update(orchestrator_config)

        # Apply provider preferences if specified
        # This is done before session creation so the mount plan has the right provider
        # We need to initialize a temporary session to resolve model patterns
        if provider_preferences:
            child_mount_plan = await apply_provider_preferences_with_resolution(
                child_mount_plan,
                provider_preferences,
                # Pass parent session's coordinator for model resolution if available
                parent_session.coordinator if parent_session else None,
            )

        from amplifier_core import AmplifierSession

        child_session = AmplifierSession(
            child_mount_plan,
            session_id=session_id,
            parent_id=parent_session.session_id if parent_session else None,
            approval_system=getattr(
                getattr(parent_session, "coordinator", None), "approval_system", None
            )
            if parent_session
            else None,
            display_system=getattr(
                getattr(parent_session, "coordinator", None), "display_system", None
            )
            if parent_session
            else None,
        )

        # Mount resolver and initialize
        await child_session.coordinator.mount("module-source-resolver", self.resolver)

        # Register session working directory capability for child session
        # Inherit from parent session if available, otherwise use session_cwd or defaults
        effective_child_cwd: Path
        if session_cwd:
            effective_child_cwd = session_cwd
        elif parent_session:
            # Try to inherit working_dir from parent session
            parent_wd = parent_session.coordinator.get_capability("session.working_dir")
            effective_child_cwd = (
                Path(parent_wd) if parent_wd else (self.bundle.base_path or Path.cwd())
            )
        else:
            effective_child_cwd = self.bundle.base_path or Path.cwd()
        child_session.coordinator.register_capability(
            "session.working_dir", str(effective_child_cwd.resolve())
        )

        await child_session.initialize()

        # Register self_delegation_depth as a coordinator capability
        # tool-delegate reads this via coordinator.get_capability("self_delegation_depth")
        if self_delegation_depth > 0:
            child_session.coordinator.register_capability(
                "self_delegation_depth", self_delegation_depth
            )

        # Inject parent messages if provided (for context inheritance)
        # This allows child sessions to have awareness of parent's conversation history.
        # Only inject for new sessions, not when resuming (session_id provided).
        if parent_messages and not session_id:
            child_context = child_session.coordinator.get("context")
            if child_context and hasattr(child_context, "set_messages"):
                await child_context.set_messages(parent_messages)

        # Register system prompt factory for dynamic @mention reprocessing
        # Note: For spawned sessions, we still want dynamic system prompts so that
        # any @mentioned files are fresh (though spawn sessions are typically short-lived)
        if effective_bundle.instruction or effective_bundle.context:
            factory = self._create_system_prompt_factory(
                effective_bundle, child_session, session_cwd=session_cwd
            )
            context = child_session.coordinator.get("context")
            if context and hasattr(context, "set_system_prompt_factory"):
                await context.set_system_prompt_factory(factory)
            elif context:
                # FALLBACK: Pre-resolve @mentions for context managers without factory support
                resolved_prompt = await factory()
                await context.add_message(
                    {"role": "system", "content": resolved_prompt}
                )

        # Capture orchestrator:complete event data from child session
        from amplifier_core.models import HookResult

        completion_data: dict[str, Any] = {}

        async def _capture_orchestrator_complete(
            event: str, data: dict[str, Any]
        ) -> HookResult:
            completion_data.update(data)
            return HookResult()

        # Register temporary hook to capture structured metadata
        unregister = child_session.coordinator.hooks.register(
            "orchestrator:complete",
            _capture_orchestrator_complete,
            priority=999,  # Run last — don't interfere with other hooks
            name="_spawn_completion_capture",
        )

        # Execute instruction and cleanup
        try:
            response = await child_session.execute(instruction)
        finally:
            # Unregister the temporary hook before cleanup
            unregister()
            await child_session.cleanup()

        return {
            "output": response,
            "session_id": child_session.session_id,
            # Enriched fields from orchestrator:complete event
            "status": completion_data.get("status", "success"),
            "turn_count": completion_data.get("turn_count", 1),
            "metadata": completion_data.get("metadata", {}),
        }
