const CATEGORY_LABELS = {
  all: "全部",
  headline: "🔥 頭條",
  research: "🔬 研究",
  tools: "🛠️ 工具",
  opinion: "💭 觀點",
};

const state = {
  data: null,
  selectedDate: null,
  selectedCategory: "all",
  query: "",
};

const els = {
  searchInput: document.getElementById("searchInput"),
  chips: document.getElementById("categoryChips"),
  stats: document.getElementById("stats"),
  content: document.getElementById("content"),
  empty: document.getElementById("empty"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function dayHasItems(day) {
  return day.sections && day.sections.some((s) => s.items && s.items.length);
}

function matchesQuery(item, q) {
  if (!q) return true;
  const hay = [
    item.title,
    item.tldr,
    item.why,
    (item.points || []).join(" "),
    item.meta && item.meta.source,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q.toLowerCase());
}

function matchesCategory(item, cat) {
  return cat === "all" || item.category === cat;
}

function buildChips() {
  els.chips.innerHTML = "";
  Object.keys(CATEGORY_LABELS).forEach((key) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip" + (key === state.selectedCategory ? " active" : "");
    btn.textContent = CATEGORY_LABELS[key];
    btn.addEventListener("click", () => {
      state.selectedCategory = key;
      buildChips();
      render();
    });
    els.chips.appendChild(btn);
  });
}

function ensureSelectedDate() {
  if (!state.selectedDate && state.data.days && state.data.days.length) {
    state.selectedDate = state.data.days[0].date;
  }
}

function metaBadges(item) {
  const m = item.meta || {};
  const out = [];
  if (m.source) {
    out.push(`<span class="badge source">${escapeHtml(m.source)}</span>`);
  }
  if (m.score != null) {
    out.push(`<span class="badge">⬆️ ${escapeHtml(m.score)}</span>`);
  }
  if (m.comments != null) {
    out.push(`<span class="badge">💬 ${escapeHtml(m.comments)}</span>`);
  }
  if (m.readingTime) {
    out.push(`<span class="badge">📖 ${escapeHtml(m.readingTime)} 分鐘</span>`);
  }
  if (m.hnUrl) {
    out.push(`<a class="badge link" href="${escapeHtml(m.hnUrl)}" target="_blank" rel="noopener">HN 討論</a>`);
  }
  if (m.summarized === false) {
    out.push(`<span class="badge warn">⚠️ 未摘要</span>`);
  }
  return out.join("");
}

function renderCard(item) {
  const points =
    item.points && item.points.length
      ? `<ul class="points">${item.points.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`
      : "";
  const tldr = item.tldr ? `<p class="tldr">${escapeHtml(item.tldr)}</p>` : "";
  const why = item.why ? `<div class="why">💡 ${escapeHtml(item.why)}</div>` : "";
  return `
    <article class="card">
      <h3 class="card-title"><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a></h3>
      ${tldr}
      ${points}
      ${why}
      <div class="meta">${metaBadges(item)}</div>
    </article>
  `;
}

function renderSingleDay(day) {
  const sections = (day.sections || [])
    .map((section) => {
      const items = (section.items || []).filter(
        (it) => matchesCategory(it, state.selectedCategory) && matchesQuery(it, state.query)
      );
      if (!items.length) return "";
      return `
        <section class="section">
          <h2 class="section-title">${escapeHtml(section.label || section.id)}</h2>
          ${items.map(renderCard).join("")}
        </section>
      `;
    })
    .filter(Boolean)
    .join("");

  const idx = currentDateIndex();
  const days = state.data.days || [];
  const prevDisabled = idx <= 0;
  const nextDisabled = idx >= days.length - 1;
  if (!sections) {
    els.content.innerHTML = `
      <div class="day-group">
        <div class="day-title-row">
          <button class="nav-btn" data-delta="-1" ${prevDisabled ? "disabled" : ""} aria-label="前一天">‹</button>
          <h2 class="day-title">🗞️ ${escapeHtml(day.date)}</h2>
          <button class="nav-btn" data-delta="1" ${nextDisabled ? "disabled" : ""} aria-label="後一天">›</button>
        </div>
        <p class="muted">這個日期下沒有符合條件的新聞。</p>
      </div>
    `;
    return;
  }
  els.content.innerHTML = `
    <div class="day-group">
      <div class="day-title-row">
        <button class="nav-btn" data-delta="-1" ${prevDisabled ? "disabled" : ""} aria-label="前一天">‹</button>
        <h2 class="day-title">🗞️ ${escapeHtml(day.date)}</h2>
        <button class="nav-btn" data-delta="1" ${nextDisabled ? "disabled" : ""} aria-label="後一天">›</button>
      </div>
      ${sections}
    </div>
  `;
}

function renderSearchResults() {
  const q = state.query.trim();
  const groups = [];
  (state.data.days || []).forEach((day) => {
    const items = [];
    (day.sections || []).forEach((section) => {
      (section.items || []).forEach((it) => {
        const item = { ...it, category: it.category || section.id, _section: section.label };
        if (matchesCategory(item, state.selectedCategory) && matchesQuery(item, q)) {
          items.push(item);
        }
      });
    });
    if (items.length) groups.push({ day, items });
  });

  if (!groups.length) {
    els.content.innerHTML = `<p class="muted">找不到符合「${escapeHtml(q)}」的新聞。</p>`;
    return;
  }

  els.content.innerHTML = groups
    .map(
      (g) => `
      <div class="day-group">
        <h2 class="day-title">🗞️ ${escapeHtml(g.day.date)}</h2>
        ${g.items.map(renderCard).join("")}
      </div>
    `
    )
    .join("");
}

function render() {
  if (!state.data || !state.data.days || !state.data.days.length) {
    els.empty.hidden = false;
    els.content.innerHTML = "";
    els.stats.textContent = "";
    return;
  }
  els.empty.hidden = true;

  const q = state.query.trim();
  if (q) {
    renderSearchResults();
    const total = (state.data.days || []).reduce(
      (n, d) =>
        n +
        (d.sections || []).reduce(
          (m, s) =>
            m +
            (s.items || []).filter((it) =>
              matchesCategory({ ...it, category: it.category || s.id }, state.selectedCategory) &&
              matchesQuery(it, q)
            ).length,
          0
        ),
      0
    );
    els.stats.textContent = "";
    return;
  }

  const day = (state.data.days || []).find((d) => d.date === state.selectedDate);
  if (!day) {
    els.content.innerHTML = "";
    return;
  }
  renderSingleDay(day);
  els.stats.textContent = "";
}

function currentDateIndex() {
  return (state.data.days || []).findIndex((d) => d.date === state.selectedDate);
}

function changeDate(delta) {
  const idx = currentDateIndex();
  const next = idx + delta;
  const days = state.data.days || [];
  if (next < 0 || next >= days.length) return;
  state.selectedDate = days[next].date;
  state.query = "";
  els.searchInput.value = "";
  render();
}

function wireEvents() {
  els.searchInput.addEventListener("input", (e) => {
    state.query = e.target.value;
    render();
  });
  els.content.addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-btn");
    if (!btn || btn.disabled) return;
    const delta = parseInt(btn.dataset.delta, 10);
    if (Number.isNaN(delta)) return;
    changeDate(delta);
  });
}

async function init() {
  buildChips();
  wireEvents();
  try {
    const res = await fetch("data/news.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
  } catch (err) {
    state.data = { days: [] };
    els.stats.textContent = "無法載入 data/news.json，請先執行 build.py。";
  }
  ensureSelectedDate();
  render();
}

init();
