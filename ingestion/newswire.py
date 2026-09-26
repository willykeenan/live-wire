#!/usr/bin/env python3
"""
newswire.py — 'our own' aggregated news wire for a 24/7 AI newsroom.

FREE, no-API-key RSS/Atom sources, Python 3.9 STDLIB ONLY
(urllib.request + xml.etree.ElementTree + concurrent.futures).

Parses BOTH RSS 2.0 (channel/item) and Atom (feed/entry) with xml.etree,
normalizes publish time to an epoch float + ISO-8601 string, strips HTML
from summaries, dedupes by normalized title, fetches every source
concurrently, and never raises on a dead feed (it is simply skipped).

Public contract (the server imports these):
    SOURCES                                  -> list[dict]
    fetch_wire(limit=60, categories=None)    -> list[dict]

CLI: ``python -m ingestion.newswire`` (or run as a script) prints the
top 10 items as ``SOURCE | rel-age | headline``.
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ---------------------------------------------------------------------------
# Sources — 19 VERIFIED free feeds (no API key required).
# ---------------------------------------------------------------------------
SOURCES = [
    {"name": "CNBC Top News",     "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",  "category": "business"},
    {"name": "CNBC Markets",      "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html",   "category": "markets"},
    {"name": "MarketWatch Top",   "url": "http://feeds.marketwatch.com/marketwatch/topstories/",   "category": "markets"},
    {"name": "Yahoo Finance",     "url": "https://finance.yahoo.com/news/rssindex",                "category": "markets"},
    {"name": "NPR News",          "url": "https://feeds.npr.org/1001/rss.xml",                     "category": "us"},
    {"name": "NPR Politics",      "url": "https://feeds.npr.org/1014/rss.xml",                     "category": "politics"},
    {"name": "BBC News",          "url": "https://feeds.bbci.co.uk/news/rss.xml",                  "category": "world"},
    {"name": "BBC Business",      "url": "https://feeds.bbci.co.uk/news/business/rss.xml",         "category": "business"},
    {"name": "BBC Technology",    "url": "https://feeds.bbci.co.uk/news/technology/rss.xml",       "category": "tech"},
    {"name": "The Verge",         "url": "https://www.theverge.com/rss/index.xml",                 "category": "tech"},
    {"name": "Ars Technica",      "url": "https://feeds.arstechnica.com/arstechnica/index",        "category": "tech"},
    {"name": "Hacker News",       "url": "https://hnrss.org/frontpage",                            "category": "tech"},
    {"name": "Guardian World",    "url": "https://www.theguardian.com/world/rss",                  "category": "world"},
    {"name": "Guardian Business", "url": "https://www.theguardian.com/business/rss",               "category": "business"},
    {"name": "Al Jazeera",        "url": "https://www.aljazeera.com/xml/rss/all.xml",              "category": "world"},
    {"name": "NYT World",         "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",  "category": "world"},
    {"name": "NYT Politics",      "url": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "category": "politics"},
    {"name": "Politico",          "url": "https://rss.politico.com/politics-news.xml",             "category": "politics"},
    {"name": "The Hill",          "url": "https://thehill.com/news/feed/",                         "category": "politics"},
]

# One wire. Business and markets feeds are ordinary news categories here; there
# is no trading terminal, ticker feed, or market-data lane in this edition.

# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"

# Namespaces. RSS items live in the default (no) namespace; Atom in its own.
_ATOM = "http://www.w3.org/2005/Atom"
_DC = "http://purl.org/dc/elements/1.1/"
_MRSS = "http://search.yahoo.com/mrss/"
_NS = {"a": _ATOM, "dc": _DC}

_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
_VID_EXT = (".mp4", ".webm", ".m4v")   # browser-playable in a plain <video> (skip HLS/.m3u8)
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.I)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# The viewer startup contract has an 8 second request ceiling. Keep every
# source attempt below that ceiling and fan the bounded source set out in one
# wave so a dead first feed cannot turn 19 feeds into two serial timeout waves.
_TIMEOUT = 4  # per-source seconds
_MAX_WORKERS = 20  # SOURCES is bounded to 19 entries
_SUMMARY_MAX = 280

def _tls_context():
    """Verified TLS. Prefer certifi's CA bundle (installed with requests)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


