#!/usr/bin/env python3
"""Copy pypdf (BSD-3-Clause) into src/filaforge/_vendor/pypdf with its imports pointing there.

OrcaSlicer runs every plugin in one Python, so a top-level "pypdf" of ours could clash with another
plugin's. Usage: python scripts/vendor_pypdf.py path/to/pypdf-X.Y.Z-py3-none-any.whl
"""

import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "filaforge" / "_vendor"
NEW = "filaforge._vendor.pypdf"


def main(wheel):
    out = TARGET / "pypdf"
    if out.exists():
        shutil.rmtree(out)
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "__init__.py").write_text('"""Third-party code shipped with FilaForge; see each folder\'s LICENSE."""\n',
                                       encoding="utf-8")
    with zipfile.ZipFile(wheel) as z:
        version = None
        for info in z.infolist():
            name = info.filename
            if name.startswith("pypdf/") and not name.endswith("/"):
                data = z.read(name)
                if name.endswith(".py"):
                    text = data.decode("utf-8")
                    text = re.sub(r"(^|\s)from pypdf(\.|\s)", lambda m: m.group(1) + "from " + NEW + m.group(2), text)
                    text = re.sub(r"(^|\s)import pypdf(\.|\s|$)", lambda m: m.group(1) + "import " + NEW + m.group(2),
                                  text, flags=re.M)
                    data = text.encode("utf-8")
                path = out / name[len("pypdf/"):]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            elif re.match(r"pypdf-[^/]+\.dist-info/(licenses/)?LICENSE", name):
                (out / "LICENSE").write_bytes(z.read(name))
            if name.endswith(".dist-info/METADATA"):
                version = re.search(rb"^Version: (.+)$", z.read(name), re.M).group(1).decode()
    (out / "VENDORED.txt").write_text("pypdf %s from PyPI, imports rewritten to %s\n" % (version, NEW),
                                      encoding="utf-8")
    left = [p for p in out.rglob("*.py") if re.search(r"(^|\s)(from|import) pypdf\b", p.read_text(encoding="utf-8"))]
    if left:
        sys.exit("imports not rewritten in: %s" % ", ".join(map(str, left)))
    print("vendored pypdf %s into %s" % (version, out))


if __name__ == "__main__":
    main(sys.argv[1])
