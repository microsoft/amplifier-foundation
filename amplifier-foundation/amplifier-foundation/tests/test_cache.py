"""Tests for cache implementations."""

import tempfile
from pathlib import Path

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.cache.disk import DiskCache
from amplifier_foundation.cache.simple import SimpleCache


class TestSimpleCache:
    """Tests for SimpleCache (in-memory)."""

    def test_get_miss(self) -> None:
        """Returns None for missing key."""
        cache = SimpleCache()
        assert cache.get("nonexistent") is None

    def test_set_and_get(self) -> None:
        """Sets and retrieves bundle."""
        cache = SimpleCache()
        bundle = Bundle(name="test", version="1.0.0")

        cache.set("test-key", bundle)
        result = cache.get("test-key")

        assert result is not None
        assert result.name == "test"
        assert result.version == "1.0.0"

    def test_contains(self) -> None:
        """Checks key existence."""
        cache = SimpleCache()
        bundle = Bundle(name="test", version="1.0.0")

        assert "test-key" not in cache
        cache.set("test-key", bundle)
        assert "test-key" in cache


class TestDiskCache:
    """Tests for DiskCache (filesystem-based)."""

    def test_requires_cache_dir(self) -> None:
        """Must provide cache_dir (mechanism not policy)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir) / "bundles")
            assert cache.cache_dir.exists()

    def test_get_miss(self) -> None:
        """Returns None for missing key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir))
            assert cache.get("nonexistent") is None

    def test_set_and_get(self) -> None:
        """Sets and retrieves bundle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir))
            bundle = Bundle(
                name="test-bundle",
                version="1.0.0",
                description="A test bundle",
            )

            cache.set("test-key", bundle)
            result = cache.get("test-key")

            assert result is not None
            assert result.name == "test-bundle"
            assert result.version == "1.0.0"
            assert result.description == "A test bundle"

    def test_persists_across_instances(self) -> None:
        """Cache persists to disk across instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)

            # First instance - write
            cache1 = DiskCache(cache_dir=cache_dir)
            bundle = Bundle(name="persistent", version="2.0.0")
            cache1.set("persist-key", bundle)

            # Second instance - read
            cache2 = DiskCache(cache_dir=cache_dir)
            result = cache2.get("persist-key")

            assert result is not None
            assert result.name == "persistent"
            assert result.version == "2.0.0"

    def test_contains(self) -> None:
        """Checks key existence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir))
            bundle = Bundle(name="test", version="1.0.0")

            assert "test-key" not in cache
            cache.set("test-key", bundle)
            assert "test-key" in cache

    def test_clear(self) -> None:
        """Clears all cached bundles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir))

            # Add multiple bundles
            cache.set("key1", Bundle(name="one", version="1.0.0"))
            cache.set("key2", Bundle(name="two", version="1.0.0"))

            assert "key1" in cache
            assert "key2" in cache

            # Clear
            cache.clear()

            assert "key1" not in cache
            assert "key2" not in cache

    def test_handles_complex_bundle(self) -> None:
        """Serializes and deserializes complex bundles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir))

            # Bundle uses lists for providers/tools/hooks (module configs)
            # and dict for context mapping names to paths
            bundle = Bundle(
                name="complex",
                version="1.0.0",
                description="Complex bundle",
                includes=["./base.yaml"],
                providers=[{"module": "anthropic", "config": {"model": "claude-sonnet"}}],
                tools=[{"module": "filesystem", "config": {"enabled": True}}],
                hooks=[{"module": "logging", "config": {"level": "debug"}}],
                agents={"explorer": {"description": "Explores code"}},
                context={"readme": Path("/bundle/context/readme.md")},
                instruction="Be helpful",
            )

            cache.set("complex-key", bundle)
            result = cache.get("complex-key")

            assert result is not None
            assert result.name == "complex"
            assert result.includes == ["./base.yaml"]
            assert result.providers == [{"module": "anthropic", "config": {"model": "claude-sonnet"}}]
            assert result.tools == [{"module": "filesystem", "config": {"enabled": True}}]
            assert result.instruction == "Be helpful"

    def test_invalid_cache_returns_none(self) -> None:
        """Returns None and removes invalid cache entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir))

            # Write invalid JSON directly
            cache_file = cache._cache_key_to_path("bad-key")
            cache_file.write_text("not valid json {{{")

            # Should return None and clean up
            result = cache.get("bad-key")
            assert result is None
            assert not cache_file.exists()

    def test_cache_key_to_path_safe_filename(self) -> None:
        """Creates safe filenames from cache keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=Path(tmpdir))

            # Test with URI-like key
            path = cache._cache_key_to_path("git+https://github.com/org/repo@main")
            assert path.parent == cache.cache_dir
            assert path.suffix == ".json"
            # Should not contain invalid filename characters
            assert "/" not in path.name
            assert ":" not in path.name
