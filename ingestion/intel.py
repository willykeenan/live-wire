#!/usr/bin/env python3
"""
Live Wire — the ON-THE-GROUND INTEL engine.

The newswire follows the media. This follows the PRIMARY sources the media itself
reports on — caught before they become headlines:

  SEC EDGAR   the 8-K corporate-events firehose (a filing IS the event, filed
              minutes before the "company announces…" article)
  USGS        real-time earthquakes (the ground literally moving)
  NWS         active severe/extreme weather + hazard alerts
  openFDA     drug/food recalls + enforcement (regulatory action, pre-coverage)
  CourtListener  freshly-filed federal court cases
  Wikipedia   edit-velocity spikes (a page suddenly churning = something happened)

Every item is normalized, given a deterministic EDGE score (earliness × impact ×
under-coverage), and — critically — cross-checked against Live Wire's own RSS wire
so we only flag what is genuinely AHEAD of media. Stdlib only, keyless, never raises.

Public contract:
    refresh_intel(wire=None, limit=40) -> list[dict]   # ranked intel items
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

_UA = "LiveWire/1.0 (news intelligence; contact via project README)"
_TIMEOUT = 12


def _tls_context():
    """Verified TLS. Prefer certifi's CA bundle (installed with requests)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


_SSL = _tls_context()

# per-host politeness (seconds between hits) — GDELT & SEC are strict
_HOST_GAP = {"api.gdeltproject.org": 6.0, "www.sec.gov": 1.0, "efts.sec.gov": 1.0,
             "en.wikipedia.org": 1.0}
_last_hit = {}
_hit_lock = threading.Lock()

_CACHE = {}
_CACHE_TTL = 240          # intel is polled ~5 min; a 4-min cache smooths retries
_CACHE_MAX = 200

# tokens we ignore when matching an intel item against the media wire (dedup)
_STOP = set(("the a an of to in on for and or with from at by as is are was were be "
             "been this that it its over after into amid new say says said will inc "
             "corp co ltd plc group holdings today report reports u.s us").split())


# --------------------------------------------------------------------------- #
# HTTP + cache
# --------------------------------------------------------------------------- #

def _throttle(host):
    with _hit_lock:
        gap = _HOST_GAP.get(host, 0.4)
        wait = gap - (time.time() - _last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(min(wait, 6.0))
        _last_hit[host] = time.time()


def _get(url, max_bytes=1_500_000):
    """Polite keyless GET → bytes, or None. Never raises."""
    key = "g|" + url
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        host = urllib.parse.urlparse(url).netloc
        _throttle(host)
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL) as r:
            data = r.read(max_bytes)
    except Exception:  # noqa: BLE001
        return None
    if len(_CACHE) > _CACHE_MAX:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _CACHE_MAX // 2]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.time() + _CACHE_TTL, data)
    return data


def _get_json(url, max_bytes=3_000_000):
    raw = _get(url, max_bytes)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", "ignore"))
    except (ValueError, UnicodeDecodeError):
        return None


def _toks(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(w) > 2 and w not in _STOP}


def _iid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _item(source, kind, title, url, ts, *, detail="", entities=None, metric=None,
          base_edge=50, why=""):
    return {
        "id": _iid(source, title),
        "source": source, "kind": kind,
        "title": (title or "").strip()[:200],
        "detail": (detail or "").strip()[:280],
        "url": url or "",
        "ts": float(ts or time.time()),
        "entities": entities or {"tickers": [], "companies": [], "people": [], "places": []},
        "metric": metric or {},
        "base_edge": int(base_edge),
        "why": why,
    }


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

