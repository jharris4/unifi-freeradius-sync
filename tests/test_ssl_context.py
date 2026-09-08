"""`buildSSLContext` -- the only place TLS verification is decided."""

import ssl

import pytest
from conftest import make_config

from unifi_freeradius_sync import buildSSLContext


def test_verification_is_off_by_default():
    # aiounifi wants the literal False, not None and not True
    assert buildSSLContext(make_config()) is False


def test_a_ca_bundle_is_ignored_while_verification_is_off():
    assert buildSSLContext(make_config(ca_bundle="/etc/ssl/certs/ca.pem")) is False


def test_enabling_verification_builds_a_default_context():
    context = buildSSLContext(make_config(verify_ssl=True))
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_a_missing_ca_bundle_fails_loudly(tmp_path):
    # the bundle is a deliberate choice, so a bad path must not degrade into
    # trusting the system store -- or worse, into no verification at all
    with pytest.raises(FileNotFoundError):
        buildSSLContext(make_config(verify_ssl=True, ca_bundle=str(tmp_path / "nope.pem")))


def test_an_unparseable_ca_bundle_fails_loudly(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("not a certificate\n")
    with pytest.raises(ssl.SSLError):
        buildSSLContext(make_config(verify_ssl=True, ca_bundle=str(ca)))
