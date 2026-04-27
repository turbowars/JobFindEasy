// JobFindEasy — small client-side helpers (clipboard + keyboard shortcuts).
// Locked to a single custom dark theme ("jia") defined in base.html — no
// runtime toggle; the design is opinionated.


// --------------------------------------------------------------------------
// Clipboard copy: any element with [data-copy] gets click-to-copy behaviour.
// Per-cell copy buttons in the table use this; quick-copy code blocks too.
// --------------------------------------------------------------------------
function copyText(el, text) {
  if (!navigator.clipboard) return;
  navigator.clipboard.writeText(text).then(() => {
    el.classList.add("copied");
    const original = el.dataset.label || el.textContent;
    if (el.tagName === "BUTTON") {
      const orig = el.textContent;
      el.textContent = "✓";
      setTimeout(() => { el.textContent = orig; el.classList.remove("copied"); }, 1100);
    } else {
      // <code> blocks: just flash the border
      setTimeout(() => el.classList.remove("copied"), 1100);
    }
  });
}

document.addEventListener("click", (e) => {
  const target = e.target.closest("[data-copy]");
  if (!target) return;
  const text = target.dataset.copy;
  if (text) {
    e.stopPropagation();
    copyText(target, text);
  }
});

// --------------------------------------------------------------------------
// Keyboard shortcuts:
//   j / ↓  → next row
//   k / ↑  → previous row
//   a       → toggle Applied on selected row
//   g       → generate resume for selected row
//   /       → focus search input
//   esc     → close detail panel
// Disabled when the user is typing in an input/textarea.
// --------------------------------------------------------------------------
function isTyping(el) {
  if (!el) return false;
  const t = el.tagName;
  // Native form fields + Shoelace web components (SL-INPUT, SL-TEXTAREA, SL-SELECT, SL-CHECKBOX, SL-SWITCH)
  if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || el.isContentEditable) return true;
  if (t && t.startsWith("SL-")) return true;
  return false;
}

// Keyboard navigation routed through AG Grid's API. The grid is exposed
// as window._jiaGrid by grid.js's onGridReady handler.

function _gridReady() {
  return window._jiaGrid && typeof window._jiaGrid.getDisplayedRowCount === "function";
}

function _currentRowIndex() {
  if (!_gridReady() || !window._jiaSelectedHash) return -1;
  const total = window._jiaGrid.getDisplayedRowCount();
  for (let i = 0; i < total; i++) {
    const node = window._jiaGrid.getDisplayedRowAtIndex(i);
    if (node && node.data && node.data.hash === window._jiaSelectedHash) return i;
  }
  return -1;
}

function _selectRowByIndex(idx) {
  if (!_gridReady()) return;
  const node = window._jiaGrid.getDisplayedRowAtIndex(idx);
  if (!node || !node.data) return;
  const hash = node.data.hash;
  window._jiaSelectedHash = hash;
  // AG Grid: highlight the row natively and scroll it into view.
  window._jiaGrid.deselectAll();
  node.setSelected(true, true);
  window._jiaGrid.ensureIndexVisible(idx, "middle");
  // Refresh the row class rules so .row-selected styling re-applies.
  window._jiaGrid.redrawRows({ rowNodes: [node] });
  // Reveal the detail pane (collapsed by default to give the grid full width).
  document.querySelector(".jia-app")?.classList.add("has-detail");
  // Load the detail panel via HTMX (mirrors the click flow in grid.js).
  if (window.htmx) {
    htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
  }
}

function navigate(delta) {
  if (!_gridReady()) return;
  const total = window._jiaGrid.getDisplayedRowCount();
  if (total === 0) return;
  const cur = _currentRowIndex();
  // From "no selection" — j goes to the top, k goes to the top too (still 0).
  // Otherwise clamp to [0, total-1].
  const next = cur < 0 ? 0 : Math.max(0, Math.min(total - 1, cur + delta));
  _selectRowByIndex(next);
}

