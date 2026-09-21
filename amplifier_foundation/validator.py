"""Bundle validator - validates bundle structure and content."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from amplifier_foundation.exceptions import BundleValidationError

if TYPE_CHECKING:
    from amplifier_foundation.bundle import Bundle


@dataclass
class ValidationResult:
    """Result of bundle validation.

    Attributes:
        valid: Whether the bundle is valid.
        errors: List of validation errors.
        warnings: List of validation warnings.
    """

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Add a validation error."""
        self.errors.append(message)
        self.valid = False

    def add_warning(self, message: str) -> None:
        """Add a validation warning."""
        self.warnings.append(message)


class BundleValidator:
    """Validates bundle structure and configuration.

    Validates:
    - Required fields (name)
    - Module list structure
    - Session configuration
    - Resource references

    Apps may extend for additional validation rules.
    """

    def __init__(self, *, strict: bool = False) -> None:
        """Initialize validator.

        Args:
            strict: If True, missing context paths are errors instead of warnings.
        """
        self._strict = strict

    def validate(self, bundle: Bundle) -> ValidationResult:
        """Validate a bundle.

        Args:
            bundle: Bundle to validate.

        Returns:
            ValidationResult with errors and warnings.
        """
        result = ValidationResult()

        # Required fields
        self._validate_required_fields(bundle, result)

        # Module lists
        self._validate_module_lists(bundle, result)

        # Session config
        self._validate_session(bundle, result)

        # Resources
        self._validate_resources(bundle, result)

        return result

    def validate_or_raise(self, bundle: Bundle) -> None:
        """Validate bundle and raise on errors.

        Args:
            bundle: Bundle to validate.

        Raises:
            BundleValidationError: If validation fails.
        """
        result = self.validate(bundle)
        if not result.valid:
            raise BundleValidationError(
                f"Bundle validation failed: {'; '.join(result.errors)}"
            )

    def _validate_required_fields(
        self, bundle: Bundle, result: ValidationResult
    ) -> None:
        """Validate required fields are present."""
        if not bundle.name:
            result.add_error("Bundle must have a name")

    def _validate_module_lists(self, bundle: Bundle, result: ValidationResult) -> None:
        """Validate module list structure."""
        for list_name, modules in [
            ("providers", bundle.providers),
            ("tools", bundle.tools),
            ("hooks", bundle.hooks),
        ]:
            for i, module in enumerate(modules):
                self._validate_module_entry(list_name, i, module, result)

    def _validate_module_entry(
        self,
        list_name: str,
        index: int,
        module: dict[str, Any],
        result: ValidationResult,
    ) -> None:
        """Validate a single module entry."""
        if not isinstance(module, dict):
            result.add_error(
                f"{list_name}[{index}]: Must be a dict, got {type(module).__name__}"
            )
            return

        # Module must have module field
        if "module" not in module:
            result.add_error(f"{list_name}[{index}]: Missing required 'module' field")

        # Config must be dict if present
        if "config" in module and not isinstance(module["config"], dict):
            result.add_error(
                f"{list_name}[{index}]: 'config' must be a dict, got {type(module['config']).__name__}"
            )

    def _validate_session(self, bundle: Bundle, result: ValidationResult) -> None:
        """Validate session configuration."""
        if not bundle.session:
            return

        # Session must be a dict
        if not isinstance(bundle.session, dict):
            result.add_error(
                f"session: Must be a dict, got {type(bundle.session).__name__}"
            )
            return

        # Validate known session fields
        # orchestrator/context can be string (module ID) or dict (with module/source keys)
        orchestrator = bundle.session.get("orchestrator")
        if orchestrator is not None:
            if isinstance(orchestrator, str):
                pass  # Valid: simple module reference
            elif isinstance(orchestrator, dict):
                if "module" not in orchestrator and "source" not in orchestrator:
                    result.add_error(
                        "session.orchestrator: Dict must have 'module' or 'source' key"
                    )
            else:
                result.add_error(
                    f"session.orchestrator: Must be string or dict, got {type(orchestrator).__name__}"
                )

        context = bundle.session.get("context")
        if context is not None:
            if isinstance(context, str):
                pass  # Valid: simple context reference
            elif isinstance(context, dict):
                if "module" not in context and "source" not in context:
                    result.add_error(
                        "session.context: Dict must have 'module' or 'source' key"
                    )
            else:
                result.add_error(
                    f"session.context: Must be string or dict, got {type(context).__name__}"
                )

    def _validate_resources(self, bundle: Bundle, result: ValidationResult) -> None:
        """Validate resource references."""
        # Agents must be dict of dicts
        for name, agent in bundle.agents.items():
            if not isinstance(agent, dict):
                result.add_error(
                    f"agents.{name}: Must be a dict, got {type(agent).__name__}"
                )

        # Context paths should exist if base_path is set
        if bundle.base_path:
            for name, path in bundle.context.items():
                if not path.exists():
                    message = f"context.{name}: Path does not exist: {path}"
                    if self._strict:
                        result.add_error(message)
                    else:
                        result.add_warning(message)

    def validate_completeness(self, bundle: Bundle) -> ValidationResult:
        """Validate that a bundle is complete for direct mounting.

        Checks that the bundle has all required sections for creating a session:
        - Session with orchestrator and context
        - At least one provider

        Use this for bundles in `bundles/` directory that should be mountable.
        Partial bundles (providers/, behaviors/, agents/) are not expected to pass.

        Args:
            bundle: Bundle to validate for completeness.

        Returns:
            ValidationResult with errors for missing required sections.
        """
        result = ValidationResult()

        # First run basic validation
        basic_result = self.validate(bundle)
        result.errors.extend(basic_result.errors)
        result.warnings.extend(basic_result.warnings)
        if basic_result.errors:
            result.valid = False

        # Check session completeness
        if not bundle.session:
            result.add_error("Mount plan requires 'session' section")
        else:
            if not bundle.session.get("orchestrator"):
                result.add_error("session: Missing required 'orchestrator' field")
            if not bundle.session.get("context"):
                result.add_error("session: Missing required 'context' field")

        # Check provider presence
        if not bundle.providers:
            result.add_error("Mount plan requires at least one provider")

        return result

    def validate_completeness_or_raise(self, bundle: Bundle) -> None:
        """Validate bundle completeness and raise on errors.

        Args:
            bundle: Bundle to validate for completeness.

        Raises:
            BundleValidationError: If completeness validation fails.
        """
        result = self.validate_completeness(bundle)
        if not result.valid:
            raise BundleValidationError(
                f"Bundle incomplete for mounting: {'; '.join(result.errors)}"
            )


