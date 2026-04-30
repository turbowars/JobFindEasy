// JobFindEasy — small client-side helpers (clipboard + keyboard shortcuts).
// Locked to a single custom dark theme ("jia") defined in base.html — no
// runtime toggle; the design is opinionated.


// --------------------------------------------------------------------------
// Browser notifications for completed generations.
//
// Chrome's Notification API requires user-gesture-attached permission
// requests, so we hook the FIRST click anywhere on the page (any user
// gesture qualifies) and ask once. Subsequent generation completions —
// detected via the generations tray's data-generation-state attribute
// flipping from "running" to "done" / "failed" — fire a notification
// labeled with the kind + title + company. The seen-set dedupes so we
// don't re-notify on every poll, and seeds itself with already-done rows
// on first render so old completions don't burst-notify on page load.
// --------------------------------------------------------------------------
const _jiaNotifySeen = new Set();
let _jiaNotifySeeded = false;
const _jiaKindLabel = { resume: "Resume", cover: "Cover letter", refine: "Refine" };

function _jiaCanNotify() {
  return typeof Notification !== "undefined" && Notification.permission === "granted";
}

function _jiaUpdateNotifyToggleLabel() {
  const el = document.getElementById("jia-notify-status");
  if (!el) return;
  if (typeof Notification === "undefined") {
    el.textContent = "🔔 Notifications: not supported";
    return;
  }
  const p = Notification.permission;
  if (p === "granted") {
    el.textContent = "🔔 Notifications: enabled ✓";
  } else if (p === "denied") {
    el.textContent =
      "🔔 Notifications: blocked — open chrome://settings/content/notifications, find 127.0.0.1:8826, allow";
  } else {
    el.textContent = "🔔 Notifications: click to enable";
  }
}

async function _jiaRequestNotificationPermissionExplicit() {
  if (typeof Notification === "undefined") return;
  // requestPermission must run from a user-gesture handler (this is fine —
  // we're inside a click listener). Calling it on a denied origin is a
  // no-op; the user has to manually unblock via chrome settings.
  try {
    await Notification.requestPermission();
  } catch (err) {
    console.warn("notification permission request failed:", err);
  }
  _jiaUpdateNotifyToggleLabel();
  // Optional: send a one-shot test notification on grant so the user
  // immediately sees that it works (pre-empts "is this even on?" doubt).
  if (Notification.permission === "granted") {
    try {
      const n = new Notification("JobFindEasy notifications enabled", {
        body: "You'll get pinged when resumes / cover letters / refines complete.",
        tag: "jia-permission-confirm",
      });
      setTimeout(() => n.close(), 4000);
    } catch (_e) {
      // Some browsers reject the test notification synchronously after
      // grant — not worth interrupting the flow.
    }
  }
}

// Wire the sidebar toggle button.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("#jia-notify-toggle");
  if (!btn) return;
  e.preventDefault();
  _jiaRequestNotificationPermissionExplicit();
});

// Initial label set + a soft auto-prompt on the first user gesture (kept
// for users whose Chrome heuristics still allow it; the toggle button is
// the reliable path for everyone else).
_jiaUpdateNotifyToggleLabel();
document.addEventListener(
  "click",
  () => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission()
        .catch(() => {})
        .finally(_jiaUpdateNotifyToggleLabel);
    }
  },
  { once: true },
);

function _jiaScanGenerationsForCompletions(rootEl) {
  const rows = rootEl.querySelectorAll("[data-generation-id][data-generation-state]");
  // First swap after page load: seed the seen-set without firing notifications
  // for completions that already happened before we got here.
  if (!_jiaNotifySeeded) {
    rows.forEach((r) => {
      const state = r.dataset.generationState;
      if (state === "done" || state === "failed") {
        _jiaNotifySeen.add(r.dataset.generationId);
      }
    });
    _jiaNotifySeeded = true;
    return;
  }
  rows.forEach((r) => {
    const id = r.dataset.generationId;
    const state = r.dataset.generationState;
    if (!id || (state !== "done" && state !== "failed")) return;
    if (_jiaNotifySeen.has(id)) return;
    _jiaNotifySeen.add(id);
    _jiaFireNotification({
      state,
      kind: r.dataset.generationKind || "resume",
      title: r.dataset.generationTitle || "",
      company: r.dataset.generationCompany || "",
      hash: r.dataset.hash || "",
    });
  });
}

