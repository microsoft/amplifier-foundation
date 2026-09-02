"""Tests for source handlers."""

import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest
from amplifier_foundation.bundle import Bundle
from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ParsedURI, ResolvedSource, parse_uri
from amplifier_foundation.sources.file import FileSourceHandler
from amplifier_foundation.sources.git import GitSourceHandler, _is_full_commit_sha
from amplifier_foundation.sources.http import HttpSourceHandler
from amplifier_foundation.sources.zip import ZipSourceHandler
from amplifier_foundation.updates import update_bundle


class TestFileSourceHandler:
    """Tests for FileSourceHandler."""

    def test_can_handle_file_uri(self) -> None:
        """Handles file:// URIs."""
        handler = FileSourceHandler()
        parsed = ParsedURI(
            scheme="file", host="", path="/some/path", ref="", subpath=""
        )
        assert handler.can_handle(parsed) is True

    def test_can_handle_absolute_path(self) -> None:
        """Handles absolute paths (is_file=True when scheme=file)."""
        handler = FileSourceHandler()
        # Absolute paths get scheme="file" from parse_uri
        parsed = ParsedURI(
            scheme="file", host="", path="/absolute/path", ref="", subpath=""
        )
        assert handler.can_handle(parsed) is True

    def test_cannot_handle_git(self) -> None:
        """Does not handle git URIs."""
        handler = FileSourceHandler()
        parsed = ParsedURI(
            scheme="git+https",
            host="github.com",
            path="/org/repo",
            ref="main",
            subpath="",
        )
        assert handler.can_handle(parsed) is False

    @pytest.mark.asyncio
    async def test_resolve_existing_file(self) -> None:
        """Resolves existing file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.yaml"
            test_file.write_text("name: test")

            handler = FileSourceHandler(base_path=Path(tmpdir))
            parsed = ParsedURI(
                scheme="file", host="", path=str(test_file), ref="", subpath=""
            )
            result = await handler.resolve(parsed, Path(tmpdir) / "cache")

            # FileSourceHandler.resolve() calls Path.resolve() internally. On
            # Windows, tempfile.TemporaryDirectory() may hand back a path
            # containing an 8.3 short component (e.g. "RUNNER~1") while
            # resolve() returns the canonical long form (e.g. "runneradmin").
            # Both spellings name the same file/directory, so resolve the
            # expected side too rather than comparing raw/short vs.
            # canonical/long.
            assert result.active_path == test_file.resolve()
            # source_root is the parent directory for non-cached files
            assert result.source_root == test_file.parent.resolve()

    @pytest.mark.asyncio
    async def test_resolve_with_subpath(self) -> None:
        """Resolves file path with subpath."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            subdir = base / "bundles" / "core"
            subdir.mkdir(parents=True)
            (subdir / "bundle.yaml").write_text("name: core")

            handler = FileSourceHandler(base_path=base)
            parsed = ParsedURI(
                scheme="file",
                host="",
                path=str(base / "bundles"),
                ref="",
                subpath="core",
            )
            result = await handler.resolve(parsed, base / "cache")

            # See resolve() note in test_resolve_existing_file above.
            assert result.active_path == subdir.resolve()
            assert result.source_root == (base / "bundles").resolve()


class TestHttpSourceHandler:
    """Tests for HttpSourceHandler."""

    def test_can_handle_https(self) -> None:
        """Handles https:// URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(
            scheme="https", host="example.com", path="/bundle.yaml", ref="", subpath=""
        )
        assert handler.can_handle(parsed) is True

    def test_can_handle_http(self) -> None:
        """Handles http:// URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(
            scheme="http", host="example.com", path="/bundle.yaml", ref="", subpath=""
        )
        assert handler.can_handle(parsed) is True

    def test_cannot_handle_file(self) -> None:
        """Does not handle file:// URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(
            scheme="file", host="", path="/local/path", ref="", subpath=""
        )
        assert handler.can_handle(parsed) is False

    def test_cannot_handle_git(self) -> None:
        """Does not handle git URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(
            scheme="git+https",
            host="github.com",
            path="/org/repo",
            ref="main",
            subpath="",
        )
        assert handler.can_handle(parsed) is False


