#!/usr/bin/env python3
"""
Fetch recent Google News RSS search results for Iran-related topics.

No third-party packages are required. The output is intentionally limited to
headline-level aggregation: title, result URL, publisher, publication time, and
the query category. Full article text is not copied.
"""

from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "news.json"

# Change these in one place if you want to tune the editorial scope.
# "when:7d" keeps the automated discovery window recent.
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
USER_AGENT = "Mozilla/5.0 DemocraticRepublicOfIran-NewsAggregator/1.0"

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
            "source": source or "Publisher",
            "source_url": source_url,
            "published": dt.isoformat().replace("+00:00", "Z"),
            "category": category,
            "query": query.replace(" when:7d", ""),
        })
    return items

def main():
    articles = []
    errors = []

    for category, query in FEEDS:
        try:
            articles.extend(fetch_feed(category, query))
        except Exception as exc:
            errors.append(f"{category}: {exc}")
        time.sleep(0.8)

    # De-duplicate primarily by normalized headline, keeping the newest version.
    articles.sort(key=lambda a: a["published"], reverse=True)
    seen_titles = set()
    seen_urls = set()
    deduped = []

    for article in articles:
        tkey = normalized_title(article["title"])
        ukey = article["url"]
        if (tkey and tkey in seen_titles) or ukey in seen_urls:
            continue
        if tkey:
            seen_titles.add(tkey)
        seen_urls.add(ukey)
        deduped.append(article)
        if len(deduped) >= MAX_TOTAL:
            break

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Google News RSS search feeds",
        "queries": [{"category": c, "query": q.replace(" when:7d", "")} for c, q in FEEDS],
        "articles": deduped,
        "errors": errors,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(deduped)} articles to {OUT}")
    if errors:
        print("Feed errors:")
        for error in errors:
            print(f" - {error}")

if __name__ == "__main__":
    main()
