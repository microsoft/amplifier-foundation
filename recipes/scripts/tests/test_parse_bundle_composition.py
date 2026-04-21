"""Tests for parse_bundle_composition.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Add parent directory to path so we can import the script as a module
sys.path.insert(0, str(Path(__file__).parent.parent))

from parse_bundle_composition import (
    AgentFields,
    BehaviorYamlFields,
    DataFlowEntry,
    ModeFields,
    ParsedManifest,
    RecipeFields,
    RecipeStepSummary,
    SkillFields,
    ToolModuleRef,
    build_manifest,
    classify_needs_llm,
    discover_cached_skills,
    discover_component_files,
    discover_skills_from_tool_configs,
    load_registry,
    parse_agent,
    parse_behavior_yaml,
    parse_context_file,
    parse_mode,
    parse_recipe,
    parse_skill,
    resolve_dependency_tree,
)


# -- Task 1: Data structures --------------------------------------------------


class TestParsedManifestSerialization:
    """ParsedManifest dataclasses serialize to JSON correctly."""

    def test_base_manifest_to_dict(self):
        m = ParsedManifest(
            file_path="/cache/foundation/context/shared/foo.md",
            component_type="context_file",
            bundle_origin="foundation",
            description=None,
            eager_mentions=["@foundation:context/bar.md"],
        )
        d = m.to_dict()
        assert d["file_path"] == "/cache/foundation/context/shared/foo.md"
        assert d["component_type"] == "context_file"
        assert d["bundle_origin"] == "foundation"
        assert d["description"] is None
        assert d["eager_mentions"] == ["@foundation:context/bar.md"]
        # Type-specific fields should be absent
        assert "mode" not in d
        assert "agent" not in d

    def test_mode_manifest_to_dict(self):
        m = ParsedManifest(
            file_path="/cache/superpowers/modes/brainstorm.md",
            component_type="mode",
            bundle_origin="superpowers",
            description="Design refinement",
            eager_mentions=[],
            mode=ModeFields(
                name="brainstorm",
                shortcut="brainstorm",
                tool_categories={
                    "safe": ["read_file", "glob"],
                    "warn": [],
                    "confirm": [],
                },
                default_action="block",
                allowed_transitions=["write-plan", "debug"],
                allow_clear=False,
            ),
        )
        d = m.to_dict()
        assert d["mode"]["name"] == "brainstorm"
        assert d["mode"]["default_action"] == "block"
        assert d["mode"]["allowed_transitions"] == ["write-plan", "debug"]
        assert d["mode"]["allow_clear"] is False

    def test_agent_manifest_to_dict(self):
        m = ParsedManifest(
            file_path="/cache/foundation/agents/explorer.md",
            component_type="agent",
            bundle_origin="foundation",
            description="Deep local-context reconnaissance agent",
            eager_mentions=[],
            agent=AgentFields(
                name="explorer",
                meta_description_raw="Deep local-context reconnaissance agent...",
                model_role=["general"],
                tool_modules=[
                    ToolModuleRef(module="tool-filesystem", source="git+https://...")
                ],
            ),
        )
        d = m.to_dict()
        assert d["agent"]["name"] == "explorer"
        assert d["agent"]["tool_modules"][0]["module"] == "tool-filesystem"

    def test_recipe_manifest_to_dict(self):
        m = ParsedManifest(
            file_path="/cache/recipes/validate-bundle.yaml",
            component_type="recipe",
            bundle_origin="foundation",
            description="Validates a bundle repo",
            eager_mentions=[],
            recipe=RecipeFields(
                name="validate-bundle",
                version="1.0.0",
                tags=["validation"],
                execution_mode="flat",
                input_interface={"required": ["repo_root"], "optional": []},
                steps=[
                    RecipeStepSummary(
                        id="discover",
                        type="bash",
                        agent=None,
                        condition=None,
                        output="discovery",
                        foreach=None,
                        parallel=None,
                    )
                ],
                data_flow={
                    "discovery": DataFlowEntry(
                        produced_by="discover", consumed_by=["validate"]
                    )
                },
                approval_gates=[],
                sub_recipe_calls=[],
            ),
        )
        d = m.to_dict()
        assert d["recipe"]["name"] == "validate-bundle"
        assert d["recipe"]["steps"][0]["id"] == "discover"
        assert d["recipe"]["data_flow"]["discovery"]["produced_by"] == "discover"

    def test_skill_manifest_to_dict(self):
        m = ParsedManifest(
            file_path="/cache/foundation/skills/bundle-to-dot/SKILL.md",
            component_type="skill",
            bundle_origin="foundation",
            description="Convention for v3 bundle docs",
            eager_mentions=[],
            skill=SkillFields(
                name="bundle-to-dot",
                context_mode=None,
                disable_model_invocation=False,
                user_invocable=False,
                model_role=None,
            ),
        )
        d = m.to_dict()
        assert d["skill"]["name"] == "bundle-to-dot"
        assert d["skill"]["context_mode"] is None

    def test_behavior_yaml_manifest_to_dict(self):
        m = ParsedManifest(
            file_path="/cache/foundation/behaviors/agents.yaml",
            component_type="behavior_yaml",
            bundle_origin="foundation",
            description="Agent orchestration capability",
            eager_mentions=[],
            behavior_yaml=BehaviorYamlFields(
                name="behavior-agents",
                tool_modules=[
                    ToolModuleRef(module="tool-delegate", source="git+https://...")
                ],
                hook_modules=[],
                context_includes=[
                    "foundation:context/agents/delegation-instructions.md"
                ],
                agent_includes=["foundation:session-analyst"],
                nested_behavior_includes=[],
            ),
        )
        d = m.to_dict()
        assert d["behavior_yaml"]["name"] == "behavior-agents"
        assert d["behavior_yaml"]["context_includes"] == [
            "foundation:context/agents/delegation-instructions.md"
        ]

    def test_manifest_roundtrips_through_json(self):
        m = ParsedManifest(
            file_path="/cache/foundation/context/foo.md",
            component_type="context_file",
            bundle_origin="foundation",
            description=None,
            eager_mentions=["@foundation:context/bar.md"],
        )
        json_str = json.dumps(m.to_dict())
        loaded = json.loads(json_str)
        assert loaded["file_path"] == m.file_path
        assert loaded["eager_mentions"] == m.eager_mentions

    def test_needs_llm_field_present(self):
        m = ParsedManifest(
            file_path="/cache/foundation/context/foo.md",
            component_type="context_file",
            bundle_origin="foundation",
            description=None,
            eager_mentions=[],
            needs_llm=True,
        )
        d = m.to_dict()
        assert d["needs_llm"] is True


# -- Task 2: Registry Walker --------------------------------------------------


class TestRegistryWalker:
    """Registry loading and dependency tree resolution."""

    def test_load_registry_returns_bundles_dict(self, tmp_path):
        registry_data = {
            "version": "1.0",
            "bundles": {
                "foundation": {
                    "name": "foundation",
                    "uri": "git+https://example.com/foundation",
                    "version": "1.0.0",
                    "local_path": "/cache/foundation",
                    "is_root": True,
                    "includes": [],
                    "included_by": [],
                    "root_name": "foundation",
                }
            },
        }
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps(registry_data))

        bundles = load_registry(registry_path)
        assert "foundation" in bundles
        assert bundles["foundation"]["name"] == "foundation"
        assert bundles["foundation"]["is_root"] is True

    def test_resolve_dependency_tree_single_bundle(self):
        bundles = {
            "foundation": {
                "name": "foundation",
                "includes": [],
            }
        }
        result = resolve_dependency_tree("foundation", bundles)
        assert result == ["foundation"]

    def test_resolve_dependency_tree_transitive(self):
        bundles = {
            "root": {
                "name": "root",
                "includes": ["mid"],
            },
            "mid": {
                "name": "mid",
                "includes": ["leaf"],
            },
            "leaf": {
                "name": "leaf",
                "includes": [],
            },
        }
        result = resolve_dependency_tree("root", bundles)
        # BFS order: root, then mid (root's dep), then leaf (mid's dep)
        assert result == ["root", "mid", "leaf"]

    def test_resolve_dependency_tree_handles_cycles(self):
        bundles = {
            "a": {
                "name": "a",
                "includes": ["b"],
            },
            "b": {
                "name": "b",
                "includes": ["a"],  # cycle back to a
            },
        }
        result = resolve_dependency_tree("a", bundles)
        # Should handle cycle without infinite loop, no duplicates
        assert "a" in result
        assert "b" in result
        assert len(result) == 2

    def test_resolve_dependency_tree_skips_missing_includes(self):
        bundles = {
            "foundation": {
                "name": "foundation",
                "includes": ["nonexistent"],
            }
        }
        result = resolve_dependency_tree("foundation", bundles)
        # Should not raise, should silently skip missing bundle names
        assert result == ["foundation"]


# -- Task 3: Component File Discovery -----------------------------------------


class TestComponentFileDiscovery:
    """discover_component_files discovers all component files under a bundle's local_path."""

    def test_discovers_modes(self, tmp_path):
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()
        (modes_dir / "brainstorm.md").write_text("# brainstorm")
        (modes_dir / "debug.md").write_text("# debug")

        result = discover_component_files(tmp_path)
        mode_entries = [(p, t) for p, t in result if t == "mode"]

        assert len(mode_entries) == 2
        paths = [p for p, _ in mode_entries]
        assert modes_dir / "brainstorm.md" in paths
        assert modes_dir / "debug.md" in paths
        # Verify files are sorted within directory
        assert paths == sorted(paths)

    def test_discovers_agents(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "explorer.md").write_text("# explorer")
        (agents_dir / "git-ops.md").write_text("# git-ops")

        result = discover_component_files(tmp_path)
        agent_entries = [(p, t) for p, t in result if t == "agent"]

        assert len(agent_entries) == 2
        paths = [p for p, _ in agent_entries]
        assert agents_dir / "explorer.md" in paths
        assert agents_dir / "git-ops.md" in paths
        # Verify files are sorted within directory
        assert paths == sorted(paths)

    def test_discovers_recipes(self, tmp_path):
        recipes_dir = tmp_path / "recipes"
        recipes_dir.mkdir()
        (recipes_dir / "validate.yaml").write_text("name: validate")
        (recipes_dir / "report.yml").write_text("name: report")

        result = discover_component_files(tmp_path)
        recipe_entries = [(p, t) for p, t in result if t == "recipe"]

        assert len(recipe_entries) == 2
        paths = [p for p, _ in recipe_entries]
        assert recipes_dir / "validate.yaml" in paths
        assert recipes_dir / "report.yml" in paths
        # Verify files are sorted within directory
        assert paths == sorted(paths)

    def test_discovers_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        (skills_dir / "bundle-to-dot").mkdir(parents=True)
        (skills_dir / "another-skill").mkdir(parents=True)
        (skills_dir / "bundle-to-dot" / "SKILL.md").write_text("# bundle-to-dot skill")
        (skills_dir / "another-skill" / "SKILL.md").write_text("# another skill")

        result = discover_component_files(tmp_path)
        skill_entries = [(p, t) for p, t in result if t == "skill"]

        assert len(skill_entries) == 2
        paths = [p for p, _ in skill_entries]
        assert skills_dir / "bundle-to-dot" / "SKILL.md" in paths
        assert skills_dir / "another-skill" / "SKILL.md" in paths
        # Verify files are sorted within directory
        assert paths == sorted(paths)

    def test_discovers_behaviors(self, tmp_path):
        behaviors_dir = tmp_path / "behaviors"
        behaviors_dir.mkdir()
        (behaviors_dir / "agents.yaml").write_text("name: agents")
        (behaviors_dir / "tools.yml").write_text("name: tools")

        result = discover_component_files(tmp_path)
        behavior_entries = [(p, t) for p, t in result if t == "behavior_yaml"]

        assert len(behavior_entries) == 2
        paths = [p for p, _ in behavior_entries]
        assert behaviors_dir / "agents.yaml" in paths
        assert behaviors_dir / "tools.yml" in paths
        # Verify files are sorted within directory
        assert paths == sorted(paths)

    def test_discovers_context_files(self, tmp_path):
        context_dir = tmp_path / "context"
        nested_dir = context_dir / "shared"
        nested_dir.mkdir(parents=True)
        (context_dir / "overview.md").write_text("# overview")
        (nested_dir / "common.md").write_text("# common")

        result = discover_component_files(tmp_path)
        context_entries = [(p, t) for p, t in result if t == "context_file"]

        assert len(context_entries) == 2
        paths = [p for p, _ in context_entries]
        assert context_dir / "overview.md" in paths
        assert nested_dir / "common.md" in paths
        # Verify files are sorted within directory
        assert paths == sorted(paths)

    def test_handles_empty_directory(self, tmp_path):
        result = discover_component_files(tmp_path)
        assert result == []

    def test_discovers_behavior_yaml_file_path(self, tmp_path):
        yaml_file = tmp_path / "bundle.yaml"
        yaml_file.write_text("name: my-bundle")

        result = discover_component_files(yaml_file)

        assert result == [(yaml_file, "behavior_yaml")]


