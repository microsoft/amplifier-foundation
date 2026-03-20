"""Bundle dataclass - the core composable unit."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

from amplifier_foundation.dicts.merge import deep_merge
from amplifier_foundation.dicts.merge import merge_module_lists
from amplifier_foundation.exceptions import BundleValidationError
from amplifier_foundation.paths.construction import construct_context_path

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
        progress_callback: Callable[[str, str], None] | None = None,
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
            progress_callback: Optional callback(action, detail) for progress reporting.
                Called at key phases during preparation to report what is happening.
                Actions include "installing_package", "activating", "installing".

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
        from amplifier_foundation.bundle._prepared import BundleModuleResolver, PreparedBundle
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
                await activator.activate_bundle_package(
                    self.base_path, progress_callback=progress_callback
                )

            # Install packages from all included bundles (from source_base_paths)
            for namespace, bundle_path in self.source_base_paths.items():
                if bundle_path and bundle_path != self.base_path:
                    await activator.activate_bundle_package(
                        bundle_path, progress_callback=progress_callback
                    )

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

        # Pre-activate modules declared in agent configs so child sessions
        # can find them via the inherited BundleModuleResolver.
        # Without this, spawned agent sessions fail silently when their
        # orchestrator/provider/tool modules aren't in the resolver's paths.
        agents_section = mount_plan.get("agents", {})
        for _agent_name, agent_def in agents_section.items():
            if not isinstance(agent_def, dict):
                continue

            # Agent's session orchestrator and context
            agent_session = agent_def.get("session", {})
            if isinstance(agent_session, dict):
                agent_orch = agent_session.get("orchestrator")
                if isinstance(agent_orch, dict) and "source" in agent_orch:
                    modules_to_activate.append(resolve_source(agent_orch))

                agent_ctx = agent_session.get("context")
                if isinstance(agent_ctx, dict) and "source" in agent_ctx:
                    modules_to_activate.append(resolve_source(agent_ctx))

            # Agent's providers, tools, hooks
            for agent_section in ("providers", "tools", "hooks"):
                agent_mods = agent_def.get(agent_section, [])
                if isinstance(agent_mods, list):
                    for mod_spec in agent_mods:
                        if isinstance(mod_spec, dict) and "source" in mod_spec:
                            modules_to_activate.append(resolve_source(mod_spec))

        # Activate all modules and get their paths
        module_paths = await activator.activate_all(
            modules_to_activate, progress_callback=progress_callback
        )

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
    if "provider_preferences" in frontmatter:
        result["provider_preferences"] = frontmatter["provider_preferences"]

    if "model_role" in frontmatter:
        result["model_role"] = frontmatter["model_role"]

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
