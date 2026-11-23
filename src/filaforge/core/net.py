"""HTTP reads with a small disk cache, so a second look at the same filament works offline and fast.

Only GET, only https (plus the user's own Spoolman address in spoolman.py), with a size cap. Tests pass a
Fetcher with canned answers instead of the network.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

USER_AGENT = "FilaForge/0.1 (OrcaSlicer plugin; +https://github.com/xtronets/filaforge)"
TIMEOUT = 15
MAX_BYTES = 24 * 1024 * 1024
DAY = 24 * 3600


class FetchError(Exception):
    pass


class Fetcher:
    """get(url) -> bytes, cached in folder for ttl seconds; a stale copy is used when the network fails."""

    def __init__(self, folder=None, ttl=DAY, opener=None):
        self.folder = folder
        self.ttl = ttl
        self._open = opener or urllib.request.urlopen

    def _path(self, url):
        if not self.folder:
            return None
        return os.path.join(self.folder, hashlib.sha256(url.encode("utf-8")).hexdigest()[:24])

    def _read_cache(self, path, fresh_only):
        try:
            age = time.time() - os.path.getmtime(path)
            if fresh_only and age > self.ttl:
                return None
            with open(path, "rb") as f:
                return f.read()
        except (OSError, TypeError):
            return None

    def get(self, url, ttl=None, accept=None):
        path = self._path(url)
        old_ttl = self.ttl
        if ttl is not None:
            self.ttl = ttl
        try:
            cached = self._read_cache(path, fresh_only=True) if path else None
            if cached is not None:
                return cached
            try:
                data = self._download(url, accept)
            except FetchError:
                stale = self._read_cache(path, fresh_only=False) if path else None
                if stale is not None:
                    return stale
                raise
            if path:
                try:
                    os.makedirs(self.folder, exist_ok=True)
                    tmp = path + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, path)
                except OSError:
                    pass
            return data
        finally:
            self.ttl = old_ttl

    def _download(self, url, accept=None):
        if not url.startswith("https://"):
            raise FetchError("only https addresses are read: %s" % url)
        headers = {"User-Agent": USER_AGENT}
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(url, headers=headers)
        try:
            with self._open(request, timeout=TIMEOUT) as response:
                data = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise FetchError("%s: HTTP %s" % (url, error.code)) from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise FetchError("%s: %s" % (url, getattr(error, "reason", error))) from None
        if len(data) > MAX_BYTES:
            raise FetchError("%s: larger than %d MB" % (url, MAX_BYTES // 2**20))
        return data

    def json(self, url, ttl=None):
        data = self.get(url, ttl=ttl, accept="application/json")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise FetchError("%s: not JSON (%s)" % (url, error)) from None


class CannedFetcher(Fetcher):
    """For tests: answers from {url: bytes | dict | list | Exception}; anything else is a FetchError."""

    def __init__(self, answers):
        super().__init__(folder=None)
        self.answers = answers
        self.asked = []

    def get(self, url, ttl=None, accept=None):
        self.asked.append(url)
        answer = self.answers.get(url)
        if callable(answer):
            answer = answer(url)
        if answer is None:
            raise FetchError("%s: not found (canned)" % url)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, (dict, list)):
            return json.dumps(answer).encode("utf-8")
        return answer
