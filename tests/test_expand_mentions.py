"""Tests for expand_mentions_in_instruction helper."""

from __future__ import annotations

from pathlib import Path
import pytest

from amplifier_foundation.mentions.deduplicator import ContentDeduplicator
from amplifier_foundation.mentions.loader import expand_mentions_in_instruction
from amplifier_foundation.mentions.protocol import MentionResolverProtocol


# ---------------------------------------------------------------------------
# Stub resolver
# ---------------------------------------------------------------------------


class StubResolver:
    """MentionResolverProtocol implementation backed by a dict."""

    def __init__(self, mapping: dict[str, Path | None]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def resolve(self, mention: str) -> Path | None:
        self.calls.append(mention)
        return self._mapping.get(mention)


# Confirm StubResolver satisfies the protocol (static check via assignment)
_: MentionResolverProtocol = StubResolver({})


# ---------------------------------------------------------------------------
# TestExpandMentionsInInstruction
# ---------------------------------------------------------------------------


class TestExpandMentionsInInstruction:
    """Unit tests for expand_mentions_in_instruction."""

    @pytest.mark.asyncio
    async def test_empty_instruction_returns_unchanged(self) -> None:
        """Empty string is returned unchanged (no resolver calls)."""
        resolver = StubResolver({})
        result = await expand_mentions_in_instruction("", resolver=resolver)
        assert result == ""
        assert resolver.calls == []

    @pytest.mark.asyncio
    async def test_no_mentions_returns_unchanged(self) -> None:
        """Instruction with no @mentions is returned unchanged."""
        resolver = StubResolver({})
        instruction = "Do the thing and make it work."
        result = await expand_mentions_in_instruction(instruction, resolver=resolver)
        assert result == instruction

    @pytest.mark.asyncio
    async def test_one_resolvable_mention_prepends_block(self, tmp_path: Path) -> None:
        """Single resolvable @mention prepends an XML context_file block.

        The original @mention is preserved verbatim in the body.
        """
        content = "# Hello from file"
        file = tmp_path / "notes.md"
        file.write_text(content, encoding="utf-8")

        resolver = StubResolver({"@notes.md": file})
        instruction = "Read @notes.md carefully."

        result = await expand_mentions_in_instruction(
            instruction, resolver=resolver, relative_to=tmp_path
        )

        # Block comes first
        assert result.startswith("<context_file")
        # File content is in the block
        assert content in result
        # Original instruction is preserved (with @mention verbatim)
        assert "Read @notes.md carefully." in result
        # Block and instruction separated by double newline
        parts = result.split("\n\n", 1)
        assert len(parts) == 2
        assert parts[1] == instruction

    @pytest.mark.asyncio
    async def test_one_unresolvable_mention_returns_unchanged(self) -> None:
        """@mention that resolves to None leaves the instruction unchanged."""
        resolver = StubResolver({"@missing.md": None})
        instruction = "Check @missing.md please."
        result = await expand_mentions_in_instruction(instruction, resolver=resolver)
        assert result == instruction

    @pytest.mark.asyncio
    async def test_multiple_mentions_same_content_single_block(
        self, tmp_path: Path
    ) -> None:
        """Two @mentions pointing to the same file produce one block (dedup).

        The paths attribute lists both @mentions for attribution.
        """
        content = "Shared content"
        file = tmp_path / "shared.md"
        file.write_text(content, encoding="utf-8")

        # Both @a.md and @b.md map to the same file
        resolver = StubResolver({"@a.md": file, "@b.md": file})
        instruction = "See @a.md and also @b.md."

        result = await expand_mentions_in_instruction(instruction, resolver=resolver)

        # Exactly one context_file block
        assert result.count("<context_file") == 1
        assert result.count("</context_file>") == 1
        # Shared content appears once
        assert result.count(content) == 1
        # Original instruction preserved
        assert instruction in result

    @pytest.mark.asyncio
    async def test_resolver_is_actually_called(self, tmp_path: Path) -> None:
        """The resolver's resolve() method is called for each @mention found."""
        file = tmp_path / "doc.md"
        file.write_text("content", encoding="utf-8")

        resolver = StubResolver({"@doc.md": file})
        instruction = "Look at @doc.md carefully"

        await expand_mentions_in_instruction(instruction, resolver=resolver)

        assert "@doc.md" in resolver.calls

    @pytest.mark.asyncio
    async def test_external_deduplicator_respected(self, tmp_path: Path) -> None:
        """When a deduplicator is supplied, it is used (not a fresh one).

        Two @mentions in the same instruction that resolve to the same file
        content produce only one context_file block (the deduplicator tracks
        the first and suppresses the second from the unique-files listing).
        """
        shared_content = "Shared content across both mentions"
        file = tmp_path / "shared.md"
        file.write_text(shared_content, encoding="utf-8")

        # Both mentions resolve to the same file — the deduplicator should
        # track the content after the first and mark the second as duplicate.
        dedup = ContentDeduplicator()
        resolver = StubResolver({"@shared.md": file, "@alias.md": file})
        instruction = "Check @shared.md and @alias.md here"

        result = await expand_mentions_in_instruction(
            instruction, resolver=resolver, deduplicator=dedup
        )

        # Only one block should be emitted (dedup merged them)
        assert result.count("<context_file") == 1
        # The content appears once
        assert result.count(shared_content) == 1
        # The deduplicator was used (its state reflects the file)
        assert dedup.is_seen(shared_content)

    @pytest.mark.asyncio
    async def test_xml_format_has_paths_attribute(self, tmp_path: Path) -> None:
        """The context_file block has a paths attribute showing @mention → path."""
        content = "file content"
        file = tmp_path / "ctx.md"
        file.write_text(content, encoding="utf-8")

        resolver = StubResolver({"@ctx.md": file})
        instruction = "Use @ctx.md for reference"

        result = await expand_mentions_in_instruction(instruction, resolver=resolver)

        assert 'paths="' in result
        assert "@ctx.md" in result
        assert str(file.resolve()) in result

    @pytest.mark.asyncio
    async def test_multiple_different_files_produce_multiple_blocks(
        self, tmp_path: Path
    ) -> None:
        """Each distinct resolvable @mention produces its own context_file block."""
        file_a = tmp_path / "alpha.md"
        file_b = tmp_path / "beta.md"
        file_a.write_text("alpha content", encoding="utf-8")
        file_b.write_text("beta content", encoding="utf-8")

        resolver = StubResolver({"@alpha.md": file_a, "@beta.md": file_b})
        instruction = "Read @alpha.md and @beta.md please"

        result = await expand_mentions_in_instruction(instruction, resolver=resolver)

        assert result.count("<context_file") == 2
        assert "alpha content" in result
        assert "beta content" in result
        assert instruction in result
