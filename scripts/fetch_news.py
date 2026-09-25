#!/usr/bin/env python3
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
TARGET_TOTAL = 42
MAX_TOTAL_CANDIDATES = 120
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
        item_id = hashlib.sha1((normalized_title(title) or link).encode("utf-8")).hexdigest()[:16]
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
            if raw:
                try:
                    self._walk(json.loads(raw))
                except Exception:
                    pass

    def _walk(self, obj):
        if isinstance(obj, dict):
            image = obj.get("image")
            if isinstance(image, str):
                self.images.append(image)
            elif isinstance(image, dict):
                v = image.get("url") or image.get("contentUrl")
                if isinstance(v, str):
                    self.images.append(v)
            elif isinstance(image, list):
                for item in image:
                    if isinstance(item, str):
                        self.images.append(item)
                    elif isinstance(item, dict):
                        v = item.get("url") or item.get("contentUrl")
                        if isinstance(v, str):
                            self.images.append(v)
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    self._walk(value)
        elif isinstance(obj, list):
            for value in obj:
                self._walk(value)

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

def decode_urls(articles):
    gnewsdecoder = get_decoder()
    urls = [a["google_news_url"] for a in articles]
    decoded_map = {}

    # First try a batch decode.
    try:
        batch = gnewsdecoder(urls, interval=None, timeout=15.0)
        if not isinstance(batch, list):
            batch = [batch]
        for article, result in zip(articles, batch):
            if isinstance(result, dict):
                direct = result.get("decoded_url")
                ok = result.get("success") if "success" in result else result.get("status")
                if direct and ok is not False:
                    decoded_map[article["id"]] = direct
    except Exception:
        pass

    # Fill any misses one-by-one.
    for article in articles:
        if article["id"] in decoded_map:
            continue
        try:
            result = gnewsdecoder(article["google_news_url"], interval=None, timeout=15.0)
            if isinstance(result, dict):
                direct = result.get("decoded_url")
                ok = result.get("success") if "success" in result else result.get("status")
                if direct and ok is not False:
                    decoded_map[article["id"]] = direct
        except Exception:
            continue

    return decoded_map

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

    seen_titles, seen_gnews, deduped = set(), set(), []
    for article in articles:
        tkey = normalized_title(article["title"])
        gkey = article["google_news_url"]
        if (tkey and tkey in seen_titles) or gkey in seen_gnews:
            continue
        if tkey:
            seen_titles.add(tkey)
        seen_gnews.add(gkey)
        deduped.append(article)
        if len(deduped) >= MAX_TOTAL_CANDIDATES:
            break

    decoded = decode_urls(deduped)
    resolved = 0
    for article in deduped:
        direct = decoded.get(article["id"], "")
        if direct and direct.startswith(("https://", "http://")):
            article["url"] = direct
            article["source_url"] = direct
            resolved += 1

    def enrich(article):
        url = article.get("url", "")
        if not url or "news.google.com/" in url:
            return article["id"], ""
        return article["id"], fetch_preview_image(url)

    image_map = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(enrich, a) for a in deduped]
        for future in as_completed(futures):
            try:
                article_id, image = future.result()
                if image:
                    image_map[article_id] = image
            except Exception:
                pass

    for article in deduped:
        article["image_url"] = image_map.get(article["id"], "")

    # Keep only articles with unique real images
    final_articles = []
    seen_images = set()
    for article in deduped:
        img = article.get("image_url", "")
        if not img or img in seen_images:
            continue
        seen_images.add(img)
        final_articles.append(article)
        if len(final_articles) >= TARGET_TOTAL:
            break

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News search discovery with publisher article preview images",
        "queries": [{"category": c, "query": q.replace(" when:7d", "")} for c, q in FEEDS],
        "articles": final_articles,
        "errors": feed_errors,
        "stats": {
            "candidate_articles": len(deduped),
            "published_articles": len(final_articles),
            "publisher_urls_resolved": resolved,
            "preview_images_found": len(seen_images),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {len(final_articles)} published articles from {len(deduped)} candidates.")
    print(f"Resolved {resolved}/{len(deduped)} publisher URLs.")
    print(f"Found {len(seen_images)} unique real preview images.")
    if feed_errors:
        print("Feed errors:")
        for error in feed_errors:
            print(f" - {error}")

if __name__ == "__main__":
    main()
