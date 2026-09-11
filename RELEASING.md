# Releasing amplifier-foundation

## How publishing works

Releases are fully automated via OIDC trusted publishing — no API tokens or secrets are
needed after one-time setup. The workflow (`.github/workflows/publish.yml`) fires when a
`v<version>` git tag is pushed to `main` and publishes both the sdist and wheel to PyPI.

## Release steps (per release)

```bash
# 1. Bump the version in pyproject.toml
#    Edit [project] version = "X.Y.Z"

# 2. Commit and merge to main
git add pyproject.toml
git commit -m "chore: bump version to X.Y.Z"
# Open a PR and merge, or push directly if you have access

# 3. Push the release tag from the tip of main
git fetch origin
git checkout main && git pull
git tag vX.Y.Z
git push origin vX.Y.Z
```

Pushing the tag triggers `.github/workflows/publish.yml`, which:
1. Verifies the tag version matches `pyproject.toml`.
2. Runs `uv build` to produce the sdist and wheel (pure-Python, `py3-none-any`).
3. Publishes both artifacts to PyPI via `pypa/gh-action-pypi-publish` using OIDC.

## One-time setup: PyPI trusted publisher

Before the **first** release, a *pending trusted publisher* must be configured on PyPI.
This only needs to be done once.

1. Go to <https://pypi.org/manage/account/publishing/> (or the project page if it already
   exists) and add a **pending publisher** (for a new project) with:

   | Field | Value |
   |---|---|
   | PyPI project name | `amplifier-foundation` |
   | GitHub repository owner | `microsoft` |
   | GitHub repository name | `amplifier-foundation` |
   | Workflow filename | `publish.yml` |
   | Environment name | `pypi` |

2. Create a GitHub Actions environment named `pypi` in the repo settings
   (`Settings → Environments → New environment`). No secrets are needed; the environment
   just scopes the OIDC token exchange. Adding a required reviewer is optional but
   recommended for production releases.

3. Push a test tag (e.g. `v0.0.1.dev0`) against the pending publisher to confirm the
   handshake works end-to-end. Delete the test release on PyPI afterward if desired.

> **Note:** The OIDC trusted-publisher handshake can only be proven by a real tag-triggered
> run after PyPI-side configuration. No local test can verify this step.

## Pre-release versions

Append a pre-release suffix to signal non-final releases:

```
v1.1.0a1   →  alpha 1
v1.1.0b2   →  beta 2
v1.1.0rc1  →  release candidate 1
```

PyPI treats these correctly; `pip install amplifier-foundation` won't pull them unless
`--pre` is passed.

## Verifying a release

After the workflow completes:

```bash
pip install --dry-run amplifier-foundation==X.Y.Z  # confirm on PyPI
pip install amplifier-foundation==X.Y.Z
python -c "import amplifier_foundation; print(amplifier_foundation.__version__)"
```