function _jiaFireNotification({ state, kind, title, company, hash }) {
  if (!_jiaCanNotify()) return;
  const kindLabel = _jiaKindLabel[kind] || kind;
  const headline = state === "failed"
    ? `${kindLabel} generation failed`
    : `${kindLabel} ready: ${company}`;
  const body = state === "failed"
    ? `${title} at ${company}`
    : title;
  try {
    const n = new Notification(headline, {
      body,
      tag: `jia-${kind}-${hash || title}`,  // newer notifications replace older ones for the same job
      icon: "/static/icons/notification.png",  // optional; falls back if missing
      silent: false,
    });
    n.onclick = () => {
      window.focus();
      if (hash && typeof window.jia_openJobDetail === "function") {
        window.jia_openJobDetail(hash);
      }
      n.close();
    };
  } catch (err) {
    // Some browsers throw if we exceed quota or are in restricted mode.
    // Notification is a nice-to-have, never block on it.
    console.warn("notification failed:", err);
  }
}

document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target && e.target.id === "generations") {
    _jiaScanGenerationsForCompletions(e.target);
  }
});


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
// Copy a rendered preview block (resume / cover letter) to the clipboard
// as plain text. Buttons use [data-action="copy-preview"][data-preview-target]
// pointing at the element whose `innerText` we read. Plain text (not HTML)
// is what ATS forms accept when they ask for "paste your resume".
// --------------------------------------------------------------------------
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action='copy-preview']");
  if (!btn) return;
  e.stopPropagation();
  const targetId = btn.dataset.previewTarget;
  const el = targetId ? document.getElementById(targetId) : null;
  if (!el) {
    flashMessage("preview not loaded yet");
    return;
  }
  const text = (el.innerText || "").trim();
  if (!text) {
    flashMessage("nothing to copy");
    return;
  }
  copyText(btn, text);
  flashMessage(`✓ copied ${text.length.toLocaleString()} chars`);
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

// Shared "open the detail panel for this hash" routine. Used by:
//   - keyboard nav (j/k) via _selectRowByIndex
//   - generations-tray click (jumps to a job from the sidebar tray)
// Always reveals the detail pane and remembers the selection.
function openJobDetail(hash) {
  if (!hash) return;
  window._jiaSelectedHash = hash;
  document.querySelector(".jia-app")?.classList.add("has-detail");
  if (window.htmx) {
    htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
  }
  // If the grid is loaded, also sync row selection so highlighting matches.
  if (_gridReady()) {
    const total = window._jiaGrid.getDisplayedRowCount();
    for (let i = 0; i < total; i++) {
      const node = window._jiaGrid.getDisplayedRowAtIndex(i);
      if (node && node.data && node.data.hash === hash) {
        window._jiaGrid.deselectAll();
        node.setSelected(true, true);
        window._jiaGrid.ensureIndexVisible(i, "middle");
        window._jiaGrid.redrawRows({ rowNodes: [node] });
        return;
      }
    }
  }
}
window.jia_openJobDetail = openJobDetail;

function _selectRowByIndex(idx) {
  if (!_gridReady()) return;
  const node = window._jiaGrid.getDisplayedRowAtIndex(idx);
  if (!node || !node.data) return;
  openJobDetail(node.data.hash);
}

