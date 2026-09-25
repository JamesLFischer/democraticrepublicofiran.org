# Democratic Republic of Iran — News Aggregator

A static GitHub Pages news aggregator with an editorial layout inspired by modern magazine/news category pages. It uses **Google News RSS search feeds** for discovery, stores headline-level metadata in `data/news.json`, and links readers to the original publisher.

## What is included

- Responsive editorial homepage
- Topic filters and headline search
- Source attribution and publication times
- About and sourcing-method pages
- Automated Google News RSS fetcher written in Python (standard library only)
- Scheduled GitHub Actions updater
- No database and no server required
- No copied full article text

## First deployment

1. Upload **all files and folders from this package** to the root of your GitHub repository.
2. Commit them to the `main` branch.
3. Open **Settings → Actions → General**.
4. Under **Workflow permissions**, select **Read and write permissions** if your repository does not already allow the workflow to write.
5. Open **Actions → Update news feed → Run workflow** and run it once.
6. Confirm `data/news.json` now contains current stories.
7. Open **Settings → Pages**.
8. Set Source to **Deploy from a branch**.
9. Select `main` and `/ (root)`, then Save.
10. Wait for GitHub Pages to publish.

Only after the `.github.io` version works should you connect the custom domain.

## Customizing searches

Edit `FEEDS` inside:

`scripts/fetch_news.py`

Current discovery queries are:

- `"democratic Iran"`
- `"Iran democracy"`
- `"Iran civil society"`
- `"Iran human rights"`
- `"Iran economy"`
- `"Iran culture"`
- `"Iran diaspora"`

The script appends `when:7d` so each hourly refresh focuses on recent material.

## Update frequency

`.github/workflows/update-news.yml` currently runs once per hour at minute 17 and can also be run manually.

## Editorial / legal notes

This starter is deliberately presented as an **independent aggregator**, not an official government, party, campaign, or opposition site.

The visual presentation is Iran-centered. Political stories are source-attributed and ordered primarily by recency. The site does not assign a political score or endorse a publisher simply by aggregating its headline.

Before public launch, add:

- A real owner/publisher disclosure
- A real contact method
- Any required privacy/cookie language for analytics you add
- Your preferred corrections/takedown procedure

## Files

- `index.html` — homepage
- `about.html` — about/independence disclosure
- `sources.html` — aggregation method
- `assets/styles.css` — responsive design
- `assets/app.js` — rendering/filter/search logic
- `scripts/fetch_news.py` — RSS aggregation
- `data/news.json` — generated feed data
- `.github/workflows/update-news.yml` — automated updater