# -- Task 4: Per-Type Parsers --------------------------------------------------


class TestPerTypeParsers:
    """Per-type parser functions return correct ParsedManifest instances."""

    def test_parse_mode(self, tmp_path):
        mode_file = tmp_path / "brainstorm.md"
        mode_file.write_text(
            "---\n"
            "mode:\n"
            "  name: brainstorm\n"
            "  description: Design refinement mode\n"
            "  shortcut: br\n"
            "  tools:\n"
            "    safe:\n"
            "      - read_file\n"
            "      - glob\n"
            "    warn:\n"
            "      - write_file\n"
            "    confirm:\n"
            "      - bash\n"
            "  default_action: block\n"
            "  allowed_transitions:\n"
            "    - write-plan\n"
            "    - debug\n"
            "  allow_clear: false\n"
            "---\n"
            "This mode uses @superpowers:context/brainstorm-guide.md for guidance.\n"
        )

        result = parse_mode(mode_file, "superpowers")

        assert isinstance(result, ParsedManifest)
        assert result.component_type == "mode"
        assert result.bundle_origin == "superpowers"
        assert result.description == "Design refinement mode"
        assert result.mode is not None
        assert result.mode.name == "brainstorm"
        assert result.mode.shortcut == "br"
        assert result.mode.tool_categories["safe"] == ["read_file", "glob"]
        assert result.mode.tool_categories["warn"] == ["write_file"]
        assert result.mode.tool_categories["confirm"] == ["bash"]
        assert result.mode.default_action == "block"
        assert result.mode.allowed_transitions == ["write-plan", "debug"]
        assert result.mode.allow_clear is False
        assert "@superpowers:context/brainstorm-guide.md" in result.eager_mentions

    def test_parse_agent(self, tmp_path):
        agent_file = tmp_path / "explorer.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: explorer\n"
            "  description: Deep local-context reconnaissance agent\n"
            "model_role: general\n"
            "tools:\n"
            "  - module: tool-filesystem\n"
            "    source: git+https://example.com/tool-filesystem\n"
            "  - module: tool-grep\n"
            "    source: git+https://example.com/tool-grep\n"
            "---\n"
            "Uses @foundation:context/exploration-guide.md for instructions.\n"
        )

        result = parse_agent(agent_file, "foundation")

        assert isinstance(result, ParsedManifest)
        assert result.component_type == "agent"
        assert result.bundle_origin == "foundation"
        assert result.description == "Deep local-context reconnaissance agent"
        assert result.agent is not None
        assert result.agent.name == "explorer"
        assert (
            result.agent.meta_description_raw
            == "Deep local-context reconnaissance agent"
        )
        # model_role: string -> always list
        assert result.agent.model_role == ["general"]
        assert len(result.agent.tool_modules) == 2
        assert result.agent.tool_modules[0].module == "tool-filesystem"
        assert (
            result.agent.tool_modules[0].source
            == "git+https://example.com/tool-filesystem"
        )
        assert result.agent.tool_modules[1].module == "tool-grep"
        assert "@foundation:context/exploration-guide.md" in result.eager_mentions

    def test_parse_agent_model_role_list(self, tmp_path):
        """model_role as a list stays as a list."""
        agent_file = tmp_path / "specialist.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: specialist\n"
            "  description: Specialist agent\n"
            "model_role:\n"
            "  - coding\n"
            "  - reasoning\n"
            "tools: []\n"
            "---\n"
            "Body text.\n"
        )

        result = parse_agent(agent_file, "foundation")

        assert result.agent is not None
        assert result.agent.model_role == ["coding", "reasoning"]

    def test_parse_recipe_flat(self, tmp_path):
        recipe_file = tmp_path / "validate-bundle.yaml"
        recipe_file.write_text(
            "name: validate-bundle\n"
            "description: Validates a bundle repo\n"
            "version: 1.0.0\n"
            "tags:\n"
            "  - validation\n"
            "  - bundle\n"
            "steps:\n"
            "  - id: discover\n"
            "    type: bash\n"
            "    output: discovery\n"
            "  - id: validate\n"
            "    type: agent\n"
            "    agent: foundation:explorer\n"
            "    condition: '{{discovery}} != null'\n"
            "    output: report\n"
        )

        result = parse_recipe(recipe_file, "foundation")

        assert isinstance(result, ParsedManifest)
        assert result.component_type == "recipe"
        assert result.bundle_origin == "foundation"
        assert result.description == "Validates a bundle repo"
        assert result.recipe is not None
        assert result.recipe.name == "validate-bundle"
        assert result.recipe.version == "1.0.0"
        assert result.recipe.tags == ["validation", "bundle"]
        assert result.recipe.execution_mode == "flat"
        assert len(result.recipe.steps) == 2
        assert result.recipe.steps[0].id == "discover"
        assert result.recipe.steps[0].type == "bash"
        assert result.recipe.steps[0].output == "discovery"
        assert result.recipe.steps[1].id == "validate"
        assert result.recipe.steps[1].agent == "foundation:explorer"
        # data_flow: 'discovery' produced by discover, consumed by validate (via {{discovery}})
        assert "discovery" in result.recipe.data_flow
        assert result.recipe.data_flow["discovery"].produced_by == "discover"
        assert "validate" in result.recipe.data_flow["discovery"].consumed_by
        assert result.recipe.approval_gates == []
        assert result.recipe.sub_recipe_calls == []

    def test_parse_recipe_staged_with_approval_gates(self, tmp_path):
        recipe_file = tmp_path / "deploy.yaml"
        recipe_file.write_text(
            "name: deploy\n"
            "description: Deploy pipeline\n"
            "stages:\n"
            "  - name: planning\n"
            "    requires_approval: true\n"
            "    approval_context_template: Review the plan\n"
            "    steps:\n"
            "      - id: plan\n"
            "        type: agent\n"
            "        output: plan_result\n"
            "  - name: execution\n"
            "    requires_approval: false\n"
            "    steps:\n"
            "      - id: deploy\n"
            "        type: bash\n"
        )

        result = parse_recipe(recipe_file, "foundation")

        assert result.recipe is not None
        assert result.recipe.execution_mode == "staged"
        assert len(result.recipe.approval_gates) == 1
        assert result.recipe.approval_gates[0].stage == "planning"
        assert result.recipe.approval_gates[0].requires_approval is True
        assert (
            result.recipe.approval_gates[0].approval_context_template
            == "Review the plan"
        )

    def test_parse_recipe_sub_recipe_calls(self, tmp_path):
        recipe_file = tmp_path / "orchestrator.yaml"
        recipe_file.write_text(
            "name: orchestrator\n"
            "steps:\n"
            "  - id: sub1\n"
            "    type: recipe\n"
            "    recipe: validate-bundle\n"
            "  - id: regular\n"
            "    type: bash\n"
        )

        result = parse_recipe(recipe_file, "foundation")

        assert result.recipe is not None
        assert len(result.recipe.sub_recipe_calls) == 1
        assert result.recipe.sub_recipe_calls[0]["step_id"] == "sub1"
        assert result.recipe.sub_recipe_calls[0]["recipe"] == "validate-bundle"

    def test_parse_skill(self, tmp_path):
        skill_dir = tmp_path / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\n"
            "name: my-skill\n"
            "description: A useful skill\n"
            "context_mode: isolated\n"
            "disable_model_invocation: true\n"
            "user_invocable: true\n"
            "model_role: reasoning\n"
            "---\n"
            "This skill loads @foundation:context/skill-guide.md for guidance\n"
        )

        result = parse_skill(skill_file, "myBundle")

        assert isinstance(result, ParsedManifest)
        assert result.component_type == "skill"
        assert result.bundle_origin == "myBundle"
        assert result.description == "A useful skill"
        assert result.skill is not None
        assert result.skill.name == "my-skill"
        assert result.skill.context_mode == "isolated"
        assert result.skill.disable_model_invocation is True
        assert result.skill.user_invocable is True
        assert result.skill.model_role == "reasoning"
        assert "@foundation:context/skill-guide.md" in result.eager_mentions

    def test_parse_behavior_yaml(self, tmp_path):
        behavior_file = tmp_path / "agents.yaml"
        behavior_file.write_text(
            "bundle:\n"
            "  name: behavior-agents\n"
            "  description: Agent orchestration capability\n"
            "tools:\n"
            "  - module: tool-delegate\n"
            "    source: git+https://example.com/tool-delegate\n"
            "hooks:\n"
            "  - module: hook-logger\n"
            "    source: git+https://example.com/hook-logger\n"
            "context:\n"
            "  include:\n"
            "    - foundation:context/agents/delegation-instructions.md\n"
            "    - foundation:context/agents/patterns.md\n"
            "agents:\n"
            "  include:\n"
            "    - foundation:session-analyst\n"
            "    - foundation:explorer\n"
            "includes:\n"
            "  - bundle: foundation\n"
            "  - bundle: core\n"
        )

        result = parse_behavior_yaml(behavior_file, "foundation")

        assert isinstance(result, ParsedManifest)
        assert result.component_type == "behavior_yaml"
        assert result.bundle_origin == "foundation"
        assert result.description == "Agent orchestration capability"
        assert result.behavior_yaml is not None
        assert result.behavior_yaml.name == "behavior-agents"
        assert len(result.behavior_yaml.tool_modules) == 1
        assert result.behavior_yaml.tool_modules[0].module == "tool-delegate"
        assert (
            result.behavior_yaml.tool_modules[0].source
            == "git+https://example.com/tool-delegate"
        )
        assert len(result.behavior_yaml.hook_modules) == 1
        assert result.behavior_yaml.hook_modules[0].module == "hook-logger"
        assert result.behavior_yaml.context_includes == [
            "foundation:context/agents/delegation-instructions.md",
            "foundation:context/agents/patterns.md",
        ]
        assert result.behavior_yaml.agent_includes == [
            "foundation:session-analyst",
            "foundation:explorer",
        ]
        # includes list: extracts 'bundle' key from dicts
        assert result.behavior_yaml.nested_behavior_includes == ["foundation", "core"]

    def test_parse_context_file(self, tmp_path):
        context_file = tmp_path / "guide.md"
        context_file.write_text(
            "# Guide\n\n"
            "See @foundation:context/shared/common.md for common patterns.\n"
            "Also refer to @foundation:context/philosophy.md here\n"
        )

        result = parse_context_file(context_file, "foundation")

        assert isinstance(result, ParsedManifest)
        assert result.component_type == "context_file"
        assert result.bundle_origin == "foundation"
        assert result.description is None
        assert "@foundation:context/shared/common.md" in result.eager_mentions
        assert "@foundation:context/philosophy.md" in result.eager_mentions
        assert len(result.eager_mentions) == 2


