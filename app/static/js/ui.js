function fatcatToggleType(selectEl) {
  const wrap = document.getElementById("installments-wrap");
  if (!wrap) return;
  const input = wrap.querySelector("input[name='installments']");
  if (!input) return;
  if (selectEl.value === "debit") {
    input.value = 1;
    input.readOnly = true;
    wrap.style.opacity = 0.6;
  } else {
    input.readOnly = false;
    wrap.style.opacity = 1;
  }
}

document.body.addEventListener("htmx:afterSwap", () => {
  const typeSelect = document.querySelector("select[name='exp_type']");
  if (typeSelect) fatcatToggleType(typeSelect);
  if (window.fatcatRenderCharts) window.fatcatRenderCharts();
});

document.addEventListener("DOMContentLoaded", () => {
  if (window.fatcatRenderCharts) window.fatcatRenderCharts();
  const typeSelect = document.querySelector("select[name='exp_type']");
  if (typeSelect) fatcatToggleType(typeSelect);
});
