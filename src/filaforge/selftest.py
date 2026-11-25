"""FilaForge's self-test inside a real OrcaSlicer: the plugin API it relies on, then the tab's flow against the
real preset bundle. It runs only when FILAFORGE_SELFTEST names a report file (the CI job sets it); users never
start it. The report is JSON: {"ok": bool, "steps": [{"name", "ok", "detail"}], ...}.
"""

import json
import os
import threading
import time
import traceback

ENV = "FILAFORGE_SELFTEST"
WAIT_PRESETS = 180      # seconds: a cold OrcaSlicer loads its system presets for a while
WAIT_REPLY = 60


def wanted():
    return bool(os.environ.get(ENV))


def start():
    """Run the self-test on a thread of its own (OrcaSlicer is still starting when the plugin registers)."""
    if not wanted():
        return
    threading.Thread(target=_run, daemon=True, name="filaforge-selftest").start()


class _Report:
    def __init__(self, path):
        self.path = path
        self.steps = []
        self.info = {}

    def step(self, name, fn):
        started = time.time()
        try:
            detail = fn()
            self.steps.append({"name": name, "ok": True, "detail": _short(detail),
                               "seconds": round(time.time() - started, 2)})
            return detail
        except Exception as error:
            self.steps.append({"name": name, "ok": False, "detail": "%s: %s" % (type(error).__name__, error),
                               "trace": traceback.format_exc()[-2000:], "seconds": round(time.time() - started, 2)})
            return None

    def write(self):
        data = dict(self.info, ok=bool(self.steps) and all(s["ok"] for s in self.steps), steps=self.steps)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        return data


