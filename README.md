# unifi-freeradius-sync

Generates a FreeRADIUS `authorize` file from UniFi client notes, so MAC-based
VLAN assignment is driven by the UniFi controller instead of a hand-maintained
RADIUS config.

Put `vlan=20` in a UniFi client's note and that device is handed
`Tunnel-Private-Group-ID := "20"` on its next RADIUS auth. Edit the note in the
UniFi UI and the file regenerates within seconds.

## Status

Extracted from a working home deployment. It runs continuously in that setup,
but it has had exactly one user, and the cleanup listed under
[Known issues](#known-issues) is not done yet. Treat it accordingly.

## ⚠️ This is fail-open by default

The generated file begins with a catch-all:

```
DEFAULT Auth-Type := Accept
   Tunnel-Type = VLAN,
   Tunnel-Medium-Type = IEEE-802,
   Tunnel-Private-Group-Id = "<default_vlan>",
   Fall-Through = Yes
```

**Any MAC address that is not listed is accepted and placed on
`default_vlan`.** This is not a MAC allowlist. If you set `default_vlan` to
your trusted LAN, every unknown device that associates lands on your trusted
LAN.

Set `default_vlan` to a guest or quarantine VLAN. An opt-in mode that rejects
unknown devices instead is a planned change, not current behaviour.

## How it works

1. Logs into the UniFi controller and reads all known clients plus the
   configured networks.
2. Parses each client's note for a VLAN directive (`vlan=20` or `vlan: 20`).
3. Writes `mods-config/files/authorize` under the output directory, sorted
   deterministically so unchanged input produces an identical file.
4. In `--watch` mode, holds a websocket to the controller and regenerates on
   client and network change events, debounced, with an hourly fallback
   resync and reconnect backoff.

A VLAN referenced in a note but not defined on the controller is treated as a
mistake: that client is written out commented, rather than being given a VLAN
that does not exist.

### A different VLAN on one SSID

Set `alt_ssid` to an SSID name, and an `alt_vlan=30` directive in a client's
note emits an extra entry matched on `Called-Station-Id`. That device then
gets VLAN 30 when it connects to that SSID, and its normal `vlan=` VLAN
everywhere else.

The case this exists for: a phone that normally sits on the trusted LAN, but
should land on the untrusted IoT VLAN while it is joined to the IoT SSID —
so it can reach hubs and devices there during commissioning. Note that the
alt VLAN is not necessarily the *more* privileged one; it is just the other
one.

Leave `alt_ssid` empty to disable. Only one such SSID is supported.

## What this does not do

It generates one file: `mods-config/files/authorize`. That is the whole output.

In particular it does **not** generate `clients.conf`, so the RADIUS clients
and their shared secrets stay yours to manage. That is deliberate — the shared
secret is not UniFi's data, the set of NAS devices changes about as often as
you buy an access point, and for most deployments `clients.conf` is a single
hand-written block covering the management subnet. There is nothing there
worth syncing, and generating it would mean handing this tool a secret to
write to disk in exchange for nothing.

The rest of the FreeRADIUS configuration — EAP, certificates, `mods-enabled`,
the server blocks — is likewise out of scope. This tool assumes you have a
working FreeRADIUS and want its VLAN assignments driven from the UniFi UI.

## Usage

```sh
pip install -r requirements.txt
cp config.example.json config.json   # then fill in host and credentials
python unifi_vlan_note.py -c config.json -o ./out
```

One-shot by default. `--watch` stays running and regenerates on change;
`--on-change '<command>'` runs a command after each regeneration, which is how
you signal FreeRADIUS to reload.

| Flag | Meaning |
| --- | --- |
| `-c`, `--config` | config file path |
| `-o`, `--output` | output directory (`mods-config/files/authorize` is created beneath it) |
| `-w`, `--watch` | stay running, regenerate on UniFi change events |
| `--on-change` | shell command to run after each regeneration |
| `--resync` | seconds between fallback full regenerations (default 3600) |
| `-D`, `--debug` | debug logging |

## Configuration

See `config.example.json`. `host`, `username`, `password` are required.

`vlan_regex` / `alt_vlan_regex` control the note syntax; the `*_match_index`
values select which capture group holds the number. Both patterns begin with
`\b` so that `vlan` at the end of another word — `alt_vlan`, `myvlan` — is not
read as a directive. `default_vlan` is the catch-all VLAN described above —
read that section.

Keys this version does not recognise are ignored, but logged at startup, so a
misspelled or renamed optional key does not fail silently.

TLS verification against the controller is currently disabled unconditionally,
because UniFi controllers ship self-signed certificates.

## Docker

The included `Dockerfile` and `sync.sh` are one worked example of deploying
this against a containerised FreeRADIUS: `sync.sh` validates the generated
file, refuses to deploy empty or MAC-less output, copies it into the live
mount, and sends `SIGHUP` to a named FreeRADIUS container over the Docker
socket so the server reloads without restarting.

That wrapper is deliberately opinionated. `--on-change` is the general
integration point if your deployment differs.

## Known issues

- Fail-open default, as described above.
- TLS verification is not configurable.
- `aiounifi` is Home Assistant's internal UniFi library and makes breaking
  changes without notice. The version is pinned hard for this reason.
- Naming is inconsistent (`unifi_vlan_note.py` predates the project name).

## Development

```sh
python3.13 -m venv .venv          # matches the Dockerfile base image
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

The suite under `tests/` is characterization, not specification: it pins the
bytes the currently deployed version produces, so a refactor can be checked
against known-good output. A failure means the generated `authorize` file
changed — confirm that was intended before updating an expectation.

## License

MIT
