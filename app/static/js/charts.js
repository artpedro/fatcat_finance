let fatcatCardChart = null;
let fatcatCatChart = null;

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

window.fatcatRenderCharts = function fatcatRenderCharts() {
  const cardData = safeJsonElement("card-chart-data");
  const catData = safeJsonElement("cat-chart-data");
  const sankeyData = safeJsonElement("sankey-data");
  fatcatCardChart = renderDoughnut("cardChart", cardData, fatcatCardChart);
  fatcatCatChart = renderDoughnut("catChart", catData, fatcatCatChart);
  renderLegend("leg-card", cardData);
  renderLegend("leg-cat", catData);
  if (sankeyData) renderSankey(sankeyData);
};
