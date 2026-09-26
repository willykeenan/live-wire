#!/usr/bin/env python3
"""
Live Wire — expanded media sourcing (keyless, stdlib-only).

Two capabilities on top of the RSS newswire:

  google_news_rss(query)  — pull fresh headlines for ANY query via Google News'
                            public RSS (keyless). Lets the editorial desk and the
                            Wire Report chase a specific story/entity instead of
                            waiting for it to surface in the fixed feeds.
  resolve_image(...)      — an attribution-aware image ladder for b-roll:
                            1) og:image scraped from the article page itself
                            2) Wikipedia REST summary thumbnail for the entity
                            Every hit carries {image, image_source, image_credit,
                            image_link} so credit renders on air.

All fetches are throttled per-host and cached with a TTL — aggregators 429 fast
and this must never hammer anyone. Failures return None/[]; never raises.
"""
from __future__ import annotations

import json
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 8
def _tls_context():
    """Verified TLS. Prefer certifi's CA bundle (installed with requests)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


_SSL_CTX = _tls_context()

# per-host politeness: min seconds between requests to the same host
_HOST_GAP = {"news.google.com": 2.0, "en.wikipedia.org": 1.0}
_last_hit = {}
_hit_lock = threading.Lock()

_CACHE = {}          # key -> (expires_ts, value)
_CACHE_TTL = 900
_CACHE_MAX = 400


def _cache_get(key):
    v = _CACHE.get(key)
    if v and v[0] > time.time():
        return v[1]
    return None


def _cache_put(key, value):
    if len(_CACHE) > _CACHE_MAX:
        # drop the oldest half — simple, bounded
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _CACHE_MAX // 2]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.time() + _CACHE_TTL, value)


def _throttle(host):
    with _hit_lock:
        gap = _HOST_GAP.get(host, 0.5)
        last = _last_hit.get(host, 0.0)
        wait = gap - (time.time() - last)
        if wait > 0:
            time.sleep(min(wait, 3.0))
        _last_hit[host] = time.time()


def _get(url, max_bytes=400_000, accept=None):
    """Polite GET. Returns bytes or None; never raises."""
    try:
        host = urllib.parse.urlparse(url).netloc
        _throttle(host)
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA, "Accept": accept or "*/*"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as r:
            return r.read(max_bytes)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Google News RSS — keyless story-chasing
# ---------------------------------------------------------------------------

def google_news_rss(query, when="1d", limit=10):
    """Fresh items for `query` from Google News RSS, wire-item shaped.
    Returns [] on any failure."""
    q = (query or "").strip()
    if not q:
        return []
    key = f"gnews|{q.lower()}|{when}"
    hit = _cache_get(key)
    if hit is not None:
        return hit[:limit]

    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(f"{q} when:{when}")
           + "&hl=en-US&gl=US&ceid=US:en")
    raw = _get(url, accept="application/rss+xml, application/xml, text/xml")
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else "Google News"
        # Google News titles end " - Outlet"; strip the suffix, keep the outlet
        m = re.match(r"^(.*)\s+-\s+([^-]{2,40})$", title)
        if m:
            title, source = m.group(1).strip(), (m.group(2).strip() or source)
        ts = 0.0
        try:
            ts = parsedate_to_datetime(pub).timestamp()
        except Exception:  # noqa: BLE001
            pass
        if title:
            out.append({"headline": title, "summary": "", "source": source, "url": link,
                        "image": "", "video": "", "published_at": pub, "ts": ts,
                        "category": "world"})
    out.sort(key=lambda a: a["ts"], reverse=True)
    _cache_put(key, out)
    return out[:limit]


# ---------------------------------------------------------------------------
# Image ladder — og:image, then Wikipedia
# ---------------------------------------------------------------------------

_OG_IMG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)',
    re.I)
_OG_IMG_RE2 = re.compile(  # attribute order flipped
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image', re.I)


def og_image(article_url):
    """The article's own og:image — the most on-story photo there is."""
    if not article_url or not article_url.startswith("http"):
        return None
    key = "og|" + article_url
    hit = _cache_get(key)
    if hit is not None:
        return hit or None
    raw = _get(article_url, max_bytes=250_000, accept="text/html")
    img = None
    if raw:
        head = raw.decode("utf-8", "ignore")
        m = _OG_IMG_RE.search(head) or _OG_IMG_RE2.search(head)
        if m:
            img = m.group(1).strip()
            if img.startswith("//"):
                img = "https:" + img
            if not img.startswith("http"):
                img = None
    _cache_put(key, img or "")
    return img


_ENTITY_RE = re.compile(r"\b([A-Z][a-zA-Z'’.-]+(?:\s+[A-Z][a-zA-Z'’.-]+){0,3})")
_ENTITY_SKIP = {"The", "A", "An", "In", "On", "At", "As", "It", "Its", "New", "US", "UK",
                "Breaking", "Live", "Watch", "Why", "How", "What", "Who", "When"}


def _headline_entity(headline):
    """Best-guess proper-noun entity from a headline (for the Wikipedia rung)."""
    for m in _ENTITY_RE.finditer(headline or ""):
        cand = m.group(1).strip(".-'’ ")
        first = cand.split()[0]
        if first in _ENTITY_SKIP or len(cand) < 4:
            continue
        return cand
    return None


def wiki_thumb(entity):
    """Wikipedia REST summary thumbnail for an entity. Returns dict or None."""
    if not entity:
        return None
    key = "wiki|" + entity.lower()
    hit = _cache_get(key)
    if hit is not None:
        return hit or None
    url = ("https://en.wikipedia.org/api/rest_v1/page/summary/"
           + urllib.parse.quote(entity.replace(" ", "_")))
    raw = _get(url, accept="application/json")
    res = None
    if raw:
        try:
            data = json.loads(raw.decode("utf-8", "ignore"))
            thumb = (data.get("thumbnail") or {}).get("source")
            if thumb and data.get("type") != "disambiguation":
                res = {"image": thumb, "image_source": "Wikipedia",
                       "image_credit": "Wikipedia / " + (data.get("title") or entity),
                       "image_link": ((data.get("content_urls") or {}).get("desktop") or {}).get("page", "")}
        except Exception:  # noqa: BLE001
            res = None
    _cache_put(key, res or "")
    return res


def resolve_image(headline, article_url=None, source=None):
    """The ladder: article og:image → Wikipedia entity thumbnail.
    Returns {image, image_source, image_credit, image_link} or None."""
    img = og_image(article_url) if article_url else None
    if img:
        dom = urllib.parse.urlparse(article_url).netloc.replace("www.", "")
        return {"image": img, "image_source": source or dom,
                "image_credit": source or dom, "image_link": article_url or ""}
    hit = wiki_thumb(_headline_entity(headline))
    return hit


if __name__ == "__main__":
    print("gnews:", [i["headline"][:50] for i in google_news_rss("federal reserve", limit=3)])
    print("wiki:", wiki_thumb("Federal Reserve"))
