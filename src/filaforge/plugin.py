"""FilaForge inside OrcaSlicer: the tab, reading presets (UI thread), and the work threads.

OrcaSlicer's preset API is only touched on its UI thread (on_message runs there): the presets a look-up needs are
read first, as plain data, then the network work runs on a daemon thread and posts its result to the page.
"""

import json
import logging
import os
import re
import tempfile
import threading
import traceback
import webbrowser

import orca

from .core import measured, merge, process, research, spoolman, writer
from .core.model import Identity
from .core.net import Fetcher
from .core.sources import ofd, spoolmandb
from .core.text import brand_key, fold, split_material, tokens

NAME = "FilaForge"
VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
FIXED_ORIGINS = ["https://api.openfilamentdatabase.org", "https://api.github.com", "https://raw.githubusercontent.com"]
MAX_ORIGINS = 40
log = logging.getLogger("filaforge")


# --- folders and settings ------------------------------------------------------------------------------------
def data_dir():
    """OrcaSlicer's data folder: the parent of orca_plugins, where this plugin is installed."""
    parts = HERE.replace("\\", "/").split("/")
    if "orca_plugins" in parts:
        return "/".join(parts[:parts.index("orca_plugins")])
    return os.path.join(os.path.expanduser("~"), ".filaforge-dev")


def own_dir():
    return os.path.join(data_dir(), "filaforge")


