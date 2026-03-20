"""PreparedBundle and module resolver classes."""

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

from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_foundation.spawn_utils import apply_provider_preferences_with_resolution

from amplifier_foundation.bundle._dataclass import Bundle

logger = logging.getLogger(__name__)


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
                    ProviderPreference(provider="openai", model="gpt-5-mini"),
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
