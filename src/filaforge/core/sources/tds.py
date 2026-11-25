"""Technical data sheets: the printing values a maker states in its PDF (or on a product page).

The text is read with pypdf (vendored, BSD licence), then each value is looked for by its usual names in a
line and the few lines after it, since data sheets are tables that a PDF reader turns into short lines.
Whatever is found is "declared" by the maker; nothing is guessed here.
"""

import io
import re

from ..model import DECLARED, Observation, SourceReport
from ..net import FetchError

WEIGHT = 0.95
WINDOW = 6          # lines after a label that may still hold its value

_DASH = re.compile(r"\s*(?:-|–|—|~|～|to|bis|a|do)\s*")
_RANGE = re.compile(r"(?<![\d.])(\d{2,3}(?:[.,]\d)?)\s*(?:°\s*C|℃|C)?\s*(?:-|–|—|~|～|\bto\b|\bbis\b|\ba\b|\bdo\b)\s*"
                    r"(\d{2,3}(?:[.,]\d)?)(?![\d.])")
_PLUSMINUS = re.compile(r"(?<![\d.])(\d{2,3}(?:[.,]\d)?)\s*(?:±|\+/-|\+-)\s*(\d{1,2}(?:[.,]\d)?)")
_NUM = re.compile(r"(?<![\d.])(\d+(?:[.,]\d+)?)(?![\d.])")

LABELS = {
    "nozzle": r"nozzle\s*temp|print(?:ing)?\s*temp|extru(?:sion|der)\s*temp|hot\s*end\s*temp|"
              r"druck\s*temperatur|düsen\s*temperatur|temperatura\s*(?:di\s*)?(?:stampa|ugello|dyszy|druku)|"
              r"température\s*d.(?:impression|extrusion)|temperatura\s*de\s*impresi",
    "bed": r"(?:plate|bed|platform|build\s*plate)\s*temp|heated\s*bed|druckbett|bett\s*temperatur|"
           r"temperatura\s*(?:del\s*)?(?:piano|stołu|stolu|cama)|température\s*du\s*plateau",
    "speed": r"print(?:ing)?\s*speed|druck\s*geschwindigkeit|velocit[aà]\s*di\s*stampa|prędkość\s*druku",
    "flow": r"volumetric|max(?:imum)?\s*flow|flow\s*rate\s*\(?mm",
    "fan": r"cooling\s*fan|fan\s*speed|part\s*cooling|lüfter|ventola|wentylator",
    "retract_len": r"retraction\s*(?:distance|length)|retract\s*length|rückzug(?:s)?\s*(?:länge|distanz)",
    "retract_speed": r"retraction\s*speed|rückzug(?:s)?\s*geschwindigkeit",
    "density": r"\bdensity\b|dichte|densit[àaé]|gęstość",
    "vicat": r"vicat",
    "hdt": r"heat\s*(?:distortion|deflection)|\bhdt\b",
    "dry": r"\bdry(?:ing)?\b|trocknung|essiccazione|suszenie",
    "chamber": r"chamber\s*temp|enclosure\s*temp|bauraum",
}
_LABEL_RE = {k: re.compile(v, re.I) for k, v in LABELS.items()}
_ANY_LABEL = re.compile("|".join("(?:%s)" % v for v in LABELS.values()), re.I)


def pdf_text(data):
    """The text of a PDF's pages, or "" when it can't be read."""
    import logging
    from ..files import no_bytecode
    with no_bytecode():
        from ..._vendor.pypdf import PdfReader   # vendored; imported here so the rest works without it
    logging.getLogger("filaforge._vendor.pypdf").setLevel(logging.ERROR)   # its warnings about odd PDFs
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return ""
        return "\n".join((page.extract_text() or "") for page in reader.pages[:12])
    except Exception:
        return ""


def html_text(data):
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|td|th)>", "\n", text)
    text = re.sub(r"<[a-zA-Z/!][^<>]*>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&deg;", "°", text)
    return re.sub(r"&[a-z]+;", " ", text)


