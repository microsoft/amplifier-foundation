"""Synthetic subprocess registration, text-only projection and ownership checks.

No provider network call, live session data or persistent checkpoint is exercised.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation import subprocess_runner as runner


@pytest.fixture
def media(tmp_path):
    directory = tmp_path / "child-1"
    directory.mkdir(mode=0o700)
    return {
        "v": 1,
        "session_id": "child-1",
        "session_directory": str(directory),
        "policy": {
            "max_bytes": 4096,
            "max_pixels": 1024,
            "max_images": 2,
            "retention": "drop",
        },
    }


def serialize(media=None, **overrides):
    return runner.serialize_subprocess_config(
        **{
            "config": {},
            "prompt": "text only",
            "parent_id": "parent-1",
            "project_path": "/unused",
            "session_id": "child-1",
            "media": media,
            **overrides,
        }
    )


def test_legacy_payload_has_no_media_field():
    payload = json.loads(serialize())
    assert set(payload) == {
        "config", "prompt", "parent_id", "project_path", "session_id",
        "module_paths", "bundle_package_paths", "sys_paths", "mention_mappings",
    }
    assert runner.deserialize_subprocess_config(json.dumps(payload)) == payload


def test_media_roundtrip_is_reference_and_callback_free(media):
    result = runner.deserialize_subprocess_config(serialize(media))
    assert result["media"] == media
    assert set(result["media"]) == {"v", "session_id", "session_directory", "policy"}


@pytest.mark.parametrize("field,value", [
    ("v", True), ("v", 2), ("session_id", "parent-1"),
    ("session_directory", "relative/child-1"),
    ("session_directory", "/tmp/secret-token=do-not-log/../child-1"),
    ("session_directory", "/tmp/foreign-1"),
    ("initializer", "untrusted.module:callback"),
    ("enabled", True), ("refs", []), ("source_store", "/parent"),
    ("pixels", "private-pixel-canary"),
])
def test_reject_bad_contract_without_echoing_payload(media, field, value):
    media[field] = value
    with pytest.raises(ValueError) as error:
        serialize(media)
    assert "private-pixel-canary" not in str(error.value)
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("max_bytes", True), ("max_pixels", 0), ("max_images", -1),
    ("retention", "durable"), ("retention", "request-only"),
    ("ttl_seconds", float("inf")), ("ttl_seconds", True),
    ("ttl_seconds", -1), ("authority", "parent-token"),
])
def test_reject_bad_policy(media, field, value):
    media["policy"][field] = value
    with pytest.raises(ValueError):
        serialize(media)


@pytest.mark.parametrize("session_id", [None, "../child-1", "parent-1", ".", ".."])
def test_requires_explicit_distinct_child(media, session_id):
    with pytest.raises(ValueError):
        serialize(media, session_id=session_id)


def test_deserializer_revalidates_untrusted_contract(media):
    payload = json.loads(serialize(media))
    payload["media"]["policy"]["retention"] = "durable"
    with pytest.raises(ValueError, match="no checkpoint"):
        runner.deserialize_subprocess_config(json.dumps(payload))
    payload["media"] = None
    with pytest.raises(ValueError):
        runner.deserialize_subprocess_config(json.dumps(payload))


def test_private_directory_required(media):
    Path(media["session_directory"]).chmod(0o755)
    with pytest.raises(ValueError, match="owner-private"):
        serialize(media)


def test_directory_must_exist(media):
    media["session_directory"] = str(Path(media["session_directory"]) / "child-1")
    with pytest.raises(ValueError, match="unavailable"):
        serialize(media)


def test_symlink_ancestor_rejected(media, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    media["session_directory"] = str(alias / "child-1")
    with pytest.raises(ValueError, match="links"):
        serialize(media)


def test_child_rechecks_directory_after_serialization(media, tmp_path):
    encoded = serialize(media)
    directory = Path(media["session_directory"])
    directory.rename(tmp_path / "original")
    directory.symlink_to(tmp_path / "original", target_is_directory=True)
    with pytest.raises(ValueError, match="links"):
        runner.deserialize_subprocess_config(encoded)


class Coordinator:
    def __init__(self, session_id, provider=None):
        self.session_id = session_id
        self.capabilities = {}
        self.providers = {} if provider is None else {"selected": provider}

    def get_capability(self, name):
        return self.capabilities.get(name)

    def register_capability(self, name, value):
        self.capabilities[name] = value

    def get(self, name):
        return self.providers if name == "providers" else None

    async def mount(self, category, value, *, name=None):
        assert category == "providers"
        self.providers[name] = value


def test_fresh_binding_and_foreign_store_rejected(media):
    from amplifier_foundation.session.media import MediaRefusal

    payload = json.loads(serialize(media))
    session = SimpleNamespace(coordinator=Coordinator("child-1"))
    store, capability = runner._bind_child_media(session, payload)
    assert capability.version == 1
    assert capability.enabled is False
    assert store.checkpoint_verifier is None
    assert store.policy.retention == "drop"
    assert store.root == Path(media["session_directory"]) / "media"
    store.close()
    second = SimpleNamespace(coordinator=Coordinator("child-1"))
    store2, cap2 = runner._bind_child_media(second, payload)
    assert store2 is not store
    assert cap2 is not capability
    store2.close()
    # A correctly named directory must not override the store's existing owner.
    owner = Path(media["session_directory"]) / "media" / "owner.json"
    owner.write_text(json.dumps({"v": 1, "session_id": "foreign-1"}))
    with pytest.raises(MediaRefusal, match="different session"):
        runner._bind_child_media(second, payload)


def test_existing_capability_not_inherited(media):
    from amplifier_foundation.session.media import MediaRefusal

    coordinator = Coordinator("child-1")
    coordinator.capabilities["experimental.media.v1"] = object()
    with pytest.raises(MediaRefusal, match="already has media"):
        runner._bind_child_media(
            SimpleNamespace(coordinator=coordinator), json.loads(serialize(media))
        )
    # Failure released the new store's lock.
    coordinator.capabilities.clear()
    binding = runner._bind_child_media(
        SimpleNamespace(coordinator=coordinator), json.loads(serialize(media))
    )
    binding[0].close()


class Provider:
    default_model = "fixture-model"

    def __init__(self):
        self.counted = None
        self.sent = None

    def get_media_support(self, model):
        # Even a provider advertising vision must remain text-only here.
        return {"v": 1, "positions": ["user", "tool"]}

    async def request_budget(self, request, **kwargs):
        self.counted = request
        return {"fits": True}

    async def complete(self, request, **kwargs):
        assert request is self.counted
        self.sent = copy.deepcopy(request)
        return "fixture-result"


@pytest.mark.asyncio
async def test_child_binds_before_init_wraps_before_count_and_closes(
    media, tmp_path, monkeypatch
):
    from amplifier_core.message_models import (
        ChatRequest,
        ImageBlock,
        Message,
        TextBlock,
    )

    provider = Provider()
    coordinator = Coordinator("child-1", provider)
    observed = {}

    async def initialize():
        cap = coordinator.get_capability("experimental.media.v1")
        assert cap is not None and cap.enabled is False
        observed["capability"] = cap
        assert coordinator.providers["selected"] is provider

    request = ChatRequest(messages=[
        Message(role="tool", tool_call_id="call-1", content=[
            TextBlock(text="before"),
            ImageBlock(source={"type": "base64", "media_type": "image/png",
                               "data": "private-pixel-canary"}),
            TextBlock(text="after"),
        ], metadata={"opaque-owner": {"signature": "keep"}})
    ])
    original = request.model_dump()

    async def execute(prompt):
        assert prompt == "text only"
        wrapper = coordinator.providers["selected"]
        observed["wrapper"] = wrapper
        assert wrapper is not provider
        await wrapper.request_budget(request)
        return await wrapper.complete(request)

    session = SimpleNamespace(
        coordinator=coordinator, initialize=initialize, execute=execute,
        cleanup=AsyncMock(),
    )
    monkeypatch.setattr(runner, "AmplifierSession", lambda **kwargs: session)
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.json"
    config_file.write_text(serialize(media, project_path=str(tmp_path)))
    assert await runner._run_child_session(str(config_file)) == "fixture-result"
    session.cleanup.assert_awaited_once()
    assert request.model_dump() == original
    sent = provider.sent.messages[0]
    assert sent.role == "tool" and sent.tool_call_id == "call-1"
    assert sent.metadata == {"opaque-owner": {"signature": "keep"}}
    assert [block.type for block in sent.content] == ["text", "text", "text"]
    assert sent.content[0].text == "before" and sent.content[2].text == "after"
    assert "Image unavailable" in sent.content[1].text
    assert "no pixels supplied" in sent.content[1].text
    assert "private-pixel-canary" not in provider.sent.model_dump_json()
    assert observed["wrapper"]._closed
    assert observed["capability"]._store._closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["initialize", "execute"])
async def test_failure_cleans_session_and_store(media, tmp_path, monkeypatch, failure):
    coordinator = Coordinator("child-1", Provider())
    session = SimpleNamespace(
        coordinator=coordinator, initialize=AsyncMock(), execute=AsyncMock(),
        cleanup=AsyncMock(),
    )
    getattr(session, failure).side_effect = RuntimeError("fixture failure")
    monkeypatch.setattr(runner, "AmplifierSession", lambda **kwargs: session)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.json"
    path.write_text(serialize(media, project_path=str(tmp_path)))
    with pytest.raises(RuntimeError, match="fixture failure"):
        await runner._run_child_session(str(path))
    session.cleanup.assert_awaited_once()
    assert coordinator.capabilities["experimental.media.v1"]._store._closed
    if failure == "initialize":
        session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unowned_ref_refused_before_provider_count(media):
    from amplifier_core.message_models import ChatRequest, Message

    from amplifier_foundation.session.media import MediaRefusal

    provider = Provider()
    coordinator = Coordinator("child-1", provider)
    session = SimpleNamespace(coordinator=coordinator)
    binding = runner._bind_child_media(session, json.loads(serialize(media)))
    wrappers = []
    try:
        await runner._mount_child_media_providers(session, binding, wrappers)
        ref = {
            "v": 1, "session_id": "child-1", "sha256": "a" * 64,
            "mime": "image/png", "bytes": 16, "width": 1, "height": 1,
            "provenance": {
                "kind": "tool", "session_id": "child-1", "id": "not-an-owned-call",
                "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None,
            },
        }
        request = ChatRequest(messages=[Message(
            role="tool", tool_call_id="not-an-owned-call", content="original text",
            metadata={"experimental.media.v1": {"refs": [ref]}},
        )])
        with pytest.raises(MediaRefusal):
            await coordinator.providers["selected"].request_budget(request)
        assert provider.counted is None and provider.sent is None
    finally:
        for wrapper in wrappers:
            await wrapper.aclose()
        binding[0].close()


@pytest.mark.asyncio
async def test_legacy_child_does_not_bind_or_wrap(tmp_path, monkeypatch):
    provider = object()
    coordinator = Coordinator("child-1", provider)
    session = SimpleNamespace(
        coordinator=coordinator, initialize=AsyncMock(),
        execute=AsyncMock(return_value="legacy"), cleanup=AsyncMock(),
    )
    monkeypatch.setattr(runner, "AmplifierSession", lambda **kwargs: session)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.json"
    path.write_text(serialize(project_path=str(tmp_path)))
    assert await runner._run_child_session(str(path)) == "legacy"
    assert "experimental.media.v1" not in coordinator.capabilities
    assert coordinator.providers["selected"] is provider
    assert not (tmp_path / "child-1").exists()


@pytest.mark.asyncio
async def test_public_launcher_carries_only_explicit_media(media, tmp_path, monkeypatch):
    """The ordinary public spawn path forwards the opt-in without a new API."""
    captured = {}

    async def spawn(*command, **kwargs):
        captured["payload"] = json.loads(Path(command[-1]).read_text())
        return SimpleNamespace(
            communicate=AsyncMock(return_value=(
                f"{runner.RESULT_START_MARKER}ok{runner.RESULT_END_MARKER}".encode(),
                b"",
            )),
            returncode=0,
        )

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", spawn)
    result = await runner.run_session_in_subprocess(
        config={}, prompt="text", parent_id="parent-1",
        project_path=str(tmp_path), session_id="child-1", media=media,
    )
    assert result == "ok"
    assert captured["payload"]["media"] == media
    assert "checkpoint_verifier" not in json.dumps(captured["payload"])


def test_real_process_reconstructs_fresh_disabled_binding(media, tmp_path):
    """Actual JSON/file/process boundary, with a synthetic no-network session."""
    config_file = tmp_path / "config.json"
    config_file.write_text(serialize(media, project_path=str(tmp_path)))
    script = """
