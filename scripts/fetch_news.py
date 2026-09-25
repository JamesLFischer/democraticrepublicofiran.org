#!/usr/bin/env python3
"""
V8 updater: restore the image-fetching approach that previously produced real
article photos, while keeping the newer clean frontend/layout.

Key safeguards:
- Decode Google News links ONE AT A TIME (the earlier working approach).
- Pull og:image / twitter:image / JSON-LD image from the publisher page.
- Publish ONLY articles with real external images.
- Never overwrite a previously good feed with an empty/broken run.
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

MAX_PER_FEED = 28
MAX_CANDIDATES = 70
TARGET_ARTICLES = 36
MAX_WORKERS = 6
TIMEOUT = 14

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

def get_decoder():
    try:
        from googlenewsdecoder import gnewsdecoder
        return gnewsdecoder
    except ImportError:
        print("Installing Google News URL decoder...")
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
            "published": parse_date(pub).isoformat().replace("+00:00", "Z"),
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
        self.jsonld_chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): v for k, v in attrs if k and v}
        tag = tag.lower()

        if tag == "base" and attrs.get("href") and not self.base_href:
            self.base_href = attrs["href"]

        if tag == "meta":
            key = (
                attrs.get("property")
                or attrs.get("name")
                or attrs.get("itemprop")
                or ""
            ).lower()
            content = (attrs.get("content") or "").strip()

            if content and key in {
                "og:image", "og:image:url", "og:image:secure_url",
                "twitter:image", "twitter:image:src", "image"
            }:
                self.images.append(content)

        if tag == "link":
            rel = (attrs.get("rel") or "").lower()
            href = (attrs.get("href") or "").strip()
            if href and "image_src" in rel:
                self.images.append(href)

        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld_chunks = []

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld_chunks.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_jsonld:
            raw = "".join(self.jsonld_chunks).strip()
            self.in_jsonld = False
            self.jsonld_chunks = []
            if raw:
                try:
                    self._walk(json.loads(raw))
                except Exception:
                    pass

    def _walk(self, value):
        if isinstance(value, dict):
            image = value.get("image")

            if isinstance(image, str):
                self.images.append(image)
            elif isinstance(image, dict):
                candidate = image.get("url") or image.get("contentUrl")
                if isinstance(candidate, str):
                    self.images.append(candidate)
            elif isinstance(image, list):
                for item in image:
                    if isinstance(item, str):
                        self.images.append(item)
                    elif isinstance(item, dict):
                        candidate = item.get("url") or item.get("contentUrl")
                        if isinstance(candidate, str):
                            self.images.append(candidate)

            for child in value.values():
                if isinstance(child, (dict, list)):
                    self._walk(child)

        elif isinstance(value, list):
            for child in value:
                self._walk(child)

def decode_one(gnewsdecoder, google_url: str) -> str:
    """
    Deliberately use the simple per-URL call that worked in the earlier version.
    No batch/concurrency arguments are passed into the decoder itself.
    """
    try:
        result = gnewsdecoder(google_url, interval=None)
        if not isinstance(result, dict):
            return ""

        direct = result.get("decoded_url") or ""
        status = result.get("status", result.get("success"))

        if direct and status is not False and direct.startswith(("http://", "https://")):
            return direct
    except Exception as exc:
        print(f"Decode failed: {type(exc).__name__}: {exc}")

    return ""

def get_preview_image(article_url: str) -> str:
    try:
        req = Request(article_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })

        with urlopen(req, timeout=TIMEOUT) as response:
            if "html" not in (response.headers.get("Content-Type") or "").lower():
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

        if not absolute.startswith(("http://", "https://")):
            continue

        lower = absolute.lower()
        if any(token in lower for token in (
            "favicon", "sprite", "avatar", "tracking", "pixel.",
            "/logo.", "/logos/", "placeholder", "default-image"
        )):
            continue

        return absolute

    return ""

def load_previous():
    if not OUT.exists():
        return {"articles": []}

    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"articles": []}

def previous_good_articles(previous):
    output = []
    seen = set()

    for article in previous.get("articles", []):
        image = article.get("image_url", "")
        if not image or image in seen:
            continue
        seen.add(image)
        output.append(article)

    return output

def main():
    gnewsdecoder = get_decoder()
    previous = load_previous()
    old_good = previous_good_articles(previous)

    collected = []
    errors = []

    for category, query in FEEDS:
        try:
            collected.extend(fetch_feed(category, query))
        except Exception as exc:
            errors.append(f"{category}: {exc}")

        time.sleep(0.25)

    collected.sort(key=lambda a: a["published"], reverse=True)

    seen_titles = set()
    seen_google = set()
    candidates = []

    for article in collected:
        title_key = normalized_title(article["title"])
        google_key = article["google_news_url"]

        if (title_key and title_key in seen_titles) or google_key in seen_google:
            continue

        if title_key:
            seen_titles.add(title_key)

        seen_google.add(google_key)
        candidates.append(article)

        if len(candidates) >= MAX_CANDIDATES:
            break

    # Step 1: decode URLs serially using the earlier reliable call signature.
    resolved = []

    for idx, article in enumerate(candidates, 1):
        direct = decode_one(gnewsdecoder, article["google_news_url"])

        if direct:
            article = article.copy()
            article["url"] = direct
            article["source_url"] = direct
            resolved.append(article)

        print(f"Decoded {idx}/{len(candidates)} | successes: {len(resolved)}")

        if len(resolved) >= TARGET_ARTICLES * 2:
            break

    # Step 2: scrape preview images concurrently from the resolved publisher pages.
    def enrich(article):
        return article["id"], get_preview_image(article["url"])

    image_map = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(enrich, article): article["id"]
            for article in resolved
        }

        for future in as_completed(futures):
            article_id = futures[future]
            try:
                _, image = future.result()
                if image:
                    image_map[article_id] = image
            except Exception:
                pass

    final_articles = []
    seen_images = set()

    for article in resolved:
        image = image_map.get(article["id"], "")

        if not image or image in seen_images:
            continue

        seen_images.add(image)

        article = article.copy()
        article["image_url"] = image
        final_articles.append(article)

        if len(final_articles) >= TARGET_ARTICLES:
            break

    print(f"Candidates: {len(candidates)}")
    print(f"Publisher URLs resolved: {len(resolved)}")
    print(f"Real unique publisher images found: {len(final_articles)}")

    # Critical safety net: never turn a working site into a blank one again.
    if not final_articles:
        if old_good:
            print(
                f"WARNING: New run found 0 usable images. "
                f"Keeping previous {len(old_good)} image-backed stories instead."
            )
            return

        print(
            "ERROR: New run found 0 usable images and there is no previous good feed. "
            "news.json was NOT overwritten."
        )
        sys.exit(1)

    # If a later run is abnormally weak, preserve a clearly better prior feed.
    if old_good and len(final_articles) < 5 and len(old_good) > len(final_articles):
        print(
            f"WARNING: New run produced only {len(final_articles)} image-backed stories. "
            f"Keeping previous {len(old_good)}-story feed."
        )
        return

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News discovery with original publisher preview images",
        "queries": [
            {"category": category, "query": query.replace(" when:7d", "")}
            for category, query in FEEDS
        ],
        "articles": final_articles,
        "errors": errors,
        "stats": {
            "candidates": len(candidates),
            "publisher_urls_resolved": len(resolved),
            "articles_with_real_unique_images": len(final_articles),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Published {len(final_articles)} real-image stories.")

if __name__ == "__main__":
    main()
