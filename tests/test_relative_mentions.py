"""Nested instructions resolve beside their source, without changing roots."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_foundation.mentions import (
    BaseMentionResolver,
    ContentDeduplicator,
    load_mentions,
    load_mentions_from_file,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("mention", ["@./tasks.md", "@./tasks"])
async def test_nested_reference_uses_referring_file_not_workspace(tmp_path, mention):
    write(tmp_path / "rules/AGENTS.md", f"Follow {mention} and @workspace.md")
    write(tmp_path / "workspace.md", "WORKSPACE RULE")
    write(tmp_path / "rules/tasks.md", "NESTED TASKS RULE")
    write(tmp_path / "tasks.md", "WRONG WORKSPACE RULE")
    resolver = BaseMentionResolver(base_path=tmp_path)
    dedup = ContentDeduplicator()
    await load_mentions("@rules/AGENTS.md", resolver, dedup)
    contents = [entry.content for entry in dedup.get_unique_files()]
    assert "NESTED TASKS RULE" in contents
    assert "WORKSPACE RULE" in contents
    assert "WRONG WORKSPACE RULE" not in contents
    assert resolver.base_path == tmp_path
    assert resolver.resolve("@tasks.md") == tmp_path / "tasks.md"


@pytest.mark.asyncio
async def test_same_content_different_roots_preserves_both_nested_files(tmp_path):
    for name in ("global", "project"):
        write(tmp_path / name / "AGENTS.md", "Read @./tasks.md")
        write(tmp_path / name / "tasks.md", f"{name} tasks rule @./AGENTS.md")
    dedup = ContentDeduplicator()
    await load_mentions(
        "@global/AGENTS.md @project/AGENTS.md",
        BaseMentionResolver(base_path=tmp_path),
        dedup,
    )
    entries = dedup.get_unique_files()
    assert len(entries) == 3
    assert len(entries[0].paths) == 2
    assert {entry.content for entry in entries} == {
        "Read @./tasks.md",
        "global tasks rule @./AGENTS.md",
        "project tasks rule @./AGENTS.md",
    }


@pytest.mark.asyncio
async def test_explicit_base_and_nested_namespace_home_absolute_paths(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    original_expanduser = Path.expanduser
    monkeypatch.setattr(
        Path,
        "expanduser",
        lambda self: (
            home / str(self)[2:]
            if str(self).startswith("~/")
            else original_expanduser(self)
        ),
    )
    write(home / "rules.md", "HOME RULE")
    absolute = write(tmp_path / "absolute.md", "ABSOLUTE RULE")
    write(tmp_path / "bundle/rules.md", "BUNDLE RULE @./child.md")
    write(tmp_path / "bundle/child.md", "BUNDLE CHILD")
    write(
        tmp_path / "workspace/AGENTS.md",
        f"@~/rules.md @{absolute} @bundle:rules.md @missing:rules.md @missing.md",
    )
    bundle = Bundle(name="bundle", base_path=tmp_path / "bundle")
    resolver = BaseMentionResolver(
        bundles={"bundle": bundle}, base_path=tmp_path / "wrong"
    )
    dedup = ContentDeduplicator()
    await load_mentions(
        "@AGENTS.md", resolver, dedup, relative_to=tmp_path / "workspace"
    )
    contents = [entry.content for entry in dedup.get_unique_files()]
    assert all(
        value in contents
        for value in (
            "HOME RULE",
            "ABSOLUTE RULE",
            "BUNDLE RULE @./child.md",
            "BUNDLE CHILD",
        )
    )
    assert resolver.base_path == tmp_path / "wrong"


@pytest.mark.asyncio
async def test_recursion_depth_and_legacy_resolver_contract(tmp_path):
    first = write(tmp_path / "first.md", "@second.md")
    second = write(tmp_path / "second.md", "@third.md")
    third = write(tmp_path / "third.md", "THIRD")

    class LegacyResolver:
        def resolve(self, mention):
            return {"@first.md": first, "@second.md": second, "@third.md": third}.get(
                mention
            )

    dedup = ContentDeduplicator()
    await load_mentions(
        "@first.md",
        LegacyResolver(),
        dedup,
        relative_to=tmp_path / "elsewhere",
        max_depth=1,
    )
    assert [entry.content for entry in dedup.get_unique_files()] == [
        "@second.md",
        "@third.md",
    ]


def test_scoped_resolution_keeps_subclass_policy(tmp_path):
    write(tmp_path / "rules/private.md", "PRIVATE")

    class RestrictedResolver(BaseMentionResolver):
        def resolve(self, mention):
            return None if "private" in mention else super().resolve(mention)

    resolver = RestrictedResolver(base_path=tmp_path)
    assert resolver.resolve_relative("@private.md", tmp_path / "rules") is None


@pytest.mark.asyncio
async def test_explicit_parent_reference_and_cycle_are_bounded(tmp_path):
    write(tmp_path / "rules/sub/AGENTS.md", "@../tasks.md")
    write(tmp_path / "rules/tasks.md", "RIGHT RULE @./sub/AGENTS.md")
    write(tmp_path / "tasks.md", "WRONG RULE")
    dedup = ContentDeduplicator()
    await load_mentions(
        "@rules/sub/AGENTS.md",
        BaseMentionResolver(base_path=tmp_path),
        dedup,
        max_depth=100,
    )
    assert [entry.content for entry in dedup.get_unique_files()] == [
        "@../tasks.md",
        "RIGHT RULE @./sub/AGENTS.md",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle_name", ["anchors-fixture", "work-fixture"])
async def test_tasks_rules_refresh_in_real_prompt_factory(
    tmp_path, monkeypatch, bundle_name
):
    home, workspace, cache = (
        tmp_path / name for name in ("home", "workspace", "cache")
    )
    original_expanduser = Path.expanduser
    monkeypatch.setattr(
        Path,
        "expanduser",
        lambda self: (
            home / str(self)[2:]
            if str(self).startswith("~/")
            else original_expanduser(self)
        ),
    )
    for root, label in (
        (home / ".amplifier", "GLOBAL"),
        (workspace / ".amplifier", "PROJECT"),
    ):
        write(root / "AGENTS.md", "Read @./rules/tasks.md")
        write(root / "rules/tasks.md", f"{label} TASKS RULE")
    write(workspace / "rules/tasks.md", "WRONG WORKSPACE RULE")
    write(workspace / "AGENTS.md", "BARE WORKSPACE RULE")
    write(cache / "context/system.md", "BUNDLE RULE @AGENTS.md")
    bundle = Bundle(
        name=bundle_name,
        base_path=cache,
        instruction=f"@{bundle_name}:context/system.md\n@~/.amplifier/AGENTS.md\n@.amplifier/AGENTS.md",
    )
    prepared = PreparedBundle({}, BundleModuleResolver({}), bundle)
    session = SimpleNamespace(
        coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock()))
    )
    render = prepared.create_system_prompt_factory(session, session_cwd=workspace)
    for factory in (
        render,
        render,
        prepared.create_system_prompt_factory(session, session_cwd=workspace),
    ):
        prompt = await factory()
        for label in (
            "GLOBAL TASKS RULE",
            "PROJECT TASKS RULE",
            "BARE WORKSPACE RULE",
        ):
            assert prompt.count(label) == 1
        assert "WRONG WORKSPACE RULE" not in prompt
    write(home / ".amplifier/rules/tasks.md", "UPDATED TASKS RULE")
    assert "UPDATED TASKS RULE" in await render()
    assert "GLOBAL TASKS RULE" not in await render()
    (workspace / ".amplifier/rules/tasks.md").unlink()
    assert "PROJECT TASKS RULE" not in await render()


@pytest.mark.asyncio
async def test_declared_context_files_share_recursive_loading_and_roots(tmp_path):
    workspace, cache = tmp_path / "workspace", tmp_path / "cache with spaces"
    write(workspace / "AGENTS.md", "WORKSPACE RULE")
    parent = write(cache / "context/rules.md", "@./tasks.md @AGENTS.md")
    write(cache / "context/tasks.md", "INCLUDED TASK RULE @./rules.md")
    bundle = Bundle(
        name="fixture", base_path=cache, instruction="Root", context={"rules": parent}
    )
    prepared = PreparedBundle({}, BundleModuleResolver({}), bundle)
    session = SimpleNamespace(
        coordinator=SimpleNamespace(hooks=SimpleNamespace(emit=AsyncMock()))
    )
    render = prepared.create_system_prompt_factory(session, session_cwd=workspace)
    for factory in (
        render,
        prepared.create_system_prompt_factory(session, session_cwd=workspace),
    ):
        prompt = await factory()
        assert prompt.count("INCLUDED TASK RULE") == 1
        assert prompt.count("WORKSPACE RULE") == 1
        assert str(parent) in prompt
    write(cache / "context/tasks.md", "EDITED INCLUDED RULE")
    assert "EDITED INCLUDED RULE" in await render()
    assert "INCLUDED TASK RULE" not in await render()
    dedup = ContentDeduplicator()
    await load_mentions_from_file(
        parent, BaseMentionResolver(base_path=workspace), dedup
    )
    assert len(dedup.get_unique_files()) == 3
