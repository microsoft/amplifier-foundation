"""Markdown to Slack Block Kit conversion utilities."""

import re
from typing import Any


def markdown_to_blocks(content: str, content_type: str = "response") -> list[dict[str, Any]]:
    """Convert markdown content to Slack Block Kit blocks.
    
    Supports:
    - Headers (# ## ###) -> section with bold text
    - Code blocks (```) -> section with code formatting
    - Inline code (`) -> preserved
    - Bold (**text**) -> *text*
    - Italic (*text* or _text_) -> _text_
    - Lists (- or *) -> preserved (Slack supports basic lists in mrkdwn)
    - Links [text](url) -> <url|text>
    
    Args:
        content: Markdown content
        content_type: Type of content for styling (response, thinking, error)
        
    Returns:
        List of Block Kit blocks
    """
    if not content.strip():
        return []
    
    blocks: list[dict[str, Any]] = []
    
    # Process code blocks first (preserve them)
    code_block_pattern = r"```(\w*)\n?(.*?)```"
    parts = re.split(code_block_pattern, content, flags=re.DOTALL)
    
    i = 0
    while i < len(parts):
        if i + 2 < len(parts) and parts[i + 1] is not None:
            # Text before code block
            if parts[i].strip():
                blocks.extend(_text_to_blocks(parts[i]))
            
            # Code block
            lang = parts[i + 1] or ""
            code = parts[i + 2]
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```{code.strip()}```",
                },
            })
            i += 3
        else:
            # Regular text
            if parts[i].strip():
                blocks.extend(_text_to_blocks(parts[i]))
            i += 1
    
    # Add styling based on content type
    if content_type == "error" and blocks:
        # Prepend error emoji
        if blocks[0].get("type") == "section":
            text = blocks[0].get("text", {})
            if text.get("type") == "mrkdwn":
                text["text"] = f"❌ {text['text']}"
    
    return blocks


def _text_to_blocks(text: str) -> list[dict[str, Any]]:
    """Convert text segment to blocks, handling headers and paragraphs."""
    blocks: list[dict[str, Any]] = []
    
    # Split by double newlines for paragraphs
    paragraphs = re.split(r"\n\n+", text.strip())
    
    for para in paragraphs:
        if not para.strip():
            continue
            
        converted = _convert_markdown_text(para)
        
        # Check if it's a header
        header_match = re.match(r"^(#{1,3})\s+(.+)$", para.strip(), re.MULTILINE)
        if header_match:
            level = len(header_match.group(1))
            header_text = header_match.group(2)
            # Use bold for headers in Slack
            if level == 1:
                converted = f"*{header_text}*"
            elif level == 2:
                converted = f"*{header_text}*"
            else:
                converted = f"*{header_text}*"
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": converted,
            },
        })
    
    return blocks


def _convert_markdown_text(text: str) -> str:
    """Convert markdown formatting to Slack mrkdwn format."""
    result = text
    
    # Convert headers to bold
    result = re.sub(r"^#{1,3}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)
    
    # Convert bold: **text** -> *text*
    result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)
    
    # Convert links: [text](url) -> <url|text>
    result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", result)
    
    # Italic is the same: *text* or _text_ stays as _text_
    # But we need to not break the bold we just created
    # So only convert *text* to _text_ if it's not already bold
    # This is tricky - for now, leave single asterisks as-is since Slack
    # interprets single * as bold anyway
    
    return result


def split_long_message(content: str, max_length: int = 39000) -> list[str]:
    """Split a long message into chunks that fit within Slack's limit.
    
    Tries to split at natural boundaries:
    1. Paragraph breaks
    2. Line breaks
    3. Word boundaries
    
    Args:
        content: Content to split
        max_length: Maximum length per chunk (default 39000, below Slack's 40k limit)
        
    Returns:
        List of content chunks
    """
    if len(content) <= max_length:
        return [content]
    
    chunks: list[str] = []
    remaining = content
    
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break
        
        # Find a good split point
        split_point = max_length
        
        # Try to split at paragraph boundary
        para_break = remaining.rfind("\n\n", 0, max_length)
        if para_break > max_length // 2:
            split_point = para_break + 2
        else:
            # Try to split at line boundary
            line_break = remaining.rfind("\n", 0, max_length)
            if line_break > max_length // 2:
                split_point = line_break + 1
            else:
                # Try to split at word boundary
                word_break = remaining.rfind(" ", 0, max_length)
                if word_break > max_length // 2:
                    split_point = word_break + 1
        
        chunks.append(remaining[:split_point])
        remaining = remaining[split_point:]
    
    return chunks