def _setup_log():
    if log.handlers:
        return
    try:
        os.makedirs(own_dir(), exist_ok=True)
        handler = logging.FileHandler(os.path.join(own_dir(), "filaforge.log"), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except OSError:
        pass


DEFAULTS = {"spoolman_url": "", "language": "auto", "online": True, "origins": []}


def read_settings():
    try:
        with open(os.path.join(own_dir(), "settings.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in data.items() if k in DEFAULTS})
    if not out["spoolman_url"]:
        out["spoolman_url"] = spooler_spoolman_url()
    return out


def write_settings(changes):
    current = read_settings()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS})
    os.makedirs(own_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=own_dir(), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=1)
    os.replace(tmp, os.path.join(own_dir(), "settings.json"))
    return current


def spooler_spoolman_url():
    """Spooler's Spoolman address, when Spooler is installed: one address to set, not two."""
    try:
        with open(os.path.join(data_dir(), "orca_plugins", "config.json"), encoding="utf-8") as f:
            config = json.load(f)
        for item in config.get("config") or []:
            url = (item.get("cap_config") or {}).get("spoolman_url")
            if item.get("capability") == "Spooler" and isinstance(url, str) and url.strip():
                return url.strip()
    except Exception:   # not there, unreadable, or outside what OrcaSlicer lets the plugin read
        pass
    return ""


def origin(url):
    found = re.match(r"^(https?)://([^/:?#]+)", str(url or "").strip())
    return "%s://%s" % (found.group(1), found.group(2).lower()) if found else ""


def remember_origin(url):
    """Keep a data sheet's or Spoolman's scheme://host, so the next start asks OrcaSlicer for it once."""
    o = origin(url)
    if not o or o in FIXED_ORIGINS:
        return
    settings = read_settings()
    if o not in settings["origins"]:
        write_settings({"origins": (settings["origins"] + [o])[-MAX_ORIGINS:]})


def request_permissions():
    request = getattr(orca, "request_permissions", None)
    if request is None:
        return
    try:
        settings = read_settings()   # never presets here: OrcaSlicer is still loading them
        network = list(FIXED_ORIGINS)
        for url in [settings.get("spoolman_url")] + list(settings.get("origins") or []):
            o = origin(url if "://" in str(url) else "http://%s" % url) if url else ""
            if o and o not in network:
                network.append(o)
        try:
            request(threading=True, fs_readwrite=[own_dir()], network_http=network)
        except TypeError:
            request(threading=True, fs_readwrite=[own_dir()])
    except Exception:
        log.exception("permission request failed")


# --- reading OrcaSlicer (UI thread only) -------------------------------------------------------------------------
def _clean(text):
    text = "" if text is None else str(text)
    return text.split(";")[0].split(",")[0].strip().strip('"')


def _selected(collection):
    for name in ("get_selected_preset", "selected_preset"):
        fn = getattr(collection, name, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                continue
    return None


def _config(preset):
    out = {}
    for key in preset.config_keys():
        value = preset.config_value(key)
        if value is not None:
            out[str(key)] = str(value)
    return out


def printer_info(bundle):
    cfg = bundle.full_config_value
    printer = _selected(bundle.printers)
    process_preset = _selected(bundle.prints)
    try:
        nozzle = float(_clean(cfg("nozzle_diameter")) or 0.4)
    except ValueError:
        nozzle = 0.4
    return {"name": getattr(printer, "name", "") or "", "model": _clean(cfg("printer_model")),
            "nozzle": nozzle, "process": getattr(process_preset, "name", "") or ""}


def filament_presets(bundle, identity):
    """The filament presets a look-up can use, as plain data: this brand's and the generic ones of the material,
    compatible with the selected printer."""
    col = bundle.filaments
    base, _ = split_material(identity.material)
    want = brand_key(identity.brand)
    brand_words = set(tokens(want)) | set(tokens(identity.brand))
    out = []
    for i in range(col.size()):
        p = col.preset(i)
        name = str(p.name)
        if not getattr(p, "is_compatible", True) or name.startswith("_local/" + writer.BUNDLE + "/"):
            continue
        folded = fold(name)
        if "generic" not in folded and not (brand_words & set(tokens(folded))):
            continue
        if folded.startswith("fdm_") or "@base" in folded or name.endswith(" @base"):
            continue
        vendor, kind = _clean(p.config_value("filament_vendor")), _clean(p.config_value("filament_type"))
        if split_material(kind)[0] != base:
            continue
        printers = _clean(p.config_value("compatible_printers"))
        out.append({"name": name, "vendor": vendor, "type": kind, "is_system": bool(p.is_system),
                    "compatible": True, "specific": bool(printers), "config": _config(p)})
    return out


def process_list(bundle):
    col = bundle.prints
    names = []
    for i in range(col.size()):
        p = col.preset(i)
        if getattr(p, "is_compatible", True) and getattr(p, "is_visible", True) and \
                not str(p.name).startswith("_local/" + writer.BUNDLE + "/"):
            names.append(str(p.name))
    return names


def preset_config(collection, name):
    selected = _selected(collection)
    preset = selected if getattr(selected, "name", None) == name else collection.find_preset(name)
    if preset is None:
        raise ValueError("preset_missing:%s" % name)
    return _config(preset)


def preset_files(bundle):
    files = []
    for col in (bundle.filaments, bundle.prints, bundle.printers):
        for i in range(col.size()):
            p = col.preset(i)
            if p.is_user() and p.file:
                files.append(str(p.file))
                if len(files) > 20:
                    return files
    return files


def install(preset, bundle):
    """Write the preset into FilaForge's bundle in the signed-in account's folder and reload it."""
    reload_fn = orca.host.preset.bundles.reload_local
    last = None
    for account in writer.account_folders(data_dir(), preset_files(bundle)):
        folder = os.path.join(data_dir(), "user", account, "_local", writer.BUNDLE)
        try:
            return writer.Bundle(folder).save(preset, reload_fn)
        except RuntimeError as error:
            last = error
            if "does not exist" not in str(error) and "not found" not in str(error):
                raise
    raise last or RuntimeError("no OrcaSlicer user folder took the bundle")


def saved_presets(bundle):
    out = []
    for account in writer.account_folders(data_dir(), preset_files(bundle)):
        folder = os.path.join(data_dir(), "user", account, "_local", writer.BUNDLE)
        if os.path.isdir(folder):
            out += [dict(item, account=account) for item in writer.Bundle(folder).listing()]
    return out


# --- the work ----------------------------------------------------------------------------------------------------
def spawn(fn, *args):
    def run():
        try:
            fn(*args)
        except Exception:
            log.exception("background task failed")
    thread = threading.Thread(target=run, daemon=True, name="filaforge")
    thread.start()
    return thread


def fetcher():
    return Fetcher(os.path.join(own_dir(), "cache"))


def store():
    return measured.Store(own_dir())


def _identity(data):
    return research.identity_from(data or {})


class Tab:
    """The page's messages. post: posts a dict to the page (any thread)."""

    def __init__(self, post):
        self.post = post
        self.busy = set()

    def reply(self, cmd, **data):
        try:
            self.post(dict(data, cmd=cmd))
        except Exception:
            log.exception("post_message failed")

    def fail(self, cmd, error):
        text = str(error) or error.__class__.__name__
        log.warning("%s: %s", cmd, text)
        self.reply("error", where=cmd, message=text)

    def handle(self, data):
        if not isinstance(data, dict):
            return
        cmd = data.get("cmd")
        handler = getattr(self, "on_" + str(cmd), None)
        if handler is None:
            return
        try:
            handler(data)
        except Exception as error:
            log.error("%s failed:\n%s", cmd, traceback.format_exc())
            self.fail(cmd, error)

    # -- start
    def on_init(self, data):
        bundle = orca.host.preset_bundle()
        settings = read_settings()
        self.reply("init", version=VERSION, settings=settings, printer=printer_info(bundle),
                   processes=process_list(bundle), saved=saved_presets(bundle))

    def on_settings(self, data):
        changes = {}
        if "spoolman_url" in data:
            url = str(data.get("spoolman_url") or "").strip()
            if url:
                spoolman.base_url(url)
            changes["spoolman_url"] = url
        if data.get("language") in ("auto", "pl", "en"):
            changes["language"] = data["language"]
        if isinstance(data.get("online"), bool):
            changes["online"] = data["online"]
        self.reply("settings", settings=write_settings(changes))

    def on_open_url(self, data):
        url = str(data.get("url") or "")
        if url.startswith(("https://", "http://")):
            webbrowser.open(url)

    # -- Spoolman
    def on_spoolman_list(self, data):
        url = read_settings()["spoolman_url"]

        def work():
            try:
                self.reply("spoolman_list", filaments=spoolman.Client(url).filaments())
                remember_origin(spoolman.base_url(url))
            except Exception as error:
                self.fail("spoolman_list", error)
        spawn(work)

    # -- look-up
    def on_research(self, data):
        identity = _identity(data)
        bundle = orca.host.preset_bundle()
        printer = printer_info(bundle)
        presets = filament_presets(bundle, identity)
        process_config = {}
        try:
            process_config = preset_config(bundle.prints, printer["process"]) if printer["process"] else {}
        except ValueError:
            pass
        settings = read_settings()
        tested_store = store().get(identity, printer["name"])
        spool_id = data.get("spoolman_id")
        urls = [u for u in (data.get("sheet_urls") or []) if isinstance(u, str) and u.startswith(("https://",
                                                                                                    "http://"))]
        files = [f for f in (data.get("sheet_files") or []) if isinstance(f, dict)][:3]
        online = bool(settings.get("online", True)) and data.get("online") is not False

        def work():
            try:
                tested = merge.measured_observations(tested_store, "Your measurement (FilaForge)")
                if spool_id and settings["spoolman_url"]:
                    try:
                        filament = spoolman.Client(settings["spoolman_url"]).filament(spool_id)
                        have = {o.key for o in tested}
                        tested += [o for o in spoolman.tested(filament, printer["name"]) if o.key not in have]
                    except spoolman.SpoolmanError as error:
                        self.reply("progress", step="spoolman", message=str(error))
                result = research.run(identity, presets, fetcher(), process_config, tested, urls, files,
                                      progress=lambda step: self.reply("progress", step=step), use_online=online)
                for report in result["reports"]:
                    if report["source"] == "tds" and report["ok"]:
                        remember_origin(report["url"])
                result.update(printer=printer, measured=store().sections(identity),
                              preset_name=writer.with_marker("%s @%s" % (identity.label(), printer["name"])
                                                             if printer["name"] else identity.label()), licences=[ofd.LICENCE, spoolmandb.LICENCE])
                self.reply("result", **result)
            except Exception as error:
                log.error("research failed:\n%s", traceback.format_exc())
                self.fail("research", error)
        spawn(work)

    # -- tested values
    def on_save_measured(self, data):
        identity = _identity(data.get("identity"))
        section = str(data.get("section") or "*").strip() or "*"
        sections = store().set(identity, section, data.get("values") or {})
        self.reply("measured", sections=sections)

    # -- OrcaSlicer filament preset
    def on_make_preset(self, data):
        identity = _identity(data.get("identity"))
        bundle = orca.host.preset_bundle()
        printer = printer_info(bundle)
        name = writer.safe_name(writer.with_marker(str(data.get("name") or "%s @%s" % (identity.label(),
                                                                                         printer["name"]))))
        base_name = str(data.get("base") or "")
        base_config = preset_config(bundle.filaments, base_name) if base_name else {}
        values = {}
        for row in data.get("settings") or []:
            key, value = str(row.get("orca_key") or ""), str(row.get("value") or "").strip()
            if key and value and (key in writer.NUMERIC_KEYS or key == "filament_notes"):
                values[key] = value
        if "filament_type" not in base_config:
            values["filament_type"] = identity.material
        else:
            values.setdefault("filament_type", identity.material)
        preset = writer.filament_preset(name, base_config, values, printer["name"], identity,
                                        measured.identity_key(identity),
                                        "FilaForge %s from %s" % (VERSION, base_name or "defaults"))
        path = install(preset, bundle)
        self.reply("saved", kind="filament", name=name, path=path, saved=saved_presets(bundle))

    def on_remove_preset(self, data):
        bundle = orca.host.preset_bundle()
        name = str(data.get("name") or "")
        for account in writer.account_folders(data_dir(), preset_files(bundle)):
            folder = os.path.join(data_dir(), "user", account, "_local", writer.BUNDLE)
            if os.path.isdir(folder) and writer.Bundle(folder).remove(name, orca.host.preset.bundles.reload_local):
                break
        self.reply("saved", kind="removed", name=name, saved=saved_presets(bundle))

    # -- Spoolman write
    def on_spoolman_plan(self, data):
        self._spoolman(data, write=False)

    def on_spoolman_write(self, data):
        self._spoolman(data, write=True)

    def _spoolman(self, data, write):
        url = read_settings()["spoolman_url"]
        fid = int(data.get("spoolman_id"))
        section = str(data.get("section") or "*")
        values = {str(r["orca_key"]): str(r["value"]) for r in data.get("settings") or []
                  if r.get("orca_key") and str(r.get("value", "")).strip()}

        def work():
            try:
                client = spoolman.Client(url)
                if write:
                    added, kept = client.write_settings(fid, section, values)
                    self.reply("spoolman_written", added=added, kept=kept, section=section, id=fid)
                else:
                    current = spoolman.settings_of(client.filament(fid))
                    if current is None:
                        raise spoolman.SpoolmanError("orca_settings_invalid")
                    _, added, kept = spoolman.add_missing(current, section, values)
                    existing = {}
                    for _, sec in spoolman.sections_for(current, section if section != "*" else ""):
                        existing.update(sec)
                    self.reply("spoolman_plan", added=added, kept=kept, section=section, id=fid,
                               existing={k: existing.get(k) for k in kept}, values=values)
            except Exception as error:
                self.fail("spoolman_write" if write else "spoolman_plan", error)
        spawn(work)

    # -- process
    def on_process_plan(self, data):
        bundle = orca.host.preset_bundle()
        printer = printer_info(bundle)
        base_name = str(data.get("process") or printer["process"])
        config = preset_config(bundle.prints, base_name)
        mvs = float(data.get("max_volumetric_speed") or 0) or None
        identity = _identity(data.get("identity"))
        changes = process.fit(config, printer["nozzle"], mvs, identity.material, identity.product)
        name = writer.with_marker(process.process_name(base_name, identity.label()))
        self.reply("process_plan", base=base_name, name=writer.safe_name(name), changes=changes,
                   layer_height=_clean(config.get("layer_height")))

    def on_process_save(self, data):
        bundle = orca.host.preset_bundle()
        printer = printer_info(bundle)
        base_name = str(data.get("base") or "")
        preset_config(bundle.prints, base_name)   # it must exist
        changes = [c for c in data.get("changes") or [] if c.get("key") in process.SPEEDS and
                   isinstance(c.get("new"), (int, float))]
        name = writer.safe_name(writer.with_marker(str(data.get("name") or base_name)))
        preset = process.preset(base_name, name, [printer["name"]] if printer["name"] else [], changes)
        path = install(preset, bundle)
        self.reply("saved", kind="process", name=name, path=path, saved=saved_presets(bundle))

    def on_saved(self, data):
        self.reply("saved", kind="list", saved=saved_presets(orca.host.preset_bundle()))
