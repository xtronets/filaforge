"""FilaForge inside OrcaSlicer: the tab, reading presets (UI thread), and the work threads.

OrcaSlicer's preset API is only touched on its UI thread (on_message runs there): the presets a look-up needs are
read first, as plain data, then the network work runs on a daemon thread and posts its result to the page.
"""

import json
import logging
import os
import re
import threading
import traceback
import webbrowser

import orca

from .core import files, habits, history, measured, merge, process, research, review, spoolman, writer
from .core.model import Identity
from .core.net import Fetcher
from .core.sources import ofd, orca_presets, spoolmandb
from .core.text import brand_key, fold, split_material, tokens

NAME = "FilaForge"
VERSION = "0.6.0"
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


DEFAULTS = {"spoolman_url": "", "language": "auto", "online": True, "origins": [], "habits": {}, "enclosed_printers": []}


def read_settings():
    try:
        with open(os.path.join(own_dir(), "settings.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in data.items() if k in DEFAULTS})
    out["habits"] = habits.clean_overrides(out.get("habits"))
    if not isinstance(out.get("enclosed_printers"), list):
        out["enclosed_printers"] = []
    if not out["spoolman_url"]:
        out["spoolman_url"] = spooler_spoolman_url()
    return out


def write_settings(changes):
    current = read_settings()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS})
    files.write_json(os.path.join(own_dir(), "settings.json"), current, indent=1)
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
        # its own folder; the user preset folders its bundle lives in; Spooler's settings (the Spoolman address)
        files = dict(fs_readwrite=[own_dir(), os.path.join(data_dir(), "user")],
                     fs_read=[os.path.join(data_dir(), "orca_plugins", "config.json")])
        try:
            request(threading=True, network_http=network, **files)
        except TypeError:       # an OrcaSlicer without some of these
            try:
                request(threading=True, fs_readwrite=files["fs_readwrite"], network_http=network)
            except TypeError:
                request(threading=True, fs_readwrite=files["fs_readwrite"])
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
            "nozzle": nozzle, "process": getattr(process_preset, "name", "") or "",
            "chamber_control": _clean(cfg("support_chamber_temp_control")).lower() in ("1", "true")}


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


def user_filaments(bundle):
    """The filament presets the user already has for the selected printer (their own and other plugins' local
    ones, not FilaForge's), with what each one says about the filament."""
    col = bundle.filaments
    selected = getattr(_selected(col), "name", "")
    out = []
    for i in range(col.size()):
        p = col.preset(i)
        name = str(p.name)
        own_bundle = name.startswith("_local/") and not name.startswith("_local/" + writer.BUNDLE + "/")
        if (getattr(p, "is_system", True) and not own_bundle) or name.startswith("_local/" + writer.BUNDLE + "/"):
            continue
        if getattr(p, "is_default", False) or not getattr(p, "is_compatible", True):
            continue
        item = {"name": name, "vendor": _clean(p.config_value("filament_vendor")),
                "type": _clean(p.config_value("filament_type")),
                "config": {k: str(p.config_value(k) or "") for k in ("default_filament_colour", "filament_colour")}}
        ident = orca_presets.identity_of(item)
        out.append({"name": name, "label": orca_presets.bare_name(name), "vendor": item["vendor"],
                    "type": item["type"], "colour": ident["colour"], "identity": ident,
                    "selected": name == selected})
    out.sort(key=lambda x: fold(x["label"]))
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


def is_system(collection, name):
    preset = collection.find_preset(name)
    return bool(preset is not None and getattr(preset, "is_system", False))


