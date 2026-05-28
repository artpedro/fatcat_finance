let fatcatCardChart = null;
let fatcatCatChart = null;
let fatcatSavingsGrowthChart = null;
let fatcatSavingsGrossChart = null;
let fatcatCdiDailyChart = null;
let fatcatCatModalBound = false;

function safeJsonElement(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch (_err) {
    return null;
  }
}

function fatcatChartBorderColor() {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
  return v || "#2F243A";
}

function fatcatFormatMoneyBR(value) {
  const n = typeof value === "number" ? value : parseFloat(value);
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(n);
}

function renderLegend(legendId, payload) {
  const el = document.getElementById(legendId);
  if (!el) return;
  if (!payload || !payload.labels || payload.labels.length === 0) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = payload.labels
    .map((label, i) => {
      const color = (payload.colors && payload.colors[i]) || "#DB8A74";
      return (
        '<span class="leg-item">' +
        '<span class="leg-sq" style="background:' +
        color +
        '"></span>' +
        String(label) +
        "</span>"
      );
    })
    .join("");
}

function renderDoughnut(canvasId, payload, previous) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !payload || !payload.labels || payload.labels.length === 0) return previous;
  if (previous) previous.destroy();
  const border = fatcatChartBorderColor();
  return new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: payload.labels,
      datasets: [
        {
          data: payload.values,
          backgroundColor: payload.colors,
          borderWidth: 4,
          borderColor: border,
          hoverBorderColor: "transparent",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "60%",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => " " + fatcatFormatMoneyBR(ctx.raw),
          },
        },
      },
    },
  });
}

function fatcatHideCatBreakdownModal() {
  const modal = document.getElementById("cat-breakdown-modal");
  if (!modal) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

function fatcatBindCatBreakdownModal() {
  if (fatcatCatModalBound) return;
  const closeBtn = document.getElementById("cat-breakdown-close");
  if (closeBtn) {
    closeBtn.addEventListener("click", fatcatHideCatBreakdownModal);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const modal = document.getElementById("cat-breakdown-modal");
    if (!modal || !modal.classList.contains("open")) return;
    fatcatHideCatBreakdownModal();
  });
  fatcatCatModalBound = true;
}