_SSL_CTX = _tls_context()


def _host_is_private(host):
    """True if the host resolves to a loopback/private/link-local/reserved address —
    i.e. somewhere an SSRF should never reach."""
    if not host:
        return True
    if host.lower() in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True   # can't resolve -> refuse
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def _assert_safe_url(url):
    """Guard against SSRF / local-file reads: /api/media?url= is public and its value
    flows here. Only http/https to a public host is allowed. Raises on violation."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise ValueError("blocked scheme")
    if _host_is_private(parts.hostname):
        raise ValueError("blocked host")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but re-validate every hop — a 30x to an internal host must
    not bypass the pre-flight SSRF check (feeds legitimately redirect http->https)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_safe_url(newurl)   # raises on scheme/host violation
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# keep the relaxed cert context (some feed CDNs trip strict verification) + safe redirects
_SAFE_OPENER = urllib.request.build_opener(_SafeRedirect, urllib.request.HTTPSHandler(context=_SSL_CTX))


def _fetch(url, timeout=_TIMEOUT):
    """Fetch raw bytes for a URL with a browser User-Agent. Enforces the SSRF guard
    (scheme + public-host, redirects re-validated). May raise."""
    _assert_safe_url(url)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": _ACCEPT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with _SAFE_OPENER.open(req, timeout=timeout) as resp:
        return resp.read()


def _strip_html(s):
    """Strip HTML tags, unescape entities, collapse whitespace."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def _parse_date(s):
    """Return (epoch_float, iso_str) for an RFC-822 or ISO-8601 date.

    (0.0, '') if the string is empty or unparseable. Naive datetimes are
    assumed UTC; the ISO output is always normalized to UTC.
    """
    if not s:
        return (0.0, "")
    s = s.strip()
    # RFC 822 (RSS pubDate: 'Sat, 27 Jun 2026 13:21:37 GMT' / '... +0000')
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt.timestamp(), dt.astimezone(timezone.utc).isoformat())
    except (TypeError, ValueError, IndexError):
        pass
    # ISO 8601 (Atom updated/published; Yahoo RSS: '2026-06-26T21:08:36Z').
    # Python 3.9 fromisoformat rejects 'Z', so normalize it first.
    try:
        iso = s.replace("Z", "+00:00").replace("z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.timestamp(), dt.astimezone(timezone.utc).isoformat())
    except ValueError:
        return (0.0, "")


def _text(el):
    return el.text.strip() if el is not None and el.text else ""


def _looks_image(url):
    return bool(url) and url.split("?")[0].lower().endswith(_IMG_EXT)


def _image(el, summary_raw=""):
    """Best-effort scene image for an RSS item / Atom entry. '' if none found.
    Scans media:thumbnail/content, <enclosure>, atom/itunes image links, then an
    <img> in the description; returns the largest candidate. Never raises.
    (Tags are matched by local name because ET tags are '{namespace}local'.)"""
    try:
        cands = []  # (url, approx_width)
        for node in el.iter():
            tag = node.tag if isinstance(node.tag, str) else ""
            local = tag.rsplit("}", 1)[-1].lower()
            url = node.get("url") or node.get("href")
            try:
                w = int(node.get("width") or 0)
            except (TypeError, ValueError):
                w = 0
            typ = (node.get("type") or node.get("medium") or "").lower()
            if local == "thumbnail" and url:
                cands.append((url, w or 200))
            elif local == "content" and url and ("image" in typ or _looks_image(url)):
                cands.append((url, w or 400))
            elif local == "enclosure" and url and (typ.startswith("image") or _looks_image(url)):
                cands.append((url, w or 500))
            elif local == "link" and node.get("rel") == "enclosure" and url and (typ.startswith("image") or _looks_image(url)):
                cands.append((url, w or 500))
            elif local == "image" and node.get("href"):
                cands.append((node.get("href"), w or 300))
        if not cands and summary_raw:
            m = _IMG_SRC_RE.search(summary_raw)
            if m:
                cands.append((m.group(1), 300))
        if not cands:
            return ""
        cands.sort(key=lambda c: c[1], reverse=True)
        return cands[0][0]
    except Exception:
        return ""


def _looks_video(url):
    return bool(url) and url.split("?")[0].lower().endswith(_VID_EXT)


def _video(el):
    """Best-effort playable scene video (mp4/webm) for an item. '' if none."""
    try:
        best = ("", 0)
        for node in el.iter():
            tag = node.tag if isinstance(node.tag, str) else ""
            local = tag.rsplit("}", 1)[-1].lower()
            url = node.get("url") or node.get("href")
            typ = (node.get("type") or node.get("medium") or "").lower()
            try:
                w = int(node.get("width") or 0)
            except (TypeError, ValueError):
                w = 0
            is_vid = bool(url) and (("video" in typ and "youtube" not in url) or _looks_video(url))
            if is_vid and (local in ("content", "enclosure")
                           or (local == "link" and node.get("rel") == "enclosure")):
                if w >= best[1]:
                    best = (url, w or 1)
        return best[0]
    except Exception:
        return ""


# Resolve a *playable* clip from a story's article page (og:video mp4 or an
# embedded YouTube). This is how we get real footage — RSS rarely ships raw video.
_OG_VIDEO_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)', re.I)
_YT_RE = re.compile(r'(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})')
_YT_ID_RE = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
_VIDEO_CACHE = {}
_VIDEO_TTL = 3600