def repair_processes(bundle):
    """Processes saved before 0.4.3 over a user's own process inherit it, and OrcaSlicer drops them on start (it
    loads plugin bundles before user presets). Rewrite them standalone while the parent is loaded. Returns names."""
    fixed = []
    for account in writer.account_folders(data_dir(), preset_files(bundle)):
        folder = os.path.join(data_dir(), "user", account, "_local", writer.BUNDLE)
        if not os.path.isdir(folder):
            continue
        for item in writer.Bundle(folder).listing():
            if item.get("kind") != "process" or not item.get("exists"):
                continue
            try:
                with open(item["file"], encoding="utf-8") as f:
                    data = json.load(f)
                parent = str(data.get("inherits") or "")
                if not parent or is_system(bundle.prints, parent) or bundle.prints.find_preset(parent) is None:
                    continue
                base = preset_config(bundle.prints, parent)
                changes = [{"key": k, "new": v} for k, v in data.items() if k in process.SPEEDS or k in process.ACCELERATIONS]
                new = process.preset(parent, data["name"], data.get("compatible_printers") or [], [], base)
                new.update({c["key"]: c["new"] for c in changes})
                ff_bundle(folder).save(new, orca.host.preset.bundles.reload_local,
                                       {k: v for k, v in item.items() if k not in ("name", "exists", "file", "kind")},
                                       "repaired")
                fixed.append(data["name"])
            except Exception:
                log.exception("repairing %s", item.get("name"))
    if fixed:
        log.info("repaired processes over user presets: %s", fixed)
    return fixed


def machine_limits(bundle):
    """The printer's max extruding and travel acceleration and max XY speed ({} parts it doesn't say)."""
    cfg = bundle.full_config_value
    out = {}
    for key, name in (("accel", "machine_max_acceleration_extruding"), ("travel_accel", "machine_max_acceleration_travel")):
        try:
            value = float(_clean(cfg(name)))
            if value > 0:
                out[key] = value
        except (TypeError, ValueError):
            pass
    speeds = []
    for name in ("machine_max_speed_x", "machine_max_speed_y"):
        try:
            speeds.append(float(_clean(cfg(name))))
        except (TypeError, ValueError):
            pass
    if speeds and min(speeds) > 0:
        out["speed"] = min(speeds)
    return out


def plate_sizes():
    """[(x, y)] mm of every object on the plate, from OrcaSlicer's model; [] when it can't be read."""
    sizes = []
    try:
        for obj in orca.host.model().objects():
            box = obj.bounding_box()
            if getattr(box, "defined", True):
                size = list(box.size)
                sizes.append((float(size[0]), float(size[1])))
    except Exception as error:
        log.info("plate sizes: %s", error)
    return sizes


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


def unique_name(name, printer, bundle):
    """(name, renamed): OrcaSlicer needs every preset name once, and a preset is made for one printer; when the
    name is already FilaForge's preset for another printer, this printer's model goes after it."""
    taken = {s["name"]: s.get("printer") for s in saved_presets(bundle)}
    if name in taken and taken[name] and taken[name] != printer["name"]:
        return writer.safe_name("%s (%s)" % (name, printer["model"] or printer["name"])), True
    return name, False


def ff_bundle(folder):
    return writer.Bundle(folder, history.History(os.path.join(own_dir(), "history")))


def bundle_folders(bundle):
    """[(account, folder)] of FilaForge's bundle, the signed-in account's first."""
    return [(a, os.path.join(data_dir(), "user", a, "_local", writer.BUNDLE))
            for a in writer.account_folders(data_dir(), preset_files(bundle))]


def install(preset, bundle, printer="", meta=None, reason="replaced"):
    """Write the preset into FilaForge's bundle in the signed-in account's folder and reload it. A preset of that
    name already in another account's folder is replaced there."""
    reload_fn = orca.host.preset.bundles.reload_local
    meta = dict(meta or {}, printer=printer)
    folders = bundle_folders(bundle)
    have = [f for _, f in folders if os.path.isdir(f) and ff_bundle(f).info(preset["name"])]
    last = None
    for folder in have[:1] or [f for _, f in folders]:
        try:
            return ff_bundle(folder).save(preset, reload_fn, meta, reason)
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
            out += [dict(item, account=account) for item in ff_bundle(folder).listing()]
    return out


def find_saved(bundle, name):
    """(bundle object, state entry) of one of FilaForge's presets, or (None, None)."""
    for _, folder in bundle_folders(bundle):
        if os.path.isdir(folder):
            b = ff_bundle(folder)
            info = b.info(name)
            if info is not None:
                return b, info
    return None, None


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def loaded_preset(collection, name):
    """OrcaSlicer's loaded preset for one of FilaForge's ("_local/filaforge/<name>"), or None."""
    preset = collection.find_preset("_local/%s/%s" % (writer.BUNDLE, name))
    if preset is not None:
        return preset
    for i in range(collection.size()):
        p = collection.preset(i)
        if str(p.name).endswith("/" + name) and str(p.name).startswith("_local/" + writer.BUNDLE + "/"):
            return p
    return None