# -- Task 5: Skills Discovery --------------------------------------------------


class TestSkillsDiscovery:
    """discover_skills_from_tool_configs extracts skill URIs from behavior YAML data."""

    def test_extract_skills_sources_from_behavior(self):
        """Extracts skill URIs from tool-skills config.skills arrays."""
        behavior_data = {
            "tools": [
                {
                    "module": "tool-filesystem",
                    "source": "git+https://example.com/tool-filesystem",
                },
                {
                    "module": "tool-skills",
                    "source": "git+https://example.com/tool-skills",
                    "config": {
                        "skills": [
                            "git+https://example.com/skill-a",
                            "git+https://example.com/skill-b",
                        ]
                    },
                },
            ]
        }
        result = discover_skills_from_tool_configs(behavior_data)
        assert result == [
            "git+https://example.com/skill-a",
            "git+https://example.com/skill-b",
        ]

    def test_no_tool_skills_returns_empty(self):
        """Returns empty list when tools list has no tool-skills entries."""
        behavior_data = {
            "tools": [
                {
                    "module": "tool-filesystem",
                    "source": "git+https://example.com/tool-filesystem",
                },
            ]
        }
        result = discover_skills_from_tool_configs(behavior_data)
        assert result == []

    def test_handles_missing_tools_key(self):
        """Returns empty list when 'tools' key is missing."""
        behavior_data = {}
        result = discover_skills_from_tool_configs(behavior_data)
        assert result == []


