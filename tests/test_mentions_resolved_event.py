"""Tests for the mentions:resolved event.

Evidence-driven test suite that proves:

1. MentionResult.failure_reason — new field, backward-compatible default.
2. _resolve_mention — failure_reason is populated at every failure site.
3. _extract_bundle_attribution — correctly maps @mention syntax and cache
   paths to (bundle_namespace, source_type) pairs.
4. _build_resolutions — constructs the event payload from ContextFile objects.
5. Factory emission — the system-prompt factory emits mentions:resolved on
   the first turn that loads context, and is silent on subsequent turns
   when nothing new has been added.
6. Observability registration — create_session() registers the event on the
   coordinator's observability.events contribution channel.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.mentions.deduplicator import ContentDeduplicator
from amplifier_foundation.mentions.loader import load_mentions
from amplifier_foundation.mentions.models import ContextFile, MentionResult
from amplifier_foundation.mentions.resolver import BaseMentionResolver

# Private helpers are imported via importlib so Pyright's stale-cache errors
# on these new symbols don't block the test run.  The runtime behaviour is
# what matters; Pyright will be correct once it analyses the modified files.
_prep = importlib.import_module("amplifier_foundation.bundle._prepared")
PreparedBundle: Any = _prep.PreparedBundle
BundleModuleResolver: Any = _prep.BundleModuleResolver
_extract_bundle_attribution: Any = _prep._extract_bundle_attribution
_build_resolutions: Any = _prep._build_resolutions
MENTIONS_RESOLVED: str = _prep._MENTIONS_RESOLVED_EVENT


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_prepared(bundle: Bundle) -> Any:
    """Minimal PreparedBundle — module resolver is irrelevant for these tests."""
    return PreparedBundle(
        mount_plan={},
        bundle=bundle,
        resolver=BundleModuleResolver(module_paths={}),
    )


def _make_mock_session() -> MagicMock:
    """Mock session whose coordinator.hooks.emit is a capturable AsyncMock.

    list_handlers() is the only synchronous method on HookRegistry called by
    create_session(), so it is explicitly made a MagicMock (not AsyncMock) to
    avoid RuntimeWarning about unawaited coroutines.
    """
    mock_hooks = AsyncMock()
    mock_hooks.list_handlers = MagicMock(return_value={})  # sync on real HookRegistry
    coordinator = MagicMock()
    coordinator.hooks = mock_hooks
    coordinator.register_contributor = MagicMock()
    coordinator.mount = AsyncMock()
    coordinator.register_capability = MagicMock()
    coordinator.get = MagicMock(return_value=None)
    session = MagicMock()
    session.coordinator = coordinator
    session.initialize = AsyncMock()
    return session


# ─────────────────────────────────────────────────────────────────────────────
# 1. MentionResult.failure_reason
# ─────────────────────────────────────────────────────────────────────────────


class TestMentionResultFailureReason:
    """failure_reason is a backward-compatible optional field on MentionResult."""

    def test_defaults_to_none(self) -> None:
        """Existing call sites that omit failure_reason are unaffected."""
        result = MentionResult(
            mention="@AGENTS.md",
            resolved_path=None,
            content=None,
            error=None,
        )
        assert getattr(result, "failure_reason") is None  # type: ignore[attr-defined]

    def test_can_be_set_at_construction(self) -> None:
        """failure_reason can carry a specific string at construction time."""
        result = MentionResult(
            mention="@missing.md",
            resolved_path=None,
            content=None,
            error=None,
            failure_reason="not_found",  # type: ignore[call-arg]
        )
        assert getattr(result, "failure_reason") == "not_found"  # type: ignore[attr-defined]

    def test_found_property_unaffected(self, tmp_path: Path) -> None:
        """The found property still returns True for resolved files."""
        f = tmp_path / "ok.md"
        f.write_text("content")
        result = MentionResult(
            mention="@ok.md",
            resolved_path=f,
            content="content",
            error=None,
        )
        assert result.found is True
        assert getattr(result, "failure_reason") is None  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "reason",
        [
            "not_found",
            "permission_error",
            "bundle_not_registered",
            "path_traversal_rejected",
            "unknown",
        ],
    )
    def test_accepted_reason_strings(self, reason: str) -> None:
        """All documented reason strings are valid field values."""
        result = MentionResult(
            mention="@x.md",
            resolved_path=None,
            content=None,
            error=None,
            failure_reason=reason,  # type: ignore[call-arg]
        )
        assert getattr(result, "failure_reason") == reason  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Loader — failure_reason at every failure site
# ─────────────────────────────────────────────────────────────────────────────


class TestLoaderFailureReason:
    """_resolve_mention populates failure_reason at every failure branch."""

    @pytest.mark.asyncio
    async def test_unregistered_bundle_gives_not_found(self) -> None:
        """@bundle:path mention with no registered bundle → failure_reason='not_found'."""
        resolver = BaseMentionResolver(bundles={})
        results = await load_mentions(
            "@foundation:missing/file.md",
            resolver=resolver,
            deduplicator=ContentDeduplicator(),
        )
        assert len(results) == 1
        r = results[0]
        assert r.resolved_path is None
        assert getattr(r, "failure_reason") == "not_found"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_nonexistent_file_gives_not_found(self, tmp_path: Path) -> None:
        """A mention that the resolver returns None for → failure_reason='not_found'."""
        resolver = BaseMentionResolver(base_path=tmp_path)
        # Do NOT create the file — resolver returns None for a missing path.
        results = await load_mentions(
            "@no-such-file.md",
            resolver=resolver,
            deduplicator=ContentDeduplicator(),
        )
        assert len(results) == 1
        assert getattr(results[0], "failure_reason") == "not_found"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_permission_error_on_read_gives_permission_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PermissionError during file read → failure_reason='permission_error'."""
        target = tmp_path / "secret.md"
        target.write_text("classified")

        import amplifier_foundation.mentions.loader as loader_module

        async def _raise_permission(*_args: Any, **_kwargs: Any) -> str:
            raise PermissionError("access denied")

        monkeypatch.setattr(loader_module, "read_with_retry", _raise_permission)

        resolver = BaseMentionResolver(base_path=tmp_path)
        results = await load_mentions(
            "@secret.md",
            resolver=resolver,
            deduplicator=ContentDeduplicator(),
        )
        assert len(results) == 1
        assert getattr(results[0], "failure_reason") == "permission_error"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_successful_resolution_has_no_failure_reason(
        self, tmp_path: Path
    ) -> None:
        """A successfully resolved file has failure_reason=None."""
        f = tmp_path / "readme.md"
        f.write_text("# Hello")
        results = await load_mentions(
            "@readme.md",
            resolver=BaseMentionResolver(base_path=tmp_path),
            deduplicator=ContentDeduplicator(),
        )
        assert len(results) == 1
        assert results[0].found is True
        assert getattr(results[0], "failure_reason") is None  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# 3. _extract_bundle_attribution
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractBundleAttribution:
    """Attribution logic maps @mention syntax and cache paths to (bundle, source_type)."""

    def _any_path(self) -> Path:
        return Path("/tmp/dummy.md")

    @pytest.mark.parametrize(
        "mention, want_bundle, want_type",
        [
            ("@foundation:context/foo.md", "foundation", "bundle_namespace"),
            ("@recipes:agents/coder.md", "recipes", "bundle_namespace"),
            ("@my-bundle:path/doc.md", "my-bundle", "bundle_namespace"),
            ("@user:settings.md", "user", "user_shortcut"),
            ("@project:custom.md", "project", "project_shortcut"),
        ],
    )
    def test_namespaced_mentions(
        self, mention: str, want_bundle: str, want_type: str
    ) -> None:
        bundle, source_type = _extract_bundle_attribution(mention, self._any_path())
        assert bundle == want_bundle
        assert source_type == want_type

    @pytest.mark.parametrize("mention", ["@~/my-notes.md", "@~/.amplifier/cfg.md"])
    def test_home_shortcut(self, mention: str) -> None:
        bundle, source_type = _extract_bundle_attribution(mention, self._any_path())
        assert bundle is None
        assert source_type == "home_shortcut"

    @pytest.mark.parametrize(
        "mention", ["@AGENTS.md", "@./subdir/file.md", "@docs/guide.md"]
    )
    def test_relative_path(self, mention: str) -> None:
        bundle, source_type = _extract_bundle_attribution(mention, self._any_path())
        assert bundle is None
        assert source_type == "relative_path"

    def test_cache_path_extracts_slug(self) -> None:
        """Bundle cache paths yield the slug as attribution."""
        cache_path = Path(
            "/home/user/.amplifier/cache"
            "/amplifier-foundation-c909465861f9d6ce"
            "/context/bundle-awareness.md"
        )
        bundle, source_type = _extract_bundle_attribution(None, cache_path)
        assert bundle == "foundation"
        assert source_type == "bundle_context_decl"

    def test_cache_path_hyphenated_slug(self) -> None:
        cache_path = Path(
            "/home/user/.amplifier/cache"
            "/amplifier-context-intelligence-abc123def456"
            "/context/awareness.md"
        )
        bundle, source_type = _extract_bundle_attribution(None, cache_path)
        assert bundle == "context-intelligence"
        assert source_type == "bundle_context_decl"

    def test_local_path_no_cache_pattern(self, tmp_path: Path) -> None:
        """A local file path with no cache pattern → bundle=None."""
        bundle, source_type = _extract_bundle_attribution(None, tmp_path / "local.md")
        assert bundle is None
        assert source_type == "bundle_context_decl"