def validate_bundle(bundle: Bundle) -> ValidationResult:
    """Convenience function to validate a bundle.

    Args:
        bundle: Bundle to validate.

    Returns:
        ValidationResult with errors and warnings.
    """
    validator = BundleValidator()
    return validator.validate(bundle)


def validate_bundle_or_raise(bundle: Bundle) -> None:
    """Convenience function to validate bundle and raise on errors.

    Args:
        bundle: Bundle to validate.

    Raises:
        BundleValidationError: If validation fails.
    """
    validator = BundleValidator()
    validator.validate_or_raise(bundle)


def validate_bundle_completeness(bundle: Bundle) -> ValidationResult:
    """Convenience function to validate bundle completeness for direct mounting.

    Args:
        bundle: Bundle to validate for completeness.

    Returns:
        ValidationResult with errors for missing required sections.
    """
    validator = BundleValidator()
    return validator.validate_completeness(bundle)


def validate_bundle_completeness_or_raise(bundle: Bundle) -> None:
    """Convenience function to validate bundle completeness and raise on errors.

    Args:
        bundle: Bundle to validate for completeness.

    Raises:
        BundleValidationError: If completeness validation fails.
    """
    validator = BundleValidator()
    validator.validate_completeness_or_raise(bundle)


_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n(.*)$", re.DOTALL)
_BARE_MENTION_RE = re.compile(r"^\s*@[\w.-]+:[\w./-]+\s*$")
_SECOND_PERSON_RE = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Markdown scaffolding: headings and horizontal rules. These carry no prose --
# a body of "# LLM Wiki Bundle" and a divider says nothing to the model, so
# there is nothing there to classify as instruction or as documentation.
_SCAFFOLDING_RE = re.compile(r"^\s{0,3}(#{1,6}\s|-{3,}\s*$|\*{3,}\s*$|_{3,}\s*$)")


def check_body_is_instruction(content: str) -> dict[str, Any]:
    """Classify the markdown body of a bundle/agent/mode file.

    Everything below the YAML frontmatter is sent to the model as system
    instruction. Only instruction -- or @mention pointers to instruction
    files -- belongs there. Descriptive prose about the artifact is a defect.

    The discriminator is whether the remaining text addresses the model
    (second person). Fenced code blocks are excluded before that test: they are
    examples, and a placeholder such as "your-server" inside a sample config is
    not the body addressing the model.

    Args:
        content: Full text of the .md file, including frontmatter.

    Returns:
        Dict with keys:
          verdict: "OK" | "FLAG" | "NO_FRONTMATTER"
          prose_chars: int, characters of body text left after removing
            fenced code, @mention lines, HTML comments, and markdown
            scaffolding (headings, horizontal rules)
          prose_preview: str, first 200 chars of that prose ("" when none)
          reason: str, short human-readable explanation
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {
            "verdict": "NO_FRONTMATTER",
            "prose_chars": 0,
            "prose_preview": "",
            "reason": "no frontmatter",
        }

    # Fenced code blocks are examples, not the body speaking to the model.
    body = _CODE_FENCE_RE.sub("", match.group(1))

    prose_lines: list[str] = []
    in_html_comment = False
    for line in body.split("\n"):
        stripped = line.strip()

        if in_html_comment:
            if "-->" in stripped:
                in_html_comment = False
            continue

        if not stripped:
            continue

        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_html_comment = True
            continue

        if _BARE_MENTION_RE.match(line):
            continue

        if _SCAFFOLDING_RE.match(line):
            continue

        prose_lines.append(line)

    prose_text = "\n".join(prose_lines)
    prose_chars = len(prose_text)
    prose_preview = prose_text[:200]

    if not prose_text.strip():
        return {
            "verdict": "OK",
            "prose_chars": prose_chars,
            "prose_preview": prose_preview,
            "reason": "body carries no prose (mentions/scaffolding only)",
        }

    if _SECOND_PERSON_RE.search(prose_text):
        return {
            "verdict": "OK",
            "prose_chars": prose_chars,
            "prose_preview": prose_preview,
            "reason": "addresses the model (second person)",
        }

    return {
        "verdict": "FLAG",
        "prose_chars": prose_chars,
        "prose_preview": prose_preview,
        "reason": "no second-person address -- reads as documentation, not instruction",
    }
