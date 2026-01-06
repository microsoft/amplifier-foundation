"""Tests for source handlers."""

import tempfile
import zipfile
from pathlib import Path

import pytest
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.sources.file import FileSourceHandler
from amplifier_foundation.sources.http import HttpSourceHandler
from amplifier_foundation.sources.zip import ZipSourceHandler


class TestFileSourceHandler:
    """Tests for FileSourceHandler."""

    def test_can_handle_file_uri(self) -> None:
        """Handles file:// URIs."""
        handler = FileSourceHandler()
        parsed = ParsedURI(scheme="file", host="", path="/some/path", ref="", subpath="")
        assert handler.can_handle(parsed) is True

    def test_can_handle_absolute_path(self) -> None:
        """Handles absolute paths (is_file=True when scheme=file)."""
        handler = FileSourceHandler()
        # Absolute paths get scheme="file" from parse_uri
        parsed = ParsedURI(scheme="file", host="", path="/absolute/path", ref="", subpath="")
        assert handler.can_handle(parsed) is True

    def test_cannot_handle_git(self) -> None:
        """Does not handle git URIs."""
        handler = FileSourceHandler()
        parsed = ParsedURI(scheme="git+https", host="github.com", path="/org/repo", ref="main", subpath="")
        assert handler.can_handle(parsed) is False

    @pytest.mark.asyncio
    async def test_resolve_existing_file(self) -> None:
        """Resolves existing file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.yaml"
            test_file.write_text("name: test")

            handler = FileSourceHandler(base_path=Path(tmpdir))
            parsed = ParsedURI(scheme="file", host="", path=str(test_file), ref="", subpath="")
            result = await handler.resolve(parsed, Path(tmpdir) / "cache")

            assert result.active_path == test_file
            # source_root is the parent directory for non-cached files
            assert result.source_root == test_file.parent

    @pytest.mark.asyncio
    async def test_resolve_with_subpath(self) -> None:
        """Resolves file path with subpath."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            subdir = base / "bundles" / "core"
            subdir.mkdir(parents=True)
            (subdir / "bundle.yaml").write_text("name: core")

            handler = FileSourceHandler(base_path=base)
            parsed = ParsedURI(scheme="file", host="", path=str(base / "bundles"), ref="", subpath="core")
            result = await handler.resolve(parsed, base / "cache")

            assert result.active_path == subdir
            assert result.source_root == (base / "bundles").resolve()


class TestHttpSourceHandler:
    """Tests for HttpSourceHandler."""

    def test_can_handle_https(self) -> None:
        """Handles https:// URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(scheme="https", host="example.com", path="/bundle.yaml", ref="", subpath="")
        assert handler.can_handle(parsed) is True

    def test_can_handle_http(self) -> None:
        """Handles http:// URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(scheme="http", host="example.com", path="/bundle.yaml", ref="", subpath="")
        assert handler.can_handle(parsed) is True

    def test_cannot_handle_file(self) -> None:
        """Does not handle file:// URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(scheme="file", host="", path="/local/path", ref="", subpath="")
        assert handler.can_handle(parsed) is False

    def test_cannot_handle_git(self) -> None:
        """Does not handle git URIs."""
        handler = HttpSourceHandler()
        parsed = ParsedURI(scheme="git+https", host="github.com", path="/org/repo", ref="main", subpath="")
        assert handler.can_handle(parsed) is False


class TestZipSourceHandler:
    """Tests for ZipSourceHandler."""

    def test_can_handle_zip_https(self) -> None:
        """Handles zip+https:// URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(scheme="zip+https", host="example.com", path="/bundle.zip", ref="", subpath="")
        assert handler.can_handle(parsed) is True

    def test_can_handle_zip_file(self) -> None:
        """Handles zip+file:// URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(scheme="zip+file", host="", path="/local/bundle.zip", ref="", subpath="")
        assert handler.can_handle(parsed) is True

    def test_cannot_handle_plain_https(self) -> None:
        """Does not handle plain https:// URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(scheme="https", host="example.com", path="/bundle.yaml", ref="", subpath="")
        assert handler.can_handle(parsed) is False

    def test_cannot_handle_git(self) -> None:
        """Does not handle git URIs."""
        handler = ZipSourceHandler()
        parsed = ParsedURI(scheme="git+https", host="github.com", path="/org/repo", ref="main", subpath="")
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
            parsed = ParsedURI(scheme="zip+file", host="", path=str(zip_path), ref="", subpath="")
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
            parsed = ParsedURI(scheme="zip+file", host="", path=str(zip_path), ref="", subpath="foundation")
            result = await handler.resolve(parsed, cache_dir)

            assert result.active_path.exists()
            assert result.active_path.name == "foundation"
            assert (result.active_path / "bundle.yaml").exists()
            assert result.source_root != result.active_path  # subpath creates a subdirectory

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
            parsed = ParsedURI(scheme="zip+file", host="", path=str(zip_path), ref="", subpath="")

            # First resolve - extracts
            result1 = await handler.resolve(parsed, cache_dir)

            # Delete original zip
            zip_path.unlink()

            # Second resolve - uses cache
            result2 = await handler.resolve(parsed, cache_dir)

            assert result1.active_path == result2.active_path
            assert result2.active_path.exists()
