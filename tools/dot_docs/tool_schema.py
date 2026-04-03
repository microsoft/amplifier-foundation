"""Tool schema token estimation for Amplifier module directories.

Reads a local Python tool module source file and estimates the JSON schema
token cost for each tool function defined within it.

Public API:
- :func:`estimate_module_tool_tokens` — Estimate token costs for a module dir
"""

import ast
import json
import re
from pathlib import Path


def _find_init_file(module_dir: Path) -> Path | None:
    """Find the package __init__.py inside an amplifier_module_* subdirectory."""
    matches = list(module_dir.glob("amplifier_module_*/__init__.py"))
    if not matches:
        return None
    return matches[0]


def _extract_balanced_braces(source: str, start: int) -> str | None:
    """Extract a balanced brace expression starting at *start* (must be '{').

    Handles single-quoted, double-quoted, and triple-quoted string literals
    so that ``{`` / ``}`` inside strings don't affect the depth count.

    Returns the substring ``source[start:end+1]`` on success, or ``None`` if
    the braces are never closed.
    """
    if start >= len(source) or source[start] != "{":
        return None

    depth = 0
    in_string = False
    string_char: str = ""
    i = start

    while i < len(source):
        ch = source[i]

        if in_string:
            # Backslash escape — skip the next character
            if ch == "\\":
                i += 2
                continue
            # Check for triple-quote close
            if len(string_char) == 3 and source[i : i + 3] == string_char:
                in_string = False
                i += 3
                continue
            # Check for single-char quote close
            if len(string_char) == 1 and ch == string_char:
                in_string = False
                i += 1
                continue
        else:
            # Check for triple-quote open
            if source[i : i + 3] in ('"""', "'''"):
                in_string = True
                string_char = source[i : i + 3]
                i += 3
                continue
            # Check for single-char quote open
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
                i += 1
                continue
            # Track brace depth
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[start : i + 1]

        i += 1

    return None  # Unbalanced braces


def _extract_description(class_source: str) -> str:
    """Extract a description string from a tool class source snippet.

    Tries, in order:
    1. Simple class attribute: ``description = "..."`` or triple-quoted variant
    2. ``base_description`` assignment inside a ``description`` property
    3. First triple-quoted string found inside the ``description`` property
    """
    # --- 1. Simple class attribute -------------------------------------------
    for pattern in (
        r'description\s*=\s*"""(.*?)"""',
        r"description\s*=\s*'''(.*?)'''",
        r'description\s*=\s*"((?:[^"\\]|\\.)*)"',
        r"description\s*=\s*'((?:[^'\\]|\\.)*)'",
    ):
        m = re.search(pattern, class_source, re.DOTALL)
        if m:
            return m.group(1)

    # --- 2 & 3. @property description ----------------------------------------
    prop_idx = class_source.find("def description")
    if prop_idx >= 0:
        desc_section = class_source[prop_idx:]

        # Look for base_description assignment (common pattern for dynamic descs)
        for pattern in (
            r'base_description\s*=\s*"""(.*?)"""',
            r"base_description\s*=\s*'''(.*?)'''",
        ):
            m = re.search(pattern, desc_section, re.DOTALL)
            if m:
                return m.group(1)

        # Fall back to any triple-quoted string inside the property body
        for pattern in (r'"""(.*?)"""', r"'''(.*?)'''"):
            m = re.search(pattern, desc_section, re.DOTALL)
            if m:
                return m.group(1)

    return ""


def _extract_input_schema(class_source: str) -> dict | None:
    """Extract and evaluate the ``input_schema`` property return dict.

    Finds the ``return {`` statement inside ``def input_schema``, extracts
    the balanced brace expression, and evaluates it with ``ast.literal_eval``.

    Returns the dict on success, or ``None`` if extraction or evaluation fails.
    """
    prop_idx = class_source.find("def input_schema")
    if prop_idx < 0:
        return None

    section = class_source[prop_idx:]

    return_match = re.search(r"\breturn\s*\{", section)
    if not return_match:
        return None

    brace_start = return_match.end() - 1  # index of the opening '{'
    dict_str = _extract_balanced_braces(section, brace_start)
    if dict_str is None:
        return None

    try:
        result = ast.literal_eval(dict_str)
    except Exception:
        return None

    if isinstance(result, dict):
        return result
    return None


def estimate_module_tool_tokens(module_dir: Path) -> dict | None:
    """Estimate token costs for tools defined in an Amplifier module directory.

    Reads the ``amplifier_module_*/__init__.py`` within *module_dir*, finds
    tool classes (those with an ``input_schema`` property), and estimates how
    many tokens their JSON schema + description would consume when passed to an
    LLM.

    Token estimation formula: ``len(json.dumps(schema) + description) // 4``

    Args:
        module_dir: Path to the module directory, e.g. ``modules/tool-delegate/``

    Returns:
        A dict of the form::

            {
                "tool_count": 1,
                "total_tokens": 350,
                "tools": [
                    {
                        "name": "delegate",
                        "description_tokens": 150,
                        "schema_tokens": 200,
                        "total_tokens": 350,
                    }
                ],
            }

        Returns ``None`` when:
        - *module_dir* does not exist
        - No ``amplifier_module_*/__init__.py`` is found
        - The source contains no ``input_schema`` property (hook module)
        - No tool classes can be extracted (malformed source)
    """
    if not module_dir.is_dir():
        return None

    init_file = _find_init_file(module_dir)
    if init_file is None:
        return None

    try:
        source = init_file.read_text(encoding="utf-8")
    except Exception:
        return None

    # Quick gate: hook modules have no input_schema property at all
    if "input_schema" not in source:
        return None

    # Find class definitions and collect those that are tool classes
    class_pattern = re.compile(r"^class\s+(\w+)", re.MULTILINE)
    class_matches = list(class_pattern.finditer(source))
    if not class_matches:
        return None

    tools = []
    for idx, match in enumerate(class_matches):
        start = match.start()
        end = (
            class_matches[idx + 1].start()
            if idx + 1 < len(class_matches)
            else len(source)
        )
        class_source = source[start:end]

        # A tool class must expose an input_schema property
        if "def input_schema" not in class_source:
            continue

        # Extract tool name attribute
        name_match = re.search(r"\bname\s*=\s*[\"']([^\"']+)[\"']", class_source)
        if not name_match:
            continue
        tool_name = name_match.group(1)

        # Extract description (best-effort; may be empty for dynamic properties)
        description = _extract_description(class_source)

        # Extract and parse the input_schema return dict
        schema_dict = _extract_input_schema(class_source)
        if schema_dict is None:
            continue

        # Compute token estimates
        desc_str = description or ""
        schema_str = json.dumps(schema_dict)
        total_tokens = len(schema_str + desc_str) // 4
        description_tokens = len(desc_str) // 4
        schema_tokens = len(schema_str) // 4

        tools.append(
            {
                "name": tool_name,
                "description_tokens": description_tokens,
                "schema_tokens": schema_tokens,
                "total_tokens": total_tokens,
            }
        )

    if not tools:
        return None

    return {
        "tool_count": len(tools),
        "total_tokens": sum(t["total_tokens"] for t in tools),
        "tools": tools,
    }