// Generations tray rows carry [data-action="open-job-detail"][data-hash="..."]
// so clicking a row in the sidebar jumps to that job's detail pane.
document.addEventListener("click", (e) => {
  const row = e.target.closest("[data-action='open-job-detail']");
  if (!row) return;
  e.stopPropagation();
  openJobDetail(row.dataset.hash);
});

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
  // htmx.ajax drives the POST so HTMX auto-processes the OOB badges fragment
  // the server returns (web/app.py:_badges_oob_html). No second round-trip
  // for /partials/badges needed.
  const values = { status };
  if (closedReason) values.closed_reason = closedReason;
  try {
    await htmx.ajax("POST", `/actions/status/${hash}`, {
      source: "body",
      values,
      swap: "none",
    });
  } catch (err) {
    flashMessage("Status update failed");
    return false;
  }
  // Detail pane + grid still need explicit refresh — they're not OOB targets.
  htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
  if (typeof window.jia_refreshGrid === "function") window.jia_refreshGrid();
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
// Click the active row again to clear. Uses the grid-level setFilterModel
// API with the text-filter shape because we're on AG Grid Community (the
// `agSetColumnFilter` declared on the column silently falls back to text;
// the set-filter `{values: [...]}` shape is enterprise-only and didn't
// apply, which is the bug the user reported).
function _applyStatusFilter(status) {
  if (!window._jiaGrid) return;
  // Per-status and Target are mutually exclusive — clicking a single
  // status while Target is active turns Target off, otherwise the row
  // disappears (status='applied' isn't in the Target union).
  window._jiaTargetFilter = false;
  const current = window._jiaGrid.getFilterModel() || {};
  const isActive = current.status && current.status.filter === status;
  const next = { ...current };
  if (isActive) {
    delete next.status;
  } else {
    next.status = { filterType: "text", type: "equals", filter: status };
  }
  window._jiaGrid.setFilterModel(next);
  // The grid usually refreshes filters automatically on setFilterModel,
  // but call onFilterChanged explicitly to be safe across versions.
  if (typeof window._jiaGrid.onFilterChanged === "function") {
    window._jiaGrid.onFilterChanged();
  }
  _paintActivePipelineButton();
}

// "All alerts" — escape hatch that clears both the Target external filter
// and any per-status column filter. Doesn't touch other column filters
// (location, company, score range — those stay).
function _clearAllPipelineFilters() {
  if (!window._jiaGrid) return;
  window._jiaTargetFilter = false;
  const current = window._jiaGrid.getFilterModel() || {};
  if (current.status) {
    const next = { ...current };
    delete next.status;
    window._jiaGrid.setFilterModel(next);
  }
  if (typeof window._jiaGrid.onFilterChanged === "function") {
    window._jiaGrid.onFilterChanged();
  }
  _paintActivePipelineButton();
}

// "Target" sidebar filter — the actionable queue (new + shortlisted +
// applying). Driven by the grid's external-filter hook (declared in
// grid.js); we just toggle the flag and ping the grid.
function _applyTargetFilter() {
  if (!window._jiaGrid) return;
  const turningOn = !window._jiaTargetFilter;
  window._jiaTargetFilter = turningOn;
  // Mutually exclusive with the column-level status filter — clearing
  // it prevents intersection-empty results.
  if (turningOn) {
    const current = window._jiaGrid.getFilterModel() || {};
    if (current.status) {
      const next = { ...current };
      delete next.status;
      window._jiaGrid.setFilterModel(next);
    }
  }
  if (typeof window._jiaGrid.onFilterChanged === "function") {
    window._jiaGrid.onFilterChanged();
  }
  _paintActivePipelineButton();
}

// Read the grid's current status filter, mark the corresponding sidebar
// button as active. Called after each click and after badges OOB swaps
// (the buttons re-render fresh from the server and lose their class).
function _paintActivePipelineButton() {
  const model = window._jiaGrid && window._jiaGrid.getFilterModel
    ? (window._jiaGrid.getFilterModel() || {})
    : {};
  const active = model.status && model.status.filter ? model.status.filter : null;
  document.querySelectorAll("[data-status-filter]").forEach((b) => {
    const isActive = b.dataset.statusFilter === active;
    b.classList.toggle("bg-accent/10", isActive);
    b.classList.toggle("text-accent", isActive);
    b.classList.toggle("ring-1", isActive);
    b.classList.toggle("ring-accent/40", isActive);
  });
  document.querySelectorAll("[data-target-filter]").forEach((b) => {
    const isActive = window._jiaTargetFilter === true;
    b.classList.toggle("bg-accent/10", isActive);
    b.classList.toggle("text-accent", isActive);
    b.classList.toggle("ring-1", isActive);
    b.classList.toggle("ring-accent/40", isActive);
  });
  // "All alerts" button is active when no pipeline filter is on (Target
  // off AND no per-status filter set).
  document.querySelectorAll("[data-all-filter]").forEach((b) => {
    const noPipelineFilter = !window._jiaTargetFilter && !active;
    b.classList.toggle("bg-accent/10", noPipelineFilter);
    b.classList.toggle("text-accent", noPipelineFilter);
    b.classList.toggle("ring-1", noPipelineFilter);
    b.classList.toggle("ring-accent/40", noPipelineFilter);
  });
}

