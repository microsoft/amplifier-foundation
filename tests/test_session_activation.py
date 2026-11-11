"""Authority survives suspension without transferring to stale work."""

import asyncio

import pytest

from amplifier_foundation.session.activation import Activation, ActivationGate


def test_released_activation_cannot_write_or_release_its_successor():
    gate = ActivationGate()
    with pytest.raises(RuntimeError, match="released or superseded"):
        gate.check_current()

    first = gate.activate()
    gate.check_current()
    gate.release(first)
    assert gate.current() is first
    assert not gate.current_valid
    with pytest.raises(RuntimeError):
        gate.check_current()

    second = gate.activate()
    with pytest.raises(RuntimeError):
        gate.bind(first)
    with pytest.raises(RuntimeError):
        gate.release(first)
    assert gate.current() is second
    gate.check_current()


def test_equal_numbers_do_not_grant_authority_across_gates_or_reconstructed_tokens():
    gate = ActivationGate()
    active = gate.activate()
    foreign = ActivationGate().activate()
    assert active.number == foreign.number
    for invalid in (None, foreign, Activation(active.number)):
        with pytest.raises(RuntimeError):
            gate.check(invalid)
    gate.check(active)


@pytest.mark.asyncio
async def test_suspended_task_keeps_old_context_after_owner_reactivation():
    gate = ActivationGate()
    first = gate.activate()
    resume = asyncio.Event()

    async def old_work():
        await resume.wait()
        assert gate.current() is first
        assert not gate.current_valid
        with pytest.raises(RuntimeError):
            gate.check_current()
        with pytest.raises(RuntimeError):
            gate.bind(first)

    task = asyncio.create_task(old_work())
    gate.release(first)
    second = gate.activate()
    resume.set()
    await task
    assert gate.current() is second
    gate.check_current()


@pytest.mark.asyncio
async def test_cancelled_binding_restores_task_context_without_releasing_owner():
    gate = ActivationGate()
    first = gate.activate()
    resume = asyncio.Event()
    bound = asyncio.Event()
    second = None

    async def task_with_old_context():
        await resume.wait()
        assert second is not None
        token = gate.bind(second)
        try:
            gate.check_current()
            bound.set()
            await asyncio.Event().wait()
        finally:
            gate.reset(token)
            assert gate.current() is first
            assert not gate.current_valid

    task = asyncio.create_task(task_with_old_context())
    gate.release(first)
    second = gate.activate()
    resume.set()
    await asyncio.wait_for(bound.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.current() is second
    gate.check_current()


def test_nested_bind_reset_restores_previous_context_without_reviving_authority():
    gate = ActivationGate()
    first = gate.activate()
    outer = gate.bind(first)
    second = gate.activate()
    inner = gate.bind(second)
    gate.reset(inner)
    gate.check_current()
    gate.reset(outer)
    assert gate.current() is first
    assert not gate.current_valid
    gate.check(second)
