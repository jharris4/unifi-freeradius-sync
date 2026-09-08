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

### Second VLAN per SSID

If `vlan_2_ssid` is set, a `vlan2=30` directive in the note emits an
additional entry matched on `Called-Station-Id`, so the device gets a
different VLAN when it connects to that specific SSID. Leave `vlan_2_ssid`
empty to disable. Only one such SSID is supported.

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

`vlan_regex` / `vlan_2_regex` control the note syntax; the `*_match_index`
values select which capture group holds the number. `default_vlan` is the
catch-all VLAN described above — read that section.

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

- No tests yet.
- Fail-open default, as described above.
- `client_secret` is a required config key that is never read — vestigial from
  an earlier version that also generated `clients.conf`. It will be removed.
- TLS verification is not configurable.
- `aiounifi` is Home Assistant's internal UniFi library and makes breaking
  changes without notice. The version is pinned hard for this reason.
- Naming is inconsistent (`unifi_vlan_note.py` predates the project name).

## License

MIT