def _youtube_search(query, timeout=6):
    """First videoId for a YouTube search of the query. '' on failure. No API key."""
    if not query:
        return ""
    try:
        from urllib.parse import quote_plus
        u = "https://www.youtube.com/results?search_query=" + quote_plus(query + " news")
        txt = _fetch(u, timeout=timeout).decode("utf-8", "ignore")
        m = _YT_ID_RE.search(txt)
        return m.group(1) if m else ""
    except Exception:
        return ""


def resolve_video(url="", query="", timeout=6):
    """Resolve real footage for a story: a relevant YouTube clip (by headline),
    else an og:video/embedded clip on the article page. Returns
    {'video':..., 'type':'youtube'|'mp4'} or {}. Cached 1h. Never raises.
    Free sites block their own video, so YouTube is the practical source."""
    ck = (query or "") + "|" + (url or "")
    now = time.time()
    hit = _VIDEO_CACHE.get(ck)
    if hit and now - hit[0] < _VIDEO_TTL:
        return hit[1]
    res = {}
    if query:                                     # primary: a relevant clip for the headline
        vid = _youtube_search(query, timeout)
        if vid:
            res = {"video": vid, "type": "youtube"}
    if not res and url:                           # bonus: exact footage embedded on the page
        try:
            txt = _fetch(url, timeout=timeout).decode("utf-8", "ignore")
            m = _OG_VIDEO_RE.search(txt)
            if m and _looks_video(html.unescape(m.group(1))):
                res = {"video": html.unescape(m.group(1)), "type": "mp4"}
            else:
                yt = _YT_RE.search(txt)
                if yt:
                    res = {"video": yt.group(1), "type": "youtube"}
        except Exception:
            pass
    _VIDEO_CACHE[ck] = (now, res)
    if len(_VIDEO_CACHE) > 500:   # bound the cache (keys are public, attacker-influenced)
        for k in sorted(_VIDEO_CACHE, key=lambda k: _VIDEO_CACHE[k][0])[:250]:
            _VIDEO_CACHE.pop(k, None)
    return res


