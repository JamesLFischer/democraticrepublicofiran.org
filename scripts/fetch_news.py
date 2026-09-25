#!/usr/bin/env python3
"""
Update the Iran news feed and attach each story's publisher-declared preview image.

Important implementation detail:
The existing GitHub workflow only calls this Python file. To avoid another hidden
.github upload problem, this script installs its pinned Google News URL decoder
itself if the package is not already present.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
import hashlib
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "news.json"

FEEDS = [
    ("Democracy", '"democratic Iran" when:7d'),
    ("Democracy", '"Iran democracy" when:7d'),
    ("Civil Society", '"Iran civil society" when:7d'),
    ("Human Rights", '"Iran human rights" when:7d'),
    ("Economy", '"Iran economy" when:7d'),
    ("Culture", '"Iran culture" when:7d'),
    ("Diaspora", '"Iran diaspora" when:7d'),
]

BASE = "https://news.google.com/rss/search"
EDITION = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
MAX_PER_FEED = 30
MAX_TOTAL = 90
MAX_WORKERS = 8
TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

def get_decoder():
    try:
        from googlenewsdecoder import gnewsdecoder
        return gnewsdecoder
    except ImportError:
        print("googlenewsdecoder is missing; installing pinned version 0.2.1...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "googlenewsdecoder==0.2.1"
        ])
        from googlenewsdecoder import gnewsdecoder
        return gnewsdecoder

def clean(text: str | None) -> str:
    if not text:
        return ""
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()

def normalized_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"\s+-\s+[^-]{2,100}$", "", title)
    title = re.sub(r"[^a-z0-9\s]", "", title)
    return re.sub(r"\s+", " ", title).strip()

def parse_date(value: str) -> datetime:
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)

def fetch_feed(category: str, query: str) -> list[dict]:
    params = dict(EDITION)
    params["q"] = query
    url = f"{BASE}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=25) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)
    items = []
    for item in root.findall(".//item")[:MAX_PER_FEED]:
        title = clean(item.findtext("title"))
        link = clean(item.findtext("link"))
        pub = clean(item.findtext("pubDate"))
        source_node = item.find("source")
        source = clean(source_node.text if source_node is not None else "")
        source_home = source_node.attrib.get("url", "") if source_node is not None else ""

        if not title or not link:
            continue

        dt = parse_date(pub)
        item_id = hashlib.sha1(
            (normalized_title(title) or link).encode("utf-8")
        ).hexdigest()[:16]

        items.append({
            "id": item_id,
            "title": title,
            "url": link,
            "google_news_url": link,
            "source": source or "Publisher",
            "source_url": source_home,
            "published": dt.isoformat().replace("+00:00", "Z"),
            "category": category,
            "query": query.replace(" when:7d", ""),
            "image_url": "",
        })
    return items

class PreviewParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.base_href = ""
        self.in_jsonld = False
        self.jsonld = []

    def handle_starttag(self, tag, attrs):
        a = {str(k).lower(): v for k, v in attrs if k and v}
        tag = tag.lower()

        if tag == "base" and a.get("href") and not self.base_href:
            self.base_href = a["href"]

        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            content = (a.get("content") or "").strip()
            if content and key in {
                "og:image", "og:image:url", "og:image:secure_url",
                "twitter:image", "twitter:image:src", "image"
            }:
                self.images.append(content)

        if tag == "link":
            rel = (a.get("rel") or "").lower()
            href = (a.get("href") or "").strip()
            if href and "image_src" in rel:
                self.images.append(href)

        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld = []

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_jsonld:
            raw = "".join(self.jsonld).strip()
            self.in_jsonld = False
            self.jsonld = []
            if not raw:
                return
            try:
                self._walk_json(json.loads(raw))
            except Exception:
                pass

    def _walk_json(self, obj):
        if isinstance(obj, dict):
            image = obj.get("image")
            if isinstance(image, str):
                self.images.append(image)
            elif isinstance(image, dict):
                value = image.get("url") or image.get("contentUrl")
                if isinstance(value, str):
                    self.images.append(value)
            elif isinstance(image, list):
                for value in image:
                    if isinstance(value, str):
                        self.images.append(value)
                    elif isinstance(value, dict):
                        u = value.get("url") or value.get("contentUrl")
                        if isinstance(u, str):
                            self.images.append(u)
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    self._walk_json(value)
        elif isinstance(obj, list):
            for value in obj:
                self._walk_json(value)

def fetch_preview_image(article_url: str) -> str:
    try:
        req = Request(article_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urlopen(req, timeout=TIMEOUT) as response:
            ctype = response.headers.get("Content-Type", "")
            if "html" not in ctype.lower():
                return ""
            final_url = response.geturl()
            html = response.read(1_250_000).decode("utf-8", "ignore")
    except Exception:
        return ""

    parser = PreviewParser()
    try:
        parser.feed(html)
    except Exception:
        return ""

    base = parser.base_href or final_url
    seen = set()
    for candidate in parser.images:
        candidate = unescape(str(candidate).strip())
        if not candidate:
            continue
        absolute = urljoin(base, candidate)
        if absolute in seen:
            continue
        seen.add(absolute)
        if not absolute.startswith(("https://", "http://")):
            continue
        lower = absolute.lower()
        if any(x in lower for x in (
            "favicon", "sprite", "avatar", "tracking", "pixel.", "/logo.", "/logos/"
        )):
            continue
        return absolute
    return ""

def load_previous() -> dict[str, dict]:
    if not OUT.exists():
        return {}
    try:
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        return {
            a["id"]: a
            for a in payload.get("articles", [])
            if isinstance(a, dict) and a.get("id")
        }
    except Exception:
        return {}

def main():
    articles = []
    feed_errors = []

    for category, query in FEEDS:
        try:
            articles.extend(fetch_feed(category, query))
        except Exception as exc:
            feed_errors.append(f"{category}: {exc}")
        time.sleep(0.35)

    articles.sort(key=lambda a: a["published"], reverse=True)

    seen_titles, seen_urls, deduped = set(), set(), []
    for article in articles:
        tkey = normalized_title(article["title"])
        ukey = article["google_news_url"]
        if (tkey and tkey in seen_titles) or ukey in seen_urls:
            continue
        if tkey:
            seen_titles.add(tkey)
        seen_urls.add(ukey)
        deduped.append(article)
        if len(deduped) >= MAX_TOTAL:
            break

    previous = load_previous()

    # Current decoder supports batching; one batch POST is faster and more reliable
    # than independently resolving every Google News wrapper.
    gnewsdecoder = get_decoder()
    google_urls = [a["google_news_url"] for a in deduped]
    try:
        decoded = gnewsdecoder(google_urls, interval=None, timeout=15.0)
        if not isinstance(decoded, list):
            decoded = [decoded]
    except Exception as exc:
        print(f"Google URL batch decode failed: {exc}")
        decoded = []

    resolved = 0
    for i, article in enumerate(deduped):
        result = decoded[i] if i < len(decoded) and isinstance(decoded[i], dict) else {}
        direct = result.get("decoded_url") if result.get("success") else None

        if direct and direct.startswith(("http://", "https://")):
            article["url"] = direct
            resolved += 1
        else:
            old = previous.get(article["id"], {})
            old_url = old.get("url", "")
            if old_url and "news.google.com/" not in old_url:
                article["url"] = old_url

    def enrich(article: dict) -> tuple[str, str]:
        old_image = previous.get(article["id"], {}).get("image_url", "")
        url = article.get("url", "")
        if not url or "news.google.com/" in url:
            return article["id"], old_image
        image = fetch_preview_image(url)
        return article["id"], image or old_image

    image_map = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(enrich, a) for a in deduped]
        for future in as_completed(futures):
            try:
                article_id, image = future.result()
                image_map[article_id] = image
            except Exception:
                pass

    for article in deduped:
        article["image_url"] = image_map.get(article["id"], "")

    image_count = sum(bool(a.get("image_url")) for a in deduped)

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News search discovery with publisher-declared preview metadata",
        "queries": [{"category": c, "query": q.replace(" when:7d", "")} for c, q in FEEDS],
        "articles": deduped,
        "errors": feed_errors,
        "stats": {
            "articles": len(deduped),
            "publisher_urls_resolved": resolved,
            "preview_images_found": image_count,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )

    print(f"Wrote {len(deduped)} articles.")
    print(f"Resolved {resolved}/{len(deduped)} publisher URLs.")
    print(f"Found {image_count}/{len(deduped)} publisher preview images.")
    if feed_errors:
        print("Feed errors:")
        for error in feed_errors:
            print(f" - {error}")

if __name__ == "__main__":
    main()