class TestZipSourceHandler:
    """Tests for ZipSourceHandler."""

    def test_can_handle_zip_https(self) -> None:
        """Handles zip+https:// URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(
            scheme="zip+https",
            host="example.com",
            path="/bundle.zip",
            ref="",
            subpath="",
        )
        assert handler.can_handle(parsed) is True

    def test_can_handle_zip_file(self) -> None:
        """Handles zip+file:// URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(
            scheme="zip+file", host="", path="/local/bundle.zip", ref="", subpath=""
        )
        assert handler.can_handle(parsed) is True

    def test_cannot_handle_plain_https(self) -> None:
        """Does not handle plain https:// URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(
            scheme="https", host="example.com", path="/bundle.yaml", ref="", subpath=""
        )
        assert handler.can_handle(parsed) is False

    def test_cannot_handle_git(self) -> None:
        """Does not handle git URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(
            scheme="git+https",
            host="github.com",
            path="/org/repo",
            ref="main",
            subpath="",
        )
        assert handler.can_handle(parsed) is False

    @pytest.mark.asyncio
    async def test_resolve_local_zip(self) -> None:
        """Resolves local zip file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            cache_dir = base / "cache"

            # Create a test zip file
            zip_path = base / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("bundle.yaml", "name: test-bundle\nversion: 1.0.0")
                zf.writestr("context/readme.md", "# Test Bundle")

            handler = ZipSourceHandler()
            parsed = ParsedURI(
                scheme="zip+file", host="", path=str(zip_path), ref="", subpath=""
            )
            result = await handler.resolve(parsed, cache_dir)

            assert result.active_path.exists()
            assert (result.active_path / "bundle.yaml").exists()
            assert (result.active_path / "context" / "readme.md").exists()

    @pytest.mark.asyncio
    async def test_resolve_local_zip_with_subpath(self) -> None:
        """Resolves local zip file with subpath."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            cache_dir = base / "cache"

            # Create a test zip file with nested structure
            zip_path = base / "bundles.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("foundation/bundle.yaml", "name: foundation")
                zf.writestr("foundation/context/readme.md", "# Foundation")
                zf.writestr("extended/bundle.yaml", "name: extended")

            handler = ZipSourceHandler()
            parsed = ParsedURI(
                scheme="zip+file",
                host="",
                path=str(zip_path),
                ref="",
                subpath="foundation",
            )
            result = await handler.resolve(parsed, cache_dir)

            assert result.active_path.exists()
            assert result.active_path.name == "foundation"
            assert (result.active_path / "bundle.yaml").exists()
            assert (
                result.source_root != result.active_path
            )  # subpath creates a subdirectory

    @pytest.mark.asyncio
    async def test_uses_cache(self) -> None:
        """Uses cached extraction on second resolve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            cache_dir = base / "cache"

            # Create a test zip file
            zip_path = base / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("bundle.yaml", "name: test")

            handler = ZipSourceHandler()
            parsed = ParsedURI(
                scheme="zip+file", host="", path=str(zip_path), ref="", subpath=""
            )

            # First resolve - extracts
            result1 = await handler.resolve(parsed, cache_dir)

            # Delete original zip
            zip_path.unlink()

            # Second resolve - uses cache
            result2 = await handler.resolve(parsed, cache_dir)

            assert result1.active_path == result2.active_path
            assert result2.active_path.exists()


class TestGitSourceHandlerCloneIntegrity:
    """Tests for GitSourceHandler._verify_clone_integrity."""

    def test_accepts_amplifier_toml_as_valid_marker(self) -> None:
        """Returns True when .git dir and amplifier.toml exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir)
            (clone_path / ".git").mkdir()
            (clone_path / "amplifier.toml").write_text(
                '[transport]\ntransport = "rust"\n'
            )
            handler = GitSourceHandler()
            assert handler._verify_clone_integrity(clone_path) is True

    def test_still_accepts_pyproject_toml(self) -> None:
        """Returns True when .git dir and pyproject.toml exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir)
            (clone_path / ".git").mkdir()
            (clone_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
            handler = GitSourceHandler()
            assert handler._verify_clone_integrity(clone_path) is True

    def test_still_accepts_bundle_md(self) -> None:
        """Returns True when .git dir and bundle.md exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir)
            (clone_path / ".git").mkdir()
            (clone_path / "bundle.md").write_text("# Bundle\n")
            handler = GitSourceHandler()
            assert handler._verify_clone_integrity(clone_path) is True

    def test_rejects_clone_with_no_markers(self) -> None:
        """Returns False when .git dir exists but no marker files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir)
            (clone_path / ".git").mkdir()
            handler = GitSourceHandler()
            assert handler._verify_clone_integrity(clone_path) is False

    def test_rejects_missing_git_directory(self) -> None:
        """Returns False when .git dir is missing (even with amplifier.toml)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir)
            (clone_path / "amplifier.toml").write_text(
                '[transport]\ntransport = "rust"\n'
            )
            handler = GitSourceHandler()
            assert handler._verify_clone_integrity(clone_path) is False


