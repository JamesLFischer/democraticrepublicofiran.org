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

const isRemotePhotoURL = (value = "") => {
  const u = safeURL(value);
  if (u === "#") return false;
  if (u.startsWith("data:") || u.includes("/assets/iran-")) return false;
  return true;
};

const validateImage = (url, timeoutMs = 8000) =>
  new Promise(resolve => {
    const img = new Image();
    let done = false;
    const finish = ok => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(ok);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    img.referrerPolicy = "no-referrer";
    img.onload = () => finish(img.naturalWidth >= 320 && img.naturalHeight >= 180);
    img.onerror = () => finish(false);
    img.src = url;
  });

async function prepareArticles(rawArticles) {
  const seenURLs = new Set();
  const candidates = [];

  for (const article of rawArticles) {
    const image = safeURL(article.image_url || "");
    if (!isRemotePhotoURL(image)) continue;
    if (seenURLs.has(image)) continue;
    seenURLs.add(image);
    candidates.push({...article, _displayImage:image});
  }

  const checks = await Promise.all(
    candidates.map(async article => ({
      article,
      ok: await validateImage(article._displayImage)
    }))
  );

  return checks.filter(item => item.ok).map(item => item.article);
}

const visual = (a, size = "card") => `
  <div class="visual has-image" aria-hidden="true">
    <img
      class="publisher-art"
      src="${safeURL(a._displayImage)}"
      alt=""
      loading="${size === "lead" ? "eager" : "lazy"}"
      decoding="async"
      referrerpolicy="no-referrer"
      onerror="window.__removeBrokenArticle('${escapeHTML(a.id || "")}')"
    >
    <div class="visual-overlay"></div>
    <div class="visual-mark">${escapeHTML(a.category || "Iran")}</div>
  </div>`;

const meta = a =>
  `<p class="meta"><span>${escapeHTML(a.source || "Source")}</span><span>${escapeHTML(relativeTime(a.published))}</span></p>`;

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

function render() {
  const articles = filteredArticles();
  els.sectionTitle.textContent = state.category === "All" ? "All stories" : state.category;
  els.resultsCount.textContent = `${articles.length} ${articles.length === 1 ? "story" : "stories"}`;

  if (!articles.length) {
    els.leadLayout.style.display = "none";
    els.newsGrid.innerHTML = "";
    els.emptyState.hidden = false;
    return;
  }

  els.emptyState.hidden = true;
  els.leadLayout.style.display = "grid";

  const lead = articles[0];
  const side = articles.slice(1, 4);
  const grid = articles.slice(4);

  els.leadLayout.innerHTML =
    leadCard(lead) + `<div class="side-stack">${side.map(sideStory).join("")}</div>`;
  els.newsGrid.innerHTML = grid.map(storyCard).join("");
}

window.__removeBrokenArticle = id => {
  if (!id) return;
  const before = state.articles.length;
  state.articles = state.articles.filter(a => a.id !== id);
  if (state.articles.length !== before) render();
};

async function loadFeed() {
  try {
    const response = await fetch(`data/news.json?v=${Date.now()}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();

    els.feedStatus.textContent = "Checking article photography…";
    state.articles = await prepareArticles(Array.isArray(data.articles) ? data.articles : []);

    els.feedStatus.textContent = state.articles.length
      ? "Latest coverage"
      : "Coverage is being updated";

    if (data.updated_at) {
      const d = new Date(data.updated_at);
      els.lastUpdated.textContent = `Updated ${d.toLocaleString()}`;
    }

    render();
  } catch (err) {
    els.feedStatus.textContent = "Could not load the current feed";
    els.leadLayout.style.display = "none";
    els.emptyState.hidden = false;
  }
}

document.querySelectorAll(".nav-link").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-link").forEach(b => b.classList.remove("active"));
    button.classList.add("active");
    state.category = button.dataset.category;
    render();
  });
});

els.searchToggle.addEventListener("click", () => {
  els.searchPanel.hidden = !els.searchPanel.hidden;
  if (!els.searchPanel.hidden) els.storySearch.focus();
});

els.storySearch.addEventListener("input", event => {
  state.search = event.target.value;
  render();
});

document.getElementById("year").textContent = new Date().getFullYear();
loadFeed();
