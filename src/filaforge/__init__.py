"""FilaForge for OrcaSlicer: filament profiles from open data, maker data sheets and your own measurements.

Copyright (C) 2026 xtronets. SPDX-License-Identifier: MIT
The core (filaforge.core) needs no OrcaSlicer and is tested on its own; this file only registers the tab.
"""

import json
import os

try:
    import orca
    import orca.pages  # noqa: F401
except (ImportError, AttributeError):   # outside OrcaSlicer (tests, tools): only the core is used
    orca = None

if orca is not None:
    from . import plugin as _plugin

    def _page_html():
        with open(os.path.join(_plugin.HERE, "ui", "page.html"), encoding="utf-8") as f:
            html = f.read()
        boot = json.dumps({"version": _plugin.VERSION, "settings": _plugin.read_settings()})
        boot = boot.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return html.replace("/*BOOT*/null", boot)

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
