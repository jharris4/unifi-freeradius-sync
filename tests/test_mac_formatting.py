import pytest

from unifi_vlan_note import toMACUppercase


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("aa:bb:cc:dd:ee:ff", "AABBCCDDEEFF"),
        ("AA:BB:CC:DD:EE:FF", "AABBCCDDEEFF"),
        ("Aa:bB:cC:Dd:eE:fF", "AABBCCDDEEFF"),
        ("aabbccddeeff", "AABBCCDDEEFF"),
        ("", ""),
    ],
)
def test_strips_colons_and_uppercases(given, expected):
    assert toMACUppercase(given) == expected


def test_only_colons_are_stripped():
    # dashes and dots are left alone -- UniFi returns colon-separated MACs, so
    # nothing else has ever needed handling
    assert toMACUppercase("aa-bb-cc-dd-ee-ff") == "AA-BB-CC-DD-EE-FF"
