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

**bundle-design-expert owns the full lifecycle**: design -> model -> implement.

## The Bundle Lifecycle

Every non-trivial bundle should go through: **design -> model -> verify -> implement**. The model is a verification artifact, not documentation -- it surfaces broken scenarios before you write any YAML.

**New bundles:** Write a mechanism spec, generate a behavioral model, review the scenarios, then implement.
**Existing bundles:** Generate a model from the bundle, identify failing scenarios, spec the changes, re-model, then implement.

Do NOT skip the model step. See `foundation:context/understanding-mechanisms/bundle-lifecycle.md` for the full workflow.

## Available Recipes

**`@foundation:recipes/objectives-to-behavioral-model.yaml`**
Starting from scratch? This designs mechanisms AND generates a behavioral model from your objectives.
Required context: `objectives_path`, `output_path`

**`@foundation:recipes/spec-to-behavioral-model.yaml`**
Have a mechanism spec? This generates a behavioral model for verification before implementation.
Required context: `spec_path`, `output_path`

**`@foundation:recipes/bundle-behavioral-model.yaml`**
Have an existing bundle? This generates a model for understanding or improvement.
Required context: `bundle_name`, `registry_path` (path to `~/.amplifier/registry.json`), `output_path`

**`@foundation:recipes/change-spec-to-behavioral-model.yaml`**
Proposing changes to an existing bundle? This combines the bundle's current composition with your change spec to produce a merged model showing the full system after changes, with impact analysis and regression scenarios.
Required context: `bundle_name`, `registry_path`, `change_spec_path`, `output_path`

## Reference Documentation (load on demand)

- Bundle lifecycle: foundation:context/understanding-mechanisms/bundle-lifecycle.md
- Design guide: foundation:context/understanding-mechanisms/designing-with-mechanisms.md
- Mechanism reference: foundation:context/understanding-mechanisms/mechanisms/
