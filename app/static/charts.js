// Renders chart specs produced by the server (app/charts.py) with Chart.js.
(function () {
  const PALETTE = ["#2f5bd3", "#e07a1f", "#2a9d6a", "#c2417a", "#7a5cd6", "#8a8f2a", "#1f8fb3", "#b5523b"];
  const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderChart(container, spec) {
    const card = document.createElement("div");
    card.className = "chart-card";
    card.innerHTML = `<h4>${esc(spec.title)}</h4>`;
    container.appendChild(card);
    if (spec.chart_type === "table") {
      const head = spec.columns.map((c) => `<th>${esc(c)}</th>`).join("");
      const rows = spec.rows.map((r) => "<tr>" + r.map((v) => (typeof v === "number" ? `<td class="num">${fmt.format(v)}</td>` : `<td>${esc(v)}</td>`)).join("") + "</tr>").join("");
      card.insertAdjacentHTML("beforeend", `<div style="overflow-x:auto"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`);
      return;
    }
    const box = document.createElement("div");
    box.className = "chart-box";
    const canvas = document.createElement("canvas");
    box.appendChild(canvas);
    card.appendChild(box);
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue("--muted").trim();
    const grid = css.getPropertyValue("--border").trim();
    const round = spec.chart_type === "pie" || spec.chart_type === "doughnut";
    const type = spec.chart_type === "horizontal_bar" ? "bar" : spec.chart_type;
    const datasets = spec.datasets.map((d, i) => ({
      label: d.label,
      data: d.data,
      backgroundColor: round ? spec.labels.map((_, j) => PALETTE[j % PALETTE.length]) : PALETTE[i % PALETTE.length],
      borderColor: round ? "transparent" : PALETTE[i % PALETTE.length],
      borderWidth: 2,
      pointRadius: spec.labels.length > 40 ? 0 : 2,
      tension: 0.25,
    }));
    new Chart(canvas, {
      type,
      data: { labels: spec.labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: spec.chart_type === "horizontal_bar" ? "y" : "x",
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: datasets.length > 1 || round, labels: { color: text } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt.format(c.parsed.y ?? c.parsed.x ?? c.parsed)}` } },
        },
        scales: round ? {} : {
          x: { ticks: { color: text, maxRotation: 0, autoSkip: true }, grid: { color: grid } },
          y: { ticks: { color: text, callback: (v) => fmt.format(v) }, grid: { color: grid }, beginAtZero: true },
        },
      },
    });
  }

  function renderMarkdown(el, md) {
    el.innerHTML = DOMPurify.sanitize(marked.parse(md || ""));
  }

  window.AE = { renderChart, renderMarkdown, esc, fmt };
})();