document.addEventListener("keydown", (e) => {
  if (isTyping(e.target)) {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  // Don't intercept modifier combos
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  switch (e.key) {
    case "j":
    case "ArrowDown":
      e.preventDefault();
      navigate(+1);
      break;
    case "k":
    case "ArrowUp":
      e.preventDefault();
      navigate(-1);
      break;
    case "a": {
      // Quick-set status to 'applied' for the selected job.
      const strip = document.querySelector("#detail [data-status-strip]");
      if (strip) {
        e.preventDefault();
        const pill = strip.querySelector("[data-status='applied']");
        if (pill) pill.click();
      }
      break;
    }
    case "g": {
      // Generate resume for the currently-selected detail row.
      const btn = document.querySelector("#detail [data-action='generate-resume']");
      if (btn) {
        e.preventDefault();
        btn.click();
      }
      break;
    }
    case "/": {
      const search = document.querySelector("[data-search]");
      if (search) {
        e.preventDefault();
        // Shoelace components expose `.focus()` on the host element which
        // delegates to the internal native input.
        if (typeof search.focus === "function") search.focus();
        if (typeof search.select === "function") search.select();
      }
      break;
    }
    case "Escape": {
      window.jia_closeDetail();
      break;
    }
  }
});

// Reset the detail pane to the empty-state placeholder. Exposed globally so
// the close-X button in detail.html can call it.
window.jia_closeDetail = function () {
  // Hide the detail pane (CSS does the rest — grid takes full width).
  document.querySelector(".jia-app")?.classList.remove("has-detail");
  // Clear AG Grid's row selection so the highlight goes away.
  if (window._jiaGrid && typeof window._jiaGrid.deselectAll === "function") {
    window._jiaGrid.deselectAll();
  }
  window._jiaSelectedHash = null;
  // Re-fit columns once the CSS transition lands (~180ms).
  setTimeout(() => window._jiaGrid && window._jiaGrid.sizeColumnsToFit(), 220);
  // Reset the detail content so a re-open starts cleanly.
  const detail = document.getElementById("detail");
  if (detail) {
    detail.innerHTML = `
      <div class="empty-state h-full flex flex-col items-center justify-center text-center px-4 py-16 text-ink-3">
        <div class="text-2xl mb-4 opacity-40">◇</div>
        <h3 class="text-sm font-semibold text-ink mb-1">No job selected</h3>
        <p class="text-xs">Click a row, or press <kbd>j</kbd>/<kbd>k</kbd></p>
      </div>`;
  }
};

// Track which row is currently selected. AG Grid sets this in grid.js when a
// row is clicked or programmatically selected; the keyboard nav reads it
// to find the index and step ±1.
window._jiaSelectedHash = window._jiaSelectedHash || null;

// Toast for generation submissions (HTMX returns 204; we surface UX feedback)
document.body.addEventListener("htmx:afterRequest", (e) => {
  const url = e.detail.requestConfig?.path || "";
  if (e.detail.xhr.status >= 200 && e.detail.xhr.status < 300) {
    if (url.includes("/actions/generate/")) {
      flashMessage("📝 queued — watch the sidebar tray");
    } else if (url.includes("/actions/regenerate/")) {
      flashMessage("🔁 regeneration queued");
    } else if (url.includes("/actions/bulk-generate")) {
      flashMessage("📦 bulk generation queued");
    }
  }
});

// --------------------------------------------------------------------------
// Status strip — pill clicks transition the selected job's state.
// Closed pill uses an <sl-dropdown> menu of reasons (sl-select event).
// --------------------------------------------------------------------------
async function setJobStatus(hash, status, closedReason) {
  const body = new URLSearchParams({ status });
  if (closedReason) body.set("closed_reason", closedReason);
  const r = await fetch(`/actions/status/${hash}`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) {
    flashMessage("Status update failed");
    return false;
  }
  // Refresh detail pane (so pill states re-render) and grid (so chip + sidebar counts update)
  htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
  if (typeof window.jia_refreshGrid === "function") window.jia_refreshGrid();
  htmx.ajax("GET", "/partials/badges", { target: "#badges", swap: "innerHTML" });
  return true;
}

document.addEventListener("click", (e) => {
  const pill = e.target.closest("[data-status-pill]");
  if (!pill) return;
  const strip = pill.closest("[data-status-strip]");
  if (!strip) return;
  e.preventDefault();
  const hash = strip.dataset.hash;
  const status = pill.dataset.status;
  setJobStatus(hash, status);
});

document.addEventListener("sl-select", (e) => {
  const menu = e.target.closest("[data-closed-menu]");
  if (!menu) return;
  const hash = menu.dataset.hash;
  const reason = e.detail.item && e.detail.item.value;
  if (!hash || !reason) return;
  setJobStatus(hash, "closed", reason);
});

// "Apply queue with Claude" — fetches a server-rendered prompt that
// embeds Dheeraj's context, the 7-step workflow, and the active queue
// (shortlisted + applying) with hashes + URLs, then copies it to the
// clipboard for paste into the Claude-for-Chrome sidebar.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("#claude-prompt-btn");
  if (!btn) return;
  e.preventDefault();
  btn.loading = true;
  btn.disabled = true;
  try {
    const r = await fetch("/api/claude-prompt.txt");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const text = await r.text();
    await navigator.clipboard.writeText(text);
    // Crude queue-size readout from the prompt header
    const m = text.match(/^## Queue \((\d+) job/m);
    const n = m ? m[1] : "?";
    flashMessage(`📋 prompt copied (${n} job${n === "1" ? "" : "s"}) — paste into Claude for Chrome`);
  } catch (err) {
    flashMessage("Copy failed: " + err.message);
  } finally {
    btn.loading = false;
    btn.disabled = false;
  }
});

