# Releasing amplifier-foundation

## How publishing works

Releases are fully automated via OIDC trusted publishing — no API tokens or secrets are
needed after one-time setup. The workflow (`.github/workflows/publish.yml`) fires when a
`v<version>` git tag is pushed and publishes both the sdist and wheel to PyPI. The
tag must identify a commit already merged into `main`, and its version must match
`pyproject.toml`. Merging this workflow does not publish a package.

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
1. Verifies this is a version tag matching `pyproject.toml` and its commit is in `main`.
2. Runs `uv build` to produce the sdist and wheel (pure-Python, `py3-none-any`).
3. Publishes both artifacts to PyPI via `pypa/gh-action-pypi-publish` using OIDC.

For a manual retry, select the existing `v<version>` tag in **Run workflow**.
Selecting a branch fails before building or publishing. PyPI does not allow
overwriting an already published version; investigate any partial publication
before retrying.

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

3. Confirm both configurations before the first intended release, then follow the
   release steps above. Do not use disposable production tags as a handshake test:
   publishing to PyPI creates a real package version.

> **Note:** The OIDC trusted-publisher handshake can only be proven by a real tag-triggered
> run after PyPI-side configuration. No local test can verify this step. The presence
> of this workflow alone does not establish that either service is configured.

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
python -c "from importlib.metadata import version; print(version('amplifier-foundation'))"
```
