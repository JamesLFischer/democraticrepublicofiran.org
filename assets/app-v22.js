const state = {
  articles: [],
  category: "All",
  search: ""
};

const els = {
  leadLayout: document.getElementById("leadLayout"),
  newsGrid: document.getElementById("newsGrid"),
  emptyState: document.getElementById("emptyState"),
  resultsCount: document.getElementById("resultsCount"),
  sectionTitle: document.getElementById("sectionTitle"),
  feedStatus: document.getElementById("feedStatus"),
  lastUpdated: document.getElementById("lastUpdated"),
  storySearch: document.getElementById("storySearch"),
  searchPanel: document.getElementById("searchPanel"),
  searchToggle: document.getElementById("searchToggle")
};

const escapeHTML = (value = "") =>
  String(value).replace(/[&<>"']/g, char => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[char]));

const safeURL = (value = "") => {
  try {
    const u = new URL(value);
    return ["http:", "https:"].includes(u.protocol) ? u.href : "#";
  } catch {
    return "#";
  }
};

const safeImageURL = (value = "") => {
  const raw = String(value || "").trim();
  if (raw.startsWith("data:image/")) return raw;
  return safeURL(raw);
};

const relativeTime = (dateString) => {
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return "";
  const diff = Date.now() - date.getTime();
  const mins = Math.max(0, Math.floor(diff / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString(undefined, {month:"short", day:"numeric", year:"numeric"});
};

function prepareArticles(rawArticles) {
  const seenIds = new Set();
  const prepared = [];

  for (const article of rawArticles) {
    if (!article || !article.title) continue;

    const url = safeURL(article.url || "");
    const image = safeImageURL(article.image_url || "");

    if (url === "#" || image === "#") continue;
    if (article.id && seenIds.has(article.id)) continue;

    if (article.id) seenIds.add(article.id);
    prepared.push({...article, _displayImage:image});
  }

  return prepared;
}

const visual = (a, size = "card") => `
  <div class="visual has-image" aria-hidden="true">
    <img
      class="publisher-art"
      src="${safeImageURL(a._displayImage)}"
      alt=""
      loading="${size === "lead" ? "eager" : "lazy"}"
      decoding="async"
    >
    <div class="visual-overlay"></div>
    <div class="visual-mark">${escapeHTML(a.category || "Iran")}</div>
  </div>`;

const meta = a =>
  `<p class="meta"><span class="meta-source">${escapeHTML(a.source || "Source")}</span><span class="meta-time">${escapeHTML(relativeTime(a.published))}</span></p>`;

const leadCard = a => `
  <article class="lead-card">
    ${visual(a, "lead")}
    <div class="copy">
      <span class="tag">${escapeHTML(a.category || "Latest")}</span>
      <h2><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h2>
      ${meta(a)}
    </div>
  </article>`;

const sideStory = a => `
  <article class="side-story">
    <div class="side-thumb">${visual(a, "side")}</div>
    <div class="side-copy">
      <span class="tag">${escapeHTML(a.category || "Latest")}</span>
      <h3><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h3>
      ${meta(a)}
    </div>
  </article>`;

const storyCard = a => `
  <article class="story-card">
    ${visual(a, "card")}
    <span class="tag">${escapeHTML(a.category || "Latest")}</span>
    <h3><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h3>
    ${meta(a)}
  </article>`;

function filteredArticles() {
  return state.articles.filter(a => {
    const categoryOK = state.category === "All" || a.category === state.category;
    const q = state.search.trim().toLowerCase();
    const searchOK = !q || `${a.title} ${a.source} ${a.category}`.toLowerCase().includes(q);
    return categoryOK && searchOK;
  });
}

function setNavAvailability() {
  document.querySelectorAll(".nav-link").forEach(button => {
    const category = button.dataset.category;
    const count = category === "All"
      ? state.articles.length
      : state.articles.filter(a => a.category === category).length;

    button.dataset.count = String(count);
    button.title = count ? `${count} current stories` : "No current stories in this category";
  });
}

function render() {
  const articles = filteredArticles();

  if (els.sectionTitle) {
    els.sectionTitle.textContent = state.category === "All" ? "All stories" : state.category;
  }
  if (els.resultsCount) {
    els.resultsCount.textContent = `${articles.length} ${articles.length === 1 ? "story" : "stories"}`;
  }

  if (!articles.length) {
    if (els.leadLayout) els.leadLayout.style.display = "none";
    if (els.newsGrid) els.newsGrid.innerHTML = "";
    if (els.emptyState) els.emptyState.hidden = false;
    return;
  }

  if (els.emptyState) els.emptyState.hidden = true;
  if (els.leadLayout) els.leadLayout.style.display = "grid";

  const lead = articles[0];
  const side = articles.slice(1, 4);
  const grid = articles.slice(4);

  if (els.leadLayout) {
    els.leadLayout.innerHTML =
      leadCard(lead) + `<div class="side-stack">${side.map(sideStory).join("")}</div>`;
  }
  if (els.newsGrid) {
    els.newsGrid.innerHTML = grid.map(storyCard).join("");
  }
}

async function loadFeed() {
  try {
    const response = await fetch(`data/news.json?v=${Date.now()}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    state.articles = prepareArticles(Array.isArray(data.articles) ? data.articles : []);

    if (els.feedStatus) {
      els.feedStatus.textContent = state.articles.length
        ? "Latest coverage"
        : "Coverage is being updated";
    }

    if (els.lastUpdated && data.updated_at) {
      const d = new Date(data.updated_at);
      els.lastUpdated.textContent = `Updated ${d.toLocaleString()}`;
    }

    setNavAvailability();
    render();
  } catch (err) {
    console.error("News feed load failed:", err);
    if (els.feedStatus) els.feedStatus.textContent = "Could not load the current feed";
    if (els.leadLayout) els.leadLayout.style.display = "none";
    if (els.emptyState) els.emptyState.hidden = false;
  }
}

document.querySelectorAll(".nav-link").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-link").forEach(b => b.classList.remove("active"));
    button.classList.add("active");
    state.category = button.dataset.category || "All";
    render();
  });
});

if (els.searchToggle && els.searchPanel && els.storySearch) {
  els.searchToggle.addEventListener("click", () => {
    els.searchPanel.hidden = !els.searchPanel.hidden;
    if (!els.searchPanel.hidden) els.storySearch.focus();
  });

  els.storySearch.addEventListener("input", event => {
    state.search = event.target.value;
    render();
  });
}

const year = document.getElementById("year");
if (year) year.textContent = new Date().getFullYear();

loadFeed();