document.addEventListener("click", (e) => {
  const allBtn = e.target.closest("[data-all-filter]");
  if (allBtn) {
    e.preventDefault();
    _clearAllPipelineFilters();
    return;
  }
  const targetBtn = e.target.closest("[data-target-filter]");
  if (targetBtn) {
    e.preventDefault();
    _applyTargetFilter();
    return;
  }
  const btn = e.target.closest("[data-status-filter]");
  if (!btn) return;
  e.preventDefault();
  _applyStatusFilter(btn.dataset.statusFilter);
});

// Re-apply the active class after the badges partial OOB-swaps (the buttons
// are re-rendered HTML, so they lose the class we set on the previous
// click). HTMX dispatches `htmx:afterSwap` on the swapped element.
document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target && e.target.id === "badges") {
    _paintActivePipelineButton();
  }
});

// Single source of truth for "mark this job as applying and refresh the UI."
// Used by:
//   - the "Apply with Claude" detail-pane button (handler below)
//   - the AG Grid URL cell renderer (links open in new tab AND mark applying)
// Returns a Promise so callers can await UI refresh if they need to. Errors
// surface as a flash message; the function never throws to its caller.
// NOTE: markApplying stays on fetch (not htmx.ajax) because /actions/apply
// returns JSON we read here (`transitioned`, `status`) to drive the flash
// message ("already applied — keeping current status" vs "▶ status:
// applying"). htmx.ajax doesn't expose the JSON body cleanly. Switching
// to OOB would require a server-side refactor to use HX-Trigger headers
// — bigger than this turn's scope. The manual /partials/badges GET below
// stays for the same reason.
async function markApplying(hash) {
  if (!hash) return;
  let payload;
  try {
    const r = await fetch(`/actions/apply/${hash}`, { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    payload = await r.json();
  } catch (err) {
    flashMessage("Apply transition failed: " + err.message);
    return;
  }
  // Server only flips status when current state is new/shortlisted; for
  // jobs already further along the pipeline it's a no-op so we keep the UI
  // quiet rather than re-rendering the whole grid for nothing.
  if (!payload.transitioned) {
    flashMessage(`already ${payload.status} — keeping current status`);
    return;
  }
  if (window._jiaSelectedHash === hash) {
    htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
  }
  if (typeof window.jia_refreshGrid === "function") window.jia_refreshGrid();
  htmx.ajax("GET", "/partials/badges", { target: "#badges", swap: "innerHTML" });
  flashMessage("▶ status: applying");
}
window.jia_markApplying = markApplying;

// Generic status transition called from the AG Grid status chip's overlay
// <select>. `value` is either a plain status ("new", "applying", ...) or
// a flattened "closed:<reason>" combo. The server validates the transition
// and clears closed_reason when status != closed.
async function setStatus(hash, value, selectEl) {
  if (!hash || !value) return;
  let status = value;
  let reason = "";
  if (value.startsWith("closed:")) {
    status = "closed";
    reason = value.slice("closed:".length);
  }
  // htmx.ajax drives the POST so HTMX auto-processes the OOB badges fragment
  // the server returns. No follow-up /partials/badges call needed.
  const values = { status };
  if (reason) values.closed_reason = reason;
  try {
    await htmx.ajax("POST", `/actions/status/${hash}`, {
      source: "body",
      values,
      swap: "none",
    });
  } catch (err) {
    flashMessage("status update failed: " + (err && err.message ? err.message : err));
    return;
  }
  if (window._jiaSelectedHash === hash) {
    htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
  }
  if (typeof window.jia_refreshGrid === "function") window.jia_refreshGrid();
  flashMessage(`status: ${status}${reason ? " · " + reason.replace(/_/g, " ") : ""}`);
}
window.jia_setStatus = setStatus;

// Apply with Claude — marks applying then opens the URL in a new tab.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action='apply-with-claude']");
  if (!btn) return;
  e.preventDefault();
  await markApplying(btn.dataset.hash);
  const url = btn.dataset.url;
  if (url) window.open(url, "_blank", "noopener");
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