def _git(args: list[str], cwd: Path) -> str:
    """Run a git command in a fixture repo and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_fixture_repo(base: Path) -> tuple[Path, list[str]]:
    """Create a local git repo with two commits.

    Returns:
        (repo_path, [first_commit_sha, second_commit_sha])
    """
    repo = base / "fixture-repo"
    repo.mkdir()
    _git(["init", "--quiet", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)

    (repo / "bundle.md").write_text("# Fixture Bundle\n")
    (repo / "data.txt").write_text("version one\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "--quiet", "-m", "first"], cwd=repo)
    first_sha = _git(["rev-parse", "HEAD"], cwd=repo)

    (repo / "data.txt").write_text("version two\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "--quiet", "-m", "second"], cwd=repo)
    second_sha = _git(["rev-parse", "HEAD"], cwd=repo)

    return repo, [first_sha, second_sha]


def _git_supports_sha256() -> bool:
    """Whether the local git can create SHA-256 object-format repositories.

    ``--object-format=sha256`` landed in git 2.29. Probed by actually creating
    one rather than parsing ``git --version``: the flag was experimental for
    several releases and a build can refuse it independently of its version
    number, so the probe answers the question the tests actually care about.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["git", "init", "--quiet", "--object-format=sha256", "-b", "main", "probe"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0


def _make_sha256_fixture_repo(base: Path) -> tuple[Path, list[str]]:
    """Create a local SHA-256 object-format git repo with two commits.

    Identical in shape to ``_make_fixture_repo``, but every object id is 64
    hex characters instead of 40 -- the case a 40-only ref classifier rejects.

    Returns:
        (repo_path, [first_commit_sha, second_commit_sha])
    """
    repo = base / "fixture-repo-sha256"
    repo.mkdir()
    _git(["init", "--quiet", "--object-format=sha256", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)

    (repo / "bundle.md").write_text("# Fixture Bundle\n")
    (repo / "data.txt").write_text("version one\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "--quiet", "-m", "first"], cwd=repo)
    first_sha = _git(["rev-parse", "HEAD"], cwd=repo)

    (repo / "data.txt").write_text("version two\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "--quiet", "-m", "second"], cwd=repo)
    second_sha = _git(["rev-parse", "HEAD"], cwd=repo)

    return repo, [first_sha, second_sha]


def _parsed_git_file_uri(repo: Path, ref: str) -> ParsedURI:
    """Build a ParsedURI for a git+file:// URI pointing at a local fixture repo."""
    return ParsedURI(scheme="git+file", host="", path=str(repo), ref=ref, subpath="")


class TestGitSourceHandlerStatus:
    """Tests for GitSourceHandler.get_status."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "source_uri",
        [
            "git+https://github.com/example/repository@main",
            (
                "git+https://github.com/example/repository@feat/status-uri"
                "#subdirectory=modules/loop-pipeline"
            ),
        ],
    )
    async def test_get_status_preserves_source_uri_semantics(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source_uri: str
    ) -> None:
        """Status URI round-trips its repo, ref, and optional subdirectory."""
        parsed = parse_uri(source_uri)
        handler = GitSourceHandler()

        async def fake_get_remote_commit(_git_url: str, _ref: str) -> str:
            return "a" * 40

        monkeypatch.setattr(handler, "_get_remote_commit", fake_get_remote_commit)

        status = await handler.get_status(parsed, tmp_path / "cache")

        assert status.source_uri == source_uri
        assert parse_uri(status.source_uri) == parsed

    @pytest.mark.asyncio
    async def test_update_bundle_preserves_subdirectory_active_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Update reinstalls the resolved module subdirectory, not its repo root."""
        from amplifier_foundation.modules.activator import ModuleActivator

        source_uri = (
            "git+https://github.com/example/repository@main"
            "#subdirectory=modules/loop-pipeline"
        )
        expected_parsed = parse_uri(source_uri)
        cache_dir = tmp_path / "cache"
        repository_root = cache_dir / "repository"
        module_path = repository_root / "modules" / "loop-pipeline"
        module_path.mkdir(parents=True)
        (module_path / "pyproject.toml").write_text(
            '[project]\nname = "test-loop-pipeline"\nversion = "0.0.0"\n'
        )

        updated_sources: list[ParsedURI] = []
        installed_paths: list[Path] = []

        async def fake_get_remote_commit(
            _self: GitSourceHandler, _git_url: str, _ref: str
        ) -> str:
            return "a" * 40

        async def fake_update(
            _self: GitSourceHandler, parsed: ParsedURI, _cache_dir: Path
        ) -> ResolvedSource:
            updated_sources.append(parsed)
            return ResolvedSource(active_path=module_path, source_root=repository_root)

        async def fake_install_dependencies(
            _self: ModuleActivator, path: Path
        ) -> None:
            installed_paths.append(path)

        monkeypatch.setattr(
            GitSourceHandler, "_get_remote_commit", fake_get_remote_commit
        )
        monkeypatch.setattr(GitSourceHandler, "update", fake_update)
        monkeypatch.setattr(
            ModuleActivator, "_install_dependencies", fake_install_dependencies
        )

        bundle = Bundle(
            name="test",
            session={"orchestrator": {"source": source_uri}},
        )
        await update_bundle(bundle, cache_dir=cache_dir)

        assert updated_sources == [expected_parsed]
        assert installed_paths == [module_path]


class TestIsFullCommitSha:
    """Tests for _is_full_commit_sha ref classification."""

    def test_accepts_full_lowercase_sha(self) -> None:
        assert _is_full_commit_sha("32d4052dad46016f91ce698646580473e4121344") is True

    def test_accepts_uppercase_sha(self) -> None:
        assert _is_full_commit_sha("32D4052DAD46016F91CE698646580473E4121344") is True

    def test_accepts_full_lowercase_sha256_sha(self) -> None:
        """A SHA-256 repository's commit ids are 64 hex characters."""
        assert (
            _is_full_commit_sha(
                "ca155c02e012aae375d51c32315a5a7962e031f216b92f2e90c544069767ab08"
            )
            is True
        )

    def test_accepts_uppercase_sha256_sha(self) -> None:
        assert (
            _is_full_commit_sha(
                "CA155C02E012AAE375D51C32315A5A7962E031F216B92F2E90C544069767AB08"
            )
            is True
        )

    def test_rejects_short_sha(self) -> None:
        assert _is_full_commit_sha("32d4052") is False

    def test_rejects_39_chars(self) -> None:
        assert _is_full_commit_sha("3" * 39) is False

    def test_rejects_41_chars(self) -> None:
        assert _is_full_commit_sha("3" * 41) is False

    def test_rejects_length_between_the_two_object_formats(self) -> None:
        """Only 40 and 64 are full hashes; nothing in between is a commit id."""
        assert _is_full_commit_sha("3" * 50) is False

    def test_rejects_63_chars(self) -> None:
        assert _is_full_commit_sha("3" * 63) is False

    def test_rejects_65_chars(self) -> None:
        assert _is_full_commit_sha("3" * 65) is False

    def test_rejects_non_hex_chars_at_sha256_length(self) -> None:
        assert _is_full_commit_sha("g" + "3" * 63) is False

    def test_rejects_non_hex_chars(self) -> None:
        assert _is_full_commit_sha("g" + "3" * 39) is False

    def test_rejects_branch_names(self) -> None:
        assert _is_full_commit_sha("main") is False
        assert _is_full_commit_sha("feat/some-branch") is False
        assert _is_full_commit_sha("v1.0.0") is False


class TestGitSourceHandlerShaRefs:
    """Tests for resolving git refs pinned to a commit SHA."""

    @pytest.mark.asyncio
    async def test_resolve_full_sha_ref_pins_non_tip_commit(self) -> None:
        """Full-SHA ref resolves to exactly that commit (not the branch tip).

        The pinned commit is NOT the branch tip and the fixture remote does
        not enable uploadpack.allowReachableSHA1InWant, so this also exercises
        the full-clone + checkout fallback.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, shas = _make_fixture_repo(base)
            pinned_sha = shas[0]  # older, non-tip commit

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref=pinned_sha)
            result = await handler.resolve(parsed, base / "cache")

            assert result.active_path.exists()
            assert (result.active_path / "data.txt").read_text() == "version one\n"
            head = _git(["rev-parse", "HEAD"], cwd=result.source_root)
            assert head == pinned_sha

    @pytest.mark.asyncio
    async def test_resolve_full_sha_ref_shallow_fetch(self) -> None:
        """When the server allows SHA fetches, the shallow fast path is used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, shas = _make_fixture_repo(base)
            # GitHub enables this; mirror it on the fixture remote.
            _git(["config", "uploadpack.allowReachableSHA1InWant", "true"], cwd=repo)
            pinned_sha = shas[0]

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref=pinned_sha)
            result = await handler.resolve(parsed, base / "cache")

            head = _git(["rev-parse", "HEAD"], cwd=result.source_root)
            assert head == pinned_sha
            # Shallow marker proves the depth-1 fetch path was taken
            # (the fallback does a full clone, which is not shallow).
            assert (result.source_root / ".git" / "shallow").exists()

    @pytest.mark.asyncio
    async def test_resolve_branch_ref_still_works(self) -> None:
        """Branch refs continue to use the existing --branch clone path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, shas = _make_fixture_repo(base)

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref="main")
            result = await handler.resolve(parsed, base / "cache")

            assert result.active_path.exists()
            head = _git(["rev-parse", "HEAD"], cwd=result.source_root)
            assert head == shas[1]  # branch tip

    @pytest.mark.asyncio
    async def test_short_sha_ref_raises_clear_error(self) -> None:
        """Short/abbreviated SHAs are not special-cased and fail clearly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, shas = _make_fixture_repo(base)
            short_sha = shas[0][:7]

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref=short_sha)
            with pytest.raises(BundleNotFoundError, match="Failed to clone"):
                await handler.resolve(parsed, base / "cache")

    @pytest.mark.asyncio
    async def test_unknown_sha_raises_clear_error(self) -> None:
        """A well-formed SHA that doesn't exist in the repo fails clearly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, _shas = _make_fixture_repo(base)
            bogus_sha = "deadbeef" * 5  # 40 hex chars, not in the repo

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref=bogus_sha)
            with pytest.raises(BundleNotFoundError, match="Failed to clone"):
                await handler.resolve(parsed, base / "cache")

    @pytest.mark.skipif(
        not _git_supports_sha256(),
        reason="local git cannot create --object-format=sha256 repositories",
    )
    @pytest.mark.asyncio
    async def test_resolve_sha256_sha_ref_pins_non_tip_commit(self) -> None:
        """A 64-hex SHA-256 commit id resolves to exactly that commit.

        Before 64-hex refs were recognised, this ref fell through to
        ``git clone --branch <sha>`` and failed with "Remote branch <sha> not
        found in upstream origin" -- the same failure 40-hex refs used to hit,
        for every repository using git's SHA-256 object format.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, shas = _make_sha256_fixture_repo(base)
            pinned_sha = shas[0]  # older, non-tip commit
            assert len(pinned_sha) == 64

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref=pinned_sha)
            result = await handler.resolve(parsed, base / "cache")

            assert result.active_path.exists()
            assert (result.active_path / "data.txt").read_text() == "version one\n"
            head = _git(["rev-parse", "HEAD"], cwd=result.source_root)
            assert head == pinned_sha

    @pytest.mark.skipif(
        not _git_supports_sha256(),
        reason="local git cannot create --object-format=sha256 repositories",
    )
    @pytest.mark.asyncio
    async def test_resolve_sha256_sha_ref_shallow_fetch(self) -> None:
        """The shallow fast path works for SHA-256 refs too, not just SHA-1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, shas = _make_sha256_fixture_repo(base)
            _git(["config", "uploadpack.allowReachableSHA1InWant", "true"], cwd=repo)
            pinned_sha = shas[0]

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref=pinned_sha)
            result = await handler.resolve(parsed, base / "cache")

            head = _git(["rev-parse", "HEAD"], cwd=result.source_root)
            assert head == pinned_sha
            assert (result.source_root / ".git" / "shallow").exists()

    @pytest.mark.skipif(
        not _git_supports_sha256(),
        reason="local git cannot create --object-format=sha256 repositories",
    )
    @pytest.mark.asyncio
    async def test_unknown_sha256_sha_raises_clear_error(self) -> None:
        """A well-formed 64-hex SHA that isn't in the repo fails clearly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, _shas = _make_sha256_fixture_repo(base)
            bogus_sha = "deadbeef" * 8  # 64 hex chars, not in the repo

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref=bogus_sha)
            with pytest.raises(BundleNotFoundError, match="Failed to clone"):
                await handler.resolve(parsed, base / "cache")

    @pytest.mark.skipif(
        not _git_supports_sha256(),
        reason="local git cannot create --object-format=sha256 repositories",
    )
    @pytest.mark.asyncio
    async def test_sha256_branch_ref_still_works(self) -> None:
        """Branch refs in a SHA-256 repo keep the existing --branch fast path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            repo, shas = _make_sha256_fixture_repo(base)

            handler = GitSourceHandler()
            parsed = _parsed_git_file_uri(repo, ref="main")
            result = await handler.resolve(parsed, base / "cache")

            head = _git(["rev-parse", "HEAD"], cwd=result.source_root)
            assert head == shas[1]  # branch tip

    def test_sha_and_branch_refs_get_distinct_cache_paths(self) -> None:
        """A SHA ref must not collide with a branch ref cache entry."""
        handler = GitSourceHandler()
        cache_dir = Path("/tmp/cache")
        sha = "32d4052dad46016f91ce698646580473e4121344"

        branch_parsed = ParsedURI(
            scheme="git+https",
            host="github.com",
            path="/org/repo",
            ref="main",
            subpath="",
        )
        sha_parsed = ParsedURI(
            scheme="git+https",
            host="github.com",
            path="/org/repo",
            ref=sha,
            subpath="",
        )

        branch_path = handler._get_cache_path(branch_parsed, cache_dir)
        sha_path = handler._get_cache_path(sha_parsed, cache_dir)
        assert branch_path != sha_path


class TestGitNetworkOpRetry343:
    """Regression coverage for issue #343 (transient clone failures).

    ``git clone`` had no retry. A single dropped connection failed the module
    permanently. Once activation failures become fatal rather than silent, that
    same blip would take down a whole session -- so the retry is what makes
    fail-loud safe to turn on.
    """

    def test_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failure followed by a success resolves without raising.

        GAP-030 cross-platform validation note: this test used to monkeypatch
        ``git_mod.subprocess.run``, but the GAP-014/GAP-025 fix rewrote the
        actual subprocess invocation to go through a new ``_run_git_subprocess``
        helper (``subprocess.Popen`` + a wall-clock timeout), which
        ``_run_git_network_op`` calls directly. Patching ``subprocess.run`` no
        longer intercepted anything, so this test silently stopped testing the
        retry loop and instead made a REAL network call to a nonexistent host
        -- passing or failing for the wrong reason depending on DNS behavior,
        not the reason its assertions claim. Patch the real seam instead:
        ``_run_git_subprocess``, the function ``_run_git_network_op`` actually
        calls per attempt.
        """
        from amplifier_foundation.sources import git as git_mod

        calls: list[list[str]] = []
        sleeps: list[float] = []

        def fake_run_subprocess(args, cwd, timeout_s):
            calls.append(args)
            if len(calls) == 1:
                raise subprocess.CalledProcessError(
                    128, args, stderr="fatal: unable to access: Connection reset"
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(git_mod, "_run_git_subprocess", fake_run_subprocess)
        monkeypatch.setattr(git_mod.time, "sleep", lambda s: sleeps.append(s))

        result = git_mod._run_git_network_op(["git", "clone", "url", "/tmp/x"])

        assert result.returncode == 0
        assert len(calls) == 2, "should have retried exactly once"
        assert sleeps == [1.0], "should have backed off before the retry"

    def test_exhausts_attempts_and_reraises_real_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When every attempt fails, the original git error is raised.

        See the GAP-030 note on ``test_retries_then_succeeds`` above: patches
        ``_run_git_subprocess`` (the real seam) rather than ``subprocess.run``
        (bypassed since GAP-014/GAP-025 switched to ``subprocess.Popen``).
        """
        from amplifier_foundation.sources import git as git_mod

        calls: list[list[str]] = []

        def always_fail(args, cwd, timeout_s):
            calls.append(args)
            raise subprocess.CalledProcessError(
                128, args, stderr="fatal: could not resolve host: github.com"
            )

        monkeypatch.setattr(git_mod, "_run_git_subprocess", always_fail)
        monkeypatch.setattr(git_mod.time, "sleep", lambda _s: None)

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            git_mod._run_git_network_op(["git", "clone", "url", "/tmp/x"])

        assert len(calls) == git_mod._CLONE_MAX_ATTEMPTS
        # The real cause survives -- not swallowed into a generic message.
        assert "could not resolve host" in (exc_info.value.stderr or "")

    def test_shallow_sha_fetch_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SHA fetch falls straight through to full clone, no retry delay.

        Servers without ``allowReachableSHA1InWant`` refuse the shallow fetch as
        a matter of course. That failure is a designed fallback, not an error,
        so retrying it would add seconds to a normal path.

        GAP-030 cross-platform validation note: same seam fix as the two tests
        above -- the network-touching fetch/clone calls go through
        ``_run_git_subprocess`` (Popen-based) since GAP-014/GAP-025, not
        ``subprocess.run``. The LOCAL git plumbing in ``_clone_at_commit``'s
        own ``run_git()`` closure (init/remote add/checkout) still genuinely
        uses ``subprocess.run`` directly, so it's faked the same way the
        original (pre-GAP-014) version of this test faked it: unconditional
        success. None of the assertions below depend on real git state, and
        the previous version of this test didn't distinguish local plumbing
        from network calls either -- it just happened to intercept both
        through one seam because both used to go through subprocess.run.
        """
        from amplifier_foundation.sources import git as git_mod
        from amplifier_foundation.sources.git import GitSourceHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            handler = GitSourceHandler()
            cache_path = Path(tmpdir) / "repo"
            seen: list[list[str]] = []

            def fake_local_run(args, **kwargs):  # noqa: ANN001, ANN202
                # Local plumbing only (init/remote add/checkout) -- the
                # network-touching fetch/clone below no longer goes through
                # subprocess.run at all, so this never sees "fetch"/"clone".
                seen.append(list(args))
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            def fake_run_subprocess(args, cwd, timeout_s):
                seen.append(list(args))
                # Refuse the shallow SHA fetch; succeed at everything else.
                if "fetch" in args:
                    raise subprocess.CalledProcessError(128, args, stderr="refused")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            monkeypatch.setattr(git_mod.subprocess, "run", fake_local_run)
            monkeypatch.setattr(git_mod, "_run_git_subprocess", fake_run_subprocess)

            handler._clone_at_commit(
                "https://example.invalid/r.git", "a" * 40, cache_path
            )

            fetch_calls = [c for c in seen if "fetch" in c]
            assert len(fetch_calls) == 1, (
                "shallow SHA fetch must not be retried -- its failure is the "
                "designed trigger for the full-clone fallback"
            )
            assert any("clone" in c for c in seen), "should fall back to full clone"


class TestSourceStatusIsPinned:
    """recipes-sro: is_pinned recognises both SHA-1 and SHA-256 commit refs."""

    def _status(self, ref: str):
        from amplifier_foundation.sources.protocol import SourceStatus

        return SourceStatus(
            source_uri="git+https://example.invalid/repo",
            is_cached=True,
            cached_ref=ref,
        )

    def test_40_hex_sha1_is_pinned(self):
        assert self._status("a" * 39 + "b").is_pinned is True

    def test_64_hex_sha256_is_pinned(self):
        assert self._status("0123456789abcdef" * 4).is_pinned is True

    def test_branch_ref_is_not_pinned(self):
        assert self._status("main").is_pinned is False

    def test_version_tag_is_pinned(self):
        assert self._status("v2.1.2").is_pinned is True

    def test_non_hex_40_chars_not_pinned(self):
        assert self._status("z" * 40).is_pinned is False

    def test_non_hex_64_chars_not_pinned(self):
        assert self._status("g" * 64).is_pinned is False