import asyncio
import sys
from amplifier_foundation import subprocess_runner as runner

class Coordinator:
    session_id = "child-1"
    def __init__(self):
        self.capabilities = {}
    def register_capability(self, name, value):
        self.capabilities[name] = value
    def get_capability(self, name):
        return self.capabilities.get(name)
    def get(self, name):
        return {} if name == "providers" else None

class Session:
    def __init__(self, **kwargs):
        self.coordinator = Coordinator()
    async def initialize(self):
        cap = self.coordinator.get_capability("experimental.media.v1")
        assert cap.version == 1 and cap.enabled is False
        assert cap._store.checkpoint_verifier is None
    async def execute(self, prompt):
        return "fresh-child-text-only"
    async def cleanup(self):
        pass

runner.AmplifierSession = Session
print(asyncio.run(runner._run_child_session(sys.argv[1])))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(config_file)],
        cwd=Path(runner.__file__).parents[1], capture_output=True, text=True,
        timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fresh-child-text-only"
    # The child exited and released the store lock; no image was persisted.
    from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore

    store = SessionMediaStore(
        Path(media["session_directory"]) / "media", "child-1",
        policy=MediaPolicy(**media["policy"]),
    )
    store.close()
    assert not list((Path(media["session_directory"]) / "media" / "assets").iterdir())