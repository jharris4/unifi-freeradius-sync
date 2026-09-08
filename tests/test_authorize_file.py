"""Byte-for-byte characterization of the generated `authorize` file.

Every expectation here is the output of the version currently deployed. A
failure means the generated FreeRADIUS config changed -- confirm that was the
intent before touching the expected text.
"""

import itertools

import pytest
from conftest import client, header, make_controller, vlan_network


async def test_file_lands_at_the_path_freeradius_expects(tmp_path):
    await make_controller().generateUsersConfig(str(tmp_path))
    assert (tmp_path / "mods-config" / "files" / "authorize").is_file()


async def test_parent_directories_are_created(tmp_path):
    nested = tmp_path / "does" / "not" / "exist"
    await make_controller().generateUsersConfig(str(nested))
    assert (nested / "mods-config" / "files" / "authorize").is_file()


async def test_no_clients_writes_the_header_alone(generate):
    assert await generate() == header()


async def test_default_vlan_is_interpolated_into_the_header(generate):
    assert await generate(default_vlan=42) == header(default_vlan=42)


async def test_unlisted_macs_are_accepted_onto_the_default_vlan(generate):
    # This is fail-open by design for the original deployment: anything not
    # named below still authenticates. Pinned so that flipping it to a
    # reject-by-default has to be a deliberate, visible change.
    assert "DEFAULT Auth-Type := Accept" in await generate()
    assert "Fall-Through = Yes" in await generate()


async def test_client_on_a_defined_vlan(generate):
    output = await generate(
        clients=[client("aa:bb:cc:dd:ee:01", note="vlan=20", name="Laptop", last_ip="10.0.20.5")],
        networks=[vlan_network(20)],
    )
    assert output == header() + (
        "# Laptop\n"
        "# 10.0.20.5\n"
        "AABBCCDDEE01\n"
        '   Tunnel-Private-Group-ID := "20"\n'
        "\n"
    )


@pytest.mark.parametrize("note", ["vlan=20", "vlan: 20", "vlan:20", "vlan = 20", "  vlan=20  "])
async def test_note_syntax_does_not_change_the_output(generate, note):
    output = await generate(
        clients=[client("aa:bb:cc:dd:ee:01", note=note, name="Laptop", last_ip="10.0.20.5")],
        networks=[vlan_network(20)],
    )
    assert output == header() + (
        "# Laptop\n"
        "# 10.0.20.5\n"
        "AABBCCDDEE01\n"
        '   Tunnel-Private-Group-ID := "20"\n'
        "\n"
    )


async def test_wired_clients_are_marked_in_the_comment(generate):
    output = await generate(
        clients=[
            client(
                "aa:bb:cc:dd:ee:01",
                note="vlan=20",
                name="NAS",
                last_ip="10.0.20.5",
                is_wired=True,
            )
        ],
        networks=[vlan_network(20)],
    )
    assert output.startswith(header() + "# NAS [WIRED]\n")


async def test_vlan_not_defined_on_the_controller_is_commented_out(generate):
    # the blocked-client path: the entry is written for visibility but every
    # line is a comment, so the MAC falls through to the default VLAN rather
    # than being handed a VLAN the controller does not have
    output = await generate(
        clients=[client("aa:bb:cc:dd:ee:02", note="vlan=99", name="Rogue", last_ip="10.0.0.9")],
        networks=[vlan_network(20)],
    )
    assert output == header() + (
        "# Rogue\n"
        "# 10.0.0.9\n"
        "# AABBCCDDEE02\n"
        '#   Tunnel-Private-Group-ID := "99"\n'
        "\n"
    )
    assert "\nAABBCCDDEE02" not in output


async def test_client_with_an_alias_but_no_vlan_is_commented_out(generate):
    output = await generate(
        clients=[client("aa:bb:cc:dd:ee:04", name="Printer", last_ip="10.0.1.4")],
    )
    assert output == header() + (
        "# Printer\n"
        "# 10.0.1.4\n"
        "# AABBCCDDEE04\n"
        '#   Tunnel-Private-Group-ID := "0"\n'
        "\n"
    )


async def test_alt_vlan_with_an_ssid_emits_the_ssid_entry_first(generate):
    # FreeRADIUS `files` takes the first match, so the SSID-qualified entry has
    # to precede the unqualified one or it would never be reached
    output = await generate(
        clients=[
            client(
                "aa:bb:cc:dd:ee:03",
                note="vlan=20 alt_vlan=30",
                name="Phone",
                last_ip="10.0.20.7",
            )
        ],
        networks=[vlan_network(20), vlan_network(30)],
        alt_ssid="GuestNet",
    )
    assert output == header() + (
        "# Phone\n"
        "# 10.0.20.7\n"
        "# SSID: GuestNet\n"
        "AABBCCDDEE03 Called-Station-Id =~ '.*:GuestNet'\n"
        '   Tunnel-Private-Group-ID := "30"\n'
        "\n"
        "# Phone\n"
        "# 10.0.20.7\n"
        "AABBCCDDEE03\n"
        '   Tunnel-Private-Group-ID := "20"\n'
        "\n"
    )