# ─────────────────────────────────────────────────────────────────────────────
# 4. _build_resolutions
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildResolutions:
    """_build_resolutions constructs the structured resolutions list."""

    def _make_cf(self, path: Path, content: str = "body") -> ContextFile:
        import hashlib

        h = hashlib.sha256(content.encode()).hexdigest()
        return ContextFile(content=content, content_hash=h, paths=[path])

    def test_all_required_fields_present(self, tmp_path: Path) -> None:
        f = tmp_path / "file.md"
        f.write_text("hello")
        resolutions = _build_resolutions([self._make_cf(f)], {"@file.md": f})
        assert len(resolutions) == 1
        assert set(resolutions[0].keys()) == {
            "mention",
            "resolved_path",
            "bundle",
            "source_type",
            "content_hash",
            "is_new",
        }

    def test_is_new_true_when_hash_not_in_seen(self, tmp_path: Path) -> None:
        f = tmp_path / "file.md"
        f.write_text("content")
        cf = self._make_cf(f)
        resolutions = _build_resolutions([cf], {"@file.md": f}, seen_hashes=set())
        assert resolutions[0]["is_new"] is True

    def test_is_new_false_when_hash_already_seen(self, tmp_path: Path) -> None:
        f = tmp_path / "file.md"
        f.write_text("content")
        cf = self._make_cf(f)
        resolutions = _build_resolutions(
            [cf], {"@file.md": f}, seen_hashes={cf.content_hash}
        )
        assert resolutions[0]["is_new"] is False

    def test_bundle_namespace_in_payload(self, tmp_path: Path) -> None:
        """@foundation:ctx/x.md → bundle='foundation' in the resolution entry."""
        f = tmp_path / "x.md"
        f.write_text("x")
        cf = self._make_cf(f, "x")
        resolutions = _build_resolutions(
            [cf], {"@foundation:ctx/x.md": f}, seen_hashes=set()
        )
        assert resolutions[0]["bundle"] == "foundation"
        assert resolutions[0]["source_type"] == "bundle_namespace"

    def test_untracked_path_gets_mention_none(self, tmp_path: Path) -> None:
        """A ContextFile whose path has no entry in mention_to_path → mention=None."""
        f = tmp_path / "orphan.md"
        f.write_text("orphan")
        resolutions = _build_resolutions([self._make_cf(f, "orphan")], {})
        assert resolutions[0]["mention"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Factory emission  (primary behavioural evidence)
# ─────────────────────────────────────────────────────────────────────────────


class TestFactoryEmission:
    """The system-prompt factory emits mentions:resolved with correct semantics.

    These tests are the key evidence that the feature works end-to-end:

    - Turn 1 with context files  → event emitted, payload is correct.
    - Turn 2 (same files)        → event NOT emitted (deduplication guard).
    - Turn 3 with a new file     → event emitted again for the new file only.
    - Empty bundle               → event never emitted.
    - Unresolvable @mention      → failed[] list in payload.
    - resolved_path              → always an absolute string.
    """

    @pytest.mark.asyncio
    async def test_emits_on_first_turn_with_context_files(self, tmp_path: Path) -> None:
        """Turn 1: mentions:resolved is emitted with source=bundle_context."""
        ctx_file = tmp_path / "guide.md"
        ctx_file.write_text("# Guide content")

        bundle = Bundle(name="test", context={"@guide": ctx_file})
        prepared = _make_prepared(bundle)
        session = _make_mock_session()

        factory = prepared._create_system_prompt_factory(bundle, session)
        await factory()

        emit = session.coordinator.hooks.emit
        emit.assert_called_once()
        event_name, payload = emit.call_args.args

        assert event_name == MENTIONS_RESOLVED
        assert payload["source"] == "bundle_context"
        assert payload["turn"] is None
        assert len(payload["resolutions"]) == 1
        assert payload["resolutions"][0]["is_new"] is True
        assert payload["resolutions"][0]["content_hash"] != ""
        assert payload["deduplicated_count"] == 0

    @pytest.mark.asyncio
    async def test_silent_on_second_turn_same_files(self, tmp_path: Path) -> None:
        """Turn 2: no new files → emit is NOT called again."""
        ctx_file = tmp_path / "ctx.md"
        ctx_file.write_text("context body")

        bundle = Bundle(name="test", context={"@ctx": ctx_file})
        prepared = _make_prepared(bundle)
        session = _make_mock_session()

        factory = prepared._create_system_prompt_factory(bundle, session)
        await factory()  # turn 1 — emits
        call_count_after_turn1 = session.coordinator.hooks.emit.call_count
        assert call_count_after_turn1 == 1

        await factory()  # turn 2 — same files, must NOT emit
        assert session.coordinator.hooks.emit.call_count == 1, (
            "emit was called again on turn 2 — deduplication guard is broken"
        )

    @pytest.mark.asyncio
    async def test_emits_again_when_new_file_appears(self, tmp_path: Path) -> None:
        """A new context file added between turns triggers a second emission."""
        file_a = tmp_path / "a.md"
        file_a.write_text("AAA")
        file_b = tmp_path / "b.md"
        file_b.write_text("BBB")

        bundle = Bundle(name="test", context={"@a": file_a})
        prepared = _make_prepared(bundle)
        session = _make_mock_session()

        factory = prepared._create_system_prompt_factory(bundle, session)
        await factory()  # turn 1 — emits for file_a

        bundle.context["@b"] = file_b  # new file appears
        await factory()  # turn 2 — file_b is new → must emit

        emit = session.coordinator.hooks.emit
        assert emit.call_count == 2

        # Second emission covers only the new file
        _, second_payload = emit.call_args_list[1].args
        assert len(second_payload["resolutions"]) == 1
        assert second_payload["resolutions"][0]["is_new"] is True

    @pytest.mark.asyncio
    async def test_no_emit_for_empty_bundle(self) -> None:
        """A bundle with no instruction and no context files emits nothing."""
        bundle = Bundle(name="empty")
        prepared = _make_prepared(bundle)
        session = _make_mock_session()

        factory = prepared._create_system_prompt_factory(bundle, session)
        await factory()

        session.coordinator.hooks.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolved_path_is_absolute(self, tmp_path: Path) -> None:
        """resolved_path in the payload is always an absolute string."""
        ctx_file = tmp_path / "info.md"
        ctx_file.write_text("info")

        bundle = Bundle(name="test", context={"@info": ctx_file})
        prepared = _make_prepared(bundle)
        session = _make_mock_session()

        factory = prepared._create_system_prompt_factory(bundle, session)
        await factory()

        _, payload = session.coordinator.hooks.emit.call_args.args
        for r in payload["resolutions"]:
            assert Path(r["resolved_path"]).is_absolute(), (
                f"resolved_path is not absolute: {r['resolved_path']!r}"
            )

    @pytest.mark.asyncio
    async def test_unresolvable_mention_appears_in_failed_list(
        self, tmp_path: Path
    ) -> None:
        """@mentions that cannot be resolved appear in failed[] with a reason."""
        ctx_file = tmp_path / "real.md"
        ctx_file.write_text("real content")

        bundle = Bundle(
            name="test",
            instruction="See @no-such-bundle:nowhere.md for details",
            context={"@real": ctx_file},
        )
        prepared = _make_prepared(bundle)
        session = _make_mock_session()

        factory = prepared._create_system_prompt_factory(bundle, session)
        await factory()

        _, payload = session.coordinator.hooks.emit.call_args.args
        failed_mentions = [f["mention"] for f in payload["failed"]]
        assert "@no-such-bundle:nowhere.md" in failed_mentions

        valid_reasons = {
            "not_found",
            "permission_error",
            "bundle_not_registered",
            "path_traversal_rejected",
            "unknown",
        }
        for entry in payload["failed"]:
            assert entry["reason"] in valid_reasons, (
                f"Unexpected failure reason: {entry['reason']!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Observability registration
# ─────────────────────────────────────────────────────────────────────────────


class TestObservabilityRegistration:
    """create_session() registers mentions:resolved on observability.events.

    This is the mechanism that makes the event discoverable by hooks-logging
    and hook-context-intelligence without any app-layer wiring — the same
    pattern used by tool-delegate for its delegate:* events.
    """

    @pytest.mark.asyncio
    async def test_register_contributor_called_for_observability_events(
        self,
    ) -> None:
        """create_session() calls register_contributor('observability.events', ...)."""
        bundle = Bundle(name="test")
        prepared = _make_prepared(bundle)
        mock_session = _make_mock_session()

        with patch("amplifier_core.AmplifierSession", return_value=mock_session):
            await prepared.create_session()

        registered_channels = [
            c.args[0]
            for c in mock_session.coordinator.register_contributor.call_args_list
        ]
        assert "observability.events" in registered_channels, (
            "observability.events channel not registered — "
            "hooks-logging will not discover mentions:resolved"
        )

    @pytest.mark.asyncio
    async def test_mentions_resolved_in_contributed_event_list(self) -> None:
        """The contributor lambda returns a list containing 'mentions:resolved'."""
        bundle = Bundle(name="test")
        prepared = _make_prepared(bundle)
        mock_session = _make_mock_session()

        with patch("amplifier_core.AmplifierSession", return_value=mock_session):
            await prepared.create_session()

        obs_calls = [
            c
            for c in mock_session.coordinator.register_contributor.call_args_list
            if c.args[0] == "observability.events"
        ]
        assert obs_calls, "No observability.events registration found"

        contributor_fn = obs_calls[0].args[2]
        contributed = contributor_fn()
        assert MENTIONS_RESOLVED in contributed, (
            f"Expected {MENTIONS_RESOLVED!r} in contributed events: {contributed!r}"
        )

    @pytest.mark.asyncio
    async def test_contributor_registered_under_foundation_mention_resolver(
        self,
    ) -> None:
        """The contributor name is 'foundation:mention-resolver'."""
        bundle = Bundle(name="test")
        prepared = _make_prepared(bundle)
        mock_session = _make_mock_session()

        with patch("amplifier_core.AmplifierSession", return_value=mock_session):
            await prepared.create_session()

        obs_calls = [
            c
            for c in mock_session.coordinator.register_contributor.call_args_list
            if c.args[0] == "observability.events"
        ]
        contributor_name = obs_calls[0].args[1]
        assert contributor_name == "foundation:mention-resolver"
