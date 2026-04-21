import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).parent.parent / "analyze_composition_effects.py")


def run_script(manifest_json: dict) -> dict:
    result = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(manifest_json),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return json.loads(result.stdout)


class TestToolAvailabilityMatrix:
    def test_mode_with_explicit_policies(self):
        manifest = {
            "manifests": [
                {
                    "component_type": "mode",
                    "file_path": "/test/write-plan.md",
                    "bundle_origin": "test",
                    "mode": {
                        "name": "write-plan",
                        "tool_policies": {
                            "safe": [
                                "read_file",
                                "glob",
                                "delegate",
                                "recipes",
                                "load_skill",
                            ],
                            "warn": ["bash"],
                            "confirm": [],
                            "block": ["write_file", "edit_file"],
                        },
                        "default_action": "block",
                    },
                }
            ],
            "llm_targets": [],
            "dependency_tree": [],
            "skipped_bundles": [],
            "skills_sources": [],
        }
        result = run_script(manifest)
        matrix = result["tool_availability_matrix"]["write-plan"]
        assert "read_file" in matrix["safe"]
        assert "bash" in matrix["warn"]
        assert matrix["default_action"] == "block"

    def test_no_modes_produces_empty_matrix(self):
        manifest = {
            "manifests": [],
            "llm_targets": [],
            "dependency_tree": [],
            "skipped_bundles": [],
            "skills_sources": [],
        }
        result = run_script(manifest)
        assert result["tool_availability_matrix"] == {}


class TestDelegationNecessityMap:
    def test_blocked_write_tools_require_delegation(self):
        manifest = {
            "manifests": [
                {
                    "component_type": "mode",
                    "file_path": "/test/write-plan.md",
                    "bundle_origin": "test",
                    "mode": {
                        "name": "write-plan",
                        "tool_policies": {
                            "safe": ["read_file", "glob", "grep", "delegate"],
                            "warn": ["bash"],
                            "confirm": [],
                            "block": ["write_file", "edit_file"],
                        },
                        "default_action": "block",
                    },
                }
            ],
            "llm_targets": [],
            "dependency_tree": [],
            "skipped_bundles": [],
            "skills_sources": [],
        }
        result = run_script(manifest)
        dnm = result["delegation_necessity_map"]["write-plan"]
        assert "MUST delegate" in dnm["file_modification"]


class TestCompositionLoopholes:
    def test_recipes_safe_in_restricted_mode(self):
        manifest = {
            "manifests": [
                {
                    "component_type": "mode",
                    "file_path": "/test/write-plan.md",
                    "bundle_origin": "test",
                    "mode": {
                        "name": "write-plan",
                        "tool_policies": {
                            "safe": ["read_file", "delegate", "recipes"],
                            "warn": [],
                            "confirm": [],
                            "block": ["write_file", "edit_file", "bash"],
                        },
                        "default_action": "block",
                    },
                }
            ],
            "llm_targets": [],
            "dependency_tree": [],
            "skipped_bundles": [],
            "skills_sources": [],
        }
        result = run_script(manifest)
        loopholes = result["composition_loopholes"]
        assert len(loopholes) >= 1
        assert any("recipes" in lh["safe_tool"] for lh in loopholes)


class TestSkillModeAssociations:
    def test_name_overlap_matching(self):
        manifest = {
            "manifests": [
                {
                    "component_type": "mode",
                    "file_path": "/test/write-plan.md",
                    "bundle_origin": "test",
                    "mode": {
                        "name": "write-plan",
                        "tool_policies": {
                            "safe": [],
                            "warn": [],
                            "confirm": [],
                            "block": [],
                        },
                        "default_action": "block",
                    },
                },
                {
                    "component_type": "skill",
                    "file_path": "/test/writing-plans/SKILL.md",
                    "bundle_origin": "test",
                    "skill": {
                        "name": "writing-plans",
                        "description": "Plan writing skill",
                    },
                },
            ],
            "llm_targets": [],
            "dependency_tree": [],
            "skipped_bundles": [],
            "skills_sources": [],
        }
        result = run_script(manifest)
        associations = result["skill_mode_associations"]
        assert any(
            a["mode"] == "write-plan" and a["likely_skill"] == "writing-plans"
            for a in associations
        )


class TestIntegration:
    """Run against the real foundation bundle manifest."""

    @pytest.mark.skipif(
        not Path.home().joinpath(".amplifier/registry.json").exists(),
        reason="No registry.json",
    )
    def test_real_foundation_manifest(self):
        # First generate the manifest
        parser = str(Path(__file__).parent.parent / "parse_bundle_composition.py")
        registry = str(Path.home() / ".amplifier/registry.json")
        p1 = subprocess.run(
            [sys.executable, parser, "foundation", registry],
            capture_output=True,
            text=True,
        )
        assert p1.returncode == 0, f"Parser failed: {p1.stderr}"

        # Then pipe to composition effects
        result = run_script(json.loads(p1.stdout))

        # Should have entries for all 6 superpowers modes
        assert len(result["tool_availability_matrix"]) >= 6
        assert "write-plan" in result["tool_availability_matrix"]
        assert "brainstorm" in result["tool_availability_matrix"]

        # Should find composition loopholes
        assert len(result["composition_loopholes"]) >= 1

        # Should find skill-mode associations
        assert len(result["skill_mode_associations"]) >= 3
