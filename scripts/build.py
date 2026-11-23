#!/usr/bin/env python3
"""Build dist/filaforge_any.whl: one pure-Python wheel for OrcaSlicer's "Install local plugin".

    python3 scripts/build.py
"""

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "filaforge"
DIST = ROOT / "dist"


def version():
    text = (SRC / "plugin.py").read_text(encoding="utf-8")
    return re.search(r'^VERSION = "([^"]+)"', text, re.M).group(1)


def record_line(name, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return "%s,sha256=%s,%d" % (name, digest, len(data))


def build():
    ver = version()
    info = "filaforge-%s.dist-info" % ver
    files = {}
    for path in sorted(SRC.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        files["filaforge/" + path.relative_to(SRC).as_posix()] = path.read_bytes()
    files[info + "/METADATA"] = (
        "Metadata-Version: 2.1\nName: FilaForge\nVersion: %s\n"
        "Summary: Filament profiles from open data, maker data sheets and your own measurements\n"
        "Author: xtronets\nLicense: MIT\n" % ver).encode()
    files[info + "/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: filaforge-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    files[info + "/top_level.txt"] = b"filaforge\n"
    files[info + "/LICENSE"] = (ROOT / "LICENSE").read_bytes()
    record = [record_line(n, d) for n, d in files.items()] + [info + "/RECORD,,"]
    files[info + "/RECORD"] = ("\n".join(record) + "\n").encode()
    DIST.mkdir(exist_ok=True)
    out = DIST / "filaforge_any.whl"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
    page = (SRC / "ui" / "page.html").stat().st_size
    print("built %s (%d files, %.0f KB, page %.0f KB)" % (out.relative_to(ROOT), len(files), out.stat().st_size / 1024,
                                                         page / 1024))
    return out


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
