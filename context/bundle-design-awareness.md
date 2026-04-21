# Bundle Design

This bundle provides an expert agent and recipes for the full bundle lifecycle: designing, modeling, and building Amplifier bundles.

## Expert Agent

Delegate to **bundle-design-expert** when the user wants to:
- Design or plan a new Amplifier bundle (mechanism selection, architecture)
- Build bundle YAML, behaviors, agent files, or context architecture
- Author agents (writing descriptions, meta.description, file structure)
- Understand Amplifier mechanisms (modes, agents, tools, hooks, skills, recipes)
- Decide which recipe to use or interpret a behavioral model
- Review an existing bundle design for anti-patterns

**bundle-design-expert owns the full lifecycle**: design → model → implement.

## Available Recipes

**`foundation:recipes/objectives-to-behavioral-model.yaml`**
Designs mechanisms and generates a behavioral model directly from objectives.
Required context: `objectives_path`, `output_path`

**`foundation:recipes/spec-to-behavioral-model.yaml`**
Generates a behavioral model from a mechanism spec document (before implementation).
Required context: `spec_path`, `output_path`

**`foundation:recipes/bundle-behavioral-model.yaml`**
Generates a behavioral model from an implemented bundle's resolved composition.
Required context: `bundle_name`, `registry_path`, `output_path`

## Reference Documentation (load on demand)

- Design guide: foundation:context/understanding-mechanisms/designing-with-mechanisms.md
- Mechanism reference: foundation:context/understanding-mechanisms/mechanisms/