def printer_facts(bundle, name):
    """(printer model, nozzle) of a printer preset by name; ("", None) when it isn't there."""
    p = bundle.printers.find_preset(name) if name else None
    if p is None:
        return "", None
    try:
        nozzle = float(_clean(p.config_value("nozzle_diameter")) or 0) or None
    except ValueError:
        nozzle = None
    return _clean(p.config_value("printer_model")) or name, nozzle


def saved_identity(info, config):
    ident = info.get("identity")
    if isinstance(ident, dict) and ident.get("brand"):
        return research.identity_from(ident)
    item = {"name": config.get("name") or "", "vendor": _clean(config.get("filament_vendor")),
            "type": _clean(config.get("filament_type"))}
    return research.identity_from(orca_presets.identity_of(dict(item, config=config)))


def review_inputs(bundle):
    """(FilaForge's presets, the user's other filament presets) as review.run takes them."""
    col = bundle.filaments
    users = []
    for i in range(col.size()):
        p = col.preset(i)
        name = str(p.name)
        if getattr(p, "is_system", True) or getattr(p, "is_default", False) or \
                name.startswith("_local/" + writer.BUNDLE + "/"):
            continue
        cfg = _config(p)
        parent = _clean(cfg.get("inherits"))
        users.append({"name": name, "label": orca_presets.bare_name(name) or name, "config": cfg, "parent": parent,
                      "parent_missing": bool(parent) and col.find_preset(parent) is None})
    shelf = store()
    items = []
    for entry in saved_presets(bundle):
        kind = entry.get("kind", "filament")
        p = loaded_preset(col if kind == "filament" else bundle.prints, entry["name"])
        cfg = _config(p) if p is not None else {}
        item = {"name": entry["name"], "label": entry["name"], "kind": kind, "exists": entry.get("exists", True),
                "loaded": p is not None, "config": cfg, "meta": entry, "derived": [], "measured": {},
                "base_hash_now": ""}
        if kind == "filament" and p is not None:
            base = entry.get("base")
            if base:
                bp = col.find_preset(base)
                item["base_hash_now"] = review.config_hash(_config(bp)) if bp is not None else None
            model, nozzle = printer_facts(bundle, entry.get("printer", ""))
            ident = saved_identity(entry, cfg)
            item["measured"] = review.measured_updates(shelf.get(ident, model, nozzle))
            fid = _clean(cfg.get("filament_id"))
            item["derived"] = [{"name": u["name"], "config": u["config"]} for u in users
                               if (fid and _clean(u["config"].get("filament_id")) == fid) or
                               (u["parent"] and u["parent"].endswith(entry["name"]))]
        items.append(item)
    return items, users


def review_issues(bundle):
    """review.run with what the page needs to act: the filament's identity, whether Spoolman can take a value."""
    items, users = review_inputs(bundle)
    by_name = {i["name"]: i for i in items}
    spoolman_on = bool(read_settings()["spoolman_url"])
    issues = review.run(items, users)
    for issue in issues:
        item = by_name.get(issue["preset"]) if issue["ff"] else None
        if item is not None and item.get("config"):
            ident = saved_identity(item["meta"], item["config"])
            issue["identity"] = {"brand": ident.brand, "material": ident.material, "product": ident.product,
                                 "colour": ident.colour}
            issue["spoolman"] = spoolman_on and bool(item["meta"].get("spoolman_id"))
    return issues


def preset_meta(preset, base_name, base_config, values, identity, spoolman_id=None):
    """What FilaForge keeps about a filament preset it wrote, for the review and the sync."""
    meta = {"base": base_name, "base_hash": review.config_hash(base_config) if base_name else "",
            "set_keys": sorted(values), "written": review.tuned(preset), "ack": {},
            "identity": {"brand": identity.brand, "material": identity.material, "product": identity.product,
                         "colour": identity.colour}}
    if spoolman_id:
        meta["spoolman_id"] = int(spoolman_id)
    return meta


