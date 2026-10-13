/* ═══════════════════════════════════════════
   ContentScope — Application Logic
   All rendering, scanning, filtering, and UI
   ═══════════════════════════════════════════ */

const $ = (id) => document.getElementById(id);

const form         = $("scanForm");
const statusEl     = $("status");
const statusText   = $("statusText");
const scanBtn      = $("scanBtn");
const results      = $("results");
const emptyState   = $("emptyState");
const exportBtn    = $("exportBtn");
const issueBody    = $("issueBody");
const pageBody     = $("pageBody");
const search       = $("search");
const sevFilter    = $("severityFilter");
const catFilter    = $("categoryFilter");
const progressBar  = $("progressBar");
const progressFill = $("progressFill");
const loadMoreWrap = $("loadMoreWrap");
const loadMoreBtn  = $("loadMoreBtn");

let lastResult = null;
let issuePageSize = 25;
let issueVisibleCount = 25;

/* ──────────────────────────────
   Theme toggle
   ────────────────────────────── */

function getStoredTheme() {
  return localStorage.getItem("cs-theme") || "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const icon = $("themeIcon");
  const label = $("themeLabel");
  if (theme === "dark") {
    icon.innerHTML = '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
    label.textContent = "Light mode";
  } else {
    icon.innerHTML = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>';
    label.textContent = "Dark mode";
  }
}

applyTheme(getStoredTheme());

$("themeToggle").addEventListener("click", () => {
  const next = getStoredTheme() === "light" ? "dark" : "light";
  localStorage.setItem("cs-theme", next);
  applyTheme(next);
});


/* ──────────────────────────────
   Sidebar nav
   ────────────────────────────── */

document.querySelectorAll("[data-nav]").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.remove("active"));
    link.classList.add("active");
  });
});


/* ──────────────────────────────
   Sample button
   ────────────────────────────── */

$("sampleBtn").addEventListener("click", () => {
  $("url").value = `${location.origin}/sample`;
  $("maxPages").value = "1";
  $("checkLinks").checked = false;
  form.requestSubmit();
});


/* ──────────────────────────────
   Scan
   ────────────────────────────── */

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("url").value.trim();
  const maxPages = Number($("maxPages").value);
  const checkLinks = $("checkLinks").checked;

  setStatus("Scanning — this may take a moment…");
  scanBtn.disabled = true;
  results.hidden = true;
  emptyState.hidden = true;
  showProgress(true);

  try {
    const res = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, max_pages: maxPages, check_links: checkLinks }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Scan failed");

    lastResult = data;
    issueVisibleCount = issuePageSize;
    render(data);
    setStatus(`Completed in ${(data.duration_ms / 1000).toFixed(1)}s — ${data.summary.total_issues} issue${data.summary.total_issues !== 1 ? "s" : ""} found`);
  } catch (err) {
    emptyState.hidden = false;
    setStatus(err.message, true);
  } finally {
    scanBtn.disabled = false;
    showProgress(false);
  }
});


/* ──────────────────────────────
   Filters
   ────────────────────────────── */

[search, sevFilter, catFilter].forEach((el) => {
  el.addEventListener("input", () => { if (lastResult) { issueVisibleCount = issuePageSize; renderIssues(lastResult.issues); } });
  el.addEventListener("change", () => { if (lastResult) { issueVisibleCount = issuePageSize; renderIssues(lastResult.issues); } });
});

loadMoreBtn.addEventListener("click", () => {
  if (!lastResult) return;
  issueVisibleCount += issuePageSize;
  renderIssues(lastResult.issues);
});


/* ──────────────────────────────
   Export
   ────────────────────────────── */

exportBtn.addEventListener("click", () => {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastResult, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "content-issue-report.json";
  a.click();
  URL.revokeObjectURL(a.href);
});


/* ──────────────────────────────
   Helpers
   ────────────────────────────── */

function setStatus(msg, isError = false) {
  statusEl.hidden = !msg;
  statusText.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

function showProgress(on) {
  progressBar.classList.toggle("active", on);
  progressBar.classList.toggle("indeterminate", on);
}

function esc(v) {
  return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}


/* ──────────────────────────────
   Main render
   ────────────────────────────── */

function render(data) {
  const s = data.summary;

  // Summary
  $("score").textContent = s.score;
  $("criticalCount").textContent = s.critical;
  $("warningCount").textContent = s.warning;
  $("infoCount").textContent = s.info;
  $("pageCount").textContent = data.pages_scanned;

  // Score bar
  const bar = $("scoreBar");
  bar.style.width = s.score + "%";
  if (s.score >= 85)      { bar.style.background = "var(--success)"; }
  else if (s.score >= 65) { bar.style.background = "var(--warning)"; }
  else                    { bar.style.background = "var(--critical)"; }

  // Grade label
  const grade = $("grade");
  grade.textContent = s.grade;
  grade.className = "grade-label";
  if (s.score >= 85)      { grade.classList.add("good"); }
  else if (s.score >= 65) { grade.classList.add("fair"); }
  else                    { grade.classList.add("poor"); }

  renderCategories(data.categories);
  renderMeta(data);
  fillCategoryOptions(data.issues);
  renderIssues(data.issues);
  renderPages(data.pages);

  results.hidden = false;
  emptyState.hidden = true;
  exportBtn.hidden = false;
}


/* ── Category bars ── */

function renderCategories(cats) {
  const max = Math.max(1, ...Object.values(cats));
  $("categoryBars").innerHTML = Object.entries(cats)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => `
      <div class="category-row">
        <span class="category-name">${esc(name)}</span>
        <div class="category-bar"><div class="category-bar-fill" style="width:${(count / max) * 100}%"></div></div>
        <span class="category-count">${count}</span>
      </div>
    `).join("") || '<p style="color:var(--text-secondary)">No categories.</p>';
}


