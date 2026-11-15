"""Unsupported prospective platforms refuse before touching their filesystem."""
from pathlib import Path

import pytest

from amplifier_foundation.session import transcript_lifetime as lifetime


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
@pytest.mark.parametrize('method', ['create_new', 'resume'])
def test_unsupported_platform_refuses_before_filesystem(monkeypatch, tmp_path, platform, method):
    monkeypatch.setattr(lifetime.sys, 'platform', platform)
    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported prospective platform touched the filesystem.')
    monkeypatch.setattr(lifetime.os, 'open', forbidden)
    monkeypatch.setattr(lifetime.os, 'mkdir', forbidden)
    # tmp_path is already created by pytest, before the no-filesystem sentinels.
    args = {'session_id': 'prospective'}
    if method == 'create_new':
        args.update(operation_id='init', admit=lambda: None)
    else:
        args['expected'] = None
    with pytest.raises(lifetime.VersionedSessionConflict, match='Linux only'):
        getattr(lifetime.VersionedSessionBinding, method)(tmp_path / 'prospective', **args)