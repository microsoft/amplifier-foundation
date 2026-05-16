---
name: code-intelligence
description: "Use when you need types, references, call hierarchy, or diagnostics for code -- anywhere LSP is more accurate than grep. Covers go-to-def, find-references, hover, incoming/outgoing calls, and language-specific (Python) nuances."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


You have access to the LSP (Language Server Protocol) tool for code intelligence operations.

## LSP vs Grep: When to Use Which

| Task | Use LSP | Use Grep |
|------|---------|----------|
| Find all calls to a function | `findReferences` - semantic, finds actual calls | May match comments, strings, similar names |
| Find where a symbol is defined | `goToDefinition` - jumps directly | May find multiple matches |
| Get type info or docstring | `hover` - shows full type signature | Not possible |
| Check for errors after editing | `diagnostics` - compiler errors/warnings | Not possible |
| Rename a symbol everywhere | `rename` - cross-file, semantic | Text-only find/replace, unsafe |
| Find text pattern in files | Not the right tool | Fast text search |
| Search across many files | Slower for bulk search | Fast parallel search |

**Rule of thumb**: Use LSP for semantic understanding, diagnostics, and refactoring. Use grep for text searching.

## When to Reach for LSP First

**Before grepping for a symbol, ask yourself:**
- "Am I looking for usages of this specific function/class?" → `findReferences`
- "Where is this defined?" → `goToDefinition`
- "What does this return/accept?" → `hover`
- "What calls this function?" → `incomingCalls`
- "Did my edit break anything?" → `diagnostics`
- "Rename this symbol everywhere" → `rename`
- "What can I do to fix this error?" → `codeAction`
- "What are the inferred types here?" → `inlayHints`

LSP gives **semantic** results (actual code relationships). Grep gives **text** matches (may include comments, strings, similar names). For code navigation, semantic wins.

## Most Useful Operations

**NAVIGATION (start here):**
1. **hover** — Get type info + docstring at a position (most reliable)
2. **findReferences** — Find all usages of a symbol
3. **goToDefinition** — Jump to where something is defined
4. **incomingCalls** / **outgoingCalls** — Trace call graphs

**VERIFICATION (after editing code):**
5. **diagnostics** — Get compiler errors and warnings for a file

**REFACTORING:**
6. **rename** — Cross-file semantic rename (returns edits to review)
7. **codeAction** — Get suggested fixes and refactorings

**INSPECTION:**
8. **inlayHints** — Get inferred types for a range of code

**EXTENSIONS:**
9. **customRequest** — Send server-specific methods (see language docs)

## After Editing Code

After editing code, use LSP to verify:
1. **diagnostics** — check for errors introduced by the edit
2. **codeAction** — get suggested fixes for any new errors
3. **rename** — if you need to rename a symbol, use this instead of find-and-replace

The diagnostics → codeAction → apply cycle is the AI equivalent of a developer watching compiler output and applying suggested fixes.

## Custom Extensions

Some language servers provide non-standard extensions accessible via **customRequest**. See language-specific context docs for available extensions.

Example: `customRequest(customMethod="server/someExtension", customParams={...})`

## All Available Operations

- **goToDefinition**: Find where a symbol is defined
- **findReferences**: Find all references to a symbol
- **hover**: Get documentation and type info for a symbol
- **documentSymbol**: Get all symbols in a document (may be slow)
- **workspaceSymbol**: Search for symbols across the workspace (needs indexing time)
- **goToImplementation**: Find implementations of interfaces/abstract methods (not supported by all servers — declared per-language via capabilities)
- **prepareCallHierarchy**: Get call hierarchy item at position
- **incomingCalls**: Find functions that call the target function
- **outgoingCalls**: Find functions called by the target function
- **diagnostics**: Get compiler errors and warnings for a file
- **rename**: Semantic cross-file rename (returns edits, does not apply them)
- **codeAction**: Get suggested fixes and refactorings for a range
- **inlayHints**: Get inferred types and parameter names for a range
- **customRequest**: Send any server-specific LSP method

## Line/Character Numbers

LSP uses 1-based line and character numbers (as shown in editors).

## Example Usage

To find where a function is defined:
```
LSP operation=goToDefinition file_path=/path/to/file.py line=42 character=15
```

To find all callers of a function:
```
LSP operation=incomingCalls file_path=/path/to/file.py line=42 character=15
```

To check for errors after editing:
```
LSP operation=diagnostics file_path=/path/to/file.py
```

## Troubleshooting

