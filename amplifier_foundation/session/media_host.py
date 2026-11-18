"""Cooperative host binding for the existing same-session media components.

No source inventory, model allowlist, file-access policy, transcript writer or
new capability registry lives here. Hosts select actual participants and supply
their existing policy/checkpoint verifiers on the store. Install before module
initialization, register late hooks through ``registration``, then ``seal``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from .media import MEDIA_NAMESPACE, MediaRefusal, SessionMediaStore, bind_session_media
from .media_hook_bridge import install_media_hook_bridge
from .media_provider import MediaPreparedProvider


class SessionMediaBinding:
    """Join store, participating construction and private provider lifetimes.

    ``participants`` explicitly includes ``owner`` and all selected modules/late
    owners; names are not source approval. The bridge retains its exact Core ABI
    and actual-construction checks. This is a cooperative Python-loader contract,
    not interception or isolation of arbitrary native/Python hooks.

    A successful constructor transfers store cleanup to this session. Failure
    can leave partial construction: discard the fresh session and close the
    caller-owned store, never retry installation on that coordinator.
    """

    def __init__(
        self, session: Any, *, store: SessionMediaStore, owner: str,
        participants: tuple[str, ...],
    ) -> None:
        coordinator = session.coordinator
        if store.session_id != session.session_id:
            raise MediaRefusal("host media store belongs to another session")
        if coordinator.get_capability(MEDIA_NAMESPACE) is not None:
            raise MediaRefusal("coordinator already has media; use a fresh session")
        if owner not in participants:
            raise ValueError("host owner must be an explicit participant")
        if not callable(store.checkpoint_verifier) or not callable(store.policy_verifier):
            raise ValueError("host media requires checkpoint and current source-policy verifiers")
        self._coordinator = coordinator
        self.store = store
        self._closed = False
        self._providers: list[MediaPreparedProvider] = []
        self.bridge = install_media_hook_bridge(coordinator)
        self.graph = self.bridge.begin_participating_graph(
            session, owner=owner, participants=participants,
        )
        self.registration = self.graph.participant(owner)
        self.capability = bind_session_media(coordinator, store=store, enabled=False)
        coordinator.register_cleanup(self.aclose)

    def _owned(self) -> bool:
        return (
            not self._closed
            and self._coordinator.get_capability(MEDIA_NAMESPACE) is self.capability
            and self._coordinator.get_capability("session.media.tool_post") is self.bridge
        )

    def seal(self) -> bool:
        """Enable producers only after actual construction and loop/writer setup.

        Loop readiness is a cooperative report, not construction/source proof.
        The host still owns the durable writer callback and must acknowledge
        its *saved primary* checkpoint using the store's existing verifier.
        Missing readiness leaves producers disabled; inconsistent construction
        raises and cannot be repaired by a Boolean or a copied report.
        """
        self.capability.enabled = False
        if not self._owned():
            raise ValueError("host media binding is closed or replaced")
        receipt = self.bridge.graph_receipt or self.graph.seal()
        report = self.capability.report(self.graph.owner, event="readiness", outcome="inspect")
        loop_ready = any(
            row.get("role") == "loop" and type(row.get("contract")) is int
            and row["contract"] == 1 and row.get("ready") is True
            for row in report.get("consumers", {}).values()
        )
        self.capability.enabled = bool(
            self._owned() and self.bridge.validate_receipt(receipt) and loop_ready
            and callable(self._coordinator.get_capability("session.durable_checkpoint"))
        )
        return self.capability.enabled

    @contextmanager
    def configure(self):
        """Observe real idle settings/mode changes; never replay registrations."""
        if not self._owned():
            raise ValueError("host media binding is closed or replaced")
        if self.bridge.graph_receipt is None:
            with self.graph.configure():
                self.capability.enabled = False
                yield self.graph
        else:
            # A refused transition must not revoke an unchanged counted request.
            with self.graph.transition() as successor:
                self.graph = successor
                self.capability.enabled = False
                yield successor
            self.seal()

    @contextmanager
    def _request_scope(self):
        if not self._owned() or self.capability.enabled is not True:
            raise ValueError("host media binding is not enabled")
        writer = self._coordinator.get_capability("session.durable_checkpoint")
        with self.bridge.request_scope() as validate_graph:
            yield lambda: (
                self._owned() and self.capability.enabled is True
                and callable(writer)
                and self._coordinator.get_capability("session.durable_checkpoint") is writer
                and validate_graph()
            )

    def wrap_provider(
        self, provider: Any, *, provider_identity: Any, model: Any = None,
        execution_scope: Any = None, prepare_overlay: Any = None,
    ) -> MediaPreparedProvider:
        """Create a tracked wrapper; mount via the participating adapter API.

        Provider/account/generation identity (optionally a live getter), overlays
        and wire support remain their actual owners' responsibilities. This
        never selects a vendor/model or remounts a provider behind the graph.
        """
        if not self._owned():
            raise ValueError("host media binding is closed or replaced")
        wrapped = MediaPreparedProvider(
            provider, self.capability, provider_identity=provider_identity, model=model,
            execution_scope=execution_scope, prepare_overlay=prepare_overlay,
            request_scope=self._request_scope,
        )
        self._providers.append(wrapped)
        return wrapped

    async def aclose(self) -> None:
        """Revoke private requests/streams and close the owned store, idempotently."""
        self._closed = True
        self.capability.enabled = False
        error = None
        for provider in tuple(self._providers):
            try:
                await provider.aclose()
            except BaseException as exc:
                error = error or exc
        try:
            self.store.close()
        finally:
            if error is not None:
                raise error