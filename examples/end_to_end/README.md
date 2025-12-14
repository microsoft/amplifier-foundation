# End-to-End Example

Interactive demo showing the complete bundle → execution workflow.

## What It Does

1. **Loads foundation bundle** - Local path or from GitHub
2. **Discovers providers** - Shows available provider bundles with API key status
3. **User selects provider** - Interactive menu with warnings for missing keys
4. **Composes bundles** - Merges foundation + provider into mount plan
5. **User enters prompt** - Any prompt you want to test
6. **Executes via AmplifierSession** - Runs the prompt and displays response

## Prerequisites

### 1. Install Dependencies

```bash
cd amplifier-foundation
uv sync
```

### 2. API Key (Optional Pre-setup)

You can set the environment variable for your chosen provider ahead of time:

```bash
# For Anthropic (Claude)
export ANTHROPIC_API_KEY=your-key-here

# For OpenAI
export OPENAI_API_KEY=your-key-here
```

**Or**, if you don't set the env var, the example will prompt you to enter your API key interactively when you select a provider.

## Running the Example

```bash
cd amplifier-foundation
uv run python examples/end_to_end.py
```

## Example Session

```
============================================================
Amplifier Foundation: End-to-End Demo
============================================================

[1/5] Using local foundation bundle: /path/to/amplifier-foundation
      Loaded: foundation v1.0.0
      Tools: 5

[2/5] Discovering available providers...
      Found 4 provider(s)

Available Providers:
------------------------------------------------------------
  [1] provider-anthropic-opus
      Model: claude-opus-4-5
      Status: ✓ (ANTHROPIC_API_KEY set)

  [2] provider-anthropic-sonnet
      Model: claude-sonnet-4-5
      Status: ✓ (ANTHROPIC_API_KEY set)

  [3] provider-openai-gpt
      Model: gpt-4o
      Status: ✗ (OPENAI_API_KEY NOT set)

  [4] provider-openai-gpt-codex
      Model: gpt-4o-codex
      Status: ✗ (OPENAI_API_KEY NOT set)

[3/5] Select a provider...
Select provider [1-4] (or 'q' to quit): 2

      Selected: provider-anthropic-sonnet (claude-sonnet-4-5)

[4/5] Composing bundles...
      Session: loop-streaming
      Providers: 1
      Tools: 5

Enter your prompt (or 'q' to quit):
----------------------------------------
> What is 2 + 2? Reply with just the number.

[5/5] Executing via AmplifierSession...
------------------------------------------------------------

Response:
4

============================================================
Demo complete
============================================================
```

## Available Providers

The foundation bundle includes these provider configurations:

| Provider | Model | Required Env Var |
|----------|-------|------------------|
| `anthropic-sonnet` | claude-sonnet-4-5 | `ANTHROPIC_API_KEY` |
| `anthropic-opus` | claude-opus-4-5 | `ANTHROPIC_API_KEY` |
| `openai-gpt` | gpt-4o | `OPENAI_API_KEY` |
| `openai-gpt-codex` | gpt-4o-codex | `OPENAI_API_KEY` |

## How It Works

### Bundle Loading

```python
# Load local or remote
foundation = await load_bundle("./path/to/foundation")
# or
foundation = await load_bundle("git+https://github.com/microsoft/amplifier-foundation@main")
```

### Provider Discovery

The example scans `providers/*.yaml` for available provider bundles and checks if the required API key is set.

### Bundle Composition and Preparation

```python
provider_bundle = await load_bundle("./providers/anthropic-sonnet.yaml")
composed = foundation.compose(provider_bundle)

# prepare() downloads modules from their git sources and installs dependencies
prepared = await composed.prepare()
```

The `prepare()` method is the key to turn-key execution:
1. Downloads modules from git URLs to local cache
2. Installs Python dependencies via uv
3. Makes modules importable via sys.path
4. Returns a `PreparedBundle` with:
   - `prepared.mount_plan` - The dict for AmplifierSession
   - `prepared.create_session()` - Convenience method for full setup

### Execution

```python
# Option 1: Use create_session() convenience method
async with prepared.create_session() as session:
    response = await session.execute(prompt)

# Option 2: Manual setup with mount_plan
from amplifier_core import AmplifierSession
session = AmplifierSession(config=prepared.mount_plan)
await session.coordinator.mount("module-source-resolver", prepared.resolver)
await session.initialize()
```

## Troubleshooting

### "amplifier-core not installed"

```bash
cd amplifier-foundation
uv sync  # This installs amplifier-core as a dependency
```

### "API key not set" warning

Set the appropriate environment variable:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or
export OPENAI_API_KEY=sk-...
```

### Provider selection loops

If you select a provider without an API key, you'll be warned and asked to confirm. Enter `y` to proceed anyway (will fail at execution) or select a different provider.

## Extending

### Add a New Provider

1. Create `providers/my-provider.yaml`:

```yaml
bundle:
  name: my-provider
  version: 1.0.0
  description: My custom provider

providers:
  - module: provider-my-backend
    source: git+https://github.com/org/amplifier-module-provider-my-backend@main
    config:
      default_model: my-model-name
```

2. Run the example - it will automatically discover the new provider.

### Non-Interactive Mode

For scripting, you can modify the example to accept command-line arguments:

```bash
uv run python examples/end_to_end.py --provider anthropic-sonnet --prompt "Hello!"
```

(This would require adding argparse - see the code for extension points.)
