// AG Grid Community init for JobFindEasy. Loads /api/jobs.json once, renders
// all rows client-side. Pagination, multi-column filtering, sort, quick search
// are all built in.
//
// Hooks back into HTMX for row click → /partials/detail/{hash} → swap into
// #detail. Applied checkbox in-row writes through to /actions/applied/{hash}.
//
// Theme: Quartz dark, recolored to match our 3-color palette via CSS vars
// in app.css (.ag-theme-quartz-dark overrides).

(function () {
  if (typeof agGrid === "undefined") {
    console.warn("AG Grid not loaded yet");
    return;
  }

  const SCORE_ACCENT = "#10b981";
  const INK_3        = "rgba(232,232,232,0.38)";

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
    if (!t) return `<span class="text-ink-3 text-[11px] uppercase tracking-wider">—</span>`;
    const cls = t === "strong" ? "text-accent" : "text-ink-3";
    return `<span class="${cls} text-[11px] uppercase tracking-wider">${t}</span>`;
  }

  function urlCellRenderer(params) {
    const u = params.value;
    if (!u) return "";
    return `<a href="${u}" target="_blank" rel="noopener" class="text-ink-3 hover:text-accent" onclick="event.stopPropagation()" title="Open posting">↗</a>`;
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

  // Applied checkbox — Shoelace <sl-checkbox> renders inside the AG Grid cell.
  // The autoloader picks it up on insertion. We listen for `sl-change`.
  function appliedCellRenderer(params) {
    const checked = params.value ? "checked" : "";
    return `
      <sl-checkbox size="small"
                   ${checked}
                   data-hash="${params.data.hash}"
                   onclick="event.stopPropagation()"
                   style="--toggle-size:14px;"></sl-checkbox>`;
  }

  // Listen for sl-change once at the document level — covers all in-row
  // checkboxes regardless of when they're rendered.
  document.addEventListener("sl-change", async (e) => {
    const cb = e.target.closest("sl-checkbox[data-hash]");
    if (!cb) return;
    const hash = cb.dataset.hash;
    const applied = cb.checked ? "on" : "off";
    try {
      await fetch(`/actions/applied/${hash}`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `applied=${applied}`,
      });
    } catch (err) {
      console.error("Applied update failed:", err);
      cb.checked = !cb.checked;
    }
  });

  // Row click → load the detail panel via HTMX and reveal the detail pane.
  function onRowClicked(event) {
    const hash = event.data && event.data.hash;
    if (!hash) return;
    const detail = document.getElementById("detail");
    if (!detail) return;
    htmx.ajax("GET", `/partials/detail/${hash}`, { target: "#detail", swap: "innerHTML" });
    window._jiaSelectedHash = hash;
    document.querySelector(".jia-app")?.classList.add("has-detail");
    // Nudge AG Grid to redraw header/columns at the new container width
    // after the CSS grid transition finishes (~180ms).
    setTimeout(() => window._jiaGrid && window._jiaGrid.sizeColumnsToFit(), 220);
  }

  const columnDefs = [
    {
      field: "url",
      headerName: "",
      width: 40,
      sortable: false,
      filter: false,
      cellRenderer: urlCellRenderer,
      cellClass: "text-center",
    },
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
      filter: "agSetColumnFilter",   // falls back to text in Community; that's fine
      cellRenderer: tierCellRenderer,
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
      field: "source",
      headerName: "Source",
      width: 110,
      filter: "agTextColumnFilter",
      cellClass: "text-ink-3 text-xs",
    },
    {
      field: "applied",
      headerName: "Applied",
      width: 90,
      filter: "agSetColumnFilter",
      cellRenderer: appliedCellRenderer,
      cellClass: "text-center",
    },
  ];

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
        window._jiaSelectedHash && params.data && params.data.hash === window._jiaSelectedHash,
    },
  };

  async function refreshData() {
    const showRejects = document.querySelector("[name=show_rejects]")?.checked || false;
    try {
      const r = await fetch(`/api/jobs.json?show_rejects=${showRejects}`);
      const data = await r.json();
      if (window._jiaGrid) {
        window._jiaGrid.setGridOption("rowData", data);
        document.getElementById("grid-count").textContent = `${data.length} jobs`;
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