def normalise(text):
    text = text.replace("℃", "°C").replace("：", ":").replace("～", "~").replace(" ", " ")
    text = re.sub(r"(\d)\s*°\s*C", r"\1 °C", text)
    return [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _f(text):
    return float(text.replace(",", "."))


def _after_label(line, rx):
    found = rx.search(line)
    return line[found.end():] if found else line


def _ranges(text, lo, hi):
    out = []
    for a, b in _RANGE.findall(text):
        a, b = _f(a), _f(b)
        if lo <= a <= b <= hi:
            out.append((a, b))
    for a, b in _PLUSMINUS.findall(text):
        mid, tol = _f(a), _f(b)
        if lo <= mid - tol and mid + tol <= hi:
            out.append((mid - tol, mid + tol))
    return out


def _numbers(text, lo, hi):
    text = re.sub(r"(\d)\s*(?:±|\+/-)\s*\d+(?:[.,]\d+)?", r"\1", text)   # 56±3 -> 56
    text = re.sub(r"(?i)\b(?:g|k?g)\s*/\s*c?m\s*[3³]|\b[cm]m\s*[3³]|\d+(?:[.,]\d+)?\s*e[+-]?\d+", " ", text)  # units, 2.9E+15
    text = re.sub(r"\b(?:ISO|ASTM|DIN|IEC|UL)\s*[A-Z]?\s*\d+(?:[-/]\d+)*", " ", text, flags=re.I)  # test methods
    return [v for v in (_f(n) for n in _NUM.findall(text)) if lo <= v <= hi]


def _window(lines, i):
    """The label's own line (after the label) plus the following lines up to the next label."""
    out = [lines[i]]
    for line in lines[i + 1:i + 1 + WINDOW]:
        if _ANY_LABEL.search(line):
            break
        out.append(line)
    return out


def nozzle_bands(lines, i):
    """[(low, high, max speed)] from a "temperature per speed" table after the nozzle label."""
    bands = []
    for line in _window(lines, i)[1:] + [_after_label(lines[i], _LABEL_RE["nozzle"])]:
        found = re.match(r"^(\d{3})\s*-\s*(\d{3})\s+(?:(\d{2,3})\s*-\s*(\d{2,3})|[≤<]=?\s*(\d{2,3}))\s*(?:mm/s)?$",
                         line)
        if found:
            lo, hi = float(found.group(1)), float(found.group(2))
            speed = float(found.group(4) or found.group(5))
            if 150 <= lo <= hi <= 450:
                bands.append((lo, hi, speed))
    return bands


def parse(text):
    """{canonical key: value} of what the text states."""
    lines = normalise(text)
    found = {}
    for i, line in enumerate(lines):
        for name, rx in _LABEL_RE.items():
            if not rx.search(line):
                continue
            window = _window(lines, i)
            joined = " ".join([_after_label(window[0], rx)] + window[1:])
            if name == "nozzle" and "nozzle_temp_range" not in found:
                bands = nozzle_bands(lines, i)
                if bands:
                    found["nozzle_temp_bands"] = bands
                    found["nozzle_temp_range"] = (min(b[0] for b in bands), max(b[1] for b in bands))
                    found.setdefault("print_speed_max", max(b[2] for b in bands))
                else:
                    r = _ranges(joined, 150, 450)
                    if r:
                        found["nozzle_temp_range"] = r[0]
                    else:
                        n = _numbers(joined, 150, 450)
                        if n:
                            found["nozzle_temp"] = n[0]
            elif name == "bed" and "bed_temp_range" not in found and "bed_temp" not in found:
                r = _ranges(joined, 0, 150)
                if r:
                    found["bed_temp_range"] = r[0]
                else:
                    n = _numbers(joined, 20, 150)
                    if n:
                        found["bed_temp"] = n[0]
            elif name == "speed" and "print_speed_max" not in found:
                r = _ranges(joined, 5, 1000)
                n = _numbers(joined, 5, 1000)
                if r:
                    found["print_speed_max"] = r[0][1]
                elif n:
                    found["print_speed_max"] = max(n)
            elif name == "flow" and "max_volumetric_speed" not in found and re.search(r"mm\s*[³3]", joined):
                n = _numbers(joined, 1, 80)
                if n:
                    found["max_volumetric_speed"] = max(n)
            elif name == "fan" and "fan_max" not in found:
                pct = re.findall(r"(\d{1,3})\s*%", joined)
                vals = [float(v) for v in pct if 0 <= float(v) <= 100]
                if vals:
                    found["fan_max"] = max(vals)
                    if len(vals) > 1:
                        found["fan_min"] = min(vals)
            elif name == "retract_len" and "retraction_length" not in found:
                r = [(a, b) for a, b in (( _f(x), _f(y)) for x, y in
                     re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:-|–|~)\s*(\d+(?:[.,]\d+)?)\s*mm(?!/)", joined)) if 0 < a <= b <= 15]
                n = [float(v.replace(",", ".")) for v in re.findall(r"(\d+(?:[.,]\d+)?)\s*mm(?!/)", joined)]
                if r:
                    found["retraction_length"] = round((r[0][0] + r[0][1]) / 2, 2)
                elif n and 0 < n[0] <= 15:
                    found["retraction_length"] = n[0]
            elif name == "retract_speed" and "retraction_speed" not in found:
                r = _ranges(joined.replace("mm/s", " "), 5, 150)
                n = _numbers(joined, 5, 150)
                if r:
                    found["retraction_speed"] = round((r[0][0] + r[0][1]) / 2)
                elif n:
                    found["retraction_speed"] = n[0]
            elif name == "density" and "density" not in found:
                n = _numbers(_after_label(line, rx), 0.5, 3.0) or _numbers(joined, 0.5, 3.0)
                if n:
                    found["density"] = n[-1]
            elif name in ("vicat", "hdt") and (name == "vicat" or "vitrification" not in found) \
                    and found.get("_vitrification_from") != "vicat":
                n = _numbers(_after_label(line, rx), 35, 300)
                if n:
                    found["vitrification"] = n[-1]
                    found["_vitrification_from"] = name
            elif name == "dry" and "dry_temp" not in found:
                n = _numbers(joined, 35, 130)
                if n:
                    found["dry_temp"] = n[0]
                hours = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:-|–|~)?\s*(\d+(?:[.,]\d+)?)?\s*(?:h\b|hours?|hrs?)", joined, re.I)
                if hours:
                    found["dry_time"] = _f(hours.group(2) or hours.group(1))
            elif name == "chamber" and "chamber_temp" not in found:
                n = _numbers(joined, 20, 120)
                if n:
                    found["chamber_temp"] = n[0]
    return found


def observations(found, label, url):
    out = []
    for key, value in found.items():
        if key.startswith("_"):
            continue
        note = ""
        if key == "vitrification":
            note = "Vicat softening temperature" if found.get("_vitrification_from") == "vicat" else "heat deflection (HDT)"
        out.append(Observation(key, value, DECLARED, "tds", label, url, WEIGHT, note))
    return out


def from_bytes(data, label, url):
    """(observations, report) of a data sheet's bytes: PDF or HTML."""
    is_pdf = data[:5] == b"%PDF-"
    text = pdf_text(data) if is_pdf else html_text(data)
    if not text.strip():
        return [], SourceReport("tds", False, 0, "no text could be read from it", url)
    found = parse(text)
    obs = observations(found, label, url)
    return obs, SourceReport("tds", True, len(obs), "%d values from the %s" % (len(obs), "PDF" if is_pdf else "page"),
                             url, {"chars": len(text)})


def lookup(fetcher, url, label="Data sheet"):
    try:
        data = fetcher.get(url, ttl=30 * 24 * 3600, accept="application/pdf,text/html;q=0.8")
    except FetchError as error:
        return [], SourceReport("tds", False, 0, str(error), url)
    return from_bytes(data, label, url)