def _short(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 400 else text[:400] + "…"


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


class _Page:
    """Stands in for the web page: collects what the tab posts and waits for a reply."""

    def __init__(self):
        self.messages = []
        self.cond = threading.Condition()

    def post(self, message):
        with self.cond:
            self.messages.append(message)
            self.cond.notify_all()

    def ask(self, tab, cmd, reply, **data):
        with self.cond:
            start = len(self.messages)
        tab.handle(dict(data, cmd=cmd))
        deadline = time.time() + WAIT_REPLY
        with self.cond:
            while True:
                new = self.messages[start:]
                for m in new:
                    if m.get("cmd") == reply:
                        return m
                    if m.get("cmd") == "error":
                        raise AssertionError("%s -> error: %s" % (cmd, m.get("message")))
                left = deadline - time.time()
                if left <= 0:
                    raise AssertionError("%s: no %s in %ss (got %s)" % (cmd, reply, WAIT_REPLY,
                                                                        [m.get("cmd") for m in new]))
                self.cond.wait(left)


def _run():
    import orca
    from . import plugin

    report = _Report(os.environ[ENV])
    report.info.update(filaforge=plugin.VERSION, orca_module=sorted(n for n in dir(orca) if not n.startswith("_")))
    try:
        _api(report, orca)
        bundle = report.step("presets loaded", lambda: _wait_presets(orca))
        if bundle is not None:
            _flow(report, orca, plugin)
    except Exception:
        report.steps.append({"name": "self-test", "ok": False, "detail": traceback.format_exc()[-2000:]})
    data = report.write()
    plugin.log.info("self-test %s: %d/%d steps", "passed" if data["ok"] else "FAILED",
                    sum(s["ok"] for s in data["steps"]), len(data["steps"]))


def _api(report, orca):
    def names():
        need = ["plugin", "base", "register_capability", "PluginType", "pages", "host"]
        missing = [n for n in need if not hasattr(orca, n)]
        _check(not missing, "orca lacks %s" % missing)
        _check(hasattr(orca.PluginType, "Pages"), "orca.PluginType lacks Pages")
        base = orca.pages.PagesPluginCapabilityBase
        _check(hasattr(base, "post_message"), "PagesPluginCapabilityBase lacks post_message")
        host = [n for n in ("preset_bundle", "model") if not hasattr(orca.host, n)]
        _check(not host, "orca.host lacks %s" % host)
        _check(hasattr(orca.host, "preset") and hasattr(orca.host.preset, "bundles")
               and hasattr(orca.host.preset.bundles, "reload_local"), "orca.host.preset.bundles.reload_local missing")
        return "request_permissions: %s" % hasattr(orca, "request_permissions")
    report.step("plugin API names", names)


def _wait_presets(orca):
    deadline = time.time() + WAIT_PRESETS
    last = ""
    while time.time() < deadline:
        try:
            bundle = orca.host.preset_bundle()
            printers, filaments = bundle.printers.size(), bundle.filaments.size()
            selected = bundle.printers.get_selected_preset()
            if printers and filaments and selected is not None and getattr(selected, "name", ""):
                return {"printers": printers, "filaments": filaments, "prints": bundle.prints.size(),
                        "printer": selected.name}
            last = "printers %s, filaments %s" % (printers, filaments)
        except Exception as error:
            last = "%s: %s" % (type(error).__name__, error)
        time.sleep(2)
    raise AssertionError("presets not loaded in %ss (%s)" % (WAIT_PRESETS, last))


def _flow(report, orca, plugin):
    bundle = orca.host.preset_bundle()

    def collections():
        out = {}
        for name in ("printers", "prints", "filaments"):
            col = getattr(bundle, name)
            p = col.preset(0)
            _check(isinstance(p.name, str) and p.name, "%s.preset(0) has no name" % name)
            _check(col.find_preset(p.name) is not None, "%s.find_preset(%r) is None" % (name, p.name))
            out[name] = p.name
        f = bundle.filaments.preset(0)
        out["filament_type"] = str(f.config_value("filament_type"))
        out["nozzle_diameter"] = str(bundle.full_config_value("nozzle_diameter"))
        out["is_system"] = getattr(f, "is_system", "missing")
        return out
    report.step("preset collections", collections)
    report.step("printer info", lambda: _printer(plugin, bundle))
    report.step("plate sizes", plugin.plate_sizes)

    page = _Page()
    tab = plugin.Tab(page.post)
    plugin.write_settings({"online": False})     # offline: the test must not depend on the open databases

    init = report.step("init", lambda: _init(page.ask(tab, "init", "init")))
    report.step("orca_filaments", lambda: len(page.ask(tab, "orca_filaments", "orca_filaments")["items"]))
    # a filament OrcaSlicer has a maker's preset for: the research must find it and give settings
    result = report.step("research (offline)", lambda: _research(page.ask(tab, "research", "result", brand="Sunlu",
                                                                          material="PLA", product="PLA+")))
    if result:
        name = "FilaForge selftest PLA"
        saved = report.step("make_preset", lambda: _make(orca, page, tab, result, name))
        if saved:
            report.step("review of a fresh preset", lambda: _review(page, tab, saved))
            report.step("remove_preset", lambda: _remove(orca, page, tab, saved))
            report.step("history restore", lambda: _restore(orca, page, tab, saved))
    if init and init.get("processes"):
        report.step("process plan/save/remove", lambda: _process(orca, page, tab, init, result))
    report.step("get_ui / get_icon", lambda: _page(orca))


def _printer(plugin, bundle):
    info = plugin.printer_info(bundle)
    _check(info["name"], "no printer name")
    _check(info["nozzle"] > 0, "no nozzle diameter")
    return info


def _init(m):
    _check(m["printer"]["name"], "init: no printer")
    _check(isinstance(m["processes"], list) and m["processes"], "init: no processes")
    return {"printer": m["printer"], "processes": m["processes"][:3]}


def _research(m):
    _check(m.get("base"), "research: no base preset")
    _check(m["base"].get("kind") == "vendor", "research: no maker's preset, base %s" % m["base"])
    _check(m.get("settings"), "research: no settings")
    return {"base": m["base"], "settings": len(m["settings"]), "identity": m["identity"],
            "rows": [s for s in m["settings"] if s.get("apply")]}


def _find(collection, name, present=True, wait=20):
    deadline = time.time() + wait
    while True:
        found = any(collection.preset(i).name.endswith(name) for i in range(collection.size()))
        if found == present or time.time() > deadline:
            return found
        time.sleep(0.5)


def _make(orca, page, tab, result, name):
    m = page.ask(tab, "make_preset", "saved", identity=result["identity"], base=result["base"]["name"],
                 settings=result["rows"], name=name)
    _check(os.path.isfile(m["path"]), "preset file missing: %s" % m["path"])
    _check(_find(orca.host.preset_bundle().filaments, m["name"]), "OrcaSlicer did not load %s" % m["name"])
    return m["name"]


def _remove(orca, page, tab, name):
    page.ask(tab, "remove_preset", "saved", name=name)
    _check(not _find(orca.host.preset_bundle().filaments, name, present=False), "%s still loaded" % name)
    return name


def _review(page, tab, name):
    """A preset FilaForge just wrote, as OrcaSlicer loaded it: nothing changed, nothing to fix (OrcaSlicer must
    read back the values FilaForge wrote, however it formats them)."""
    m = page.ask(tab, "review", "review")
    mine = [i for i in m["issues"] if i["preset"] == name and i["code"] in ("orca_changed", "not_loaded",
                                                                           "measured_newer", "file_missing")]
    _check(not mine, "review of a fresh preset: %s" % mine)
    return {"issues": len(m["issues"])}


def _restore(orca, page, tab, name):
    entries = [e for e in page.ask(tab, "history", "history")["entries"] if e["name"] == name]
    _check(entries and entries[0]["reason"] == "removed", "no history entry for the removed preset")
    page.ask(tab, "history_restore", "saved", id=entries[0]["id"])
    _check(_find(orca.host.preset_bundle().filaments, name), "OrcaSlicer did not load the restored %s" % name)
    _remove(orca, page, tab, name)
    return {"versions": len(entries)}


def _process(orca, page, tab, init, result):
    identity = (result or {}).get("identity") or {"brand": "Generic", "material": "PLA", "product": ""}
    plan = page.ask(tab, "process_plan", "process_plan", process=init["processes"][0], max_volumetric_speed=12,
                    identity=identity)
    saved = page.ask(tab, "process_save", "saved", base=plan["base"], name=plan["name"], changes=plan["changes"])
    _check(_find(orca.host.preset_bundle().prints, saved["name"]), "OrcaSlicer did not load %s" % saved["name"])
    page.ask(tab, "remove_preset", "saved", name=saved["name"])
    return {"name": saved["name"], "changes": len(plan["changes"])}


def _page(orca):
    import filaforge
    cls = filaforge.FilaForgePage
    page = cls()
    html = page.get_ui()
    _check("/*BOOT*/null" not in html and "{{img:" not in html, "page placeholders left")
    icon = page.get_icon()
    _check(os.path.isfile(icon), "icon missing: %s" % icon)
    return {"html": len(html), "icon": os.path.basename(icon)}