If LSP operations fail silently or return empty:
1. **Server not installed**: Check that the language server is installed
2. **Broken installation**: Reinstall the language server if you see interpreter errors
3. **Indexing not complete**: `workspaceSymbol` needs time to index — try `hover` or `findReferences` first
4. **Wrong position**: Ensure cursor is on the symbol name, not whitespace
5. **Server does not support operation**: Not all servers support all operations — the tool returns clear errors when an operation is unsupported


You have access to Python code intelligence via the LSP tool with Pyright.

## Quick Start - Most Useful Operations

| Want to... | Use this |
|------------|----------|
| See what type a variable is | `hover` on the variable |
| Find all usages of a function | `findReferences` on the function name |
| Jump to a function's definition | `goToDefinition` on a call site |
| See what calls a function | `incomingCalls` on the function |
| See what a function calls | `outgoingCalls` on the function |

**Tip**: `hover` and `findReferences` are the most reliable. Start with these.

## Python-Specific Capabilities

- **Type Information**: Get precise type hints and inferred types
- **Import Resolution**: Trace imports across the project
- **Class Hierarchies**: Navigate inheritance chains
- **Method Resolution Order**: Understand Python MRO
- **Virtual Environments**: Respects pyproject.toml and venv configurations

## Effective Python Navigation

### Finding Class Definitions
1. Position cursor on class name
2. Use `goToDefinition` to find where it's defined
3. Use `findReferences` to see all usages

### Understanding Type Hierarchies
1. Use `hover` on a class to see its bases
2. Use `findReferences` on the base class and filter for `class` definitions (goToImplementation not supported by Pyright)
3. Navigate inheritance with repeated `goToDefinition`

### Tracing Function Calls
1. Position on function name
2. Use `incomingCalls` to see what calls this function
3. Use `outgoingCalls` to see what this function calls

## Common Patterns

### Finding Where an Exception is Raised
```
1. hover on ExceptionType to understand it
2. workspaceSymbol to find all definitions
3. findReferences on each to see raise statements
```

### Understanding a Decorator
```
1. goToDefinition on @decorator_name
2. hover to see signature and docstring
3. outgoingCalls to see what it wraps
```

### Navigating Imports
```
1. goToDefinition on imported name
2. Follow chain through __init__.py files
3. documentSymbol to see module structure
```

## Workspace Detection

The Python LSP detects workspace root by looking for:
- pyproject.toml (preferred)
- setup.py
- setup.cfg
- requirements.txt
- .git directory

Ensure your project has one of these at the root for accurate analysis.

## Known Limitations

### Operations Not Fully Supported by Pyright

- **goToImplementation**: Returns empty results. Pyright doesn't support finding subclasses/implementations directly.
  - **Workaround**: Use `findReferences` on the base class and filter for `class` definitions.

- **workspaceSymbol**: May return empty on first use before workspace is indexed.
  - **Workaround**: Run `documentSymbol` on relevant files first to trigger indexing, then retry.

### Type Resolution

- Complex generic types or dynamically-created classes may show as `Unknown`
- Missing stub packages (e.g., `types-requests`) can cause type resolution failures
- Circular imports may confuse type inference

## Common Installation Issues

### "Cannot find module" pointing to Homebrew Cellar path

This usually means you have stale wrapper scripts from a previous Homebrew installation:

1. **Check**: `cat $(which pyright)` - if it's a bash script pointing to `/opt/homebrew/Cellar/...`, it's stale
2. **Fix**: Remove stale wrappers (the error message will show the exact path):
   ```bash
   rm ~/.local/bin/pyright ~/.local/bin/pyright-langserver
   ```
3. **Reinstall**: `npm install -g pyright`
4. **Verify**: `which pyright && pyright --version`

### npm install succeeds but LSP still fails

The new installation might be shadowed by an older one earlier in PATH:

1. **Check**: `which -a pyright` to see all locations
2. **Remove stale ones**: Usually in `~/.local/bin/` or old Homebrew paths
3. **Verify**: The first result of `which pyright` should be the working one

### "bad interpreter" error

The pyright script has a broken shebang (common after Homebrew updates):

1. **Check**: `head -1 $(which pyright)` - look for `@@HOMEBREW_PREFIX@@` or missing node path
2. **Fix**: Remove and reinstall via npm:
   ```bash
   rm $(which pyright)
   npm install -g pyright
   ```

### Both pyright AND pyright-langserver need to work

The LSP uses `pyright-langserver`, not just `pyright`. Both can have stale wrappers:

```bash
# Check both
which pyright && pyright --version
which pyright-langserver

# If either fails, remove stale wrappers from the reported path
```

