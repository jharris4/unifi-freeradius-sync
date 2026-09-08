"""Record-level behaviour of `getMACVLANs`: note parsing, inclusion, ordering."""

import itertools

import pytest
from conftest import client, make_controller, vlan_network


async def records(clients=(), networks=(), **config_overrides):
    return await make_controller(clients, networks, **config_overrides).getMACVLANs()


# --- note parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "note",
    [
        "vlan=20",
        "vlan:20",
        "vlan = 20",
        "vlan : 20",
        "vlan =20",
        "vlan= 20",
        "vlan\t=\t20",
        "office printer, vlan=20, do not move",
        "vlan=20\nsecond line",
    ],
)
async def test_both_note_syntaxes_parse(note):
    [record] = await records([client("aa:bb:cc:dd:ee:01", note=note)], [vlan_network(20)])
    assert record.vlan == 20
    assert record.blocked is False


@pytest.mark.parametrize(
    "note",
    [
        "vlan 20",
        "vlan20",
        "vlan-20",
        "vlan",
        "vlan=",
        "VLAN=20",
        # notes are free text, so "vlan" turning up as the tail of another word
        # must not count -- the word boundary is what stops these
        "myvlan=99",
        "alt_vlan=30",
        "old_vlan: 5",
    ],
)
async def test_notes_that_do_not_carry_a_vlan_directive(note):
    # the regex requires an explicit [=:] and a word boundary before "vlan".
    # It is also case sensitive, so "VLAN=20" is inert.
    records_out = await records(
        [client("aa:bb:cc:dd:ee:01", note=note, name="Thing")], [vlan_network(20)]
    )
    assert [r.vlan for r in records_out] == [0]


@pytest.mark.parametrize("note", ["no-vlan=7", "iot-vlan: 20", "the vlan=20"])
async def test_a_non_word_character_before_vlan_still_counts(note):
    # \b only rejects letters, digits and underscores, so a hyphen or a space
    # still reads as a directive. That keeps "iot-vlan=20" working, at the cost
    # of "no-vlan=7" also being taken literally -- worth knowing, not a bug to
    # fix by tightening further and silently ignoring notes people do write.
    [record] = await records(
        [client("aa:bb:cc:dd:ee:01", note=note)], [vlan_network(7), vlan_network(20)]
    )
    assert record.vlan in (7, 20)
    assert record.blocked is False


async def test_first_match_in_the_note_wins():
    [record] = await records(
        [client("aa:bb:cc:dd:ee:01", note="vlan=20 then vlan=30")],
        [vlan_network(20), vlan_network(30)],
    )
    assert record.vlan == 20


@pytest.mark.parametrize("note", ["vlan=41,alt_vlan=31", "vlan=41, alt_vlan=31"])
async def test_both_directives_in_one_comma_separated_note(note):
    # the shape a real note takes. The comma gives \b its boundary before
    # alt_vlan, and re.search taking the leftmost match is what stops the
    # primary pattern from picking up the "vlan=31" inside "alt_vlan=31"
    [record] = await records(
        [client("aa:bb:cc:dd:ee:01", note=note)],
        [vlan_network(41), vlan_network(31)],
    )
    assert (record.vlan, record.alt_vlan, record.blocked) == (41, 31, False)


async def test_alt_vlan_does_not_satisfy_the_primary_regex():
    # the word boundary keeps "alt_vlan=30" from matching as "vlan=30", so a
    # note carrying only an alt VLAN leaves the primary unset and gets
    # commented out. Without \b this silently assigned the alt VLAN as primary.
    [record] = await records(
        [client("aa:bb:cc:dd:ee:01", note="alt_vlan=30", name="Phone")],
        [vlan_network(30)],
    )
    assert (record.vlan, record.alt_vlan) == (0, 30)


async def test_alt_vlan_is_parsed_alongside_vlan_in_either_order():
    for note in ("vlan=20 alt_vlan=30", "alt_vlan=30 vlan=20"):
        [record] = await records(
            [client("aa:bb:cc:dd:ee:01", note=note)],
            [vlan_network(20), vlan_network(30)],
        )
        assert (record.vlan, record.alt_vlan) == (20, 30)


# --- which VLANs count as defined ----------------------------------------


async def test_vlan_missing_from_the_controller_is_blocked():
    [record] = await records([client("aa:bb:cc:dd:ee:01", note="vlan=99")], [vlan_network(20)])
    assert record.vlan == 99
    assert record.blocked is True


async def test_vlan_1_is_always_considered_defined():
    # getVLANs seeds the set with {1}; the default network carries no VLAN tag
    [record] = await records([client("aa:bb:cc:dd:ee:01", note="vlan=1")], [])
    assert record.blocked is False