def _item_to_record(title, link, summary, date_raw, source, category, image="", video=""):
    epoch, iso = _parse_date(date_raw)
    return {
        "headline": _strip_html(title),
        "summary": _strip_html(summary)[:_SUMMARY_MAX],
        "source": source,
        "url": link,
        "image": image or "",
        "video": video or "",
        "published_at": iso,
        "ts": epoch,
        "category": category,
    }


def _parse_feed(raw, source, category):
    """Parse RSS or Atom bytes into a list of normalized records."""
    root = ET.fromstring(raw)
    out = []

    # --- RSS 2.0 / RDF: channel/item (item in the no-namespace tree) ---
    items = root.findall(".//item")
    if items:
        for it in items:
            title = _text(it.find("title"))
            link = _text(it.find("link"))
            summary = _text(it.find("description"))
            # date: pubDate (RSS) or dc:date (RDF / Dublin Core) fallback
            date_raw = _text(it.find("pubDate")) or _text(it.find("dc:date", _NS))
            out.append(_item_to_record(title, link, summary, date_raw, source, category,
                                       image=_image(it, summary), video=_video(it)))
        return out

    # --- Atom: feed/entry ---
    for e in root.findall(".//a:entry", _NS):
        title = _text(e.find("a:title", _NS))
        # link: prefer rel="alternate" (or no rel) href, else first link href
        link = ""
        for ln in e.findall("a:link", _NS):
            if ln.get("rel", "alternate") == "alternate":
                link = ln.get("href", "")
                break
        if not link:
            ln = e.find("a:link", _NS)
            link = ln.get("href", "") if ln is not None else ""
        summary = _text(e.find("a:summary", _NS)) or _text(e.find("a:content", _NS))
        date_raw = _text(e.find("a:published", _NS)) or _text(e.find("a:updated", _NS))
        out.append(_item_to_record(title, link, summary, date_raw, source, category,
                                   image=_image(e, summary), video=_video(e)))
    return out


def _fetch_one(source):
    """Fetch + parse a single source. Never raises — returns [] on failure."""
    try:
        raw = _fetch(source["url"])
        return _parse_feed(raw, source["name"], source["category"])
    except Exception:
        # A dead/slow/malformed feed must not take down the wire.
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_wire(limit=60, categories=None):
    """Aggregate sources into one deduped, newest-first news wire.

    Parameters
    ----------
    limit : int
        Maximum number of items to return.
    categories : iterable[str] | None
        If given, only items whose ``category`` is in this set are kept.

    Returns
    -------
    list[dict] — each item has keys: headline, summary, source, url,
    published_at, ts, category. Never raises; dead feeds are skipped.
    """
    cat_filter = set(categories) if categories else None
    pool_sources = SOURCES

    records = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for batch in pool.map(_fetch_one, pool_sources):
            records.extend(batch)

    seen = set()
    wire = []
    for rec in records:
        if cat_filter is not None and rec["category"] not in cat_filter:
            continue
        key = _WS_RE.sub(" ", rec["headline"].lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        wire.append(rec)

    # newest first; undated items (ts 0.0) sink to the bottom
    wire.sort(key=lambda a: a["ts"], reverse=True)

    if limit is not None and limit >= 0:
        wire = wire[:limit]
    return wire


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _rel_age(ts):
    """Human relative age for an epoch timestamp, e.g. '3m', '2h', '1d'."""
    if not ts:
        return "  ?"
    delta = datetime.now(timezone.utc).timestamp() - ts
    if delta < 0:
        delta = 0
    secs = int(delta)
    if secs < 60:
        return "%ds" % secs
    mins = secs // 60
    if mins < 60:
        return "%dm" % mins
    hours = mins // 60
    if hours < 24:
        return "%dh" % hours
    return "%dd" % (hours // 24)


def main(argv=None):
    wire = fetch_wire(limit=60)
    print("=== Live Wire: top 10 of %d items ===" % len(wire))
    for item in wire[:10]:
        print("%-18s | %4s | %s" % (
            item["source"][:18],
            _rel_age(item["ts"]),
            item["headline"][:90],
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