# -- Task 6: Triage Logic ------------------------------------------------------


class TestTriageLogic:
    """classify_needs_llm determines if a file needs LLM extraction."""

    def test_behavior_yaml_skips_llm(self):
        """behavior_yaml always returns False regardless of body content."""
        assert classify_needs_llm("behavior_yaml", "some content here") is False

    def test_context_file_needs_llm(self):
        """context_file with non-empty body returns True."""
        assert classify_needs_llm("context_file", "# Guide\n\nSome content.") is True

    def test_mode_needs_llm(self):
        """mode with non-empty body returns True."""
        assert classify_needs_llm("mode", "This mode is for brainstorming.") is True

    def test_agent_needs_llm(self):
        """agent with non-empty body returns True."""
        assert classify_needs_llm("agent", "You are an expert agent.") is True

    def test_skill_needs_llm(self):
        """skill with non-empty body returns True."""
        assert classify_needs_llm("skill", "Use this skill for refactoring.") is True

    def test_recipe_with_prompts_needs_llm(self):
        """recipe body containing 'prompt:' returns True."""
        body = "steps:\n  - id: step1\n    prompt: Write a summary\n"
        assert classify_needs_llm("recipe", body) is True

    def test_recipe_without_prompts_skips_llm(self):
        """recipe body without 'prompt:' returns False."""
        body = "steps:\n  - id: step1\n    type: bash\n    command: echo hello\n"
        assert classify_needs_llm("recipe", body) is False

    def test_empty_context_file_skips_llm(self):
        """context_file with empty/whitespace-only body returns False."""
        assert classify_needs_llm("context_file", "   \n  \t  ") is False

    def test_empty_mode_body_skips_llm(self):
        """mode with empty body returns False."""
        assert classify_needs_llm("mode", "") is False


