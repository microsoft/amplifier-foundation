"""End-to-end shadow environment test: load real bundle, edit, save, verify with Foundation.

This script is designed to be run directly with the Amplifier Python interpreter
(which has both amplifier_configurator and amplifier_foundation installed).

Usage:
    ~/.local/share/uv/tools/amplifier/bin/python tests/e2e_shadow_test.py

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def fail(msg: str) -> None:
    print(f"  ✗  {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"     {msg}")


# ---------------------------------------------------------------------------
# Results accumulator
# ---------------------------------------------------------------------------

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        ok(label)
        return True
    else:
        msg = label + (f": {detail}" if detail else "")
        fail(msg)
        _failures.append(msg)
        return False


# ---------------------------------------------------------------------------
# Main test body
# ---------------------------------------------------------------------------


async def run_e2e() -> int:
    from amplifier_configurator import BundleConfigurator, PartKind
    from amplifier_foundation import load_bundle

    # -----------------------------------------------------------------------
    # Step 1: Load foundation bundle
    # -----------------------------------------------------------------------
    section("Step 1: Load 'foundation' bundle with full provenance")

    c = await BundleConfigurator.load("foundation")

    check("Bundle loaded without exception", c is not None)
    check(
        "root_name is 'foundation'",
        c.provenance.root_name == "foundation",
        f"got {c.provenance.root_name!r}",
    )

    behaviors = c.list_behaviors()
    check("Has behaviors (>0)", len(behaviors) > 0, f"got {len(behaviors)}")
    info(f"Behavior count: {len(behaviors)}")

    total_tok = c.total_tokens()
    check(
        "total_tokens() > 5,000 (context files are being counted)",
        total_tok > 5_000,
        f"got {total_tok} — if this fails, _pending_context backfill is broken",
    )
    info(f"Total tokens: {total_tok:,}")

    all_parts = c.list_parts()
    check("Has parts (>0)", len(all_parts) > 0, f"got {len(all_parts)}")
    info(f"Total parts: {len(all_parts)}")

    ctx_parts = c.list_parts(kind=PartKind.CONTEXT)
    check(
        "Has CONTEXT parts (>0)",
        len(ctx_parts) > 0,
        "0 context parts — _pending_context extraction may be broken",
    )
    info(f"Context parts: {len(ctx_parts)}")

    zero_tok_ctx = [p.name for p in ctx_parts if p.tokens == 0]
    check(
        "All CONTEXT parts have non-zero token counts",
        len(zero_tok_ctx) == 0,
        f"zero-token context parts: {zero_tok_ctx}",
    )

    tool_names = {p.name for p in c.list_parts(kind=PartKind.TOOL)}
    check(
        "'tool-bash' present",
        "tool-bash" in tool_names,
        f"tools found: {sorted(tool_names)}",
    )
    check("'tool-filesystem' present", "tool-filesystem" in tool_names)

    behavior_names = {b.name for b in behaviors}
    # Check structural presence: ≥5 named behaviors (not hardcoded names, since
    # the live foundation bundle changes as upstream repositories update).
    check(
        "at least 5 named behaviors loaded",
        sum(1 for n in behavior_names if n) >= 5,
        f"named behaviors: {sorted(n for n in behavior_names if n)}",
    )

    # -----------------------------------------------------------------------
    # Step 2: Token breakdown
    # -----------------------------------------------------------------------
    section("Step 2: Token breakdown by behavior")

    by_beh = c.tokens_by_behavior()
    check("tokens_by_behavior() returns non-empty dict", len(by_beh) > 0)
    check("'<root>' key present in breakdown", "<root>" in by_beh)

    total_from_breakdown = sum(by_beh.values())
    check(
        "tokens_by_behavior() sum roughly matches total_tokens()",
        abs(total_from_breakdown - total_tok) <= 50,
        f"breakdown sum={total_from_breakdown:,}  total_tokens={total_tok:,}",
    )

    info("Top 8 behaviors by token cost:")
    for name, tokens in list(by_beh.items())[:8]:
        info(f"  {name}: {tokens:,}")

    # -----------------------------------------------------------------------
    # Step 3: Remove behaviors
    # -----------------------------------------------------------------------
    section("Step 3: Remove behaviors (immutable mutation)")

    original_tokens = c.total_tokens()
    original_behavior_count = len(c.list_behaviors())

    removable_candidates = [
        b.name
        for b in behaviors
        if not any(x in b.name for x in ["foundation", "core", "base"])
    ]
    info(
        f"Removable candidates ({len(removable_candidates)}): {removable_candidates[:5]}..."
    )

    edited = c
    removed: list[str] = []

    for bname in removable_candidates[:3]:
        try:
            candidate = edited.remove_behavior(bname)
            # verify it actually reduced behaviors
            if len(candidate.list_behaviors()) < len(edited.list_behaviors()):
                edited = candidate
                removed.append(bname)
                info(f"  Removed: {bname}")
        except Exception as exc:
            info(f"  Skipped {bname!r}: {exc}")

    check(
        "At least one behavior was successfully removed",
        len(removed) > 0,
        f"no behaviors removed from candidates: {removable_candidates[:3]}",
    )

    new_tokens = edited.total_tokens()
    new_behavior_count = len(edited.list_behaviors())
    token_saving = original_tokens - new_tokens
    saving_pct = (token_saving / original_tokens * 100) if original_tokens else 0

    check(
        "Token count did not increase after removal",
        new_tokens <= original_tokens,
        f"{original_tokens:,} → {new_tokens:,}",
    )
    check(
        "Behavior count decreased",
        new_behavior_count < original_behavior_count,
        f"{original_behavior_count} → {new_behavior_count}",
    )

    info(f"Before: {original_tokens:,} tokens  |  {original_behavior_count} behaviors")
    info(f"After:  {new_tokens:,} tokens  |  {new_behavior_count} behaviors")
    info(f"Saved:  {token_saving:,} tokens ({saving_pct:.1f}%)")

    # -----------------------------------------------------------------------
    # Step 4: Diff
    # -----------------------------------------------------------------------
    section("Step 4: Diff (original vs. edited)")

    diff = c.diff(edited)

    check("diff.removed_behaviors is non-empty", len(diff.removed_behaviors) > 0)
    check(
        "diff.added_behaviors is empty",
        len(diff.added_behaviors) == 0,
        f"unexpected additions: {diff.added_behaviors}",
    )
    check(
        "diff.token_delta <= 0 (removing only reduces tokens)",
        diff.token_delta <= 0,
        f"token_delta={diff.token_delta}",
    )
    check(
        "diff.before_tokens matches original total_tokens()",
        diff.before_tokens == original_tokens,
        f"before={diff.before_tokens}  expected={original_tokens}",
    )
    check(
        "diff.after_tokens matches edited total_tokens()",
        diff.after_tokens == new_tokens,
        f"after={diff.after_tokens}  expected={new_tokens}",
    )

    info(f"Removed behaviors: {diff.removed_behaviors}")
    info(f"Removed parts:     {len(diff.removed_parts)}")
    info(f"Token delta:       {diff.token_delta:,}")

    # -----------------------------------------------------------------------
    # Step 5: Validate
    # -----------------------------------------------------------------------
    section("Step 5: Validate edited bundle")

    errors, warnings = edited.validate()

    check("validate() returns no hard errors", len(errors) == 0, f"errors: {errors}")
    info(f"Warnings ({len(warnings)}): {warnings[:3]}")

    # -----------------------------------------------------------------------
    # Step 6: Save to file
    # -----------------------------------------------------------------------
    section("Step 6: Save edited bundle to .md file")

    output_dir = Path(tempfile.mkdtemp(prefix="configurator_e2e_"))
    output_path = output_dir / "edited-foundation.md"

    try:
        saved_path = edited.save(str(output_path))
    except Exception as exc:
        fail(f"save() raised: {exc}")
        _failures.append(f"save() raised: {exc}")
        # Can't continue without the file
        _print_summary()
        return 1

    check("save() returned a Path", isinstance(saved_path, Path))
    check("Output file exists", output_path.exists())
    file_size = output_path.stat().st_size
    check("Output file is non-empty (>100 bytes)", file_size > 100, f"size={file_size}")
    info(f"Saved to: {output_path}")
    info(f"File size: {file_size:,} bytes")

    # -----------------------------------------------------------------------
    # Step 7: Verify saved file structure
    # -----------------------------------------------------------------------
    section("Step 7: Verify saved file structure")

    content = output_path.read_text()

    check("File starts with YAML frontmatter '---'", content.startswith("---"))
    check("File has closing frontmatter delimiter", content.count("---") >= 2)
    check("File contains 'includes:' section", "includes:" in content)

    check("No absolute '/Users/' paths leaked into file", "/Users/" not in content)
    check("No absolute '/home/' paths leaked into file", "/home/" not in content)
    check("No '/tmp/' paths leaked into file", "/tmp/" not in content)

    # Parse the YAML frontmatter
    import yaml as _yaml

    parts = content.split("---", 2)
    yaml_ok = len(parts) >= 3
    check(
        "File has at least 3 '---' split sections (header/yaml/body)",
        yaml_ok,
        f"got {len(parts)} sections",
    )

    if yaml_ok:
        try:
            parsed = _yaml.safe_load(parts[1])
            check(
                "YAML frontmatter parses without error",
                isinstance(parsed, dict),
                f"got {type(parsed)}",
            )
            if isinstance(parsed, dict):
                check("YAML has 'bundle' key", "bundle" in parsed)
                bundle_meta = parsed.get("bundle", {})
                check(
                    "Bundle has 'name' field",
                    "name" in bundle_meta,
                    f"keys: {list(bundle_meta)}",
                )
                check(
                    "Bundle name is 'foundation'",
                    bundle_meta.get("name") == "foundation",
                    f"got {bundle_meta.get('name')!r}",
                )

                has_includes = (
                    "includes" in parsed.get("bundle", {}) or "includes" in parsed
                )
                check("Saved bundle has 'includes' section", has_includes)

                # Check the removed behaviors are absent
                file_includes_raw = str(parsed)
                for rname in removed:
                    check(
                        f"Removed behavior '{rname}' not referenced in saved YAML",
                        rname not in file_includes_raw,
                        f"found in: {file_includes_raw[:200]}",
                    )

        except _yaml.YAMLError as exc:
            fail(f"YAML parse error: {exc}")
            _failures.append(f"YAML parse error: {exc}")

    info("Saved file preview (first 30 lines):")
    for i, line in enumerate(content.splitlines()[:30], 1):
        info(f"  {i:2d}: {line}")

    # -----------------------------------------------------------------------
    # Step 8: Verify saved file loads back with Foundation's load_bundle
    # -----------------------------------------------------------------------
    section("Step 8: Verify Foundation can load the saved file")

    try:
        foundation_bundle = await load_bundle(str(output_path), auto_include=True)
        check("Foundation load_bundle() succeeded on saved file", True)
        info(f"Bundle name:     {foundation_bundle.name!r}")
        info(f"Tools:           {len(foundation_bundle.tools)}")
        info(f"Hooks:           {len(foundation_bundle.hooks)}")
        info(f"Agents:          {len(foundation_bundle.agents)}")
        info(f"_pending_context:{len(foundation_bundle._pending_context)}")
        info(
            f"Instruction len: {len(foundation_bundle.instruction) if foundation_bundle.instruction else 0}"
        )

        check(
            "Loaded bundle has tools",
            len(foundation_bundle.tools) > 0,
            "empty tools list",
        )
        check(
            "Loaded bundle name matches",
            foundation_bundle.name == "foundation",
            f"got {foundation_bundle.name!r}",
        )

        # Verify removed behaviors are gone from the loaded bundle tools
        loaded_tool_names = {
            t.get("module") or t.get("id") for t in foundation_bundle.tools
        }
        info(f"Tool count in loaded bundle: {len(loaded_tool_names)}")

        check("ROUND-TRIP SUCCESS: Foundation loaded the saved file", True)

    except Exception as exc:
        # This can legitimately fail if the saved bundle references sub-behavior
        # URIs that Foundation can't resolve without a registry. But we still
        # want to surface the error clearly.
        info(f"Foundation load error: {exc}")
        info(
            "NOTE: This may be expected if sub-behavior URIs need Foundation's bundle registry."
        )
        check("ROUND-TRIP: Foundation accepted the saved file format", False, str(exc))

    # -----------------------------------------------------------------------
    # Step 9: Round-trip through BundleConfigurator.load (file path)
    # -----------------------------------------------------------------------
    section("Step 9: Round-trip — load saved file back with BundleConfigurator")

    try:
        c2 = await BundleConfigurator.load(str(output_path))
        check("BundleConfigurator.load() succeeded on saved file", True)
        info(f"  root_name: {c2.provenance.root_name!r}")
        info(f"  behaviors: {len(c2.list_behaviors())}")
        info(f"  tokens:    {c2.total_tokens():,}")
        check(
            "Round-trip behavior count <= original",
            len(c2.list_behaviors()) <= original_behavior_count,
        )
        check(
            "Round-trip token count <= original",
            c2.total_tokens() <= original_tokens + 50,
        )
    except Exception as exc:
        info(f"BundleConfigurator round-trip error: {exc}")
        check("BundleConfigurator round-trip succeeded", False, str(exc))

    # -----------------------------------------------------------------------
    # Print summary and return
    # -----------------------------------------------------------------------
    _print_summary()
    info(f"Output file preserved at: {output_path}")
    return 0 if not _failures else 1


def _print_summary() -> None:
    section("SUMMARY")
    if not _failures:
        print("\n  ALL CHECKS PASSED ✓\n")
    else:
        print(f"\n  {len(_failures)} CHECK(S) FAILED:\n")
        for f in _failures:
            print(f"    ✗ {f}")
        print()


if __name__ == "__main__":
    sys.exit(asyncio.run(run_e2e()))