function fatcatOpenCategoryBreakdown(label, total, rows) {
  const modal = document.getElementById("cat-breakdown-modal");
  const title = document.getElementById("cat-breakdown-title");
  const bodyRows = document.getElementById("cat-breakdown-rows");
  const totalEl = document.getElementById("cat-breakdown-total-value");
  if (!modal || !title || !bodyRows || !totalEl) return;

  title.textContent = "Categoria: " + label;
  totalEl.textContent = fatcatFormatMoneyBR(total);
  bodyRows.innerHTML = "";

  if (!rows || rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "text-muted";
    td.textContent = "Nenhum lançamento nesta categoria no ciclo selecionado.";
    tr.appendChild(td);
    bodyRows.appendChild(tr);
  } else {
    rows.forEach((item) => {
      const tr = document.createElement("tr");

      const descTd = document.createElement("td");
      descTd.textContent = item.description || "-";

      const originTd = document.createElement("td");
      originTd.textContent = item.origin || "-";

      const valueTd = document.createElement("td");
      valueTd.className = "line-amount";
      valueTd.textContent = fatcatFormatMoneyBR(item.amount || 0);

      tr.appendChild(descTd);
      tr.appendChild(originTd);
      tr.appendChild(valueTd);
      bodyRows.appendChild(tr);
    });
  }

  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function renderSankey(payload) {
  const wrap = document.getElementById("sankey-wrap");
  if (!wrap || !payload || !payload.nodes || payload.nodes.length === 0) return;
  wrap.innerHTML = "";
  const width = Math.max(720, wrap.clientWidth || 720);
  const height = 280;
  const textFill =
    getComputedStyle(document.documentElement).getPropertyValue("--text").trim() || "#F5EDE8";
  const svg = d3
    .select(wrap)
    .append("svg")
    .attr("id", "sankey-svg")
    .attr("width", width)
    .attr("height", height)
    .style("overflow", "visible");
  const sankey = d3
    .sankey()
    .nodeWidth(16)
    .nodePadding(12)
    .extent([
      [2, 2],
      [width - 2, height - 2],
    ]);
  const graph = sankey({
    nodes: payload.nodes.map((node) => ({ ...node })),
    links: payload.links.map((link) => ({ ...link })),
  });
  svg
    .append("g")
    .selectAll("path")
    .data(graph.links)
    .join("path")
    .attr("d", d3.sankeyLinkHorizontal())
    .attr("stroke", (d) => d.color || "#DB8A74")
    .attr("stroke-width", (d) => Math.max(1, d.width))
    .attr("fill", "none")
    .attr("stroke-opacity", 0.3);
  svg
    .append("g")
    .selectAll("rect")
    .data(graph.nodes)
    .join("rect")
    .attr("x", (d) => d.x0)
    .attr("y", (d) => d.y0)
    .attr("width", (d) => d.x1 - d.x0)
    .attr("height", (d) => Math.max(2, d.y1 - d.y0))
    .attr("fill", (d) => d.color || "#BEBBBB")
    .attr("rx", 2);
  svg
    .append("g")
    .selectAll("text")
    .data(graph.nodes)
    .join("text")
    .attr("x", (d) => (d.x0 < width / 2 ? d.x1 + 6 : d.x0 - 6))
    .attr("y", (d) => (d.y0 + d.y1) / 2)
    .attr("dy", "0.35em")
    .attr("text-anchor", (d) => (d.x0 < width / 2 ? "start" : "end"))
    .style("font-family", '"Nunito",sans-serif')
    .style("font-size", "11px")
    .style("font-weight", "700")
    .style("fill", textFill)
    .text((d) => d.name);
}

function renderSavingsGrowth(payload, previous) {
  const canvas = document.getElementById("savingsGrowthChart");
  if (!canvas || !payload || !payload.labels || !payload.rendimento_by_box_weekly) return previous;
  if (previous) previous.destroy();
  if (payload.labels.length === 0 || payload.rendimento_by_box_weekly.length === 0) return null;
  const textColor =
    getComputedStyle(document.documentElement).getPropertyValue("--text2").trim() || "#D4C4CE";
  const borderColor =
    getComputedStyle(document.documentElement).getPropertyValue("--border").trim() || "rgba(255,255,255,0.1)";
  const rendimentoTotalWeekly = payload.rendimento_total_weekly || [];
  const runningTotal = payload.rendimento_running_total || [];
  const datasets = payload.rendimento_by_box_weekly.map((dataset) => ({
    type: "bar",
    label: dataset.label,
    data: dataset.values,
    borderColor: dataset.color || "#82C4A8",
    backgroundColor: dataset.color || "#82C4A8",
    borderWidth: 1,
    borderRadius: 4,
    stack: "weekly-rendimento",
    order: 2,
  }));
  datasets.push({
    type: "line",
    label: "Rendimento total semanal",
    data: rendimentoTotalWeekly,
    borderColor: "#FAC9B8",
    backgroundColor: "rgba(250,201,184,0.18)",
    borderWidth: 2,
    pointRadius: 2,
    pointHoverRadius: 4,
    tension: 0.2,
    fill: false,
    yAxisID: "y",
    order: 1,
  });
  return new Chart(canvas, {
    type: "bar",
    data: {
      labels: payload.labels,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          labels: { color: textColor },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fatcatFormatMoneyBR(ctx.raw || 0)}`,
            afterBody: (items) => {
              if (!items || items.length === 0) return "";
              const idx = items[0].dataIndex;
              const weekTotal = rendimentoTotalWeekly[idx] || 0;
              const weekRunningTotal = runningTotal[idx] || 0;
              return [
                `Total semanal: ${fatcatFormatMoneyBR(weekTotal)}`,
                `Acumulado no intervalo: ${fatcatFormatMoneyBR(weekRunningTotal)}`,
              ];
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: textColor },
          grid: { color: borderColor },
          stacked: true,
        },
        y: {
          ticks: {
            color: textColor,
            callback: (value) => fatcatFormatMoneyBR(value),
          },
          grid: { color: borderColor },
          stacked: true,
        },
      },
    },
  });
}

function renderSavingsGross(payload, previous) {
  const canvas = document.getElementById("savingsGrossChart");
  if (!canvas || !payload || !payload.labels || !payload.datasets) return previous;
  if (previous) previous.destroy();
  if (payload.labels.length === 0 || payload.datasets.length === 0) return null;
  const textColor =
    getComputedStyle(document.documentElement).getPropertyValue("--text2").trim() || "#D4C4CE";
  const borderColor =
    getComputedStyle(document.documentElement).getPropertyValue("--border").trim() || "rgba(255,255,255,0.1)";
  const grossTotalWeekly = payload.gross_total_weekly || [];
  const datasets = payload.datasets.map((dataset) => ({
    type: "bar",
    label: dataset.label,
    data: dataset.values,
    borderColor: dataset.color || "#82C4A8",
    backgroundColor: dataset.color || "#82C4A8",
    borderWidth: 1,
    borderRadius: 4,
    stack: "gross-balance",
    order: 2,
  }));
  datasets.push({
    type: "line",
    label: "Total bruto semanal",
    data: grossTotalWeekly,
    borderColor: "#FAC9B8",
    backgroundColor: "rgba(250,201,184,0.2)",
    borderWidth: 2,
    pointRadius: 2,
    pointHoverRadius: 4,
    tension: 0.2,
    fill: false,
    order: 1,
    yAxisID: "y",
  });
  return new Chart(canvas, {
    type: "bar",
    data: {
      labels: payload.labels,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, labels: { color: textColor } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fatcatFormatMoneyBR(ctx.raw || 0)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: textColor },
          grid: { color: borderColor },
          stacked: true,
        },
        y: {
          ticks: {
            color: textColor,
            callback: (value) => fatcatFormatMoneyBR(value),
          },
          grid: { color: borderColor },
          stacked: true,
        },
      },
    },
  });
}

function renderCdiDaily(payload, previous) {
  const canvas = document.getElementById("cdiDailyChart");
  if (!canvas || !payload || !payload.labels || !payload.values) return previous;
  if (previous) previous.destroy();
  if (payload.labels.length === 0 || payload.values.length === 0) return null;
  const textColor =
    getComputedStyle(document.documentElement).getPropertyValue("--text2").trim() || "#D4C4CE";
  const borderColor =
    getComputedStyle(document.documentElement).getPropertyValue("--border").trim() || "rgba(255,255,255,0.1)";
  return new Chart(canvas, {
    type: "line",
    data: {
      labels: payload.labels,
      datasets: [
        {
          label: "CDI diário (%)",
          data: payload.values,
          borderColor: "#88B8E0",
          backgroundColor: "rgba(136,184,224,0.2)",
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 3,
          tension: 0.2,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          labels: { color: textColor },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `CDI: ${Number(ctx.raw).toFixed(6)}%`,
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: textColor,
            maxTicksLimit: 12,
          },
          grid: { color: borderColor },
        },
        y: {
          ticks: {
            color: textColor,
            callback: (value) => `${Number(value).toFixed(4)}%`,
          },
          grid: { color: borderColor },
        },
      },
    },
  });
}

window.fatcatRenderCharts = function fatcatRenderCharts() {
  const cardData = safeJsonElement("card-chart-data");
  const catData = safeJsonElement("cat-chart-data");
  const catDetails = safeJsonElement("cat-chart-details-data") || {};
  const sankeyData = safeJsonElement("sankey-data");
  const savingsGrossData = safeJsonElement("savings-gross-data");
  const savingsGrowthData = safeJsonElement("savings-growth-data");
  const cdiDailyData = safeJsonElement("cdi-daily-data");
  fatcatCardChart = renderDoughnut("cardChart", cardData, fatcatCardChart);
  fatcatCatChart = renderDoughnut("catChart", catData, fatcatCatChart);
  fatcatSavingsGrossChart = renderSavingsGross(savingsGrossData, fatcatSavingsGrossChart);
  fatcatSavingsGrowthChart = renderSavingsGrowth(savingsGrowthData, fatcatSavingsGrowthChart);
  fatcatCdiDailyChart = renderCdiDaily(cdiDailyData, fatcatCdiDailyChart);
  fatcatBindCatBreakdownModal();
  if (fatcatCatChart && catData && catData.labels) {
    fatcatCatChart.options.onClick = (event, elements, chart) => {
      if (!elements || elements.length === 0) return;
      const idx = elements[0].index;
      const label = catData.labels[idx];
      const total = catData.values[idx];
      const rows = catDetails[label] || [];
      fatcatOpenCategoryBreakdown(label, total, rows);
    };
    fatcatCatChart.update("none");
  }
  renderLegend("leg-card", cardData);
  renderLegend("leg-cat", catData);
  if (sankeyData) renderSankey(sankeyData);
};
