import os
import sys
import asyncio
import logging
import json
import re
import argparse
import textwrap
from pathlib import Path
import aiohttp
from aiohttp import ClientSession
import aiounifi
from aiounifi.models.configuration import Configuration
from aiounifi.models.api import ApiRequest
from aiounifi.models.message import MessageKey

DEFAULT_CONFIG_PATH = "./unifi_vlan_note_config.json"
DEFAULT_OUTPUT_PATH = "./"

LOGGER = logging.getLogger(__name__)
UNIFI_LOGGER = logging.getLogger("aiounifi")
UNIFI_LOGGER.setLevel(logging.INFO)

# websocket messages that can affect the generated authorize file: client
# note/name edits and removals, and VLAN network changes
WATCH_MESSAGE_KEYS = (
    MessageKey.CLIENT_UPDATED,
    MessageKey.CLIENT_REMOVED,
    MessageKey.NETWORK_CONF_UPDATED,
)
DEFAULT_RESYNC_INTERVAL = 3600
DEBOUNCE_SECONDS = 5
RECONNECT_MIN_SECONDS = 5
RECONNECT_MAX_SECONDS = 300

VLAN_REGEX = r"(vlan)(\s*)([=:])(\s*)([0-9]+)"
VLAN_REGEX_MATCH_INDEX = 5 # whole match is at index 0, so index is 1 based...
VLAN_2_REGEX = r"(vlan2)(\s*)([=:])(\s*)([0-9]+)"
VLAN_2_REGEX_MATCH_INDEX = 5 # whole match is at index 0, so index is 1 based...
VLAN_2_SSID = ""

def toMACUppercase(mac: str):
    return re.sub('[:]', '', mac).upper()

class MACVLANRecord:
    mac: str
    vlan: int
    vlan_2: int
    alias: str
    ip: str
    wired: bool
    blocked: bool

    def __init__(self, mac: str, vlan: int, vlan_2: int, alias: str, ip: str, wired: bool, blocked: bool):
        self.mac = mac
        self.vlan = vlan
        self.vlan_2 = vlan_2
        self.alias = alias
        self.ip = ip
        self.wired = wired
        self.blocked = blocked

class UnifiVLANNoteConfig:
    username: str
    password: str
    host: str
    port: int = 8443
    site: str = "default"
    vlan_regex: str = VLAN_REGEX
    vlan_regex_match_index: int = VLAN_REGEX_MATCH_INDEX
    vlan_2_regex: str = VLAN_2_REGEX
    vlan_2_regex_match_index: int = VLAN_2_REGEX_MATCH_INDEX
    vlan_2_ssid : str = VLAN_2_SSID
    default_vlan: int = 1
    client_secret: str

    def __init__(self, host: str, username: str, password: str, client_secret: str):
        self.host = host
        self.username = username
        self.password = password
        self.client_secret = client_secret

    def loadFromFile(config_path: str):
        c = json.loads(Path(config_path).read_text())
        missing = [key for key in ("host", "username", "password", "client_secret") if key not in c]
        if missing:
            LOGGER.error(f"Config {config_path} is missing required keys: {', '.join(missing)}")
        else:
            config = UnifiVLANNoteConfig(c["host"], c["username"], c["password"], c["client_secret"])
            if ("port" in c):
                config.port = c["port"]
            if ("site" in c):
                config.site = c["site"]
            if ("vlan_regex" in c):
                config.vlan_regex = c["vlan_regex"]
            if ("vlan_regex_match_index" in c):
                config.vlan_regex_match_index = c["vlan_regex_match_index"]
            if ("vlan_2_regex" in c):
                config.vlan_2_regex = c["vlan_2_regex"]
            if ("vlan_2_regex_match_index" in c):
                config.vlan_2_regex_match_index = c["vlan_2_regex_match_index"]
            if ("vlan_2_ssid" in c):
                config.vlan_2_ssid = c["vlan_2_ssid"]
            if ("default_vlan" in c):
                config.default_vlan = c["default_vlan"]
            return config

