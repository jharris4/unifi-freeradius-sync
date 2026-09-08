"""`UnifiVLANNoteConfig.loadFromFile` -- validation and defaults."""

import json
from pathlib import Path

import pytest

from unifi_vlan_note import UnifiVLANNoteConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
MINIMAL = {"host": "unifi.example.test", "username": "user", "password": "password"}


def write_config(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_host_username_password_are_the_only_required_keys(tmp_path):
    config = UnifiVLANNoteConfig.loadFromFile(write_config(tmp_path, MINIMAL))
    assert (config.host, config.username, config.password) == (
        "unifi.example.test",
        "user",
        "password",
    )


@pytest.mark.parametrize("missing", sorted(MINIMAL))
def test_a_missing_required_key_returns_none(tmp_path, caplog, missing):
    data = {key: value for key, value in MINIMAL.items() if key != missing}
    assert UnifiVLANNoteConfig.loadFromFile(write_config(tmp_path, data)) is None
    assert missing in caplog.text


def test_unknown_keys_are_ignored_but_reported(tmp_path, caplog):
    # keys the tool no longer reads must not break an existing deployment's
    # config file -- `client_secret` was required until it was dropped. They
    # are still logged, because every optional key has a working default and a
    # renamed one would otherwise be ignored without a word.
    data = MINIMAL | {"client_secret": "left over", "something_else": 1}
    config = UnifiVLANNoteConfig.loadFromFile(write_config(tmp_path, data))
    assert config is not None
    assert not hasattr(config, "client_secret")
    assert "client_secret" in caplog.text
    assert "something_else" in caplog.text


def test_the_old_vlan_2_key_names_are_reported(tmp_path, caplog):
    # the rename to alt_ssid / alt_vlan_regex is exactly the silent-failure
    # case the warning exists for: the old key is optional, so without it the
    # SSID entry would just stop being emitted with no explanation
    data = MINIMAL | {"vlan_2_ssid": "IoT", "vlan_2_regex": "(vlan2)([=:])([0-9]+)"}
    config = UnifiVLANNoteConfig.loadFromFile(write_config(tmp_path, data))
    assert config.alt_ssid == ""
    assert "vlan_2_ssid" in caplog.text
    assert "vlan_2_regex" in caplog.text


def test_recognised_keys_are_not_reported(tmp_path, caplog):
    data = MINIMAL | {"port": 443, "alt_ssid": "IoT", "default_vlan": 9}
    assert UnifiVLANNoteConfig.loadFromFile(write_config(tmp_path, data)) is not None
    assert "does not use" not in caplog.text


def test_defaults_apply_when_optional_keys_are_absent(tmp_path):
    config = UnifiVLANNoteConfig.loadFromFile(write_config(tmp_path, MINIMAL))
    assert (config.port, config.site, config.default_vlan, config.alt_ssid) == (
        8443,
        "default",
        1,
        "",
    )


def test_optional_keys_override_the_defaults(tmp_path):
    data = MINIMAL | {
        "port": 443,
        "site": "other",
        "default_vlan": 42,
        "alt_ssid": "GuestNet",
        "vlan_regex": "(v)([=:])([0-9]+)",
        "vlan_regex_match_index": 3,
        "alt_vlan_regex": "(v2)([=:])([0-9]+)",
        "alt_vlan_regex_match_index": 3,
    }
    config = UnifiVLANNoteConfig.loadFromFile(write_config(tmp_path, data))
    assert (config.port, config.site, config.default_vlan, config.alt_ssid) == (
        443,
        "other",
        42,
        "GuestNet",
    )
    assert (config.vlan_regex, config.vlan_regex_match_index) == ("(v)([=:])([0-9]+)", 3)
    assert (config.alt_vlan_regex, config.alt_vlan_regex_match_index) == ("(v2)([=:])([0-9]+)", 3)


def test_the_shipped_example_config_validates():
    config = UnifiVLANNoteConfig.loadFromFile(str(REPO_ROOT / "config.example.json"))
    assert config is not None
