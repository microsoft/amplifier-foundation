"""Tests for KernelClient in amplifier_foundation.grpc_adapter.__main__.

Covers:
- Constructor stores all attributes
- parent_id defaults to None
- emit_hook calls stub.EmitHook once
- get_capability returns parsed JSON when found
- get_capability returns None when not found
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


class TestKernelClient:
    """Tests for the KernelClient class."""

    # ------------------------------------------------------------------
    # Attribute access
    # ------------------------------------------------------------------

    def test_session_id_accessible(self) -> None:
        """KernelClient stores session_id as an accessible attribute."""
        from amplifier_foundation.grpc_adapter.__main__ import KernelClient  # type: ignore[import-not-found]

        stub = MagicMock()
        metadata: list[tuple[str, str]] = [("authorization", "Bearer token")]
        client = KernelClient(
            stub=stub,
            metadata=metadata,
            session_id="test-session-123",
        )
        assert client.session_id == "test-session-123"

    def test_parent_id_accessible(self) -> None:
        """KernelClient stores parent_id when provided."""
        from amplifier_foundation.grpc_adapter.__main__ import KernelClient  # type: ignore[import-not-found]

        stub = MagicMock()
        metadata: list[tuple[str, str]] = []
        client = KernelClient(
            stub=stub,
            metadata=metadata,
            session_id="child-session",
            parent_id="parent-session-456",
        )
        assert client.parent_id == "parent-session-456"

    def test_parent_id_defaults_to_none(self) -> None:
        """KernelClient parent_id defaults to None when not provided."""
        from amplifier_foundation.grpc_adapter.__main__ import KernelClient  # type: ignore[import-not-found]

        stub = MagicMock()
        metadata: list[tuple[str, str]] = []
        client = KernelClient(
            stub=stub,
            metadata=metadata,
            session_id="session-no-parent",
        )
        assert client.parent_id is None

    # ------------------------------------------------------------------
    # emit_hook
    # ------------------------------------------------------------------

    def test_emit_hook_calls_stub(self) -> None:
        """emit_hook calls stub.EmitHook exactly once with correct args and metadata."""
        from amplifier_foundation.grpc_adapter.__main__ import KernelClient  # type: ignore[import-not-found]

        stub = MagicMock()
        metadata: list[tuple[str, str]] = [("authorization", "Bearer secret")]

        # Mock pb2 to avoid requiring real grpcio/protobuf in test environment
        mock_pb2 = MagicMock()
        mock_request = MagicMock()
        mock_pb2.EmitHookRequest.return_value = mock_request

        with patch(
            "amplifier_foundation.grpc_adapter.__main__.amplifier_module_pb2",
            mock_pb2,
        ):
            client = KernelClient(
                stub=stub,
                metadata=metadata,
                session_id="session-emit",
            )
            event_data = {"key": "value"}
            client.emit_hook(event="test.event", data=event_data)

        stub.EmitHook.assert_called_once_with(mock_request, metadata=metadata)
        mock_pb2.EmitHookRequest.assert_called_once_with(
            event="test.event",
            data_json=json.dumps(event_data),
        )

    # ------------------------------------------------------------------
    # get_capability
    # ------------------------------------------------------------------

    def test_get_capability_returns_value(self) -> None:
        """get_capability returns parsed JSON value when capability is found."""
        from amplifier_foundation.grpc_adapter.__main__ import KernelClient  # type: ignore[import-not-found]

        stub = MagicMock()
        metadata: list[tuple[str, str]] = [("authorization", "Bearer token")]

        # Mock pb2
        mock_pb2 = MagicMock()
        mock_request = MagicMock()
        mock_pb2.GetCapabilityRequest.return_value = mock_request

        # Mock response: found=True, value_json='{"feature": "enabled"}'
        mock_resp = MagicMock()
        mock_resp.found = True
        mock_resp.value_json = json.dumps({"feature": "enabled"})
        stub.GetCapability.return_value = mock_resp

        with patch(
            "amplifier_foundation.grpc_adapter.__main__.amplifier_module_pb2",
            mock_pb2,
        ):
            client = KernelClient(
                stub=stub,
                metadata=metadata,
                session_id="session-get-cap",
            )
            result = client.get_capability(name="my-feature")

        assert result == {"feature": "enabled"}
        stub.GetCapability.assert_called_once_with(mock_request, metadata=metadata)
        mock_pb2.GetCapabilityRequest.assert_called_once_with(name="my-feature")

    def test_get_capability_returns_none_when_not_found(self) -> None:
        """get_capability returns None when capability is not found."""
        from amplifier_foundation.grpc_adapter.__main__ import KernelClient  # type: ignore[import-not-found]

        stub = MagicMock()
        metadata: list[tuple[str, str]] = []

        mock_pb2 = MagicMock()
        mock_request = MagicMock()
        mock_pb2.GetCapabilityRequest.return_value = mock_request

        # Mock response: found=False
        mock_resp = MagicMock()
        mock_resp.found = False
        mock_resp.value_json = ""
        stub.GetCapability.return_value = mock_resp

        with patch(
            "amplifier_foundation.grpc_adapter.__main__.amplifier_module_pb2",
            mock_pb2,
        ):
            client = KernelClient(
                stub=stub,
                metadata=metadata,
                session_id="session-not-found",
            )
            result = client.get_capability(name="missing-cap")

        assert result is None