# -- Task 7: Main Script -------------------------------------------------------


class TestBuildManifest:
    """build_manifest wires all components together into a JSON manifest."""

    def test_build_manifest_produces_correct_structure(self, tmp_path):
        """build_manifest returns dict with correct top-level keys, parses all
        component types, sets needs_llm correctly, and filters llm_targets."""
        # Create bundle directory with multiple component types
        bundle_dir = tmp_path / "mybundle"
        bundle_dir.mkdir()

        # Agent file: has body content -> needs_llm=True
        agents_dir = bundle_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "myagent.md").write_text(
            "---\n"
            "meta:\n"
            "  name: myagent\n"
            "  description: My test agent\n"
            "model_role: general\n"
            "tools: []\n"
            "---\n"
            "You are an expert agent with detailed instructions.\n"
        )

        # Behavior YAML with tool-skills -> provides skills_sources, needs_llm=False
        behaviors_dir = bundle_dir / "behaviors"
        behaviors_dir.mkdir()
        (behaviors_dir / "tools.yaml").write_text(
            "bundle:\n"
            "  name: mybundle-tools\n"
            "tools:\n"
            "  - module: tool-skills\n"
            "    source: git+https://example.com/tool-skills\n"
            "    config:\n"
            "      skills:\n"
            "        - git+https://example.com/skill-one\n"
        )

        # Context file: has content -> needs_llm=True
        context_dir = bundle_dir / "context"
        context_dir.mkdir()
        (context_dir / "guide.md").write_text("# Guide\nSome helpful content here.\n")

        bundles = {
            "mybundle": {
                "name": "mybundle",
                "local_path": str(bundle_dir),
                "includes": [],
            }
        }

        result = build_manifest("mybundle", bundles)

        # Check all top-level keys present
        assert "bundle_name" in result
        assert "dependency_tree" in result
        assert "manifests" in result
        assert "llm_targets" in result
        assert "skipped_bundles" in result
        assert "skills_sources" in result

        # Check bundle_name and dependency_tree
        assert result["bundle_name"] == "mybundle"
        assert result["dependency_tree"] == ["mybundle"]
        assert result["skipped_bundles"] == []

        # Check component types are present in manifests
        component_types = {m["component_type"] for m in result["manifests"]}
        assert "agent" in component_types
        assert "behavior_yaml" in component_types
        assert "context_file" in component_types

        # Triage correctness: agent with body -> needs_llm=True
        agent_manifests = [
            m for m in result["manifests"] if m["component_type"] == "agent"
        ]
        assert len(agent_manifests) == 1
        assert agent_manifests[0]["needs_llm"] is True

        # Triage correctness: behavior_yaml -> always needs_llm=False
        beh_manifests = [
            m for m in result["manifests"] if m["component_type"] == "behavior_yaml"
        ]
        assert len(beh_manifests) == 1
        assert beh_manifests[0]["needs_llm"] is False

        # llm_targets is subset of manifests where needs_llm=True
        assert all(m["needs_llm"] is True for m in result["llm_targets"])
        llm_file_paths = {m["file_path"] for m in result["llm_targets"]}
        all_file_paths = {m["file_path"] for m in result["manifests"]}
        assert llm_file_paths.issubset(all_file_paths)

        # skills_sources collected from behavior_yaml tool-skills configs
        assert "git+https://example.com/skill-one" in result["skills_sources"]

    def test_build_manifest_handles_null_local_path(self, tmp_path):
        """Bundles with local_path=None are added to skipped_bundles and skipped."""
        bundles = {
            "mybundle": {
                "name": "mybundle",
                "local_path": None,
                "includes": [],
            }
        }

        result = build_manifest("mybundle", bundles)

        assert "mybundle" in result["skipped_bundles"]
        assert result["manifests"] == []
        assert result["llm_targets"] == []
        assert result["bundle_name"] == "mybundle"
        assert result["dependency_tree"] == ["mybundle"]


