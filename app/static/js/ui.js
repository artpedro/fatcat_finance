function fatcatToggleNewCategory(selectEl) {
  const wrap = selectEl.closest("[data-category-wrap]");
  if (!wrap) return;
  const prompt = wrap.querySelector(".js-new-category-prompt");
  if (!prompt) return;
  if (selectEl.value === "__new__") {
    prompt.classList.remove("hidden");
  } else {
    prompt.classList.add("hidden");
  }
}

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

function fatcatSubscriptionTermUpdateStatus(form) {
  const end = form.querySelector("input[name='end']");
  const dur = form.querySelector("input[name='duration_months']");
  const status = form.querySelector("[data-sub-term-status]");
  if (!status || !end || !dur) return;
  const hasEnd = !!end.value;
  const d = parseInt(dur.value, 10);
  const hasDur = !Number.isNaN(d) && d > 0;
  if (!hasEnd && !hasDur) {
    status.value = "Indefinida — sem data final nem duração fixa";
  } else if (hasDur) {
    status.value = "Com término por duração (meses)";
  } else {
    status.value = "Com término na data final indicada";
  }
}

function fatcatSubscriptionTermOnInput(form) {
  const end = form.querySelector("input[name='end']");
  const dur = form.querySelector("input[name='duration_months']");
  if (!end || !dur) return;
  const hasEnd = !!end.value;
  const d = parseInt(dur.value, 10);
  const hasDur = !Number.isNaN(d) && d > 0;
  if (hasEnd) {
    dur.value = "";
    dur.disabled = true;
    end.disabled = false;
  } else {
    dur.disabled = false;
  }
  if (hasDur) {
    end.value = "";
    end.disabled = true;
    dur.disabled = false;
  } else {
    end.disabled = false;
  }
  fatcatSubscriptionTermUpdateStatus(form);
}

function fatcatSubscriptionTermInit(form) {
  if (!form || !form.hasAttribute("data-sub-term")) return;
  const end = form.querySelector("input[name='end']");
  const dur = form.querySelector("input[name='duration_months']");
  if (!end || !dur) return;
  const hasEnd = !!end.value;
  const d = parseInt(dur.value, 10);
  const hasDur = !Number.isNaN(d) && d > 0;
  if (hasDur) end.disabled = true;
  else end.disabled = false;
  if (hasEnd) dur.disabled = true;
  else dur.disabled = false;
  fatcatSubscriptionTermUpdateStatus(form);
  end.addEventListener("input", () => fatcatSubscriptionTermOnInput(form));
  dur.addEventListener("input", () => fatcatSubscriptionTermOnInput(form));
}

function fatcatIncomeFormInit() {
  const form = document.querySelector("form[data-income-form]");
  if (!form) return;
  const cb = form.querySelector("#income-has-end");
  const end = form.querySelector("#income-end-date");
  if (!cb || !end) return;
  end.disabled = !cb.checked;
}

function fatcatIncomeEndToggle(cb) {
  const end = document.getElementById("income-end-date");
  if (!end) return;
  end.disabled = !cb.checked;
  if (!cb.checked) end.value = "";
}

function fatcatModalBackdrop(event, modal) {
  if (event.target !== modal) return;
  const clearUrl = modal.getAttribute("data-modal-clear");
  const target = modal.getAttribute("data-modal-target");
  if (clearUrl && target && window.htmx) {
    window.htmx.ajax("GET", clearUrl, { target: target, swap: "innerHTML" });
  }
}

function fatcatModalEscape(event) {
  if (event.key !== "Escape") return;
  const open = document.querySelector(".mo.open");
  if (!open) return;
  const clearUrl = open.getAttribute("data-modal-clear");
  const target = open.getAttribute("data-modal-target");
  if (clearUrl && target && window.htmx) {
    window.htmx.ajax("GET", clearUrl, { target: target, swap: "innerHTML" });
  }
}

document.body.addEventListener("htmx:afterSwap", () => {
  const typeSelect = document.querySelector("select[name='exp_type']");
  if (typeSelect) fatcatToggleType(typeSelect);
  document.querySelectorAll("select[name='category_id']").forEach((sel) => fatcatToggleNewCategory(sel));
  document.querySelectorAll("form[data-sub-term]").forEach((f) => fatcatSubscriptionTermInit(f));
  fatcatIncomeFormInit();
  if (window.fatcatRenderCharts) window.fatcatRenderCharts();
});

document.addEventListener("DOMContentLoaded", () => {
  if (window.fatcatRenderCharts) window.fatcatRenderCharts();
  const typeSelect = document.querySelector("select[name='exp_type']");
  if (typeSelect) fatcatToggleType(typeSelect);
  document.querySelectorAll("select[name='category_id']").forEach((sel) => fatcatToggleNewCategory(sel));
  document.querySelectorAll("form[data-sub-term]").forEach((f) => fatcatSubscriptionTermInit(f));
  fatcatIncomeFormInit();
  document.addEventListener("keydown", fatcatModalEscape);
});