// Pipeline rows in the sidebar — click filters the grid by that status.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-status-filter]");
  if (!btn) return;
  e.preventDefault();
  if (!window._jiaGrid) return;
  const status = btn.dataset.statusFilter;
  // AG Grid set filter for the status column
  const filter = window._jiaGrid.getFilterInstance("status");
  if (filter && filter.setModel) {
    filter.setModel({ values: [status] });
    window._jiaGrid.onFilterChanged();
  }
});

// Apply with Claude — flips status to 'applying' then opens the URL in a
// new tab. Session-brief generation is a follow-up PR.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action='apply-with-claude']");
  if (!btn) return;
  e.preventDefault();
  const hash = btn.dataset.hash;
  const url = btn.dataset.url;
  try {
    const r = await fetch(`/actions/apply/${hash}`, { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  } catch (err) {
    flashMessage("Apply transition failed: " + err.message);
    return;
  }
  htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
  if (typeof window.jia_refreshGrid === "function") window.jia_refreshGrid();
  htmx.ajax("GET", "/partials/badges", { target: "#badges", swap: "innerHTML" });
  if (url) window.open(url, "_blank", "noopener");
  flashMessage("▶ status: applying");
});

// --------------------------------------------------------------------------
// Inject job by URL — fetch + extract + score, then refresh the grid and
// auto-select the new row.
// --------------------------------------------------------------------------
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("#inject-submit");
  if (!btn) return;
  const form = document.getElementById("inject-form");
  if (!form) return;
  const input = form.querySelector("sl-input[name=url]");
  const status = document.getElementById("inject-status");
  const url = (input && input.value || "").trim();
  if (!url) { status.textContent = "Paste a URL first."; return; }

  btn.loading = true;
  btn.disabled = true;
  status.textContent = "Fetching and extracting…";

  try {
    const body = new URLSearchParams({ url });
    const r = await fetch("/actions/inject-url", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    const data = await r.json();
    if (!r.ok || !data.ok) {
      status.textContent = "✗ " + (data.error || `HTTP ${r.status}`);
      flashMessage("Inject failed: " + (data.error || `HTTP ${r.status}`));
      return;
    }
    if (data.duplicate) {
      status.textContent = `Already in DB: ${data.title} @ ${data.company}`;
    } else {
      status.textContent = `✓ ${data.title} @ ${data.company}`;
    }
    input.value = "";
    flashMessage(data.duplicate ? "↺ already had it" : "✓ injected & scored");

    if (typeof window.jia_refreshGrid === "function") {
      await window.jia_refreshGrid();
    }
    if (window._jiaGrid && data.hash) {
      window._jiaGrid.forEachNode((node) => {
        if (node.data && node.data.hash === data.hash) {
          node.setSelected(true, true);
          window._jiaGrid.ensureNodeVisible(node, "middle");
        }
      });
    }
  } catch (err) {
    status.textContent = "✗ " + err.message;
    flashMessage("Inject failed: " + err.message);
  } finally {
    btn.loading = false;
    btn.disabled = false;
  }
});

function flashMessage(msg) {
  let el = document.getElementById("flash");
  if (!el) {
    el = document.createElement("div");
    el.id = "flash";
    Object.assign(el.style, {
      position: "fixed", bottom: "1rem", right: "1rem",
      background: "#10b981", color: "white",
      padding: "0.6rem 1rem", borderRadius: "6px",
      fontSize: "0.85rem", zIndex: 1000,
      boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
      transition: "opacity 0.3s",
    });
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = "1";
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = "0"; }, 2500);
}
