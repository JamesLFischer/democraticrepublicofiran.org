#!/usr/bin/env python3
"""
V21 news updater.

The previous feed contained valid publisher image URLs, but many publishers reject
browser hot-linking. The page therefore loaded the article list and then lost every
story when those remote images returned 403/blocked responses in the visitor's browser.

V21 downloads the publisher-declared image during the GitHub Action, creates a small
JPEG thumbnail, and embeds it directly into data/news.json as a data:image URI.
The browser no longer contacts the publisher's image server, so category pages do not
empty themselves after rendering.

The updater also:
- preserves a previous good feed if a refresh fails;
- migrates old remote-image articles to embedded thumbnails;
- searches multiple Iran-focused terms for every category;
- selects image-backed stories in a category-balanced round-robin.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
import base64
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
    ("Democracy", '"Iran opposition" when:7d'),

    ("Civil Society", '"Iran civil society" when:7d'),
    ("Civil Society", '"Iran women" when:7d'),
    ("Civil Society", '"Iran students" when:7d'),

    ("Human Rights", '"Iran human rights" when:7d'),
    ("Human Rights", '"Iran political prisoners" when:7d'),
    ("Human Rights", '"Iran executions" when:7d'),

    ("Economy", '"Iran economy" when:7d'),
    ("Economy", '"Iran oil" when:7d'),
    ("Economy", '"Iran sanctions" when:7d'),

    ("Culture", '"Iran culture" when:7d'),
    ("Culture", '"Iran art" when:7d'),
    ("Culture", '"Iran film" when:7d'),

    ("Diaspora", '"Iran diaspora" when:7d'),
    ("Diaspora", '"Iranian diaspora" when:7d'),
    ("Diaspora", '"Iranian Americans" when:7d'),
]

BASE = "https://news.google.com/rss/search"
EDITION = {"hl": "en-US", "gl": "US", "ceid": "US:en"}

MAX_PER_FEED = 18
MAX_CANDIDATES = 120
TARGET_ARTICLES = 42
MAX_WORKERS = 6
TIMEOUT = 15
MAX_IMAGE_BYTES = 10_000_000
THUMB_WIDTH = 900
THUMB_HEIGHT = 520
JPEG_QUALITY = 76

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

def ensure_dependencies():
    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "googlenewsdecoder==0.2.1"
        ])
        from googlenewsdecoder import gnewsdecoder

    try:
        from PIL import Image, ImageOps
    except ImportError:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "Pillow>=10,<12"
        ])
        from PIL import Image, ImageOps

    return gnewsdecoder, Image, ImageOps

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
    try:
        result = gnewsdecoder(google_url, interval=0.45)
        if not isinstance(result, dict):
            return ""

        direct = result.get("decoded_url") or ""
        status = result.get("status", result.get("success"))

        if direct and status is not False and direct.startswith(("http://", "https://")):
            return direct
    except Exception:
        pass

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
            html = response.read(1_400_000).decode("utf-8", "ignore")
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

def thumbnail_data_uri(image_url: str, article_url: str, Image, ImageOps):
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        if article_url:
            headers["Referer"] = article_url

        req = Request(image_url, headers=headers)
        with urlopen(req, timeout=TIMEOUT) as response:
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "image/" not in ctype:
                return None

            data = response.read(MAX_IMAGE_BYTES + 1)

        if len(data) < 8_000 or len(data) > MAX_IMAGE_BYTES:
            return None

        source_hash = hashlib.sha1(data).hexdigest()

        with Image.open(BytesIO(data)) as im:
            im = im.convert("RGB")

            # Reject obviously tiny/icon-like assets.
            if im.width < 300 or im.height < 160:
                return None

            # Produce one predictable editorial thumbnail size.
            fitted = ImageOps.fit(
                im,
                (THUMB_WIDTH, THUMB_HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

            out = BytesIO()
            fitted.save(
                out,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )

        encoded = base64.b64encode(out.getvalue()).decode("ascii")
        return {
            "image_url": "data:image/jpeg;base64," + encoded,
            "source_image_url": image_url,
            "source_hash": source_hash,
        }

    except Exception:
        return None

def load_previous():
    if not OUT.exists():
        return {"articles": []}

    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"articles": []}

def migrate_previous(previous, Image, ImageOps):
    migrated = []
    seen_hashes = set()

    for article in previous.get("articles", []):
        image = article.get("image_url", "")

        # Already self-contained: preserve as-is.
        if isinstance(image, str) and image.startswith("data:image/"):
            clone = article.copy()
            hash_key = hashlib.sha1(image.encode("utf-8")).hexdigest()
            if hash_key in seen_hashes:
                continue
            seen_hashes.add(hash_key)
            migrated.append(clone)
            continue

        if not image or not image.startswith(("http://", "https://")):
            continue

        cached = thumbnail_data_uri(
            image,
            article.get("url", ""),
            Image,
            ImageOps,
        )
        if not cached or cached["source_hash"] in seen_hashes:
            continue

        seen_hashes.add(cached["source_hash"])
        clone = article.copy()
        clone["image_url"] = cached["image_url"]
        clone["source_image_url"] = cached["source_image_url"]
        migrated.append(clone)

    return migrated

def balanced_select(articles):
    category_order = [
        "Democracy",
        "Civil Society",
        "Human Rights",
        "Economy",
        "Culture",
        "Diaspora",
    ]

    buckets = {
        category: [a for a in articles if a.get("category") == category]
        for category in category_order
    }

    selected = []
    used = set()

    # First guarantee up to 4 per category when available.
    for round_index in range(4):
        for category in category_order:
            bucket = buckets[category]
            if round_index >= len(bucket):
                continue

            article = bucket[round_index]
            key = article.get("id") or article.get("url")
            if key in used:
                continue

            selected.append(article)
            used.add(key)

            if len(selected) >= TARGET_ARTICLES:
                return selected

    # Then fill remaining slots by publication recency.
    for article in sorted(articles, key=lambda a: a.get("published", ""), reverse=True):
        key = article.get("id") or article.get("url")
        if key in used:
            continue

        selected.append(article)
        used.add(key)

        if len(selected) >= TARGET_ARTICLES:
            break

    return selected

def main():
    gnewsdecoder, Image, ImageOps = ensure_dependencies()

    previous = load_previous()
    previous_cached = migrate_previous(previous, Image, ImageOps)

    collected = []
    errors = []

    for category, query in FEEDS:
        try:
            collected.extend(fetch_feed(category, query))
        except Exception as exc:
            errors.append(f"{category}: {exc}")
        time.sleep(0.18)

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

    resolved = []

    for idx, article in enumerate(candidates, 1):
        direct = decode_one(gnewsdecoder, article["google_news_url"])

        if direct:
            clone = article.copy()
            clone["url"] = direct
            clone["source_url"] = direct
            resolved.append(clone)

        print(f"Decoded {idx}/{len(candidates)} | successes: {len(resolved)}")

    def enrich(article):
        preview = get_preview_image(article["url"])
        if not preview:
            return article["id"], None

        embedded = thumbnail_data_uri(
            preview,
            article["url"],
            Image,
            ImageOps,
        )
        return article["id"], embedded

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

    new_articles = []
    seen_hashes = set()

    for article in resolved:
        embedded = image_map.get(article["id"])
        if not embedded:
            continue

        if embedded["source_hash"] in seen_hashes:
            continue
        seen_hashes.add(embedded["source_hash"])

        clone = article.copy()
        clone["image_url"] = embedded["image_url"]
        clone["source_image_url"] = embedded["source_image_url"]
        new_articles.append(clone)

    # Prefer fresh stories, then retain still-recent cached stories for category coverage.
    combined = []
    seen_ids = set()

    for article in new_articles + previous_cached:
        key = article.get("id") or article.get("url")
        if not key or key in seen_ids:
            continue
        seen_ids.add(key)
        combined.append(article)

    final_articles = balanced_select(combined)

    print(f"Candidates: {len(candidates)}")
    print(f"Publisher URLs resolved: {len(resolved)}")
    print(f"Fresh embedded-image stories: {len(new_articles)}")
    print(f"Previous stories successfully migrated: {len(previous_cached)}")
    print(f"Published stories: {len(final_articles)}")

    for category in [
        "Democracy", "Civil Society", "Human Rights",
        "Economy", "Culture", "Diaspora"
    ]:
        count = sum(1 for a in final_articles if a.get("category") == category)
        print(f"{category}: {count}")

    if not final_articles:
        print("ERROR: No image-backed stories are available; existing news.json was left untouched.")
        sys.exit(1)

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News discovery with self-contained publisher preview thumbnails",
        "queries": [
            {"category": category, "query": query.replace(" when:7d", "")}
            for category, query in FEEDS
        ],
        "articles": final_articles,
        "errors": errors,
        "stats": {
            "candidates": len(candidates),
            "publisher_urls_resolved": len(resolved),
            "fresh_embedded_image_stories": len(new_articles),
            "migrated_previous_stories": len(previous_cached),
            "published_stories": len(final_articles),
            "category_counts": {
                category: sum(1 for a in final_articles if a.get("category") == category)
                for category in [
                    "Democracy", "Civil Society", "Human Rights",
                    "Economy", "Culture", "Diaspora"
                ]
            },
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

if __name__ == "__main__":
    main()
