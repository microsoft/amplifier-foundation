"""Bundle dataclass - the core composable unit."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from amplifier_foundation.dicts.merge import deep_merge
from amplifier_foundation.dicts.merge import merge_module_lists
from amplifier_foundation.paths.construction import construct_context_path


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

    # Resources
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    context: dict[str, Path] = field(default_factory=dict)
    instruction: str | None = None

    # Internal
    base_path: Path | None = None

    def compose(self, *others: Bundle) -> Bundle:
        """Compose this bundle with others (later overrides earlier).

        Creates a new Bundle with merged configuration. For each section:
        - session: deep merge (later overrides)
        - providers/tools/hooks: merge by module ID
        - agents/context: later overrides earlier
        - instruction: later replaces earlier

        Args:
            others: Bundles to compose with.

        Returns:
            New Bundle with merged configuration.
        """
        result = Bundle(
            name=self.name,
            version=self.version,
            description=self.description,
            includes=list(self.includes),
            session=dict(self.session),
            providers=list(self.providers),
            tools=list(self.tools),
            hooks=list(self.hooks),
            agents=dict(self.agents),
            context=dict(self.context),
            instruction=self.instruction,
            base_path=self.base_path,
        )

        for other in others:
            # Metadata: later wins
            result.name = other.name or result.name
            result.version = other.version or result.version
            if other.description:
                result.description = other.description

            # Session: deep merge
            result.session = deep_merge(result.session, other.session)

            # Module lists: merge by module ID
            result.providers = merge_module_lists(result.providers, other.providers)
            result.tools = merge_module_lists(result.tools, other.tools)
            result.hooks = merge_module_lists(result.hooks, other.hooks)

            # Resources: later overrides
            result.agents.update(other.agents)
            result.context.update(other.context)

            # Instruction: later replaces
            if other.instruction:
                result.instruction = other.instruction

            # Base path: use other's if set
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

        return mount_plan

    async def prepare(self, install_deps: bool = True) -> PreparedBundle:
        """Prepare bundle for execution by activating all modules.

        Downloads and installs all modules specified in the bundle's mount plan,
        making them importable. Returns a PreparedBundle containing the mount plan
        and a module resolver for use with AmplifierSession.

        This is the turn-key method for apps that want to load a bundle and
        execute it without managing module resolution themselves.

        Args:
            install_deps: Whether to install Python dependencies for modules.

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
        """
        from amplifier_foundation.modules.activator import ModuleActivator

        # Get mount plan
        mount_plan = self.to_mount_plan()

        # Create activator
        activator = ModuleActivator(install_deps=install_deps)

        # Collect all modules that need activation
        modules_to_activate = []

        # Session orchestrator and context
        session_config = mount_plan.get("session", {})
        if isinstance(session_config.get("orchestrator"), dict):
            orch = session_config["orchestrator"]
            if "source" in orch:
                modules_to_activate.append(orch)
        if isinstance(session_config.get("context"), dict):
            ctx = session_config["context"]
            if "source" in ctx:
                modules_to_activate.append(ctx)

        # Providers, tools, hooks
        for section in ["providers", "tools", "hooks"]:
            for mod_spec in mount_plan.get(section, []):
                if isinstance(mod_spec, dict) and "source" in mod_spec:
                    modules_to_activate.append(mod_spec)

        # Activate all modules and get their paths
        module_paths = await activator.activate_all(modules_to_activate)

        # Create resolver from activated paths
        resolver = BundleModuleResolver(module_paths)

        return PreparedBundle(mount_plan=mount_plan, resolver=resolver, bundle=self)

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
        - "foundation:bug-hunter" -> strips prefix, looks for bug-hunter.md
        - "bug-hunter" -> looks for bug-hunter.md

        Args:
            name: Agent name (may include bundle prefix).

        Returns:
            Path to agent file, or None if not found.
        """
        if not self.base_path:
            return None

        # Strip bundle prefix if present (e.g., "foundation:bug-hunter" -> "bug-hunter")
        simple_name = name.split(":", 1)[-1] if ":" in name else name

        # Look in agents/ directory
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

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_path: Path | None = None) -> Bundle:
        """Create Bundle from parsed dict (from YAML/frontmatter).

        Args:
            data: Dict with bundle configuration.
            base_path: Path to bundle root directory.

        Returns:
            Bundle instance.
        """
        bundle_meta = data.get("bundle", {})

        return cls(
            name=bundle_meta.get("name", ""),
            version=bundle_meta.get("version", "1.0.0"),
            description=bundle_meta.get("description", ""),
            includes=data.get("includes", []),
            session=data.get("session", {}),
            providers=data.get("providers", []),
            tools=data.get("tools", []),
            hooks=data.get("hooks", []),
            agents=_parse_agents(data.get("agents", {}), base_path),
            context=_parse_context(data.get("context", {}), base_path),
            instruction=None,  # Set separately from markdown body
            base_path=base_path,
        )


