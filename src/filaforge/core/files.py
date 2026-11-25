"""Writing JSON files safely, in a way OrcaSlicer's sandbox can check.

A new file goes to a temporary name next to it and replaces the old one only when complete, so an interrupted
write never leaves a broken file. The temporary file is opened by its path, not by a descriptor from
tempfile.mkstemp: OrcaSlicer's permission check sees the "open" audit event, and with a descriptor it can't tell
which file it is, so it asked the user (0.4.3 and older).
"""

import contextlib
import json
import os
import sys
import uuid


@contextlib.contextmanager
def no_bytecode():
    """Import without writing __pycache__ files. Python writes a module's .pyc through a descriptor
    (os.open, then FileIO(fd)), and OrcaSlicer can't tell from that "open" audit event which file it is, so it
    asks the user for permission (FilaForge's own modules after an update, and pypdf on the first data sheet)."""
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = before


def write_json(path, value, **dump_args):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), uuid.uuid4().hex[:8])
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, **dump_args)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