/* ── Scan meta ── */

function renderMeta(data) {
  const rows = [
    ["Start URL",     data.start_url],
    ["Pages scanned", data.pages_scanned],
    ["Duration",      (data.duration_ms / 1000).toFixed(1) + "s"],
    ["Avg. words",    data.summary.avg_words],
    ["Total issues",  data.summary.total_issues],
  ];
  $("scanMeta").innerHTML = rows.map(([k, v]) =>
    `<div class="kv-row"><span class="kv-key">${esc(k)}</span><span class="kv-val">${esc(v)}</span></div>`
  ).join("");
}


/* ── Category filter options ── */

function fillCategoryOptions(issues) {
  const cats = [...new Set(issues.map((i) => i.category))].sort();
  const cur = catFilter.value;
  catFilter.innerHTML = '<option value="all">All categories</option>' +
    cats.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  catFilter.value = cats.includes(cur) ? cur : "all";
}


/* ── Issues table ── */

function filterIssues(issues) {
  const q = search.value.trim().toLowerCase();
  const sev = sevFilter.value;
  const cat = catFilter.value;
  return issues.filter((i) => {
    const hay = `${i.title} ${i.detail} ${i.page_url} ${i.evidence}`.toLowerCase();
    return (sev === "all" || i.severity === sev) &&
           (cat === "all" || i.category === cat) &&
           (!q || hay.includes(q));
  });
}

function renderIssues(issues) {
  const filtered = filterIssues(issues);
  const visible = filtered.slice(0, issueVisibleCount);
  const hasMore = filtered.length > issueVisibleCount;

  $("issueEmpty").hidden = filtered.length > 0;
  loadMoreWrap.hidden = !hasMore;

  issueBody.innerHTML = visible.map((issue, idx) => {
    const badgeCls = issue.severity === "critical" ? "badge-critical"
                   : issue.severity === "warning"  ? "badge-warning"
                   : "badge-info";
    const dotCls   = issue.severity;

    return `
      <tr class="issue-row" data-idx="${idx}">
        <td><span class="badge ${badgeCls}"><span class="sev-dot ${dotCls}"></span>${esc(issue.severity)}</span></td>
        <td>${esc(issue.category)}</td>
        <td>
          <div class="issue-cell-title">${esc(issue.title)}</div>
          <div class="issue-cell-desc">${esc(issue.detail)}</div>
        </td>
        <td><div class="issue-page-url" title="${esc(issue.page_url)}">${esc(issue.page_url)}</div></td>
        <td><svg class="issue-expand-icon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg></td>
      </tr>
      <tr class="issue-detail-row" hidden data-detail="${idx}">
        <td colspan="5">
          <div class="issue-detail-content">
            <div class="issue-fix">
              <strong>How to fix</strong>
              ${esc(issue.recommendation)}
            </div>
            ${issue.evidence ? `<div class="issue-evidence">${esc(issue.evidence)}</div>` : ""}
          </div>
        </td>
      </tr>
    `;
  }).join("");

  // Bind expand/collapse
  issueBody.querySelectorAll(".issue-row").forEach((row) => {
    row.addEventListener("click", () => {
      const idx = row.dataset.idx;
      const detail = issueBody.querySelector(`[data-detail="${idx}"]`);
      const isOpen = !detail.hidden;
      // close all first
      issueBody.querySelectorAll(".issue-detail-row").forEach((r) => r.hidden = true);
      issueBody.querySelectorAll(".issue-row").forEach((r) => r.classList.remove("expanded"));
      if (!isOpen) {
        detail.hidden = false;
        row.classList.add("expanded");
      }
    });
  });

  if (hasMore) {
    loadMoreBtn.textContent = `Show more (${filtered.length - issueVisibleCount} remaining)`;
  }
}


/* ── Pages table ── */

function renderPages(pages) {
  pageBody.innerHTML = pages.map((p) => {
    const statusCls = p.status >= 400 ? "http-error" : p.status >= 300 ? "http-redirect" : "http-ok";
    return `
      <tr>
        <td>
          <div class="page-title-cell">${esc(p.title || "(no title)")}</div>
          <div class="page-url-cell">${esc(p.url)}</div>
        </td>
        <td><span class="http-badge ${statusCls}">${p.status}</span></td>
        <td class="num-cell">${p.word_count.toLocaleString()}</td>
        <td class="num-cell">${p.h1_count}</td>
        <td class="num-cell">${p.image_count}</td>
        <td class="num-cell ${p.missing_alt > 0 ? 'num-warn' : ''}">${p.missing_alt}</td>
      </tr>
    `;
  }).join("");
}