def _parse_agents(agents_config: dict[str, Any], base_path: Path | None) -> dict[str, dict[str, Any]]:
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


def _parse_context(context_config: dict[str, Any], base_path: Path | None) -> dict[str, Path]:
    """Parse context config section.

    Handles both include lists and direct path mappings.
    Context names may have bundle prefix (e.g., "foundation:file.md") which
    should be stripped when constructing paths.
    """
    if not context_config:
        return {}

    result: dict[str, Path] = {}

    # Handle include list
    if "include" in context_config:
        for name in context_config["include"]:
            if base_path:
                # Strip bundle prefix if present (e.g., "foundation:file.md" -> "file.md")
                path_part = name.split(":", 1)[-1] if ":" in name else name
                result[name] = construct_context_path(base_path, path_part)

    # Handle direct path mappings
    for key, value in context_config.items():
        if key != "include" and isinstance(value, str):
            if base_path:
                result[key] = base_path / value
            else:
                result[key] = Path(value)

    return result


class BundleModuleSource:
    """Simple module source that returns a pre-resolved path."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def resolve(self) -> Path:
        """Return the pre-resolved module path."""
        return self._path


class BundleModuleResolver:
    """Module resolver for prepared bundles.

    Maps module IDs to their activated paths. Implements the kernel's
    ModuleSourceResolver protocol.
    """

    def __init__(self, module_paths: dict[str, Path]) -> None:
        """Initialize with activated module paths.

        Args:
            module_paths: Dict mapping module ID to local path.
        """
        self._paths = module_paths

    def resolve(self, module_id: str, hint: Any = None) -> BundleModuleSource:
        """Resolve module ID to source.

        Args:
            module_id: Module identifier (e.g., "tool-bash").
            hint: Optional hint (unused, for protocol compliance).

        Returns:
            BundleModuleSource with the module path.

        Raises:
            ModuleNotFoundError: If module not in activated paths.
        """
        if module_id not in self._paths:
            raise ModuleNotFoundError(
                f"Module '{module_id}' not found in prepared bundle. Available modules: {list(self._paths.keys())}"
            )
        return BundleModuleSource(self._paths[module_id])

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
    """

    mount_plan: dict[str, Any]
    resolver: BundleModuleResolver
    bundle: Bundle

    async def create_session(
        self,
        session_id: str | None = None,
        parent_id: str | None = None,
        approval_system: Any = None,
        display_system: Any = None,
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
        )

        # Mount the resolver before initialization
        await session.coordinator.mount("module-source-resolver", self.resolver)

        # Initialize the session (loads all modules)
        await session.initialize()

        return session

    async def spawn(
        self,
        child_bundle: Bundle,
        instruction: str,
        *,
        compose: bool = True,
        parent_session: Any = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Spawn a sub-session with a child bundle.

        This is the core spawning MECHANISM. It handles:
        1. Optionally composes child with parent bundle
        2. Creates a new session with the child's mount plan
        3. Injects system instruction if present
        4. Executes the instruction
        5. Returns the result

        Agent name resolution is APP-LAYER POLICY. Apps should resolve
        agent names to Bundle objects before calling this method.
        See the end_to_end example for a reference implementation.

        Args:
            child_bundle: Bundle to spawn (already resolved by app layer).
            instruction: Task instruction for the sub-session.
            compose: Whether to compose child with parent bundle (default True).
            parent_session: Parent session for lineage tracking and UX inheritance.
            session_id: Optional session ID for resuming existing session.

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
        """
        # Compose with parent if requested
        effective_bundle = child_bundle
        if compose:
            effective_bundle = self.bundle.compose(child_bundle)

        # Get mount plan and create session
        child_mount_plan = effective_bundle.to_mount_plan()

        from amplifier_core import AmplifierSession

        child_session = AmplifierSession(
            child_mount_plan,
            session_id=session_id,
            parent_id=parent_session.session_id if parent_session else None,
            approval_system=getattr(getattr(parent_session, "coordinator", None), "approval_system", None)
            if parent_session
            else None,
            display_system=getattr(getattr(parent_session, "coordinator", None), "display_system", None)
            if parent_session
            else None,
        )

        # Mount resolver and initialize
        await child_session.coordinator.mount("module-source-resolver", self.resolver)
        await child_session.initialize()

        # Inject system instruction if present
        if effective_bundle.instruction:
            context = child_session.coordinator.get("context")
            if context and hasattr(context, "add_message"):
                await context.add_message({"role": "system", "content": effective_bundle.instruction})

        # Execute instruction and cleanup
        try:
            response = await child_session.execute(instruction)
        finally:
            await child_session.cleanup()

        return {"output": response, "session_id": child_session.session_id}
