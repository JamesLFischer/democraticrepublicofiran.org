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
MEDIA_NS = "http://search.yahoo.com/mrss/"

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
        from PIL import Image
    except ImportError:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "Pillow>=10,<12"
        ])
        from PIL import Image

    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install",
                "--disable-pip-version-check", "googlenewsdecoder==0.2.1"
            ])
            from googlenewsdecoder import gnewsdecoder
        except Exception:
            gnewsdecoder = None

    return Image, gnewsdecoder

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

def normalize_image_url(value: str) -> str:
    value = unescape((value or "").strip())
    if value.startswith("//"):
        value = "https:" + value
    return value if value.startswith(("https://", "http://")) else ""

class DescriptionImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "img":
            return
        attrs = {str(k).lower(): v for k, v in attrs if k and v}
        for key in ("src", "data-src", "data-original", "data-lazy-src"):
            value = normalize_image_url(attrs.get(key, ""))
            if value:
                self.images.append(value)

def rss_image_candidates(item: ET.Element) -> list[str]:
    candidates = []

    for tag in (
        f"{{{MEDIA_NS}}}content",
        f"{{{MEDIA_NS}}}thumbnail",
    ):
        for node in item.findall(tag):
            value = normalize_image_url(node.attrib.get("url", ""))
            if value:
                candidates.append(value)

    for node in item.findall("enclosure"):
        if "image" in (node.attrib.get("type") or "").lower():
            value = normalize_image_url(node.attrib.get("url", ""))
            if value:
                candidates.append(value)

    description = item.findtext("description") or ""
    if description:
        parser = DescriptionImageParser()
        try:
            parser.feed(description)
            candidates.extend(parser.images)
        except Exception:
            pass

        for match in re.findall(r"(?:src|data-src)=[\"']([^\"']+)[\"']", description, re.I):
            value = normalize_image_url(match)
            if value:
                candidates.append(value)

    output, seen = [], set()
    for value in candidates:
        if value in seen:
            continue
        seen.add(value)
        lower = value.lower()
        if any(token in lower for token in (
            "favicon", "sprite", "avatar", "tracking", "pixel.", "logo", "placeholder"
        )):
            continue
        output.append(value)

    return output[:10]

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
            "rss_image_candidates": rss_image_candidates(item),
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

def publisher_image_candidates(article_url: str) -> list[str]:
    try:
        req = Request(article_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urlopen(req, timeout=TIMEOUT) as response:
            if "html" not in (response.headers.get("Content-Type") or "").lower():
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
    output, seen = [], set()

    for candidate in parser.images:
        candidate = normalize_image_url(urljoin(base, str(candidate).strip()))
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        lower = candidate.lower()
        if any(token in lower for token in (
            "favicon", "sprite", "avatar", "tracking", "pixel.",
            "/logo.", "/logos/", "default-image", "placeholder"
        )):
            continue

        output.append(candidate)

    return output[:8]

def validate_remote_image(url: str, Image):
    try:
        req = Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        })

        with urlopen(req, timeout=TIMEOUT) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "image/" not in content_type:
                return None

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                return None

            data = response.read(MAX_IMAGE_BYTES + 1)

        if len(data) > MAX_IMAGE_BYTES or len(data) < 8_000:
            return None

        with Image.open(BytesIO(data)) as im:
            width, height = im.size

        if width < 300 or height < 160:
            return None

        ratio = width / max(height, 1)
        if ratio < 1.05 or ratio > 3.0:
            return None

        return {
            "url": url,
            "sha1": hashlib.sha1(data).hexdigest(),
            "width": width,
            "height": height,
        }

    except Exception:
        return None

def try_decode(article, gnewsdecoder):
    if not gnewsdecoder:
        return ""

    try:
        result = gnewsdecoder(
            article["google_news_url"],
            interval=0.25,
            timeout=15.0,
        )

        if isinstance(result, dict):
            ok = result.get("success")
            if ok is None:
                ok = result.get("status")

            direct = result.get("decoded_url")
            if ok is not False and direct and direct.startswith(("http://", "https://")):
                return direct

    except Exception:
        pass

    return ""

def main():
    Image, gnewsdecoder = ensure_dependencies()

    candidates = []
    feed_errors = []

    for category, query in FEEDS:
        try:
            candidates.extend(fetch_feed(category, query))
        except Exception as exc:
            feed_errors.append(f"{category}: {exc}")

        time.sleep(0.25)

    candidates.sort(key=lambda a: a["published"], reverse=True)

    seen_titles, seen_google_urls, deduped = set(), set(), []

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

    def enrich(article):
        # Primary path: use real imagery already exposed by the Google News RSS item.
        for candidate in article.get("rss_image_candidates", []):
            valid = validate_remote_image(candidate, Image)
            if valid:
                return article["id"], "", valid, "rss"

        # Secondary path: decode the Google wrapper and inspect the original publisher page.
        direct = try_decode(article, gnewsdecoder)

        if direct:
            for candidate in publisher_image_candidates(direct):
                valid = validate_remote_image(candidate, Image)
                if valid:
                    return article["id"], direct, valid, "publisher"

        return article["id"], direct, None, "none"

    results = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(enrich, article): article["id"]
            for article in deduped
        }

        for future in as_completed(futures):
            try:
                article_id, direct, image, origin = future.result()
                results[article_id] = {
                    "direct_url": direct,
                    "image": image,
                    "origin": origin,
                }
            except Exception:
                pass

    final_articles = []
    seen_image_hashes = set()
    seen_image_urls = set()

    rss_image_count = 0
    publisher_image_count = 0
    decoded_count = 0

    for article in deduped:
        result = results.get(article["id"], {})
        image = result.get("image")

        if not image:
            continue

        if image["sha1"] in seen_image_hashes or image["url"] in seen_image_urls:
            continue

        seen_image_hashes.add(image["sha1"])
        seen_image_urls.add(image["url"])

        article = article.copy()
        article.pop("rss_image_candidates", None)

        if result.get("direct_url"):
            article["url"] = result["direct_url"]
            article["source_url"] = result["direct_url"]
            decoded_count += 1

        article["image_url"] = image["url"]
        article["image_width"] = image["width"]
        article["image_height"] = image["height"]
        article["image_origin"] = result.get("origin", "unknown")

        if article["image_origin"] == "rss":
            rss_image_count += 1
        elif article["image_origin"] == "publisher":
            publisher_image_count += 1

        final_articles.append(article)

        if len(final_articles) >= TARGET_ARTICLES:
            break

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News discovery using real RSS/publisher article imagery",
        "queries": [
            {"category": category, "query": query.replace(" when:7d", "")}
            for category, query in FEEDS
        ],
        "articles": final_articles,
        "errors": feed_errors,
        "stats": {
            "candidates": len(deduped),
            "published_articles": len(final_articles),
            "rss_images_used": rss_image_count,
            "publisher_images_used": publisher_image_count,
            "publisher_urls_decoded": decoded_count,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Candidates: {len(deduped)}")
    print(f"Published articles with unique real images: {len(final_articles)}")
    print(f"Images obtained directly from Google News RSS: {rss_image_count}")
    print(f"Images obtained from publisher metadata: {publisher_image_count}")
    print(f"Publisher URLs decoded: {decoded_count}")

    if not final_articles:
        print("WARNING: No image-qualified stories were found in this run.")

    if feed_errors:
        print("Feed notices:")
        for error in feed_errors:
            print(" -", error)

if __name__ == "__main__":
    main()
