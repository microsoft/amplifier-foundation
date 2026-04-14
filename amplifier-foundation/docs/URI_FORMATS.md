# URI Formats

Quick reference for source URIs. For complete details, see `parse_uri()` docstring in `paths/resolution.py`.

## Supported Formats

| Format | Example |
|--------|---------|
| **Local file** | `/path/to/bundle.md`, `./relative.md` |
| **Local directory** | `/path/to/bundle/` (finds `bundle.md` inside) |
| **Git HTTPS** | `git+https://github.com/org/repo@main` |
| **Git SSH** | `git+ssh://git@github.com/org/repo@main` |
| **Subdirectory** | `git+https://github.com/org/repo@main#subdirectory=path/to/bundle` |

## Common Examples

```python
from amplifier_foundation import load_bundle

# Local
bundle = await load_bundle("./bundles/dev.md")

# Git with branch
bundle = await load_bundle("git+https://github.com/microsoft/amplifier-foundation-bundle@main")

# Git with subdirectory
bundle = await load_bundle("git+https://github.com/org/repo@main#subdirectory=bundles/dev")
```

## Parsing URIs

```python
from amplifier_foundation import parse_uri, ParsedURI

parsed = parse_uri("git+https://github.com/org/repo@main#subdirectory=bundles/dev")
# parsed.scheme = "git+https"
# parsed.host = "github.com"
# parsed.path = "/org/repo"
# parsed.ref = "main"
# parsed.subpath = "bundles/dev"
```

## Namespace Resolution with Subdirectories

When loading a bundle via `#subdirectory=`, understand how the namespace and paths are determined:

### Namespace Comes from bundle.name

The namespace is **always** the `bundle.name` field from YAML frontmatter, NOT from the git URL:

```
# Repository structure:
amplifier-expert-cookbook/          # Git repo root
└── cli-tool-builder/               # Subdirectory containing bundle
    ├── bundle.md                   # Has: bundle.name: cli-tool-builder
    └── context/
        └── instructions.md
```

When loaded via:
```
git+https://github.com/org/amplifier-expert-cookbook@main#subdirectory=cli-tool-builder
```

| Question | Answer |
|----------|--------|
| **Namespace is:** | `cli-tool-builder` (from `bundle.name`) |
| **Namespace is NOT:** | `amplifier-expert-cookbook` (the repo name) |

### Path Resolution

Paths are relative to the **bundle root** (the subdirectory), not the git repository root:

```yaml
# ❌ WRONG: Including subdirectory in path
context:
  include:
    - cli-tool-builder:cli-tool-builder/context/instructions.md  # Duplicates path!

# ✅ CORRECT: Path relative to bundle root
context:
  include:
    - cli-tool-builder:context/instructions.md
```

**Rule:** If you loaded via `#subdirectory=X`, you're already "inside" X. Don't repeat it in paths.

---

## Reading the Source

For complete URI parsing logic:

```bash
python -c "from amplifier_foundation import parse_uri; help(parse_uri)"
```

Or read `amplifier_foundation/paths/resolution.py` directly.