# -- Bug 1: Null Path Resolution -----------------------------------------------


class TestNullPathResolution:
    """Bundles with null local_path are resolved through parent bundles."""

    def test_resolve_null_path_finds_parent(self):
        bundles = {
            "child": {
                "name": "child",
                "local_path": None,
                "is_root": True,
                "includes": [],
                "included_by": ["parent"],
            },
            "parent": {
                "name": "parent",
                "local_path": "/cache/parent-dir",
                "is_root": True,
                "includes": ["child"],
                "included_by": [],
            },
        }
        from parse_bundle_composition import _resolve_null_path

        assert _resolve_null_path("child", bundles) == "/cache/parent-dir"

    def test_resolve_null_path_skips_yaml_files(self):
        """Parent with .yaml local_path is not a directory -- skip it."""
        bundles = {
            "child": {
                "name": "child",
                "local_path": None,
                "is_root": True,
                "includes": [],
                "included_by": ["parent"],
            },
            "parent": {
                "name": "parent",
                "local_path": "/cache/foundation/behaviors/parent.yaml",
                "is_root": False,
                "includes": ["child"],
                "included_by": [],
            },
        }
        from parse_bundle_composition import _resolve_null_path

        assert _resolve_null_path("child", bundles) is None

    def test_resolve_null_path_no_parent(self):
        bundles = {
            "orphan": {
                "name": "orphan",
                "local_path": None,
                "is_root": True,
                "includes": [],
                "included_by": [],
            },
        }
        from parse_bundle_composition import _resolve_null_path

        assert _resolve_null_path("orphan", bundles) is None


