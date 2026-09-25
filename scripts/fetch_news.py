#!/usr/bin/env python3
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
MAX_PER_FEED = 35
MAX_CANDIDATES = 130
TARGET_ARTICLES = 45
MAX_WORKERS = 8
TIMEOUT = 12
MAX_IMAGE_BYTES = 8_000_000

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
        from PIL import Image
    except ImportError:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "Pillow>=10,<12"
        ])
        from PIL import Image

    return gnewsdecoder, Image

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
                "og:image",
                "og:image:url",
                "og:image:secure_url",
                "twitter:image",
                "twitter:image:src",
                "image",
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

def fetch_page_image_candidates(article_url: str) -> list[str]:
    try:
        req = Request(article_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urlopen(req, timeout=TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return []

            final_url = response.geturl()
            html = response.read(1_400_000).decode("utf-8", "ignore")
    except Exception:
        return []

    parser = PreviewParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    base = parser.base_href or final_url
    output = []
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
        if any(token in lower for token in (
            "favicon",
            "sprite",
            "avatar",
            "tracking",
            "pixel.",
            "/logo.",
            "/logos/",
            "default-image",
            "placeholder",
        )):
            continue

        output.append(absolute)

    return output[:8]

def validate_remote_image(url: str, Image):
    try:
        req = Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "",
        })

        with urlopen(req, timeout=TIMEOUT) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "image/" not in content_type:
                return None

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                return None

            data = response.read(MAX_IMAGE_BYTES + 1)

        if len(data) > MAX_IMAGE_BYTES or len(data) < 10_000:
            return None

        with Image.open(BytesIO(data)) as im:
            width, height = im.size

        # Reject logos, icons, portrait headshots, and tiny/generic preview graphics.
        if width < 640 or height < 320:
            return None

        ratio = width / max(height, 1)
        if ratio < 1.15 or ratio > 2.6:
            return None

        digest = hashlib.sha1(data).hexdigest()
        return {
            "url": url,
            "sha1": digest,
            "width": width,
            "height": height,
        }
    except Exception:
        return None

def decode_google_urls(articles, gnewsdecoder):
    urls = [article["google_news_url"] for article in articles]
    decoded = {}

    try:
        results = gnewsdecoder(
            urls,
            interval=None,
            timeout=15.0,
            concurrency=8,
        )
        if not isinstance(results, list):
            results = [results]

        for article, result in zip(articles, results):
            if not isinstance(result, dict):
                continue

            if result.get("success") and result.get("decoded_url"):
                decoded[article["id"]] = result["decoded_url"]
    except Exception as exc:
        print("Batch decode failed:", exc)

    return decoded

def main():
    gnewsdecoder, Image = ensure_dependencies()

    candidates = []
    feed_errors = []

    for category, query in FEEDS:
        try:
            candidates.extend(fetch_feed(category, query))
        except Exception as exc:
            feed_errors.append(f"{category}: {exc}")
        time.sleep(0.25)

    candidates.sort(key=lambda a: a["published"], reverse=True)

    seen_titles = set()
    seen_google_urls = set()
    deduped = []

    for article in candidates:
        title_key = normalized_title(article["title"])
        google_key = article["google_news_url"]

        if (title_key and title_key in seen_titles) or google_key in seen_google_urls:
            continue

        if title_key:
            seen_titles.add(title_key)
        seen_google_urls.add(google_key)
        deduped.append(article)

        if len(deduped) >= MAX_CANDIDATES:
            break

    decoded = decode_google_urls(deduped, gnewsdecoder)

    resolved_articles = []
    for article in deduped:
        direct_url = decoded.get(article["id"])
        if not direct_url or not direct_url.startswith(("http://", "https://")):
            continue

        article = article.copy()
        article["url"] = direct_url
        article["source_url"] = direct_url
        resolved_articles.append(article)

    print(f"Resolved {len(resolved_articles)}/{len(deduped)} publisher URLs.")

    def find_valid_image(article):
        for candidate in fetch_page_image_candidates(article["url"]):
            validated = validate_remote_image(candidate, Image)
            if validated:
                return article["id"], validated
        return article["id"], None

    image_results = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(find_valid_image, article): article["id"]
            for article in resolved_articles
        }

        for future in as_completed(futures):
            try:
                article_id, result = future.result()
                image_results[article_id] = result
            except Exception:
                image_results[futures[future]] = None

    final_articles = []
    seen_image_hashes = set()
    seen_image_urls = set()

    for article in resolved_articles:
        image = image_results.get(article["id"])
        if not image:
            continue

        if image["sha1"] in seen_image_hashes or image["url"] in seen_image_urls:
            continue

        seen_image_hashes.add(image["sha1"])
        seen_image_urls.add(image["url"])

        article = article.copy()
        article["image_url"] = image["url"]
        article["image_width"] = image["width"]
        article["image_height"] = image["height"]
        final_articles.append(article)

        if len(final_articles) >= TARGET_ARTICLES:
            break

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News discovery with original publisher preview photography",
        "queries": [
            {"category": category, "query": query.replace(" when:7d", "")}
            for category, query in FEEDS
        ],
        "articles": final_articles,
        "errors": feed_errors,
        "stats": {
            "candidates": len(deduped),
            "publisher_urls_resolved": len(resolved_articles),
            "articles_with_unique_valid_images": len(final_articles),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Published {len(final_articles)} articles.")
    print("Every published article has a unique validated external image.")
    if feed_errors:
        print("Feed notices:")
        for error in feed_errors:
            print(" -", error)

if __name__ == "__main__":
    main()
