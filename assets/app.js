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

const hashString = (value = "") => {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
};

const categoryPalette = (category = "") => {
  const palettes = {
    "Democracy": ["#174c3a", "#b3483f", "#e0b36a"],
    "Civil Society": ["#285b66", "#8f3f4a", "#dfc78d"],
    "Human Rights": ["#5c2733", "#1f4f47", "#d7b56d"],
    "Economy": ["#244a54", "#74552f", "#d0a75d"],
    "Culture": ["#54375f", "#1e5b50", "#d5a85f"],
    "Diaspora": ["#23445d", "#7d3a43", "#d0b16f"]
  };
  return palettes[category] || ["#1e6d4f", "#7f3c3c", "#d3ac67"];
};

const uniqueFallback = (a, size = "card") => {
  const seed = hashString(`${a.id || ""}|${a.title || ""}|${a.source || ""}`);
  const [c1, c2, gold] = categoryPalette(a.category);
  const w = size === "lead" ? 1400 : size === "side" ? 640 : 900;
  const h = size === "lead" ? 800 : size === "side" ? 400 : 540;

  const n = (shift, min, max) => {
    const v = ((seed >>> shift) & 255) / 255;
    return Math.round(min + v * (max - min));
  };

  const mountainA = n(0, 180, 360);
  const mountainB = n(4, 430, 660);
  const mountainC = n(8, 720, 1060);
  const sunX = n(12, Math.round(w * .18), Math.round(w * .82));
  const sunY = n(16, 70, Math.round(h * .32));
  const archX = n(20, Math.round(w * .18), Math.round(w * .60));
  const archW = n(3, Math.round(w * .25), Math.round(w * .42));
  const tileRotation = n(7, 0, 45);
  const category = escapeHTML((a.category || "Iran").toUpperCase());

  const svg = `
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}">
    <defs>
      <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="${c1}"/>
        <stop offset=".58" stop-color="#171a18"/>
        <stop offset="1" stop-color="${c2}"/>
      </linearGradient>
      <pattern id="tile" width="92" height="92" patternUnits="userSpaceOnUse" patternTransform="rotate(${tileRotation})">
        <path d="M46 5 L60 32 L87 46 L60 60 L46 87 L32 60 L5 46 L32 32 Z"
              fill="none" stroke="${gold}" stroke-opacity=".23" stroke-width="2"/>
        <circle cx="46" cy="46" r="12" fill="none" stroke="#fff" stroke-opacity=".12" stroke-width="1.5"/>
      </pattern>
      <linearGradient id="mount" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#dfe7e3" stop-opacity=".42"/>
        <stop offset="1" stop-color="#0b1110" stop-opacity=".12"/>
      </linearGradient>
    </defs>

    <rect width="${w}" height="${h}" fill="url(#bg)"/>
    <rect width="${w}" height="${h}" fill="url(#tile)" opacity=".58"/>

    <circle cx="${sunX}" cy="${sunY}" r="${Math.round(h*.095)}"
            fill="${gold}" fill-opacity=".58"/>

    <path d="M0 ${Math.round(h*.52)}
             L${mountainA} ${Math.round(h*.30)}
             L${mountainB} ${Math.round(h*.48)}
             L${mountainC} ${Math.round(h*.25)}
             L${w} ${Math.round(h*.50)}
             L${w} ${h} L0 ${h} Z"
          fill="url(#mount)"/>

    <path d="M${archX} ${Math.round(h*.70)}
             V${Math.round(h*.43)}
             Q${archX + archW/2} ${Math.round(h*.16)}
              ${archX + archW} ${Math.round(h*.43)}
             V${Math.round(h*.70)}"
          fill="none" stroke="#f1eadc" stroke-opacity=".30"
          stroke-width="${Math.max(4, Math.round(w/220))}"/>

    <path d="M0 ${Math.round(h*.78)} H${w}" stroke="#fff" stroke-opacity=".12" stroke-width="2"/>
    <path d="M0 ${Math.round(h*.80)} H${w}" stroke="${gold}" stroke-opacity=".32" stroke-width="1"/>

    <g fill="#f4efe4" fill-opacity=".96">
      <text x="${Math.round(w*.06)}" y="${Math.round(h*.88)}"
            font-family="Arial,Helvetica,sans-serif" font-size="${Math.round(h*.032)}"
            font-weight="700" letter-spacing="${Math.round(h*.008)}">${category}</text>
    </g>
  </svg>`;

  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
};

const prepareArticleImages = (articles) => {
  const seen = new Set();
  return articles.map(article => {
    const raw = article.image_url ? safeURL(article.image_url) : "";
    const display = raw && raw !== "#" && !seen.has(raw) ? raw : "";
    if (display) seen.add(display);
    return {...article, _displayImage: display};
  });
};

const visual = (a, size = "card") => {
  const category = escapeHTML(a.category || "Iran");
  const publisherImage = a._displayImage || "";
  const fallback = uniqueFallback(a, size);

  return `
    <div class="visual has-fallback ${publisherImage ? "has-image" : "fallback-only"}" aria-hidden="true">
      <img class="fallback-art" src="${fallback}" alt="" decoding="async">
      ${publisherImage ? `<img class="publisher-art" src="${publisherImage}" alt="" loading="${size === "lead" ? "eager" : "lazy"}" decoding="async" referrerpolicy="no-referrer" onerror="this.remove();this.parentElement.classList.remove('has-image');this.parentElement.classList.add('fallback-only')">` : ""}
      <div class="visual-overlay"></div>
      <div class="visual-mark">${category}</div>
    </div>`;
};

const meta = (a) =>
  `<p class="meta"><span>${escapeHTML(a.source || "Source")}</span><span>${escapeHTML(relativeTime(a.published))}</span></p>`;

const leadCard = (a) => `
  <article class="lead-card">
    ${visual(a, "lead")}
    <div class="copy">
      <span class="tag">${escapeHTML(a.category || "Latest")}</span>
      <h2><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h2>
      ${meta(a)}
    </div>
  </article>`;

const sideStory = (a) => `
  <article class="side-story">
    <div class="side-thumb">${visual(a, "side")}</div>
    <div class="side-copy">
      <span class="tag">${escapeHTML(a.category || "Latest")}</span>
      <h3><a href="${safeURL(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(a.title)}</a></h3>
      ${meta(a)}
    </div>
  </article>`;

const storyCard = (a) => `
  <article class="story-card">
    ${visual(a, "card")}
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
    state.articles = prepareArticleImages(Array.isArray(data.articles) ? data.articles : []);
    els.feedStatus.textContent = state.articles.length
      ? "Latest coverage"
      : "Coverage is being updated";
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