# -- Bug 2: Cached Skills Discovery --------------------------------------------


class TestCachedSkillsDiscovery:
    """Skills from ~/.amplifier/cache/skills/ are discovered."""

    def test_discover_cached_skills(self, tmp_path):
        """Set up a fake skills cache and verify discovery."""
        cache_dir = tmp_path / "cache" / "skills"
        # Simulate superpowers-abc123/skills/brainstorming/SKILL.md
        skill_dir = cache_dir / "superpowers-abc123" / "skills" / "brainstorming"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: brainstorming\ndescription: Brainstorm skill\n---\nBody text.\n"
        )
        # Another skill
        skill_dir2 = cache_dir / "superpowers-abc123" / "skills" / "debugging"
        skill_dir2.mkdir(parents=True)
        (skill_dir2 / "SKILL.md").write_text(
            "---\nname: systematic-debugging\ndescription: Debug skill\n---\nBody.\n"
        )

        results = discover_cached_skills(tmp_path / "registry.json")
        assert len(results) == 2
        paths = [str(p) for p, _ in results]
        assert any("brainstorming" in p for p in paths)
        assert all(t == "skill" for _, t in results)

    def test_discover_cached_skills_empty(self, tmp_path):
        results = discover_cached_skills(tmp_path / "registry.json")
        assert results == []