def habits_info():
    """What the page needs to show and edit the material table."""
    return {"classes": list(habits.CLASSES), "defaults": habits.DEFAULTS,
            "fields": {k: {"kind": v[0], "min": v[1], "max": v[2]} for k, v in habits.FIELDS.items()}}


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
        if not getattr(Tab, "_repaired", False):
            Tab._repaired = True
            repair_processes(bundle)
        settings = read_settings()
        self.reply("init", version=VERSION, settings=settings, printer=printer_info(bundle),
                   processes=process_list(bundle), saved=saved_presets(bundle), habits=habits_info())
        try:
            self.on_review({"quiet": True})
        except Exception:
            log.exception("review on start")

    def on_orca_filaments(self, data):
        self.reply("orca_filaments", items=user_filaments(orca.host.preset_bundle()))

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
        if isinstance(data.get("habits"), dict):
            changes["habits"] = habits.clean_overrides(data["habits"])
        if isinstance(data.get("enclosed"), dict):
            name = str(data["enclosed"].get("printer") or "").strip()
            have = [p for p in read_settings()["enclosed_printers"] if p != name]
            changes["enclosed_printers"] = have + [name] if name and data["enclosed"].get("on") else have
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
        own = None
        own_name = data.get("orca_preset")
        if isinstance(own_name, str) and own_name:
            config = preset_config(bundle.filaments, own_name)
            own = {"name": own_name, "vendor": _clean(config.get("filament_vendor")),
                   "type": _clean(config.get("filament_type")), "is_system": False, "compatible": True,
                   "config": config}
            presets = [p for p in presets if p["name"] != own_name]
        process_config = {}
        try:
            process_config = preset_config(bundle.prints, printer["process"]) if printer["process"] else {}
        except ValueError:
            pass
        settings = read_settings()
        tested_store = store().get(identity, printer["name"], printer["nozzle"])
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
                                      progress=lambda step: self.reply("progress", step=step), use_online=online,
                                      own=own, table=habits.table(settings["habits"]), printer=printer,
                                      enclosed_printers=settings["enclosed_printers"])
                for report in result["reports"]:
                    if report["source"] == "tds" and report["ok"]:
                        remember_origin(report["url"])
                result.update(printer=printer, measured=store().sections(identity),
                              preset_name=writer.with_marker(orca_presets.bare_name(own["name"]) if own
                                                             else identity.label()), licences=[ofd.LICENCE, spoolmandb.LICENCE])
                self.reply("result", **result)
            except Exception as error:
                log.error("research failed:\n%s", traceback.format_exc())
                self.fail("research", error)
        spawn(work)

    # -- tested values
    def on_save_measured(self, data):
        identity = _identity(data.get("identity"))
        section = str(data.get("section") or "*").strip() or "*"
        nozzle = measured.nozzle_tag(data.get("nozzle")) or printer_info(orca.host.preset_bundle())["nozzle"]
        sections = store().set(identity, section, data.get("values") or {}, nozzle)
        self.reply("measured", sections=sections, nozzle=measured.nozzle_tag(nozzle))

    # -- OrcaSlicer filament preset
    def on_make_preset(self, data):
        identity = _identity(data.get("identity"))
        bundle = orca.host.preset_bundle()
        printer = printer_info(bundle)
        name = writer.safe_name(writer.with_marker(str(data.get("name") or identity.label())))
        name, renamed = unique_name(name, printer, bundle)
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
        spool = data.get("spoolman_id")
        meta = preset_meta(preset, base_name, base_config, values, identity,
                           spool if isinstance(spool, int) and not isinstance(spool, bool) else None)
        path = install(preset, bundle, printer["name"], meta)
        self.reply("saved", kind="filament", name=name, path=path, renamed=renamed, saved=saved_presets(bundle))

    def on_remove_preset(self, data):
        bundle = orca.host.preset_bundle()
        name = str(data.get("name") or "")
        target, _ = find_saved(bundle, name)
        if target is not None:
            target.remove(name, orca.host.preset.bundles.reload_local)
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

        overwrite = [str(k) for k in data.get("overwrite") or []]

        def work():
            try:
                client = spoolman.Client(url)
                if write:
                    result = client.write_settings(fid, section, values, overwrite)
                    self.reply("spoolman_written", section=section, id=fid, **result)
                else:
                    current = spoolman.settings_of(client.filament(fid))
                    if current is None:
                        raise spoolman.SpoolmanError("orca_settings_invalid")
                    result = spoolman.merge_settings(current, section, values)
                    result.pop("data")
                    self.reply("spoolman_plan", section=section, id=fid, values=values, **result)
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
        variant = str(data.get("variant") or "standard")
        sizes = plate_sizes() if variant == "plate" else []
        used = process.variant_for(sizes) if variant == "plate" else (variant if variant in process.VARIANTS
                                                                      else "standard")
        settings = read_settings()
        table = habits.table(settings["habits"])
        cls = habits.material_class(identity.material, identity.product)
        changes = process.fit(config, printer["nozzle"], mvs, identity.material, identity.product,
                              machine_limits(bundle), used, table)
        label = identity.label() + ("" if used == "standard" else " small parts")
        name = writer.with_marker(process.process_name(base_name, label))
        self.reply("process_plan", base=base_name, name=writer.safe_name(name), changes=changes, variant=used,
                   asked=variant, sizes=[[round(x), round(y)] for x, y in sizes],
                   layer_height=_clean(config.get("layer_height")), material_class=cls,
                   warnings=habits.warnings(cls, table[cls], printer, settings["enclosed_printers"]))

    def on_process_save(self, data):
        bundle = orca.host.preset_bundle()
        printer = printer_info(bundle)
        base_name = str(data.get("base") or "")
        base_config = preset_config(bundle.prints, base_name)   # it must exist
        own_base = not is_system(bundle.prints, base_name)
        allowed = set(process.SPEEDS) | set(process.ACCELERATIONS)
        changes = [c for c in data.get("changes") or [] if c.get("key") in allowed and
                   isinstance(c.get("new"), (int, float))]
        name = writer.safe_name(writer.with_marker(str(data.get("name") or base_name)))
        name, renamed = unique_name(name, printer, bundle)
        preset = process.preset(base_name, name, [printer["name"]] if printer["name"] else [], changes,
                                base_config if own_base else None)
        path = install(preset, bundle, printer["name"])
        self.reply("saved", kind="process", name=name, path=path, renamed=renamed, saved=saved_presets(bundle))

    # -- the maker's products, to pick from
    def on_products(self, data):
        brand = str(data.get("brand") or "").strip()
        if len(brand) < 2 or not read_settings().get("online", True):
            self.reply("products", brand=brand, products=[])
            return

        def work():
            try:
                self.reply("products", brand=brand, products=ofd.products(fetcher(), brand))
            except Exception as error:
                log.info("products of %s: %s", brand, error)
                self.reply("products", brand=brand, products=[])
        spawn(work)

    def on_saved(self, data):
        self.reply("saved", kind="list", saved=saved_presets(orca.host.preset_bundle()))

    # -- the preset review, and what changed in OrcaSlicer
    def on_review(self, data):
        self.reply("review", issues=review_issues(orca.host.preset_bundle()), quiet=bool(data.get("quiet")))

    def _issue(self, bundle, data):
        items, users = review_inputs(bundle)
        for issue in review.run(items, users):
            if issue["code"] == data.get("code") and issue["preset"] == data.get("preset") and \
                    issue["details"].get("source", issue["preset"]) == data.get("source", issue["preset"]):
                item = next((i for i in items if i["name"] == issue["preset"]), None)
                return issue, item
        raise ValueError("review_gone")

    def on_review_fix(self, data):
        bundle = orca.host.preset_bundle()
        fix = str(data.get("fix") or "")
        issue, item = self._issue(bundle, data)
        if fix not in issue["fixes"]:
            raise ValueError("review_gone")
        name = issue["preset"]
        target, info = find_saved(bundle, name) if issue["ff"] else (None, None)
        reload_fn = orca.host.preset.bundles.reload_local
        note = ""
        if fix == "forget":
            target.forget(name)
        elif fix == "remove":
            target.remove(name, reload_fn)
        elif fix == "repair":
            repair_processes(bundle)
        elif fix in ("to_measured", "keep"):
            changes = issue["details"]["changes"]
            source = issue["details"]["source"]
            if fix == "to_measured":
                note = self._to_measured(bundle, info, item, changes, bool(data.get("spoolman")))
            now = {c["orca_key"]: c["now"] for c in changes}
            if source == name:
                target.update_meta(name, {"written": dict(info.get("written") or {}, **now)})
            else:
                ack = dict(info.get("ack") or {})
                ack[source] = dict(ack.get(source) or info.get("written") or {}, **now)
                target.update_meta(name, {"ack": ack})
        elif fix == "apply_measured":
            preset = read_json(info["file"])
            updates = {c["orca_key"]: c["now"] for c in issue["details"]["changes"]}
            updates.update({k: v for k, v in review.measured_updates({c["key"]: c["now"] for c in
                                                                      issue["details"]["changes"]}).items()
                            if k not in review.SYNC_KEYS})
            new = review.apply_values(preset, updates)
            target.save(new, reload_fn, dict(self._meta(info), written=review.tuned(new)), "measured")
        elif fix == "rebase":
            preset = read_json(info["file"])
            base_config = preset_config(bundle.filaments, info["base"])
            values = {}
            for key in info.get("set_keys") or []:
                value = preset.get(key)
                values[key] = value[0] if isinstance(value, list) and value else str(value or "")
            ident = saved_identity(info, item["config"])
            new = writer.filament_preset(name, base_config, values, info.get("printer", ""), ident,
                                         measured.identity_key(ident), preset.get("description", ""))
            new["filament_id"] = preset.get("filament_id", new["filament_id"])
            target.save(new, reload_fn, dict(self._meta(info), base_hash=review.config_hash(base_config),
                                             written=review.tuned(new)), "rebased")
        else:
            raise ValueError("review_gone")
        self.reply("review", issues=review_issues(bundle), fixed=fix, preset=name, note=note,
                   saved=saved_presets(bundle))

    @staticmethod
    def _meta(info):
        return {k: v for k, v in (info or {}).items() if k not in ("file", "kind")}

    def _to_measured(self, bundle, info, item, changes, to_spoolman):
        """Keep the changed values as the user's measurement (and in Spoolman); returns a note for the page."""
        model, nozzle = printer_facts(bundle, info.get("printer", ""))
        ident = saved_identity(info, item["config"])
        section = model or "*"
        shelf = store()
        values = measured.section_values(shelf.sections(ident).get(section), nozzle)
        values.update({c["key"]: c["now"] for c in changes})
        shelf.set(ident, section, values, nozzle)
        if not (to_spoolman and info.get("spoolman_id")):
            return "measured"
        url = read_settings()["spoolman_url"]
        if not url:
            return "measured"
        orca_values = {c["orca_key"]: "%g" % c["now"] for c in changes}
        try:
            spoolman.Client(url).write_settings(int(info["spoolman_id"]), section, orca_values, list(orca_values))
        except spoolman.SpoolmanError as error:
            log.warning("spoolman sync: %s", error)
            return "measured_no_spoolman"
        return "measured_spoolman"

    # -- earlier versions
    def on_history(self, data):
        bundle = orca.host.preset_bundle()
        names = {s["name"] for s in saved_presets(bundle)}
        entries = history.History(os.path.join(own_dir(), "history")).entries()
        self.reply("history", entries=[dict(e, current=e["name"] in names) for e in entries])

    def on_history_diff(self, data):
        entry = history.History(os.path.join(own_dir(), "history")).get(data.get("id"))
        if entry is None:
            raise ValueError("history_gone")
        _, info = find_saved(orca.host.preset_bundle(), entry["name"])
        current = {}
        if info and os.path.exists(info.get("file", "")):
            current = read_json(info["file"])
        self.reply("history_diff", id=data.get("id"), current=bool(current),
                   changes=history.differences(entry["preset"], current))

    def on_history_restore(self, data):
        bundle = orca.host.preset_bundle()
        entry = history.History(os.path.join(own_dir(), "history")).get(data.get("id"))
        if entry is None:
            raise ValueError("history_gone")
        meta = dict(entry.get("meta") or {})
        printer = meta.pop("printer", "")
        path = install(entry["preset"], bundle, printer, meta, "restored")
        self.reply("saved", kind="restored", name=entry["name"], path=path, saved=saved_presets(bundle))
