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

function renderDoughnut(canvasId, payload, previous) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !payload || !payload.labels || payload.labels.length === 0) return previous;
  if (previous) previous.destroy();
  return new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: payload.labels,
      datasets: [{ data: payload.values, backgroundColor: payload.colors }],
    },
    options: {
      plugins: {
        legend: { position: "bottom" },
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
  const svg = d3.select(wrap).append("svg").attr("width", width).attr("height", height);
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
    .style("font-size", "11px")
    .style("font-weight", "700")
    .text((d) => d.name);
}

window.fatcatRenderCharts = function fatcatRenderCharts() {
  const cardData = safeJsonElement("card-chart-data");
  const catData = safeJsonElement("cat-chart-data");
  const sankeyData = safeJsonElement("sankey-data");
  fatcatCardChart = renderDoughnut("cardChart", cardData, fatcatCardChart);
  fatcatCatChart = renderDoughnut("catChart", catData, fatcatCatChart);
  if (sankeyData) renderSankey(sankeyData);
};
