"""Scope merging and cooperating writers are host-independent."""

import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from amplifier_foundation.settings import read_settings, update_settings


def test_order_provider_instances_empty_files_and_unknown_keys(tmp_path):
    paths = [
        tmp_path / name
        for name in ("global.yaml", "project.yaml", "local.yaml", "session.yaml")
    ]
    paths[0].write_text(
        yaml.safe_dump(
            {
                "config": {
                    "providers": [
                        {
                            "id": "a",
                            "module": "provider-example",
                            "config": {"model": "one", "account": "a"},
                        },
                        {
                            "id": "b",
                            "module": "provider-example",
                            "config": {"model": "two", "account": "b"},
                        },
                    ]
                },
                "unknown": {"keep": True},
                "ordinary": [1, 2],
            }
        )
    )
    paths[1].write_text("ordinary: [3]\n")
    paths[2].write_text("")
    paths[3].write_text(
        yaml.safe_dump(
            {
                "config": {
                    "providers": [
                        {
                            "id": "b",
                            "module": "provider-example",
                            "config": {"model": "session model"},
                        },
                    ]
                }
            }
        )
    )
    before = [p.read_bytes() for p in paths]
    actual = read_settings(paths)
    assert actual["config"]["providers"][0]["config"]["model"] == "one"
    assert actual["config"]["providers"][1]["config"] == {
        "model": "session model",
        "account": "b",
    }
    assert actual["ordinary"] == [3] and actual["unknown"] == {"keep": True}
    assert [p.read_bytes() for p in paths] == before


def test_empty_provider_list_keeps_inherited_instances_and_bad_yaml_fails(tmp_path):
    global_path, session_path = tmp_path / "global", tmp_path / "session"
    global_path.write_text("config:\n  providers:\n    - module: provider-one\n")
    session_path.write_text("config:\n  providers: []\n")
    assert read_settings([global_path, session_path])["config"]["providers"] == [
        {"module": "provider-one"}
    ]
    session_path.write_text("[this, is, not, settings]")
    with pytest.raises(ValueError, match="mapping"):
        read_settings([global_path, session_path])
    assert session_path.read_text() == "[this, is, not, settings]"


def test_simultaneous_updates_preserve_other_fields_and_permissions(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("unknown: retained\n")
    path.chmod(0o640)

    def update(index):
        update_settings(path, lambda value: {**value, f"field_{index}": index})

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(update, range(15)))
    value = read_settings([path])
    assert value == {"unknown": "retained", **{f"field_{i}": i for i in range(15)}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