class UnifiVLANNoteController:
    config: UnifiVLANNoteConfig
    unifi_controller: aiounifi.Controller
    session: ClientSession

    def __init__(self, config: UnifiVLANNoteConfig):
        self.config = config
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self.unifi_controller = aiounifi.Controller(
            config=Configuration(
                self.session,
                config.host,
                username=config.username,
                password=config.password,
                port=config.port,
                site=config.site,
                ssl_context=False,
            )
        )

    async def doLogout(self):
        await self.session.close()

    async def doLogin(self):
        try:
            async with asyncio.timeout(10):
                await self.unifi_controller.login()
            return True

        except aiounifi.LoginRequired:
            LOGGER.warning(f"Connected to UniFi at {self.config.host} but couldn't log in")

        except aiounifi.Unauthorized:
            LOGGER.warning(f"Connected to UniFi at {self.config.host} but not registered")

        except (asyncio.TimeoutError, aiounifi.RequestError):
            LOGGER.exception(f"Error connecting to the UniFi controller at {self.config.host}")

        except aiounifi.AiounifiException:
            LOGGER.exception("Unknown UniFi communication error occurred")

        return False

    async def getNetworks(self):
        networksResponse = await self.unifi_controller.request(ApiRequest(method="GET",path="/rest/networkconf"))
        return networksResponse["data"] if "data" in networksResponse else []

    async def getClients(self):
        clientsResponse = await self.unifi_controller.request(ApiRequest(method="GET",path="/stat/alluser"))
        return clientsResponse["data"] if "data" in clientsResponse else []

    async def getVLANs(self):
        vlan_set = {1}
        for network in await self.getNetworks():
            if ("vlan" in network and network["vlan"]):
                vlan_set.add(network["vlan"])
        return vlan_set

    async def getMACVLANs(self):
        vlan_set = await self.getVLANs()
        clients = await self.getClients()
        mac_vlans = []
        blocked_vlans = set()
        for client in clients:
            # the controller serializes cleared fields as JSON null, so treat
            # null and absent the same — a None note would crash re.search and
            # a None name would crash the sort below
            note = client.get("note") or ""
            mac = client.get("mac") or ""
            alias = client.get("name") or ""
            ip = client.get("last_ip") or client.get("fixed_ip") or ""
            wired = bool(client.get("is_wired"))
            vlan = 0
            vlan_2 = 0
            blocked = False
            matches = re.search(self.config.vlan_regex, note)
            if (matches):
                vlan = int(matches[self.config.vlan_regex_match_index])
                blocked = vlan not in vlan_set
                if blocked:
                    blocked_vlans.add(vlan)
            matches = re.search(self.config.vlan_2_regex, note)
            if (matches):
                vlan_2 = int(matches[self.config.vlan_2_regex_match_index])
                if vlan_2 not in vlan_set:
                    blocked = True
                    blocked_vlans.add(vlan_2)
            if (mac and vlan) or alias:
                # print("client:\n\n")
                # print(alias)
                # print("\n\n")
                # print(client)
                # print("\n\n")
                mac_vlans.append(MACVLANRecord(mac, vlan, vlan_2, alias, ip, wired, blocked))

        if len(blocked_vlans) > 0:
            print("blocked vlans:")
            print(blocked_vlans)
        # the controller does not return clients in a stable order, so sort to
        # keep the generated file deterministic across runs
        mac_vlans.sort(key=lambda r: (r.alias.lower(), r.mac))
        return mac_vlans

    async def generateUsersConfig(self, output_path: str):
        Path(os.path.join(output_path, "mods-config/files")).mkdir(parents=True, exist_ok=True)
        # write errors propagate: watch mode retries them, one-shot mode must
        # exit non-zero rather than report success over a stale file
        with open(os.path.join(output_path, 'mods-config/files/authorize'), 'w') as f:
            f.write(textwrap.dedent(f''' \
                DEFAULT Auth-Type := Accept
                    Tunnel-Type = VLAN,
                    Tunnel-Medium-Type = IEEE-802,
                    Tunnel-Private-Group-Id = "{self.config.default_vlan}",
                    Fall-Through = Yes'''))
            f.write("\n\n")

            for mac_vlan in await self.getMACVLANs():
                if mac_vlan.blocked or not mac_vlan.mac or not mac_vlan.vlan:
                    f.write(textwrap.dedent(f'''\
                        # {mac_vlan.alias}{" [WIRED]" if mac_vlan.wired else ""}
                        # {mac_vlan.ip}
                        # {toMACUppercase(mac_vlan.mac)}
                        #   Tunnel-Private-Group-ID := "{mac_vlan.vlan}"'''))
                else:
                    if mac_vlan.vlan_2 and len(self.config.vlan_2_ssid) > 0:
                        f.write(textwrap.dedent(f'''\
                            # {mac_vlan.alias}{" [WIRED]" if mac_vlan.wired else ""}
                            # {mac_vlan.ip}
                            # SSID: {self.config.vlan_2_ssid}
                            {toMACUppercase(mac_vlan.mac)} Called-Station-Id =~ '.*:{self.config.vlan_2_ssid}'
                               Tunnel-Private-Group-ID := "{mac_vlan.vlan_2}"'''))
                        f.write("\n\n")

                    f.write(textwrap.dedent(f'''\
                        # {mac_vlan.alias}{" [WIRED]" if mac_vlan.wired else ""}
                        # {mac_vlan.ip}
                        {toMACUppercase(mac_vlan.mac)}
                           Tunnel-Private-Group-ID := "{mac_vlan.vlan}"'''))

                f.write("\n\n")

    async def generateConfig(self, output_path: str):
        await self.generateUsersConfig(output_path)

    async def runOnChange(self, on_change: str):
        process = await asyncio.create_subprocess_shell(on_change)
        returncode = await process.wait()
        if returncode != 0:
            LOGGER.warning(f"on-change command exited with code {returncode}")

    async def watchRegenerate(self, dirty: asyncio.Event, output_path: str, on_change: str):
        while True:
            await dirty.wait()
            # debounce: a single UI edit can emit several messages, so wait
            # until things have been quiet before regenerating
            while True:
                dirty.clear()
                await asyncio.sleep(DEBOUNCE_SECONDS)
                if not dirty.is_set():
                    break
            try:
                await self.generateConfig(output_path)
            except Exception:
                LOGGER.exception("Failed to regenerate config, will retry")
                await asyncio.sleep(RECONNECT_MIN_SECONDS)
                dirty.set()
                continue
            LOGGER.info("Regenerated config")
            if on_change:
                await self.runOnChange(on_change)

    async def watchResync(self, dirty: asyncio.Event, resync_interval: int):
        while True:
            await asyncio.sleep(resync_interval)
            LOGGER.info("Periodic resync")
            dirty.set()

    async def watchWebsocket(self, dirty: asyncio.Event):
        backoff = RECONNECT_MIN_SECONDS
        loop = asyncio.get_running_loop()
        while True:
            connected_at = loop.time()
            try:
                await self.unifi_controller.start_websocket()
                LOGGER.warning("UniFi websocket closed")
            except Exception as e:
                LOGGER.warning(f"UniFi websocket error: {e}")
            # a connection that survived a while means the last login was
            # good, so don't keep escalating the backoff
            if loop.time() - connected_at > 60:
                backoff = RECONNECT_MIN_SECONDS
            LOGGER.info(f"Reconnecting UniFi websocket in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)
            await self.doLogin()
            # regenerate to catch anything missed while disconnected
            dirty.set()

    async def watch(self, output_path: str, on_change: str, resync_interval: int):
        dirty = asyncio.Event()
        dirty.set() # always regenerate on startup

        def onMessage(message):
            LOGGER.info(f"UniFi change: {message.meta.message.value}")
            dirty.set()

        unsubscribe = self.unifi_controller.messages.subscribe(onMessage, WATCH_MESSAGE_KEYS)
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.watchWebsocket(dirty))
                tg.create_task(self.watchResync(dirty, resync_interval))
                tg.create_task(self.watchRegenerate(dirty, output_path, on_change))
        finally:
            unsubscribe()

