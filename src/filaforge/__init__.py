"""FilaForge for OrcaSlicer: filament profiles from open data, maker data sheets and your own measurements.

Copyright (C) 2026 xtronets. SPDX-License-Identifier: MIT
The core (filaforge.core) needs no OrcaSlicer and is tested on its own; this file only registers the tab.
"""

import json
import os
import sys

try:
    import orca
    import orca.pages  # noqa: F401
except (ImportError, AttributeError):   # outside OrcaSlicer (tests, tools): only the core is used
    orca = None

if orca is not None:
    # no __pycache__ writes while FilaForge's modules load: Python writes a .pyc through a file descriptor and
    # OrcaSlicer, unable to name that file, would ask the user about an "open" audit event (core/files.py)
    _bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        from . import plugin as _plugin
    finally:
        sys.dont_write_bytecode = _bytecode

    def _page_html():
        with open(os.path.join(_plugin.HERE, "ui", "page.html"), encoding="utf-8") as f:
            html = f.read()
        boot = json.dumps({"version": _plugin.VERSION, "settings": _plugin.read_settings()})
        boot = boot.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return _inline_images(html.replace("/*BOOT*/null", boot))

    _MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".jpg": "image/jpeg"}

    def _inline_images(html):
        # The page comes to OrcaSlicer as a string, with no folder to load files from: {{img:name}} becomes the
        # file in ui/ as a data: address.
        import base64
        import re

        def data_url(m):
            name = m.group(1)
            try:
                with open(os.path.join(_plugin.HERE, "ui", name), "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
            except OSError:
                return ""
            return "data:%s;base64,%s" % (_MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream"), data)

        return re.sub(r"\{\{img:([\w.-]+)\}\}", data_url, html)

    class FilaForgePage(orca.pages.PagesPluginCapabilityBase):
        def __init__(self):
            super().__init__()
            self._tab = _plugin.Tab(self.post_message)

        def get_name(self):
            return _plugin.NAME

        def get_type(self):
            return orca.PluginType.Pages

        def get_icon(self):
            # A copy named after its content: OrcaSlicer keeps a tab icon by its name, so a new icon needs a new
            # name (and a plain "icon" could meet another plugin's).
            source = os.path.join(_plugin.HERE, "ui", "icon.png")
            try:
                import hashlib
                with open(source, "rb") as f:
                    data = f.read()
                path = os.path.join(_plugin.own_dir(), "filaforge-tab-%s.png" % hashlib.sha256(data).hexdigest()[:8])
                if not os.path.exists(path):
                    os.makedirs(_plugin.own_dir(), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(data)
                return path
            except OSError:
                return source

        def get_default_config(self):
            return {}

        def get_ui(self):
            return _page_html()

        def on_message(self, data):
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    return
            self._tab.handle(data)

        def on_lifecycle_event(self, event, ctx):
            # after every G-code export (the preview's too) OrcaSlicer passes its path in ctx.msg; this runs on the
            # slicing thread, so it only starts the reading in the background (pre-flight check)
            try:
                events = getattr(orca, "LifecycleEvent", None)
                codes = getattr(orca, "LifecycleEvtCode", None)
                if events is None or event != events.GCodeExportFinished:
                    return
                if codes is not None and ctx.code != codes.Ok:
                    return
                _plugin.Preflight.on_exported(ctx.msg)
            except Exception:
                _plugin.log.exception("pre-flight event")

        def on_load(self):
            _plugin._setup_log()
            _plugin.log.info("FilaForge %s loaded", _plugin.VERSION)

        def on_unload(self):
            _plugin.log.info("FilaForge unloaded")

    @orca.plugin
    class FilaForge(orca.base):
        def register_capabilities(self):
            _plugin._setup_log()
            _plugin.request_permissions()
            orca.register_capability(FilaForgePage)
            from . import selftest      # only with FILAFORGE_SELFTEST set (the real-OrcaSlicer CI job)
            selftest.start()
