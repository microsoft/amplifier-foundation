---
name: editing-files
description: "Use when about to make non-trivial file edits via edit_file or apply_patch -- exact-match rules, replace_all semantics, indentation traps, and when to read before editing."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


You have access to multiple file editing tools. Choose the right one for the job:

## apply_patch

**Best for:** Multi-file changes, large refactors, adding new files, deleting files, renaming files.

Use `apply_patch` when you need to:
- Modify multiple files in a single operation
- Create new files from scratch
- Delete or rename files
- Make surgical changes with context anchors

The tool description contains the complete format reference.

## edit_file (string replacement)

**Best for:** Small, targeted single-file edits where you know the exact text to replace.

Use `edit_file` when you need to:
- Replace a specific string in one file
- Make a single small change
- The old text is unique in the file

## write_file (full file write)

**Best for:** Creating new files or completely rewriting small files.

Use `write_file` when you need to:
- Create a brand new file with known content
- Completely replace a small file's content

## Decision Flow

1. Multiple files or file creation/deletion? -> `apply_patch`
2. Single small replacement in one file? -> `edit_file`
3. Writing a completely new small file? -> `write_file` or `apply_patch`
4. Large refactor across files? -> `apply_patch`

