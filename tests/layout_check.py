"""The tab's layout in a browser: in a short window no card may be cut off and every button stays reachable.
Run: python tests/layout_check.py   (needs: pip install playwright && playwright install chromium)"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def messages():
    import importlib

    import fake_orca
    import helpers
    data = tempfile.mkdtemp()
    orca = fake_orca.install(helpers.orca_presets(), os.path.join(data, "user", "4242", "process", "x.json"))
    for name in [m for m in sys.modules if m == "filaforge" or m.startswith("filaforge.")]:
        if not name.startswith(("filaforge.core", "filaforge._vendor")):
            del sys.modules[name]       # imported by helpers before the fake orca was there
    pkg = importlib.import_module("filaforge")
    plugin = sys.modules["filaforge.plugin"]
    plugin.data_dir = lambda: data
    plugin.own_dir = lambda: os.path.join(data, "filaforge")
    canned = helpers.fetcher()
    plugin.fetcher = lambda: canned
    plugin.spawn = lambda fn, *a: fn(*a)
    next(c for c in orca.registered if c.__name__ == "FilaForge")().register_capabilities()
    page = next(c for c in orca.registered if c.__name__ == "FilaForgePage")()
    page.on_message({"cmd": "init"})
    page.on_message({"cmd": "research", "brand": "Sunlu", "material": "PLA+"})
    result = [m for m in page.posted if m.get("cmd") == "result"][-1]
    page.on_message({"cmd": "process_plan", "process": "0.20mm Standard @Creality K1C", "max_volumetric_speed": 12,
                     "identity": result["identity"]})
    wanted = ("init", "result", "process_plan")
    return pkg._page_html(), [m for m in page.posted if m.get("cmd") in wanted]


CHECK = """() => {
  const out = [];
  document.querySelectorAll('.card:not([hidden])').forEach(card => {
    const name = (card.querySelector('h2, .title') || {}).textContent || card.id;
    if (card.scrollHeight > card.clientHeight + 1) out.push(`${name}: cut off (${card.clientHeight} of ${card.scrollHeight}px)`);
    card.querySelectorAll('button').forEach(b => {
      if (b.closest('[hidden]') || getComputedStyle(b).display === 'none') return;
      const r = b.getBoundingClientRect(), c = card.getBoundingClientRect();
      if (r.width < 10 || r.height < 10 || r.bottom > c.bottom + 1 || r.right > c.right + 1)
        out.push(`${name}: button "${b.textContent.trim()}" not reachable`);
    });
  });
  return out;
}"""


def main():
    from playwright.sync_api import sync_playwright
    html, msgs = messages()
    path = os.path.join(tempfile.mkdtemp(), "page.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((1500, 650), (1100, 600), (800, 700)):
            for scheme in ("light", "dark"):
                page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
                page.add_init_script("window.orca = { postMessage() {}, onMessage(f) {} };")
                page.goto("file://" + path)
                page.evaluate("ms => ms.forEach(m => window.__filaforge.onMessage(m))", msgs)
                page.wait_for_timeout(200)
                problems += ["%dx%d %s: %s" % (width, height, scheme, x) for x in page.evaluate(CHECK)]
                page.close()
        browser.close()
    for line in problems:
        print("::error::" + line)
    print("layout: %s" % ("OK" if not problems else "%d problems" % len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