async def test_networks_without_a_vlan_tag_are_ignored():
    networks = [
        {"_id": "n1", "name": "Default", "vlan": None},
        {"_id": "n2", "name": "No key"},
        {"_id": "n3", "name": "Zero", "vlan": 0},
        vlan_network(20),
    ]
    [defined] = await records([client("aa:bb:cc:dd:ee:01", note="vlan=20")], networks)
    assert defined.blocked is False

    # VLAN 0 is not carried over from the untagged network above, so a client
    # asking for it is blocked -- though `(mac and vlan) or alias` means it
    # only reaches the output at all if it also has an alias
    [undefined] = await records(
        [client("aa:bb:cc:dd:ee:02", note="vlan=0", name="Zero")], networks
    )
    assert (undefined.vlan, undefined.blocked) == (0, True)


async def test_undefined_alt_vlan_blocks_the_whole_client():
    [record] = await records(
        [client("aa:bb:cc:dd:ee:01", note="vlan=20 alt_vlan=99")], [vlan_network(20)]
    )
    assert (record.vlan, record.alt_vlan, record.blocked) == (20, 99, True)


# --- which clients get a record ------------------------------------------


async def test_client_with_neither_alias_nor_vlan_is_dropped():
    assert await records([client("aa:bb:cc:dd:ee:01")], []) == []


async def test_client_with_an_alias_but_no_vlan_is_kept():
    [record] = await records([client("aa:bb:cc:dd:ee:01", name="Printer")], [])
    assert (record.alias, record.vlan) == ("Printer", 0)


async def test_client_with_a_vlan_but_no_alias_is_kept():
    [record] = await records([client("aa:bb:cc:dd:ee:01", note="vlan=20")], [vlan_network(20)])
    assert (record.alias, record.vlan) == ("", 20)


async def test_a_vlan_alone_is_not_enough_without_a_mac():
    # `(mac and vlan) or alias` -- a null MAC with no alias drops the record
    assert await records([client(None, note="vlan=20")], [vlan_network(20)]) == []


# --- null handling --------------------------------------------------------


async def test_null_fields_are_treated_as_empty_strings():
    # the controller sends JSON null for cleared fields; a None note used to
    # crash re.search and a None name used to crash the sort
    raw = {
        "mac": None,
        "note": None,
        "name": "Cleared Out",
        "last_ip": None,
        "fixed_ip": None,
        "is_wired": None,
    }
    [record] = await records([raw], [])
    assert (record.mac, record.alias, record.ip, record.vlan) == ("", "Cleared Out", "", 0)
    assert record.wired is False


async def test_ip_prefers_last_ip_and_falls_back_to_fixed_ip():
    clients = [
        client("aa:bb:cc:dd:ee:01", name="Both", last_ip="10.0.0.1", fixed_ip="10.0.0.2"),
        client("aa:bb:cc:dd:ee:02", name="Fixed only", last_ip=None, fixed_ip="10.0.0.3"),
        client("aa:bb:cc:dd:ee:03", name="Neither"),
    ]
    assert [r.ip for r in await records(clients, [])] == ["10.0.0.1", "10.0.0.3", ""]


# --- ordering -------------------------------------------------------------


SORT_SAMPLE = [
    client("aa:bb:cc:dd:ee:03", name="cherry"),
    client("aa:bb:cc:dd:ee:01", name="Banana"),
    client("aa:bb:cc:dd:ee:04", name="apple"),
    client("aa:bb:cc:dd:ee:02", name="apple"),
]


async def test_records_sort_by_lowercased_alias_then_mac():
    out = await records(SORT_SAMPLE, [])
    assert [(r.alias, r.mac) for r in out] == [
        ("apple", "aa:bb:cc:dd:ee:02"),
        ("apple", "aa:bb:cc:dd:ee:04"),
        ("Banana", "aa:bb:cc:dd:ee:01"),
        ("cherry", "aa:bb:cc:dd:ee:03"),
    ]


async def test_input_order_does_not_affect_output_order():
    # the controller does not return clients in a stable order
    expected = [(r.alias, r.mac) for r in await records(SORT_SAMPLE, [])]
    for permutation in itertools.permutations(SORT_SAMPLE):
        out = await records(list(permutation), [])
        assert [(r.alias, r.mac) for r in out] == expected


# --- reporting ------------------------------------------------------------


async def test_undefined_vlans_are_named_in_a_warning(caplog):
    clients = [
        client("aa:bb:cc:dd:ee:01", note="vlan=99", name="Rogue"),
        client("aa:bb:cc:dd:ee:02", note="vlan=20,alt_vlan=77", name="Phone"),
    ]
    await records(clients, [vlan_network(20)])
    assert "99" in caplog.text
    assert "77" in caplog.text
    assert "commented out" in caplog.text


async def test_nothing_is_logged_when_every_vlan_is_defined(caplog):
    await records([client("aa:bb:cc:dd:ee:01", note="vlan=20")], [vlan_network(20)])
    assert caplog.text == ""
