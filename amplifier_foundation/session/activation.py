"""Task-local authority for one activation of a native session.

This fence complements the session's exclusive writer lease. Applications still
own lease acquisition, cancellation, persistence and release; this module only
prevents a task from borrowing a newer activation's authority.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class Activation:
    """An opaque, per-acquisition authority token; its number is diagnostic."""

    number: int


class ActivationGate:
    """Reject writes from a released or superseded worker activation.

    A gate belongs to one session's event-loop owner. Capture ``current()`` when
    admitting work and bind that exact token in the task performing the work.
    Token identity, not its number, determines authority.
    """

    def __init__(self) -> None:
        self._number = 0
        self._active: Activation | None = None
        self._current: ContextVar[Activation | None] = ContextVar(
            "amplifier_session_activation", default=None
        )

    def activate(self) -> Activation:
        self._number += 1
        self._active = Activation(self._number)
        # Mount-time checkpoints run before the first input exists. A spawned
        # turn still binds its own captured token explicitly.
        self._current.set(self._active)
        return self._active

    def release(self, activation: Activation) -> None:
        self.check(activation)
        self._active = None

    def check(self, activation: Activation | None) -> None:
        if activation is None or activation is not self._active:
            raise RuntimeError("This session activation has been released or superseded.")

    def bind(self, activation: Activation) -> Token[Activation | None]:
        self.check(activation)
        return self._current.set(activation)

    def reset(self, token: Token[Activation | None]) -> None:
        self._current.reset(token)

    def check_current(self) -> None:
        self.check(self._current.get())

    def current(self) -> Activation | None:
        """Capture the calling task's token, never the latest owner's token."""
        return self._current.get()

    @property
    def current_valid(self) -> bool:
        return self._active is not None and self._current.get() is self._active
