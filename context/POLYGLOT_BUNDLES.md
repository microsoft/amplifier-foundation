# Polyglot Bundle Patterns

Bundles can contain modules written in different languages. Each module is
self-describing via an `amplifier.toml` file that declares its transport type.
This allows a single bundle to mix Python tools, Rust providers, WASM modules,
and gRPC sidecars — Foundation handles each module independently.

## How It Works

- **Foundation** resolves module sources in a transport-agnostic way. It fetches
  or clones the source, then checks `amplifier.toml` to determine whether to skip
  Python-specific setup steps.
- **Core** reads `amplifier.toml` for the `transport` type (`python`, `rust`,
  `wasm`, `grpc`) to decide how to load and communicate with the module.
- **Host** uses the transport type to select the right consumption strategy
  (in-process Python import, subprocess binary, WASM runtime, or gRPC channel).

## Module Activation

Four-step flow for each module directory:

1. Download or clone the module source to the local cache.
2. Check `amplifier.toml` for the `transport` field.
3. If transport is `rust`, `wasm`, or `grpc` — skip `uv pip install` and skip
   adding the directory to `sys.path`.
4. If transport is `python` or `amplifier.toml` is absent — proceed with normal
   Python install and `sys.path` registration.

## Clone Integrity

`_verify_clone_integrity` accepts these markers to confirm a successful clone:

- **Python modules**: `pyproject.toml`, `setup.py`, or `setup.cfg`
- **Bundles**: `bundle.md` or `bundle.yaml`
- **Non-Python modules**: `amplifier.toml`

A directory containing any of these is considered a valid clone. The function
does not require all markers — one match is sufficient.

## Bundle Structure with Mixed-Language Modules

```
my-bundle/
├── python-tool/
│   └── pyproject.toml
├── rust-provider/
│   ├── amplifier.toml
│   ├── Cargo.toml
│   └── src/
├── providers/
└── context/
```

Note: Foundation treats each module directory independently. The presence of
`python-tool/` does not affect how `rust-provider/` is installed or loaded.
