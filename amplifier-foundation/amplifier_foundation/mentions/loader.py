"""Load @mentioned files recursively."""

from __future__ import annotations

from pathlib import Path

from amplifier_foundation.io.files import read_with_retry

from .deduplicator import ContentDeduplicator
from .models import MentionResult
from .parser import parse_mentions
from .protocol import MentionResolverProtocol
from .utils import format_directory_listing


def format_context_block(
    deduplicator: ContentDeduplicator,
    mention_to_path: dict[str, Path] | None = None,
) -> str:
    """Format all loaded files as XML context blocks for prepending.

    Creates XML-wrapped context blocks that the LLM sees BEFORE the instruction.
    The @mentions in the original instruction remain as semantic references.

    Args:
        deduplicator: Deduplicator containing loaded context files.
        mention_to_path: Optional mapping from @mention strings to resolved paths,
            used to show both @mention and absolute path in XML attributes.

    Returns:
        Formatted context string with XML blocks, or empty string if no files.

    Example output:
        <context_file paths="@AGENTS.md → /home/user/project/AGENTS.md">
        [file content here]
        </context_file>

        <context_file paths="@foundation:context/KERNEL.md → /path/to/KERNEL.md">
        [file content here]
        </context_file>
    """
    unique_files = deduplicator.get_unique_files()
    if not unique_files:
        return ""

    # Build reverse lookup: path -> mention(s) for attribution
    path_to_mentions: dict[Path, list[str]] = {}
    if mention_to_path:
        for mention, path in mention_to_path.items():
            resolved = path.resolve()
            if resolved not in path_to_mentions:
                path_to_mentions[resolved] = []
            path_to_mentions[resolved].append(mention)

    blocks = []
    for cf in unique_files:
        # Build paths attribute showing @mention → absolute path for ALL paths
        # (ContextFile now tracks multiple paths where same content was found)
        path_displays = []
        for p in cf.paths:
            resolved = p.resolve()
            mentions = path_to_mentions.get(resolved, [])
            if mentions:
                # Show each @mention with its resolved path
                for m in mentions:
                    path_displays.append(f"{m} → {resolved}")
            else:
                # No @mention tracked, just show path
                path_displays.append(str(resolved))

        paths_attr = ", ".join(path_displays)
        block = f'<context_file paths="{paths_attr}">\n{cf.content}\n</context_file>'
        blocks.append(block)

    return "\n\n".join(blocks)


async def load_mentions(
    text: str,
    resolver: MentionResolverProtocol,
    deduplicator: ContentDeduplicator | None = None,
    relative_to: Path | None = None,
    max_depth: int = 3,
) -> list[MentionResult]:
    """Load @mentioned files recursively with deduplication.

    All mentions are opportunistic - if a file can't be found, it's
    silently skipped (no error raised).

    Args:
        text: Text containing @mentions.
        resolver: Resolver to convert mentions to paths.
        deduplicator: Optional deduplicator for content. If None, creates one.
        relative_to: Base path for relative mentions (defaults to cwd).
        max_depth: Maximum recursion depth to prevent infinite loops (default 3).

    Returns:
        List of MentionResult for each mention found.
    """
    if deduplicator is None:
        deduplicator = ContentDeduplicator()

    results: list[MentionResult] = []
    mentions = parse_mentions(text)

    for mention in mentions:
        result = await _resolve_mention(
            mention=mention,
            resolver=resolver,
            deduplicator=deduplicator,
            relative_to=relative_to,
            max_depth=max_depth,
            current_depth=0,
        )
        results.append(result)

    return results


async def _resolve_mention(
    mention: str,
    resolver: MentionResolverProtocol,
    deduplicator: ContentDeduplicator,
    relative_to: Path | None,
    max_depth: int,
    current_depth: int,
) -> MentionResult:
    """Resolve a single mention and recursively load its mentions."""
    # Resolve mention to path
    path = resolver.resolve(mention)
    if path is None:
        return MentionResult(
            mention=mention,
            resolved_path=None,
            content=None,
            error=None,  # Opportunistic - no error for not found
        )

    # Handle directories: generate listing as content
    if path.is_dir():
        try:
            content = format_directory_listing(path)
            deduplicator.add_file(path, content)
            return MentionResult(
                mention=mention,
                resolved_path=path,
                content=content,
                error=None,
                is_directory=True,
            )
        except (PermissionError, OSError):
            # Can't list directory - return without content
            return MentionResult(
                mention=mention,
                resolved_path=path,
                content=None,
                error=None,
                is_directory=True,
            )

    # Read file
    try:
        content = await read_with_retry(path)
    except (FileNotFoundError, OSError):
        return MentionResult(
            mention=mention,
            resolved_path=path,
            content=None,
            error=None,  # Opportunistic - no error for read failure
        )

    # Check for duplicate content
    if not deduplicator.add_file(path, content):
        return MentionResult(
            mention=mention,
            resolved_path=path,
            content=None,  # Already seen, don't include again
            error=None,
        )

    # Recursively load mentions from this file (if not at max depth)
    if current_depth < max_depth:
        nested_mentions = parse_mentions(content)
        for nested in nested_mentions:
            await _resolve_mention(
                mention=nested,
                resolver=resolver,
                deduplicator=deduplicator,
                relative_to=path.parent,
                max_depth=max_depth,
                current_depth=current_depth + 1,
            )

    return MentionResult(
        mention=mention,
        resolved_path=path,
        content=content,
        error=None,
    )