async def main(config: UnifiVLANNoteConfig, output_path: str, watch: bool = False,
               on_change: str = None, resync_interval: int = DEFAULT_RESYNC_INTERVAL):
    """Main function."""
    LOGGER.info("Starting aioUniFi")

    controller = UnifiVLANNoteController(config)
    try:
        if not await controller.doLogin():
            return 1
        if watch:
            await controller.watch(output_path, on_change, resync_interval)
        else:
            await controller.generateConfig(output_path)
        return 0
    finally:
        await controller.doLogout()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", type=str, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-o", "--output", type=str, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("-D", "--debug", action="store_true")
    parser.add_argument("-w", "--watch", action="store_true",
                        help="stay running and regenerate on UniFi websocket change events")
    parser.add_argument("--on-change", type=str, default=None,
                        help="shell command to run after each regeneration in watch mode")
    parser.add_argument("--resync", type=int, default=DEFAULT_RESYNC_INTERVAL,
                        help="seconds between fallback full regenerations in watch mode")
    args = parser.parse_args()

    loglevel = logging.INFO
    if args.debug:
        loglevel = logging.DEBUG
        UNIFI_LOGGER.setLevel(logging.DEBUG)
    logging.basicConfig(format="%(message)s", level=loglevel)

    config_path = args.config

    config = UnifiVLANNoteConfig.loadFromFile(config_path)

    if config:
        output_path = args.output

        LOGGER.info(f"Loaded config for {config.host}:{config.port}, site {config.site}")

        try:
            sys.exit(asyncio.run(
                main(config, output_path, watch=args.watch,
                     on_change=args.on_change, resync_interval=args.resync)
            ))
        except KeyboardInterrupt:
            pass
    else:
        sys.exit(1)