# -- Task 9: Integration Tests -------------------------------------------------

_REGISTRY_PATH = Path.home() / ".amplifier" / "registry.json"
_SCRIPT_PATH = Path(__file__).parent.parent / "parse_bundle_composition.py"


class TestIntegration:
    """Integration tests that run parser CLI as subprocess against real registry.

    These tests are skipped in CI environments where ~/.amplifier/registry.json
    is not present.
    """

    @pytest.fixture(autouse=True)
    def require_registry(self):
        """Skip all tests in this class if the real registry is not available."""
        if not _REGISTRY_PATH.exists():
            pytest.skip("~/.amplifier/registry.json not present (CI environment)")

    def test_parser_runs_against_foundation_bundle(self):
        """Runs parse_bundle_composition.py with 'foundation' bundle and real registry.

        Asserts: returncode==0, bundle_name=='foundation', dependency_tree has >=5 bundles,
        manifests has >=20 items, component types include behavior_yaml/agent/context_file,
        llm_targets is a non-empty subset of manifests, no behavior_yaml in llm_targets.
        """
        result = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "foundation", str(_REGISTRY_PATH)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"Parser failed:\nstdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )

        assert result.stdout.strip(), (
            f"Parser produced no output:\nstderr: {result.stderr[:500]}"
        )
        data = json.loads(result.stdout)

        assert data["bundle_name"] == "foundation"
        assert len(data["dependency_tree"]) >= 5
        assert len(data["manifests"]) >= 50

        component_types = {m["component_type"] for m in data["manifests"]}
        assert "behavior_yaml" in component_types
        assert "agent" in component_types
        assert "context_file" in component_types
        assert "mode" in component_types

        skill_count = sum(
            1 for m in data["manifests"] if m["component_type"] == "skill"
        )
        assert skill_count >= 10

        # llm_targets must be a subset of manifests
        llm_file_paths = {m["file_path"] for m in data["llm_targets"]}
        all_file_paths = {m["file_path"] for m in data["manifests"]}
        assert llm_file_paths.issubset(all_file_paths)

        # llm_targets must be non-empty
        assert len(data["llm_targets"]) > 0

        # behavior_yaml files must never appear in llm_targets
        llm_types = {m["component_type"] for m in data["llm_targets"]}
        assert "behavior_yaml" not in llm_types

    def test_parser_handles_unknown_bundle(self):
        """Runs parser with 'nonexistent-bundle-xyz'. Asserts returncode==1 and error JSON."""
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_PATH),
                "nonexistent-bundle-xyz",
                str(_REGISTRY_PATH),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 1

        assert result.stdout.strip(), (
            f"Parser produced no output:\nstderr: {result.stderr[:500]}"
        )
        data = json.loads(result.stdout)
        assert "error" in data