async def test_alt_vlan_without_an_ssid_emits_only_the_plain_entry(generate):
    output = await generate(
        clients=[
            client(
                "aa:bb:cc:dd:ee:03",
                note="vlan=20 alt_vlan=30",
                name="Phone",
                last_ip="10.0.20.7",
            )
        ],
        networks=[vlan_network(20), vlan_network(30)],
    )
    assert output == header() + (
        "# Phone\n"
        "# 10.0.20.7\n"
        "AABBCCDDEE03\n"
        '   Tunnel-Private-Group-ID := "20"\n'
        "\n"
    )
    assert "Called-Station-Id" not in output


async def test_alt_vlan_is_ignored_when_the_ssid_is_unset_even_for_blocked_clients(generate):
    # an undefined alt_vlan still blocks the client, ssid or not
    output = await generate(
        clients=[client("aa:bb:cc:dd:ee:03", note="vlan=20 alt_vlan=99", name="Phone")],
        networks=[vlan_network(20)],
        alt_ssid="GuestNet",
    )
    assert output == header() + (
        "# Phone\n"
        "# \n"
        "# AABBCCDDEE03\n"
        '#   Tunnel-Private-Group-ID := "20"\n'
        "\n"
    )


async def test_null_fields_survive_generation(generate):
    raw = {
        "mac": "aa:bb:cc:dd:ee:05",
        "note": None,
        "name": "Cleared Out",
        "last_ip": None,
        "fixed_ip": None,
        "is_wired": None,
    }
    output = await generate(clients=[raw])
    assert output == header() + (
        "# Cleared Out\n"
        "# \n"
        "# AABBCCDDEE05\n"
        '#   Tunnel-Private-Group-ID := "0"\n'
        "\n"
    )


async def test_clients_with_every_field_null_are_skipped_entirely(generate):
    raw = {"mac": None, "note": None, "name": None, "last_ip": None, "fixed_ip": None}
    assert await generate(clients=[raw]) == header()


async def test_missing_keys_are_treated_like_nulls(generate):
    # older controller versions omit keys outright rather than sending null
    assert await generate(clients=[{"mac": "aa:bb:cc:dd:ee:06"}]) == header()


SHUFFLE_SAMPLE = [
    client("aa:bb:cc:dd:ee:03", note="vlan=20", name="cherry", last_ip="10.0.20.3"),
    client("aa:bb:cc:dd:ee:01", note="vlan=30", name="Banana", last_ip="10.0.30.1"),
    client("aa:bb:cc:dd:ee:04", note="vlan=99", name="apple", last_ip="10.0.0.4"),
    client("aa:bb:cc:dd:ee:02", name="apple", last_ip="10.0.0.2"),
]
SHUFFLE_NETWORKS = [vlan_network(20), vlan_network(30)]


async def test_shuffled_input_produces_identical_bytes(generate):
    expected = await generate(clients=SHUFFLE_SAMPLE, networks=SHUFFLE_NETWORKS)
    for permutation in itertools.permutations(SHUFFLE_SAMPLE):
        assert await generate(clients=list(permutation), networks=SHUFFLE_NETWORKS) == expected


async def test_full_file_with_a_mixed_client_set(generate):
    output = await generate(
        clients=SHUFFLE_SAMPLE, networks=SHUFFLE_NETWORKS, alt_ssid="GuestNet"
    )
    assert output == header() + (
        # apple, sorted ahead of "Banana" case-insensitively, MAC breaking the
        # tie between the two apples
        "# apple\n"
        "# 10.0.0.2\n"
        "# AABBCCDDEE02\n"
        '#   Tunnel-Private-Group-ID := "0"\n'
        "\n"
        "# apple\n"
        "# 10.0.0.4\n"
        "# AABBCCDDEE04\n"
        '#   Tunnel-Private-Group-ID := "99"\n'
        "\n"
        "# Banana\n"
        "# 10.0.30.1\n"
        "AABBCCDDEE01\n"
        '   Tunnel-Private-Group-ID := "30"\n'
        "\n"
        "# cherry\n"
        "# 10.0.20.3\n"
        "AABBCCDDEE03\n"
        '   Tunnel-Private-Group-ID := "20"\n'
        "\n"
    )


async def test_regeneration_overwrites_rather_than_appends(tmp_path):
    controller = make_controller(
        [client("aa:bb:cc:dd:ee:01", note="vlan=20", name="Laptop")], [vlan_network(20)]
    )
    await controller.generateUsersConfig(str(tmp_path))
    first = (tmp_path / "mods-config" / "files" / "authorize").read_text()
    await controller.generateUsersConfig(str(tmp_path))
    assert (tmp_path / "mods-config" / "files" / "authorize").read_text() == first