def edgar_8k(limit=25):
    """The SEC 8-K current-events firehose — corporate material events at the moment
    of filing. Atom feed; each entry is a fresh filing."""
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
           "&company=&dateb=&owner=include&count={}&output=atom".format(limit))
    raw = _get(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", "", ns) or "").strip()   # "8-K - COMPANY (CIK) (Filer)"
        if not title:
            continue
        link_el = e.find("a:link", ns)
        href = link_el.get("href") if link_el is not None else ""
        upd = e.findtext("a:updated", "", ns) or ""
        ts = time.time()
        try:
            ts = parsedate_to_datetime(upd).timestamp() if upd else ts
        except Exception:  # noqa: BLE001
            try:
                ts = time.mktime(time.strptime(upd[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:  # noqa: BLE001
                pass
        m = re.match(r"^(8-K[^-]*)\s*-\s*(.+?)\s*\(", title)
        company = (m.group(2).strip() if m else title)
        out.append(_item("SEC EDGAR", "filing", company + " files 8-K", href, ts,
                         detail="Material corporate event just filed with the SEC.",
                         entities={"tickers": [], "companies": [company], "people": [], "places": []},
                         base_edge=54, why="primary filing — before the press release"))
        if len(out) >= limit:
            break
    return out


def edgar_insider(limit=25):
    """SEC Form 4 firehose — insider transactions the moment they're reported."""
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4"
           "&company=&dateb=&owner=only&count={}&output=atom".format(limit))
    raw = _get(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", "", ns) or "").strip()   # "4 - PERSON (CIK) (Reporting)"
        if not title:
            continue
        link_el = e.find("a:link", ns)
        href = link_el.get("href") if link_el is not None else ""
        upd = e.findtext("a:updated", "", ns) or ""
        ts = time.time()
        try:
            ts = parsedate_to_datetime(upd).timestamp() if upd else ts
        except Exception:  # noqa: BLE001
            pass
        m = re.match(r"^4\s*-\s*(.+?)\s*\(", title)
        who = (m.group(1).strip() if m else title)
        out.append(_item("SEC EDGAR", "insider", "Insider filing: " + who, href, ts,
                         detail="A corporate insider just reported a transaction (Form 4).",
                         entities={"tickers": [], "companies": [], "people": [who], "places": []},
                         base_edge=44, why="insider transaction reported"))
        if len(out) >= limit:
            break
    return out


def usgs_quakes(min_mag=4.5):
    """Significant earthquakes in the last hour — real-time ground truth."""
    data = _get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson")
    if not isinstance(data, dict):
        return []
    out = []
    for f in data.get("features", []):
        p = f.get("properties") or {}
        mag = p.get("mag")
        if mag is None or mag < min_mag:
            continue
        place = p.get("place") or "unknown location"
        ts = (p.get("time") or 0) / 1000.0 or time.time()
        edge = 55 + min(35, int((mag - min_mag) * 12))
        out.append(_item("USGS", "quake", "M{:.1f} earthquake — {}".format(mag, place),
                         p.get("url") or "", ts,
                         detail="Magnitude {:.1f}. {}".format(mag, place),
                         entities={"tickers": [], "companies": [], "people": [], "places": [place]},
                         metric={"magnitude": mag}, base_edge=min(95, edge),
                         why="seismograph — beats any newsroom"))
    return out


def nws_alerts(limit=15):
    """Active severe/extreme weather + hazard alerts (US)."""
    url = ("https://api.weather.gov/alerts/active?severity=Severe,Extreme"
           "&urgency=Immediate,Expected&status=actual")
    data = _get_json(url, max_bytes=2_000_000)
    if not isinstance(data, dict):
        return []
    out = []
    for f in data.get("features", []):
        p = f.get("properties") or {}
        event = p.get("event") or "Alert"
        area = p.get("areaDesc") or ""
        sev = p.get("severity") or ""
        ts = time.time()
        try:
            ts = parsedate_to_datetime(p.get("sent")).timestamp() if p.get("sent") else ts
        except Exception:  # noqa: BLE001
            pass
        edge = 60 if sev == "Extreme" else 52
        out.append(_item("NWS", "alert", "{}: {}".format(event, area[:80]),
                         (p.get("id") or ""), ts,
                         detail=(p.get("headline") or event)[:200],
                         entities={"tickers": [], "companies": [], "people": [], "places": [area[:80]]},
                         metric={"severity": sev}, base_edge=edge,
                         why="official hazard alert — issued at the source"))
        if len(out) >= limit:
            break
    return out


def openfda_recalls(limit=8):
    """Recent FDA enforcement / recalls (drug + food)."""
    out = []
    for kind_url, label in (
        ("https://api.fda.gov/drug/enforcement.json?sort=report_date:desc&limit=6", "Drug"),
        ("https://api.fda.gov/food/enforcement.json?sort=report_date:desc&limit=6", "Food"),
    ):
        data = _get_json(kind_url)
        for r in (data or {}).get("results", []) if isinstance(data, dict) else []:
            firm = r.get("recalling_firm") or "A company"
            reason = (r.get("reason_for_recall") or "").strip()
            cls = r.get("classification") or ""
            rd = r.get("report_date") or ""
            ts = time.time()
            try:
                ts = time.mktime(time.strptime(rd, "%Y%m%d"))
            except Exception:  # noqa: BLE001
                pass
            edge = 58 if "I" == cls.replace("Class ", "").strip()[:1] else 48
            out.append(_item("openFDA", "recall",
                             "{} recall — {}".format(label, firm), "", ts,
                             detail=(cls + ": " if cls else "") + reason[:220],
                             entities={"tickers": [], "companies": [firm], "people": [], "places": []},
                             metric={"classification": cls}, base_edge=edge,
                             why="FDA enforcement action"))
            if len(out) >= limit:
                return out
    return out


def courtlistener(limit=8):
    """Freshly-filed federal court cases (RECAP/PACER via CourtListener free API)."""
    url = "https://www.courtlistener.com/api/rest/v4/search/?type=r&order_by=dateFiled+desc"
    data = _get_json(url)
    out = []
    _junk = ("v.", "unknown case", "miscellaneous entry", "adversary proceeding", "new case", "sealed")
    for r in (data or {}).get("results", []) if isinstance(data, dict) else []:
        name = (r.get("caseName") or r.get("caption") or "").strip()
        low = name.lower()
        if len(name) < 10 or any(j in low for j in _junk):
            continue   # RECAP dockets are messy — skip the unusable case names
        court = r.get("court") or ""
        df = r.get("dateFiled") or ""
        ts = time.time()
        try:
            ts = time.mktime(time.strptime(df[:10], "%Y-%m-%d"))
        except Exception:  # noqa: BLE001
            pass
        url_abs = r.get("absolute_url") or ""
        out.append(_item("CourtListener", "court", name[:120],
                         ("https://www.courtlistener.com" + url_abs) if url_abs else "", ts,
                         detail="Filed in {}.".format(court) if court else "New federal filing.",
                         base_edge=42, why="court filing — public record"))
        if len(out) >= limit:
            break
    return out


_WIKI_SKIP = re.compile(r"\bleague\b|\bcup\b|\bseason\b|\bf\.?c\b|tournament|championship|"
                        r"\bgrand prix\b|\bopen\b|20\d\d[–\-]\d|\bfootball\b|\bmatch\b|"
                        r"discograph|filmograph|list of", re.I)


def wiki_velocity(limit=10, window_min=45):
    """Wikipedia edit-velocity: articles churning fast right now = something is
    happening to that person/company/place before it's a settled headline."""
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=recentchanges"
           "&rcprop=title|timestamp&rcnamespace=0&rctype=edit&rclimit=500&format=json")
    data = _get_json(url)
    if not isinstance(data, dict):
        return []
    counts = {}
    for rc in (data.get("query") or {}).get("recentchanges", []):
        t = rc.get("title")
        if t and ":" not in t.split(" ")[0] and not _WIKI_SKIP.search(t):
            counts[t] = counts.get(t, 0) + 1
    hot = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for title, n in hot:
        if n < 5:                # need a real burst
            break
        edge = 34 + min(22, (n - 5) * 3)   # a corroborating signal, ranks below hard sources
        out.append(_item("Wikipedia", "wiki", "Spike: '{}' being edited fast".format(title),
                         "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                         time.time(), detail="{} edits in the last ~{} min — something's developing.".format(n, window_min),
                         entities={"tickers": [], "companies": [], "people": [title], "places": []},
                         metric={"edits": n}, base_edge=min(85, edge),
                         why="edit-velocity spike"))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Aggregate + edge scoring + dedup vs the media wire
# --------------------------------------------------------------------------- #

# Public-record and hazard sources only. No prediction markets, odds, or trading feeds.
_SOURCES = (edgar_8k, edgar_insider, usgs_quakes, nws_alerts,
            openfda_recalls, courtlistener, wiki_velocity)


def _freshness_bonus(ts):
    age_min = (time.time() - (ts or 0)) / 60.0
    if age_min <= 15:
        return 22
    if age_min <= 60:
        return 12
    if age_min <= 240:
        return 4
    return 0


def _covered_by_wire(item, wire_index):
    """Is this intel already a media headline? (dedup → proves lead vs lag)."""
    it = _toks(item.get("title")) | _toks(item.get("detail"))
    for ent in ((item.get("entities") or {}).get("companies", [])
                + (item.get("entities") or {}).get("people", [])):
        it |= _toks(ent)
    if not it:
        return False
    for wt in wire_index:
        if len(it & wt) >= 3:
            return True
    return False


def refresh_intel(wire=None, limit=40):
    """Pull every primary source concurrently, score EDGE (earliness × impact ×
    under-coverage), flag which items are already in the media wire, and return the
    ranked list. Never raises; a dead source is simply skipped."""
    items = []
    with ThreadPoolExecutor(max_workers=len(_SOURCES)) as pool:
        futs = [pool.submit(fn) for fn in _SOURCES]
        for f in futs:
            try:
                items.extend(f.result() or [])
            except Exception:  # noqa: BLE001
                pass

    # dedup within intel (by id)
    seen, uniq = set(), []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        uniq.append(it)

    wire_index = [_toks(w.get("headline")) for w in (wire or [])]
    for it in uniq:
        covered = _covered_by_wire(it, wire_index) if wire_index else False
        # freshness bonus ONLY for genuinely timestamped EVENTS. Wiki spikes
        # (velocity-based) bake their signal into base_edge and pin ts to "now",
        # so a freshness bonus would falsely float them to the top.
        fresh = _freshness_bonus(it.get("ts")) if it.get("kind") in (
            "quake", "alert", "filing", "insider", "recall", "court", "gdelt") else 0
        edge = it.pop("base_edge", 50) + fresh
        if covered:
            edge = int(edge * 0.45)          # already a headline → little edge left
            it["why"] = "now confirmed by media"
        it["covered"] = covered
        it["edge"] = max(0, min(100, int(edge)))

    uniq.sort(key=lambda x: x["edge"], reverse=True)
    # per-source diversity: no single firehose (insider filings, alerts) should bury
    # the rest — cap each source so the desk shows a real MIX of primary signal.
    per, diverse = {}, []
    for it in uniq:
        s = it["source"]
        if per.get(s, 0) >= 6:
            continue
        per[s] = per.get(s, 0) + 1
        diverse.append(it)
    return diverse[:limit]


if __name__ == "__main__":
    import sys
    rows = refresh_intel(limit=30)
    print("== {} intel items ==".format(len(rows)))
    for r in rows:
        cov = " [covered]" if r.get("covered") else ""
        print("edge {:>3}  {:<11} {:<9} {}{}".format(
            r["edge"], r["source"], r["kind"], r["title"][:66], cov))
    sys.exit(0)
