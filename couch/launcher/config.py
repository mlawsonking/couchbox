"""/etc/couch/couch.toml loading and validation (stdlib tomllib). Never raises on bad input: a broken config yields
a Config with `error` set, so the launcher can show a kid-readable screen plus the parent detail line."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field

DEFAULT_PATH = "/etc/couch/couch.toml"
LIBRARY_PATH = "/var/lib/couch/library.json"     # written by `couch-update --import` from the workstation exporter
ART_DIR = "/var/lib/couch/art"

# Quality ladder (GAMEPLAN section 2, Network). Name -> (resolution, fps, bitrate kbps).
LADDER = {
    "720p60-10": ("1280x720", 60, 10000),
    "1080p60-15": ("1920x1080", 60, 15000),
    "1080p60-20": ("1920x1080", 60, 20000),
    "1440p60-30": ("2560x1440", 60, 30000),
    "4k60-40": ("3840x2160", 60, 40000),
}
DISPLAY_MODES = {"auto", "1080p60", "4k60", "4k120", "custom"}
MODE_TO_RUNG = {"1080p60": ("1920x1080", 60, 15000), "4k60": ("3840x2160", 60, 40000),
                "4k120": ("3840x2160", 120, 60000)}
CODECS = {"HEVC", "H.264", "AV1", "auto"}
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


@dataclass(frozen=True)
class Tile:
    name: str
    app: str
    art: str = ""


@dataclass
class Config:
    host: str = "gamepc.local"
    mac: str = ""
    display_mode: str = "auto"
    default_rung: str = "1080p60-15"
    codec: str = "HEVC"
    custom_resolution: str = "1920x1080"
    custom_fps: int = 60
    custom_bitrate_kbps: int = 15000
    pair_policy: str = "menu"
    setup_pin: str = ""              # [gamepad] setup_pin: 4 digits; empty = no "Set up a controller" menu item
    nightly_reboot: str = "04:30"
    ntfy_url: str = ""
    tiles: list[Tile] = field(default_factory=list)
    error: str | None = None          # parent-readable detail when the file is broken/missing
    warnings: list[str] = field(default_factory=list)
    path: str = DEFAULT_PATH

    def stream_params(self, measured_rung: str | None = None) -> tuple[str, int, int]:
        """(resolution, fps, bitrate_kbps) for the next stream."""
        if self.display_mode == "custom":
            return self.custom_resolution, self.custom_fps, self.custom_bitrate_kbps
        if self.display_mode in MODE_TO_RUNG:
            return MODE_TO_RUNG[self.display_mode]
        return LADDER.get(measured_rung or self.default_rung, LADDER["1080p60-15"])

    def stream_codec(self, resolution: str) -> str | None:
        """Codec for moonlight-qt's --video-codec. 4K rungs are HEVC-only (the decode path couch-doctor verifies);
        AV1 only when explicitly configured (off by default: many client iGPUs, e.g. Gen9.5, cannot decode it).
        None = let moonlight-qt choose ("auto")."""
        if resolution == "3840x2160":
            return "HEVC"
        if self.codec == "auto":
            return None
        return self.codec

    def stream_argv(self, moonlight: str, target: str, app: str, measured_rung: str | None = None) -> list[str]:
        """The one place a moonlight-qt stream command line is built (launcher and daemon both use it).
        HDR is never requested (moonlight-qt's default is SDR); v1 does not target HDR."""
        res, fps, kbps = self.stream_params(measured_rung)
        argv = [moonlight, "stream", target, app, "--quit-after", "--display-mode", "fullscreen",
                "--resolution", res, "--fps", str(fps), "--bitrate", str(kbps), "--video-decoder", "hardware"]
        codec = self.stream_codec(res)
        if codec:
            argv += ["--video-codec", codec]
        return argv


def _default_tiles() -> list[Tile]:
    return [Tile("Steam Big Picture", "Steam Big Picture"), Tile("Desktop", "Desktop")]


def library_tiles(path: str = LIBRARY_PATH, art_dir: str = ART_DIR) -> list[Tile]:
    """Per-game tiles from the imported Steam library (installed games only). Unreadable file -> none."""
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    games = data.get("games", data) if isinstance(data, dict) else data
    out = []
    for g in games if isinstance(games, list) else []:
        if not isinstance(g, dict) or not g.get("installed", True):
            continue
        name, appid = g.get("name"), str(g.get("appid", ""))
        if not isinstance(name, str) or not name.strip() or not appid.isdigit():
            continue
        art = f"{appid}.jpg" if os.path.isfile(os.path.join(art_dir, f"{appid}.jpg")) else ""
        out.append(Tile(name.strip()[:40], name.strip(), art))
    return sorted(out, key=lambda t: t.name.lower())


def load(path: str = DEFAULT_PATH, library: str | None = None) -> Config:
    cfg = _load(path)
    lib = library_tiles(library) if library else (library_tiles() if path == DEFAULT_PATH else [])
    have = {t.app for t in cfg.tiles}
    cfg.tiles = cfg.tiles + [t for t in lib if t.app not in have]
    return cfg


def _load(path: str) -> Config:
    cfg = Config(path=path, tiles=_default_tiles())
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        cfg.error = f"{path} is missing"
        return cfg
    except PermissionError:
        cfg.error = f"{path} is not readable by the couch user"
        return cfg
    except tomllib.TOMLDecodeError as exc:
        cfg.error = f"{path}: TOML syntax error: {exc}"
        return cfg
    problems: list[str] = []

    def section(name: str) -> dict:
        v = data.get(name, {})
        if not isinstance(v, dict):
            problems.append(f"[{name}] must be a table")
            return {}
        return v

    def get(sec: dict, key: str, typ, default):
        v = sec.get(key, default)
        if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
            problems.append(f"{key} must be {typ.__name__}")
            return default
        return v

    host = section("host")
    cfg.host = get(host, "name", str, cfg.host).strip()
    if IPV4_RE.match(cfg.host):
        cfg.warnings.append("host.name is an IP address; DHCP leases change - use the PC's name")
    elif not HOST_RE.match(cfg.host):
        problems.append(f"host.name {cfg.host!r} is not a host name")
    cfg.mac = get(host, "mac", str, "").strip()
    if cfg.mac and not MAC_RE.match(cfg.mac):
        problems.append(f"host.mac {cfg.mac!r} is not a MAC address (AA:BB:CC:DD:EE:FF)")
    if not cfg.mac:
        cfg.warnings.append("host.mac is empty: the box cannot wake the game PC")

    st = section("stream")
    cfg.display_mode = get(st, "display_mode", str, cfg.display_mode)
    if cfg.display_mode not in DISPLAY_MODES:
        problems.append(f"stream.display_mode must be one of {sorted(DISPLAY_MODES)}")
        cfg.display_mode = "auto"
    cfg.default_rung = get(st, "default_rung", str, cfg.default_rung)
    if cfg.default_rung not in LADDER:
        problems.append(f"stream.default_rung must be one of {list(LADDER)}")
        cfg.default_rung = "1080p60-15"
    cfg.codec = get(st, "codec", str, cfg.codec)
    if cfg.codec not in CODECS:
        problems.append(f"stream.codec must be one of {sorted(CODECS)}")
        cfg.codec = "HEVC"
    if cfg.codec == "AV1":
        cfg.warnings.append("stream.codec AV1 needs AV1 hardware decode on this box - check couch-doctor's decode line")
    cfg.custom_resolution = get(st, "custom_resolution", str, cfg.custom_resolution)
    if not re.match(r"^\d{3,4}x\d{3,4}$", cfg.custom_resolution):
        problems.append("stream.custom_resolution must look like 1920x1080")
        cfg.custom_resolution = "1920x1080"
    cfg.custom_fps = get(st, "custom_fps", int, cfg.custom_fps)
    if not 24 <= cfg.custom_fps <= 240:
        problems.append("stream.custom_fps must be 24-240")
        cfg.custom_fps = 60
    cfg.custom_bitrate_kbps = get(st, "custom_bitrate_kbps", int, cfg.custom_bitrate_kbps)
    if not 500 <= cfg.custom_bitrate_kbps <= 150000:
        problems.append("stream.custom_bitrate_kbps must be 500-150000")
        cfg.custom_bitrate_kbps = 15000

    pair = section("pair")
    cfg.pair_policy = get(pair, "policy", str, cfg.pair_policy)
    if cfg.pair_policy not in ("menu", "off"):
        problems.append("pair.policy must be menu or off")
        cfg.pair_policy = "menu"

    gp = section("gamepad")
    cfg.setup_pin = str(get(gp, "setup_pin", str, ""))
    if cfg.setup_pin and not re.match(r"^\d{4}$", cfg.setup_pin):
        problems.append("gamepad.setup_pin must be 4 digits (or empty)")
        cfg.setup_pin = ""

    mt = section("maintenance")
    cfg.nightly_reboot = get(mt, "nightly_reboot", str, cfg.nightly_reboot)
    if cfg.nightly_reboot and not TIME_RE.match(cfg.nightly_reboot):
        problems.append("maintenance.nightly_reboot must be HH:MM or empty")
        cfg.nightly_reboot = ""
    cfg.ntfy_url = get(mt, "ntfy_url", str, "")
    if cfg.ntfy_url and not cfg.ntfy_url.startswith(("https://", "http://")):
        problems.append("maintenance.ntfy_url must be an http(s) URL")
        cfg.ntfy_url = ""

    raw_tiles = data.get("tile", [])
    tiles: list[Tile] = []
    if not isinstance(raw_tiles, list):
        problems.append("[[tile]] entries must be an array of tables")
        raw_tiles = []
    for i, t in enumerate(raw_tiles):
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t.get("name", "").strip():
            problems.append(f"tile #{i + 1} needs a name")
            continue
        app = t.get("app", t["name"])
        art = t.get("art", "")
        if not isinstance(app, str) or not isinstance(art, str):
            problems.append(f"tile {t['name']!r}: app/art must be strings")
            continue
        if art.startswith(("http://", "https://")):
            cfg.warnings.append(f"tile {t['name']!r}: art URLs are not fetched at runtime; import art with couch-update")
            art = ""
        tiles.append(Tile(t["name"].strip()[:40], app.strip(), art.strip()))
    if tiles:
        cfg.tiles = tiles
    if problems:
        cfg.error = f"{os.path.basename(path)}: " + "; ".join(problems)
    return cfg


class Watcher:
    """Hot-reload: call poll() each second; returns a new Config when the file's mtime/size changed."""

    def __init__(self, path: str = DEFAULT_PATH, library: str | None = None):
        self.path = path
        self.library = library or (LIBRARY_PATH if path == DEFAULT_PATH else None)
        self._sig = self._stat()

    def _stat(self):
        sig = []
        for f in (self.path, self.library):
            try:
                st = os.stat(f) if f else None
                sig.append((st.st_mtime_ns, st.st_size) if st else None)
            except OSError:
                sig.append(None)
        return tuple(sig)

    def poll(self) -> Config | None:
        sig = self._stat()
        if sig != self._sig:
            self._sig = sig
            return load(self.path, self.library)
        return None
