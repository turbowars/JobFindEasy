// AG Grid Community init for JobFindEasy. Loads /api/jobs.json once, renders
// all rows client-side. Pagination, multi-column filtering, sort, quick search
// are all built in.
//
// Hooks back into HTMX for row click → /partials/detail/{hash} → swap into
// #detail. Status transitions happen in the detail pane's status strip.
//
// Theme: Quartz dark, recolored to match our 3-color palette via CSS vars
// in app.css (.ag-theme-quartz-dark overrides).

(function () {
  if (typeof agGrid === "undefined") {
    console.warn("AG Grid not loaded yet");
    return;
  }

  const SCORE_ACCENT = "#10b981";
  const INK_3 = "rgba(232,232,232,0.38)";

  // Cell renderer for the score column: progress bar + number, accent only
  // when score >= 80.
  function scoreCellRenderer(params) {
    const v = params.value;
    if (v === null || v === undefined) {
      return `<span class="text-ink-3">—</span>`;
    }
    const strong = v >= 80;
    const color = strong ? SCORE_ACCENT : INK_3;
    return `
      <div class="flex items-center gap-2 h-full">
        <span class="font-semibold tabular-nums w-7 ${strong ? "text-accent" : ""}">${v}</span>
        <div class="w-12 h-1 rounded-full bg-base-300/40 overflow-hidden">
          <div style="width:${v}%; height:100%; background:${color}; opacity:${strong ? 1 : 0.5};"></div>
        </div>
      </div>`;
  }

  function tierCellRenderer(params) {
    const t = params.value;
    if (!t)
      return `<span class="text-ink-3 text-[11px] uppercase tracking-wider">—</span>`;
    const cls = t === "strong" ? "text-accent" : "text-ink-3";
    return `<span class="${cls} text-[11px] uppercase tracking-wider">${t}</span>`;
  }

  function urlCellRenderer(params) {
    const u = params.value;
    if (!u) return "";
    const hash = (params.data && params.data.hash) || "";
    // Browser handles the new-tab open via target=_blank. The inline onclick
    // stops row-selection bubbling AND fires the status change to "applying"
    // in the background, mirroring the "Apply with Claude" button behavior.
    const onclick = `event.stopPropagation(); if (window.jia_markApplying) window.jia_markApplying('${hash}')`;
    return `<a href="${u}" target="_blank" rel="noopener"
              class="text-ink-3 hover:text-accent"
              onclick="${onclick}"
              title="Open posting (marks job as applying)">Do IT↗</a>`;
  }

  function copyableCellRenderer(params) {
    const v = (params.value || "").replace(/"/g, "&quot;");
    if (!v) return "";
    return `
      <span class="jia-copyable inline-block w-full">
        <span class="truncate block pr-5">${v}</span>
        <button class="copy-btn btn btn-ghost btn-xs h-5 min-h-0 px-1 text-ink-3"
                data-copy="${v}"
                onclick="event.stopPropagation()"
                title="Copy">⎘</button>
      </span>`;
  }

  // Status chip — read-only in the grid; transitions happen in the detail
  // pane's status strip. Distinction is glyph + opacity, not extra colors
  // (3-color palette discipline).
  const STATUS_GLYPHS = {
    new: "○",
    not_interested: "⊘",
    no_sponsorship: "∅",
    shortlisted: "★",
    applying: "▶",
    applied: "✓",
    interviewing: "⟳",
    offer: "◆",
    closed: "—",
  };
  const STATUS_LABELS = {
    new: "New",
    not_interested: "Not Interested",
    no_sponsorship: "No Sponsorship",
    shortlisted: "Shortlisted",
    applying: "Applying",
    applied: "Applied",
    interviewing: "Interviewing",
    offer: "Offer",
    closed: "Closed",
  };
  // Native <select> overlaid on the chip gives free dropdown UX (browser-
  // owned positioning, keyboard a11y, click-outside-to-close) while the
  // visible chip stays as-is. The select is invisible (opacity:0) and
  // covers the chip; a click on the cell hits the select, opening the
  // native picker. Closed states are flattened to "Closed (reason)" so
  // the user picks status + reason in one action.
  const STATUS_OPTIONS = [
    { value: "new",            label: "○ New" },
    { value: "not_interested", label: "⊘ Not Interested" },
    { value: "no_sponsorship", label: "∅ No Sponsorship" },
    { value: "shortlisted",    label: "★ Shortlisted" },
    { value: "applying",       label: "▶ Applying" },
    { value: "applied",        label: "✓ Applied" },
    { value: "interviewing",   label: "⟳ Interviewing" },
    { value: "offer",          label: "◆ Offer" },
    { value: "closed:rejected",          label: "— Closed (rejected)" },
    { value: "closed:withdrew",          label: "— Closed (withdrew)" },
    { value: "closed:ghosted",           label: "— Closed (ghosted)" },
    { value: "closed:declined_offer",    label: "— Closed (declined offer)" },
    { value: "closed:accepted_elsewhere",label: "— Closed (accepted elsewhere)" },
  ];

  function statusCellRenderer(params) {
    const s = params.value || "new";
    const hash = (params.data && params.data.hash) || "";
    const reason = params.data && params.data.closed_reason;
    const label =
      s === "closed" && reason
        ? `Closed · ${reason.replace(/_/g, " ")}`
        : STATUS_LABELS[s] || s;
    const currentValue = s === "closed" && reason ? `closed:${reason}` : s;
    const opts = STATUS_OPTIONS.map(
      (o) => `<option value="${o.value}"${o.value === currentValue ? " selected" : ""}>${o.label}</option>`
    ).join("");
    return (
      `<span class="status-chip-wrapper">` +
        `<span class="status-chip status-${s}">` +
          `<span class="status-glyph">${STATUS_GLYPHS[s] || "•"}</span>` +
          `<span class="status-label">${label}</span>` +
        `</span>` +
        `<select class="status-chip-select" aria-label="Change status" ` +
                `data-hash="${hash}" ` +
                `onclick="event.stopPropagation()" ` +
                `onchange="event.stopPropagation(); window.jia_setStatus && window.jia_setStatus(this.dataset.hash, this.value, this)">` +
          opts +
        `</select>` +
      `</span>`
    );
  }

  // Row click → load the detail panel via HTMX and reveal the detail pane.
  function onRowClicked(event) {
    const hash = event.data && event.data.hash;
    if (!hash) return;
    const detail = document.getElementById("detail");
    if (!detail) return;
    htmx.ajax("GET", `/partials/detail/${hash}`, {
      target: "#detail",
      swap: "innerHTML",
    });
    window._jiaSelectedHash = hash;
    document.querySelector(".jia-app")?.classList.add("has-detail");
    // Nudge AG Grid to redraw header/columns at the new container width
    // after the CSS grid transition finishes (~180ms).
    setTimeout(
      () => window._jiaGrid && window._jiaGrid.sizeColumnsToFit(),
      220,
    );
  }

  const columnDefs = [
    {
      field: "score",
      headerName: "Score",
      width: 130,
      filter: "agNumberColumnFilter",
      cellRenderer: scoreCellRenderer,
      sort: "desc",
    },
    {
      field: "tier",
      headerName: "Tier",
      width: 110,
      filter: "agTextColumnFilter",
      cellRenderer: tierCellRenderer,
    },
    {
      // "Apply" sits before Title so the action button lands closer to
      // the cursor's natural rest position after a Tier glance — the
      // user reaches the apply link without crossing the long title cell.
      field: "url",
      headerName: "Apply",
      width: 80,
      sortable: false,
      filter: false,
      cellRenderer: urlCellRenderer,
      cellClass: "text-center",
    },
    {
      field: "title",
      headerName: "Title",
      flex: 2,
      minWidth: 280,
      filter: "agTextColumnFilter",
      cellRenderer: copyableCellRenderer,
      tooltipField: "title",
    },
    {
      field: "company",
      headerName: "Company",
      flex: 1,
      minWidth: 160,
      filter: "agTextColumnFilter",
      cellRenderer: copyableCellRenderer,
      tooltipField: "company",
    },
    {
      field: "location",
      headerName: "Location",
      flex: 1,
      minWidth: 160,
      filter: "agTextColumnFilter",
      cellClass: "text-ink-3 text-xs",
    },
    {
      field: "posted",
      headerName: "Posted",
      width: 110,
      filter: "agTextColumnFilter",
      cellClass: "text-ink-3 text-xs tabular-nums",
    },
    {
      // Source column is hidden by default — low signal once you've seen
      // a few rows. Toggle visible via the column-menu funnel if needed
      // (right-click any column header → Choose columns).
      field: "source",
      headerName: "Source",
      width: 110,
      filter: "agTextColumnFilter",
      cellClass: "text-ink-3 text-xs",
      hide: true,
    },
    {
      field: "status",
      headerName: "Status",
      width: 150,
      filter: "agTextColumnFilter",
      cellRenderer: statusCellRenderer,
      cellClass: "text-left",
    },
  ];

  // External filter for the "Target" sidebar button (multi-status union:
  // new + shortlisted + applying). Column-level text filters only support
  // OR of 2 conditions on AG Grid Community; the external-filter hook is
  // the documented Community-supported way to express N-of-many.
  const TARGET_STATUSES = new Set(["new", "shortlisted", "applying"]);
  function isExternalFilterPresent() {
    return window._jiaTargetFilter === true;
  }
  function doesExternalFilterPass(node) {
    if (!window._jiaTargetFilter) return true;
    return node.data && TARGET_STATUSES.has(node.data.status);
  }

  const gridOptions = {
    columnDefs,
    rowData: [],
    defaultColDef: {
      sortable: true,
      filter: true,
      resizable: true,
      floatingFilter: true,
    },
    pagination: true,
    paginationPageSize: 50,
    paginationPageSizeSelector: [25, 50, 100, 250, 500],
    rowSelection: "single",
    suppressRowClickSelection: false,
    animateRows: true,
    rowHeight: 38,
    headerHeight: 36,
    floatingFiltersHeight: 32,
    isExternalFilterPresent,
    doesExternalFilterPass,
    onRowClicked,
    onGridReady: async (params) => {
      window._jiaGrid = params.api;
      await refreshData();
    },
    getRowClass: (params) => {
      const s = params.data && params.data.score;
      if (s === null || s === undefined) return "";
      if (s >= 80) return "row-strong";
      if (s >= 60) return "row-possible";
      if (s >= 40) return "row-stretch";
      return "row-weak";
    },
    rowClassRules: {
      "row-selected": (params) =>
        window._jiaSelectedHash &&
        params.data &&
        params.data.hash === window._jiaSelectedHash,
      "row-closed": (params) => params.data && params.data.status === "closed",
    },
  };

  async function refreshData() {
    const showRejects =
      document.querySelector("[name=show_rejects]")?.checked || false;
    try {
      const r = await fetch(`/api/jobs.json?show_rejects=${showRejects}`);
      const data = await r.json();
      if (window._jiaGrid) {
        window._jiaGrid.setGridOption("rowData", data);
        document.getElementById("grid-count").textContent =
          `${data.length} jobs`;
      }
    } catch (e) {
      console.error("Failed to load jobs:", e);
    }
  }

  // Initialize when the grid div is in the DOM.
  function init() {
    const gridDiv = document.querySelector("#jia-grid");
    if (!gridDiv) return;
    agGrid.createGrid(gridDiv, gridOptions);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Quick-filter input wiring — Shoelace fires `sl-input` on every keystroke.
  document.addEventListener("sl-input", (e) => {
    if (e.target.matches("[data-grid-search]")) {
      if (window._jiaGrid) {
        window._jiaGrid.setGridOption("quickFilterText", e.target.value);
      }
    }
  });

  // Toggle for showing prefilter rejects (sl-checkbox fires sl-change)
  document.addEventListener("sl-change", (e) => {
    if (e.target.matches("sl-checkbox[name=show_rejects]")) {
      refreshData();
    }
  });

  // Expose refresh for after mutations.
  window.jia_refreshGrid = refreshData;
})();
