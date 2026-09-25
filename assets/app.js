const state = {
  articles: [],
  category: "All",
  search: ""
};

const els = {
  leadLayout: document.getElementById("leadLayout"),
  sideStack: document.getElementById("sideStack"),
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
  value.replace(/[&<>"']/g, char => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[char]));

const safeURL = (value = "") => {
  try {
    const u = new URL(value);
    return ["http:", "https:"].includes(u.protocol) ? u.href : "#";
  } catch { return "#"; }
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

const visual = (category) => `
  <div class="visual" aria-hidden="true">
    <div class="visual-mark">${escapeHTML(category || "Iran")}</div>
  </div>`;

const meta = (a) =>
  `<p class="meta"><span>${escapeHTML(a.source || "Source")}</span><span>${escapeHTML(relativeTime(a.published))}</span></p>`;

const leadCard = (a) => `
  <article class="lead-card">
    ${visual(a.category)}
    <div class="copy">
      <span class="tag">${escapeHTML(a.category || "Latest")}</span>
      <h2><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h2>
      ${meta(a)}
    </div>
  </article>`;

const sideStory = (a) => `
  <article class="side-story">
    <span class="tag">${escapeHTML(a.category || "Latest")}</span>
    <h3><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h3>
    ${meta(a)}
  </article>`;

const storyCard = (a) => `
  <article class="story-card">
    ${visual(a.category)}
    <span class="tag">${escapeHTML(a.category || "Latest")}</span>
    <h3><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h3>
    ${meta(a)}
  </article>`;

function filteredArticles(){
  return state.articles.filter(a => {
    const categoryOK = state.category === "All" || a.category === state.category;
    const q = state.search.trim().toLowerCase();
    const searchOK = !q || `${a.title} ${a.source} ${a.category}`.toLowerCase().includes(q);
    return categoryOK && searchOK;
  });
}

function render(){
  const articles = filteredArticles();
  els.sectionTitle.textContent = state.category === "All" ? "All stories" : state.category;
  els.resultsCount.textContent = `${articles.length} ${articles.length === 1 ? "story" : "stories"}`;

  if (!articles.length){
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

  els.leadLayout.innerHTML = leadCard(lead) + `<div class="side-stack">${side.map(sideStory).join("")}</div>`;
  els.newsGrid.innerHTML = grid.map(storyCard).join("");
}

async function loadFeed(){
  try{
    const response = await fetch(`data/news.json?v=${Date.now()}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.articles = Array.isArray(data.articles) ? data.articles : [];
    els.feedStatus.textContent = state.articles.length
      ? "Live automated feed"
      : "Feed ready — run the updater once";
    if (data.updated_at){
      const d = new Date(data.updated_at);
      els.lastUpdated.textContent = `Updated ${d.toLocaleString()}`;
    }
    render();
  }catch(err){
    els.feedStatus.textContent = "Could not load data/news.json";
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
