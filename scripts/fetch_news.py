#!/usr/bin/env python3
"""
Fetch recent Google News RSS search results for Iran-related topics.

For each newly discovered article the script attempts to:
1. Decode the Google News wrapper URL to the publisher's original article URL.
2. Fetch the publisher page.
3. Read its declared social/news preview image (Open Graph, Twitter, or JSON-LD).

The site hot-links the publisher-declared preview image; it does not copy article
text or image files into this repository. If a publisher blocks fetching or
hot-linking, the frontend automatically falls back to the site's category art.
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
import time
import xml.etree.ElementTree as ET

try:
    from googlenewsdecoder import gnewsdecoder
except Exception:
    gnewsdecoder = None

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
IMAGE_ENRICH_LIMIT = 90
MAX_WORKERS = 6
TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

def clean(text: str | None) -> str:
    if not text:
        return ""
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()

def normalized_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"\s+-\s+[^-]{2,80}$", "", title)
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
        source_url = source_node.attrib.get("url", "") if source_node is not None else ""

        if not title or not link:
            continue

        dt = parse_date(pub)
        key = normalized_title(title)
        item_id = hashlib.sha1((key or link).encode("utf-8")).hexdigest()[:16]
        items.append({
            "id": item_id,
            "title": title,
            "url": link,
            "google_news_url": link,
            "source": source or "Publisher",
            "source_url": source_url,
            "published": dt.isoformat().replace("+00:00", "Z"),
            "category": category,
            "query": query.replace(" when:7d", ""),
            "image_url": "",
        })
    return items

class MetaImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.candidates = []
        self.base_href = ""
        self._in_json_ld = False
        self._json_ld_chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): v for k, v in attrs if k and v}
        if tag.lower() == "base" and attrs.get("href") and not self.base_href:
            self.base_href = attrs["href"]

        if tag.lower() == "meta":
            prop = (attrs.get("property") or attrs.get("name") or "").lower()
            content = attrs.get("content", "").strip()
            if content and prop in {
                "og:image", "og:image:url", "og:image:secure_url",
                "twitter:image", "twitter:image:src"
            }:
                self.candidates.append(content)

        if tag.lower() == "link":
            rel = (attrs.get("rel") or "").lower()
            href = attrs.get("href", "").strip()
            if href and "image_src" in rel:
                self.candidates.append(href)

        if tag.lower() == "script":
            script_type = (attrs.get("type") or "").lower()
            if script_type == "application/ld+json":
                self._in_json_ld = True
                self._json_ld_chunks = []

    def handle_data(self, data):
        if self._in_json_ld:
            self._json_ld_chunks.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._in_json_ld:
            raw = "".join(self._json_ld_chunks).strip()
            self._in_json_ld = False
            self._json_ld_chunks = []
            if raw:
                try:
                    payload = json.loads(raw)
                    self._extract_jsonld_images(payload)
                except Exception:
                    pass

    def _extract_jsonld_images(self, value):
        if isinstance(value, dict):
            image = value.get("image")
            if isinstance(image, str):
                self.candidates.append(image)
            elif isinstance(image, dict):
                url = image.get("url") or image.get("contentUrl")
                if isinstance(url, str):
                    self.candidates.append(url)
            elif isinstance(image, list):
                for item in image:
                    if isinstance(item, str):
                        self.candidates.append(item)
                    elif isinstance(item, dict):
                        url = item.get("url") or item.get("contentUrl")
                        if isinstance(url, str):
                            self.candidates.append(url)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    self._extract_jsonld_images(child)
        elif isinstance(value, list):
            for child in value:
                self._extract_jsonld_images(child)

def decode_google_url(url: str) -> str:
    if not gnewsdecoder:
        return url
    try:
        result = gnewsdecoder(url, interval=None)
        if isinstance(result, dict):
            decoded = result.get("decoded_url")
            status = result.get("status", result.get("success"))
            if decoded and status is not False:
                return decoded
    except Exception:
        pass
    return url

def get_article_image(article_url: str) -> str:
    if not article_url or "news.google.com/" in article_url:
        return ""
    try:
        req = Request(
            article_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urlopen(req, timeout=TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return ""
            # Enough for virtually all metadata while avoiding full large-page downloads.
            html = response.read(900_000).decode("utf-8", "ignore")
            final_url = response.geturl()
    except Exception:
        return ""

    parser = MetaImageParser()
    try:
        parser.feed(html)
    except Exception:
        return ""

    base = parser.base_href or final_url
    seen = set()
    for candidate in parser.candidates:
        candidate = unescape(candidate.strip())
        absolute = urljoin(base, candidate)
        if absolute in seen:
            continue
        seen.add(absolute)
        if absolute.startswith(("http://", "https://")):
            lower = absolute.lower()
            # Skip common non-article assets.
            if any(token in lower for token in (
                "logo", "favicon", "icon-", "/icon/", "avatar", "sprite", "tracking"
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
            a.get("id"): a
            for a in payload.get("articles", [])
            if isinstance(a, dict) and a.get("id")
        }
    except Exception:
        return {}

def enrich_article(article: dict, previous: dict[str, dict]) -> dict:
    old = previous.get(article["id"], {})

    # Reuse prior enrichment so hourly runs do not repeatedly hit publisher pages.
    old_url = old.get("url", "")
    old_image = old.get("image_url", "")
    if old_url and "news.google.com/" not in old_url:
        article["url"] = old_url
        article["source_url"] = old_url
        article["image_url"] = old_image
        return article

    direct = decode_google_url(article["google_news_url"])
    if direct and "news.google.com/" not in direct:
        article["url"] = direct
        article["source_url"] = direct
        article["image_url"] = get_article_image(direct)
    else:
        article["image_url"] = old_image
    return article

def main():
    previous = load_previous()
    articles = []
    errors = []

    for category, query in FEEDS:
        try:
            articles.extend(fetch_feed(category, query))
        except Exception as exc:
            errors.append(f"{category}: {exc}")
        time.sleep(0.5)

    articles.sort(key=lambda a: a["published"], reverse=True)

    seen_titles = set()
    seen_urls = set()
    deduped = []
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

    enrich_targets = deduped[:IMAGE_ENRICH_LIMIT]
    enriched_by_id = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(enrich_article, article.copy(), previous): article["id"]
            for article in enrich_targets
        }
        for future in as_completed(futures):
            article_id = futures[future]
            try:
                enriched_by_id[article_id] = future.result()
            except Exception as exc:
                fallback = next(a.copy() for a in enrich_targets if a["id"] == article_id)
                fallback["image_url"] = previous.get(article_id, {}).get("image_url", "")
                enriched_by_id[article_id] = fallback
                errors.append(f"enrichment {article_id}: {exc}")

    final_articles = []
    for article in deduped:
        final_articles.append(enriched_by_id.get(article["id"], article))

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News RSS search feeds; preview metadata declared by original publishers",
        "queries": [{"category": c, "query": q.replace(" when:7d", "")} for c, q in FEEDS],
        "articles": final_articles,
        "errors": errors,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    image_count = sum(bool(a.get("image_url")) for a in final_articles)
    direct_count = sum("news.google.com/" not in a.get("url", "") for a in final_articles)
    print(f"Wrote {len(final_articles)} articles to {OUT}")
    print(f"Resolved {direct_count} publisher URLs; found {image_count} publisher preview images.")
    if errors:
        print("Non-fatal feed/enrichment notices:")
        for error in errors:
            print(f" - {error}")

if __name__ == "__main__":
    main()
