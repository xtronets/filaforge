"""Earlier versions of FilaForge's presets, so a save or a removal can be undone.

Before FilaForge replaces or removes one of its presets, the old file goes into <FilaForge folder>/history/, one
folder per preset, one JSON file per version: {"at", "reason", "kind", "name", "meta", "preset"}. The newest KEEP
versions of each preset are kept.
"""

import hashlib
import json
import os
import re
import time

from . import files

KEEP = 20
_ID = re.compile(r"^[0-9a-f]{12}/[0-9]+-[a-z_]+\.json$")


def _folder_name(kind, name):
    return hashlib.sha1(("%s|%s" % (kind, name)).encode("utf-8")).hexdigest()[:12]


class History:
    def __init__(self, folder, keep=KEEP):
        self.folder = folder
        self.keep = keep

    def add(self, kind, name, preset, meta=None, reason="replaced", at=None):
        """Keep one version (preset: the dict, or the file's bytes); returns its id, "" when it can't be read."""
        if isinstance(preset, (bytes, bytearray)):
            try:
                preset = json.loads(preset.decode("utf-8"))
            except ValueError:
                return ""
        if not isinstance(preset, dict):
            return ""
        at = time.time() if at is None else at
        reason = re.sub(r"[^a-z_]", "", str(reason or "replaced")) or "replaced"
        sub = _folder_name(kind, name)
        stamp = int(at * 1000)
        try:                                  # always after the versions already kept, even in the same millisecond
            stamps = [int(f.split("-")[0]) for f in os.listdir(os.path.join(self.folder, sub)) if f.endswith(".json")]
        except (OSError, ValueError):
            stamps = []
        stamp = max([stamp] + [s + 1 for s in stamps])
        path = os.path.join(self.folder, sub, "%d-%s.json" % (stamp, reason))
        files.write_json(path, {"at": int(at), "reason": reason, "kind": kind, "name": name,
                                "meta": dict(meta or {}), "preset": preset}, ensure_ascii=False, indent=1)
        self._trim(sub)
        return "%s/%s" % (sub, os.path.basename(path))

    def _trim(self, sub):
        folder = os.path.join(self.folder, sub)
        versions = sorted(f for f in os.listdir(folder) if f.endswith(".json"))
        for old in versions[:-self.keep] if self.keep else []:
            try:
                os.unlink(os.path.join(folder, old))
            except OSError:
                pass

    def get(self, entry_id):
        """The stored version, or None for an unknown id."""
        if not _ID.match(str(entry_id or "")):
            return None
        try:
            with open(os.path.join(self.folder, *entry_id.split("/")), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("preset"), dict) else None

    def entries(self):
        """[{id, kind, name, at, reason}] of every kept version, newest first."""
        out = []
        try:
            subs = os.listdir(self.folder)
        except OSError:
            return out
        for sub in subs:
            folder = os.path.join(self.folder, sub)
            if not os.path.isdir(folder):
                continue
            for f in os.listdir(folder):
                entry = self.get("%s/%s" % (sub, f))
                if entry:
                    out.append({"id": "%s/%s" % (sub, f), "kind": entry.get("kind"), "name": entry.get("name"),
                                "at": entry.get("at"), "reason": entry.get("reason")})
        out.sort(key=lambda e: -int(e["id"].split("/")[1].split("-")[0]))     # the file name's milliseconds
        return out


def differences(old, new):
    """[{key, old, new}] of the settings two presets differ in (names and ids left out)."""
    skip = {"name", "filament_settings_id", "version", "description", "filament_id"}
    out = []
    for key in sorted(set(old) | set(new)):
        if key in skip:
            continue
        a, b = old.get(key), new.get(key)
        if a != b:
            out.append({"key": key, "old": _short(a), "new": _short(b)})
    return out


def _short(value):
    if isinstance(value, list):
        value = value[0] if len(set(value)) == 1 and value else ", ".join(value)
    text = "" if value is None else str(value)
    return text if len(text) <= 60 else text[:57] + "…"
