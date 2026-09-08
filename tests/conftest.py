"""Shared helpers for the characterization tests.

These tests pin the *current* generated output byte for byte. The tool is
already deployed and its output feeds the FreeRADIUS `files` module in an
auth path, so a diff here means a behaviour change -- update an expectation
only when the change is the point.

`UnifiVLANNoteController.__init__` opens an aiohttp session and builds an
aiounifi controller, so these helpers bypass it entirely and stub the two
methods that reach the network. Everything downstream of `getNetworks` and
`getClients` -- including `getVLANs` -- runs for real.
"""

import pytest

from unifi_vlan_note import UnifiVLANNoteConfig, UnifiVLANNoteController


def make_config(**overrides):
    """A config with the documented defaults, minus anything overridden."""
    config = UnifiVLANNoteConfig(
        host="unifi.example.test",
        username="user",
        password="password",
    )
    for key, value in overrides.items():
        assert hasattr(config, key), f"unknown config key {key!r}"
        setattr(config, key, value)
    return config


def make_controller(clients=(), networks=(), **config_overrides):
    controller = object.__new__(UnifiVLANNoteController)
    controller.config = make_config(**config_overrides)

    async def get_networks():
        return list(networks)

    async def get_clients():
        return list(clients)

    controller.getNetworks = get_networks
    controller.getClients = get_clients
    return controller


def vlan_network(vlan, name=None):
    """Shaped like one entry of a `/rest/networkconf` response."""
    return {"_id": f"net{vlan}", "name": name or f"VLAN {vlan}", "vlan": vlan}


def client(mac, note=None, name=None, last_ip=None, fixed_ip=None, is_wired=False):
    """Shaped like one entry of a `/stat/alluser` response.

    Cleared fields really do come back as JSON null rather than absent, which
    is why the defaults here are None instead of "".
    """
    return {
        "mac": mac,
        "note": note,
        "name": name,
        "last_ip": last_ip,
        "fixed_ip": fixed_ip,
        "is_wired": is_wired,
    }


@pytest.fixture
def generate(tmp_path):
    """Run a full generation and return the authorize file as text."""

    async def _generate(clients=(), networks=(), **config_overrides):
        controller = make_controller(clients, networks, **config_overrides)
        await controller.generateUsersConfig(str(tmp_path))
        return (tmp_path / "mods-config" / "files" / "authorize").read_text()

    return _generate


def header(default_vlan=1):
    """The fixed preamble of every generated file.

    Note the three-space indent: it is not a style choice, it falls out of the
    `''' \\` line continuation in `generateUsersConfig`, which leaves the first
    line one column short of the rest and so shifts what `textwrap.dedent`
    considers common. Pinned here because the file is what FreeRADIUS parses.
    """
    return (
        "DEFAULT Auth-Type := Accept\n"
        "   Tunnel-Type = VLAN,\n"
        "   Tunnel-Medium-Type = IEEE-802,\n"
        f'   Tunnel-Private-Group-Id = "{default_vlan}",\n'
        "   Fall-Through = Yes\n"
        "\n"
    )
