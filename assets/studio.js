"use strict";

const el = id => document.getElementById(id);
const demo = typeof photoTelDemo === "undefined" ? null : photoTelDemo;
const statuses = {
  queued: "Заказ в очереди",
  running: "Заказ выполняется",
  done: "Заказ готов",
  failed: "Заказ приостановлен",
  blocked: "Заказ ждёт сверки в NanoGPT",
  ...demo?.statuses
};
const tones = {
  light: { label: "Светлый", preview: "Пример светлых обоев", hint: "Светлый кадр с местом для тёмных часов." },
  dark: { label: "Тёмный", preview: "Пример тёмных обоев", hint: "Тёмный кадр с местом для белых часов." }
};
const priceFormat = new Intl.NumberFormat("ru-RU", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 3
});
const dateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const pluralRules = new Intl.PluralRules("ru");
const customThemes = new Map();
const manualCounts = new Map();
let presets = {};
let price = null;
let maxCount = 100;
let submitting = false;
let activeId = null;
let lastStatus = null;
let poll = null;

async function api(path, options) {
  if (demo) return demo.request(path, options);
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const message = Array.isArray(data.detail)
      ? data.detail.map(item => item.msg.replace(/^Value error, /, "")).join(" ")
      : data.detail;
    const error = new Error(message || "Не удалось выполнить запрос. Попробуйте ещё раз.");
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function showError(id, message = "") {
  const element = el(id);
  if (element.textContent !== message) element.textContent = message;
  element.hidden = !message;
}

function chosenTone() {
  return document.querySelector('input[name="tone"]:checked').value;
}

function isManual() {
  return document.querySelector('input[name="allocation"]:checked').value === "manual";
}

function chosenThemes() {
  const selected = Array.from(el("themes").querySelectorAll("input:checked"), input => ({
    id: input.value, name: presets[input.value], custom: false
  }));
  for (const [id, name] of customThemes) selected.push({ id, name, custom: true });
  return selected;
}

function balanced(total, themeCount) {
  if (!themeCount || !Number.isInteger(total) || total < themeCount || total > maxCount) return [];
  const base = Math.floor(total / themeCount);
  return Array.from({ length: themeCount }, (_, index) => base + Number(index < total % themeCount));
}

function quantityText(count) {
  const noun = { one: "кадр", few: "кадра", many: "кадров" }[pluralRules.select(count)] || "кадра";
  return count + " " + noun;
}

function quote() {
  const themes = chosenThemes();
  const manual = isManual();
  const amounts = manual ? themes.map(theme => Number(manualCounts.get(theme.id) ?? 1)) : null;
  const total = manual ? amounts.reduce((sum, amount) => sum + amount, 0) : el("count").valueAsNumber;
  const validTotal = Number.isInteger(total) && total >= 1 && total <= maxCount;
  const uniqueNames = new Set(themes.map(theme => theme.name.toLocaleLowerCase("ru-RU")));
  let message = "";
  if (!themes.length) message = "Выберите или добавьте тему.";
  else if (uniqueNames.size !== themes.length) message = "Каждая тема должна встречаться один раз.";
  else if (manual && amounts.some(amount => !Number.isInteger(amount) || amount < 1 || amount > maxCount)) {
    message = "Укажите от 1 до " + maxCount + " кадров для каждой темы.";
  } else if (!validTotal) message = "В заказе может быть от 1 до " + maxCount + " кадров.";
  else if (total < themes.length) message = "На каждую тему нужен хотя бы один кадр.";

  el("quote-count").textContent = Number.isInteger(total) ? quantityText(total) : "—";
  el("quote-tone").textContent = tones[chosenTone()].label;
  el("estimate").textContent = validTotal && price !== null ? priceFormat.format(total * price) : "—";
  el("checkout-hint").textContent = message;
  el("checkout-hint").hidden = !message;
  el("submit").disabled = submitting || price === null || !!message;
  return { themes, amounts, total, message };
}

function renderAllocation() {
  const manual = isManual();
  el("auto-total").hidden = manual;
  el("count").disabled = manual;
  el("allocation-hint").textContent = manual
    ? "Введите количество для каждой темы. Итог посчитается автоматически."
    : "Кадры распределятся по темам поровну или с разницей в один.";

  const themes = chosenThemes();
  const amounts = balanced(el("count").valueAsNumber, themes.length);
  const rows = themes.map((theme, index) => {
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.className = "allocation-name";
    name.textContent = theme.name;
    row.append(name);
    if (manual) {
      const input = document.createElement("input");
      input.type = "number";
      input.min = "1";
      input.max = String(maxCount);
      input.step = "1";
      input.inputMode = "numeric";
      input.required = true;
      input.value = manualCounts.get(theme.id) ?? "1";
      input.setAttribute("aria-label", "Количество кадров — " + theme.name);
      input.addEventListener("input", () => {
        manualCounts.set(theme.id, input.value);
        showError("form-error");
        quote();
      });
      row.append(input);
    } else {
      const amount = document.createElement("span");
      amount.className = "allocation-count";
      amount.textContent = amounts.length ? quantityText(amounts[index]) : "—";
      row.append(amount);
    }
    if (theme.custom) {
      const remove = document.createElement("button");
      remove.className = "remove-theme";
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Удалить тему «" + theme.name + "»");
      remove.addEventListener("click", () => {
        customThemes.delete(theme.id);
        manualCounts.delete(theme.id);
        renderAllocation();
        el("custom").focus();
      });
      row.append(remove);
    }
    return row;
  });
  el("allocation").replaceChildren(...rows);
  el("allocation").hidden = !rows.length;
  quote();
}

function normalize(value) {
  return value.normalize("NFC").trim().replace(/\s+/gu, " ");
}

function addCustomThemes() {
  const field = el("custom");
  const additions = field.value.split(/\r?\n/u).map(normalize).filter(Boolean);
  showError("custom-error");
  if (!additions.length) {
    showError("custom-error", "Введите название темы.");
    field.focus();
    return;
  }
  const used = new Set(chosenThemes().map(theme => theme.name.toLocaleLowerCase("ru-RU")));
  if (used.size + additions.length > maxCount) {
    showError("custom-error", "В одном заказе может быть до " + maxCount + " тем.");
    return;
  }
  for (const name of additions) {
    const normalized = name.toLocaleLowerCase("ru-RU");
    if (Array.from(name).length > 160 || used.has(normalized)) {
      showError("custom-error", Array.from(name).length > 160
        ? "Название темы должно содержать до 160 символов."
        : "Тема «" + name + "» уже выбрана.");
      return;
    }
    used.add(normalized);
  }
  for (const name of additions) customThemes.set("custom:" + name.toLocaleLowerCase("ru-RU"), name);
  field.value = "";
  renderAllocation();
  showError("form-error");
  field.focus();
}

function updateTone() {
  const tone = chosenTone();
  el("preview").dataset.tone = tone;
  el("preview-label").textContent = tones[tone].preview;
  el("tone-hint").textContent = tones[tone].hint;
  showError("form-error");
  quote();
}

function renderThemes() {
  for (const [key, name] of Object.entries(presets)) {
    const label = document.createElement("label");
    label.className = "theme";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = key;
    const card = document.createElement("span");
    card.className = "theme-card";
    const image = document.createElement("img");
    image.src = "./assets/themes/" + key + ".webp";
    image.alt = "";
    image.decoding = "async";
    const title = document.createElement("span");
    title.className = "theme-name";
    title.textContent = name;
    const indicator = document.createElement("span");
    indicator.className = "theme-indicator";
    indicator.setAttribute("aria-hidden", "true");
    card.append(image, title, indicator);
    label.append(input, card);
    el("themes").append(label);
  }
  el("themes").setAttribute("aria-busy", "false");
}

function showProgress(order) {
  const seconds = Math.round(order.elapsed_seconds);
  const duration = seconds >= 60 ? Math.floor(seconds / 60) + " мин " + seconds % 60 + " с" : seconds + " с";
  const text = statuses[order.status] + (order.status === "done" ? " · " + duration : "");
  if (el("status").textContent !== text) el("status").textContent = text;
  const themeCounts = Object.entries(order.themes).map(([theme, count]) => theme + " — " + count);
  const tone = tones[order.selection.tone].label;
  el("theme-counts").textContent = [tone + " тон", ...themeCounts].join(" · ");
  for (const stage of ["generated", "jpeg_ready", "uploaded"]) {
    const total = order.count + Number(stage === "uploaded");
    el(stage).max = total;
    el(stage).value = order[stage];
    el(stage + "-count").textContent = order[stage] + " / " + total;
  }
  showError("order-error", order.error || "");
  el("resume").hidden = order.status !== "failed";
  el("folder").hidden = !order.folder_url;
  if (order.folder_url) el("folder").href = order.folder_url;
  el("actions").hidden = el("resume").hidden && el("folder").hidden;
  el("progress").hidden = false;
  el("history-empty").hidden = true;
  if (demo) el("demo-examples").hidden = order.status !== "done";
}

async function refreshHistory() {
  const orders = await api("/api/orders");
  el("orders").replaceChildren(...orders.map(order => {
    const label = dateFormat.format(new Date(order.created_at)) + " · " + order.count + " шт. · " + statuses[order.status];
    return new Option(label, order.id);
  }));
  el("order-select").hidden = !orders.length;
  el("history-empty").hidden = !!orders.length;
  if (activeId) el("orders").value = activeId;
  return orders;
}

async function refreshOrder(id) {
  let keepPolling = true;
  try {
    const order = await api("/api/orders/" + id);
    if (id !== activeId) return;
    showProgress(order);
    keepPolling = order.status === "queued" || order.status === "running";
    if (lastStatus !== order.status) {
      await refreshHistory();
      if (id === activeId) lastStatus = order.status;
    }
  } catch (error) {
    if (id === activeId) {
      el("progress").hidden = false;
      showError("order-error", error.message);
    }
  }
  if (id === activeId && keepPolling) poll = setTimeout(() => refreshOrder(id), demo?.pollInterval ?? 1500);
}

function selectOrder(id) {
  activeId = id;
  lastStatus = null;
  clearTimeout(poll);
  if (el("order-select").hidden) el("history-empty").textContent = "Получаем состояние заказа…";
  refreshOrder(id);
}

el("order-form").addEventListener("submit", async event => {
  event.preventDefault();
  showError("form-error");
  if (el("custom").value.trim()) {
    showError("form-error", "Добавьте введённую тему или очистите поле.");
    el("custom").focus();
    return;
  }
  const { themes, amounts, total, message } = quote();
  if (message || submitting) return;
  const body = JSON.stringify({
    presets: themes.filter(theme => !theme.custom).map(theme => theme.id),
    custom: themes.filter(theme => theme.custom).map(theme => theme.name),
    count: total,
    quantities: amounts,
    tone: chosenTone()
  });
  const previous = JSON.parse(sessionStorage.getItem("photo-tel-pending") || "null");
  const id = previous?.body === body ? previous.id : crypto.randomUUID();
  sessionStorage.setItem("photo-tel-pending", JSON.stringify({ body, id }));
  submitting = true;
  quote();
  try {
    await api("/api/orders/" + id, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body
    });
    sessionStorage.removeItem("photo-tel-pending");
    selectOrder(id);
  } catch (error) {
    if (error.status >= 400 && error.status < 500) sessionStorage.removeItem("photo-tel-pending");
    showError("form-error", error.message);
  } finally {
    submitting = false;
    quote();
  }
});

el("resume").addEventListener("click", async () => {
  const id = activeId;
  el("resume").disabled = true;
  try {
    await api("/api/orders/" + id + "/resume", { method: "POST" });
    if (id === activeId) selectOrder(id);
  } catch (error) {
    if (id === activeId) showError("order-error", error.message);
  } finally {
    el("resume").disabled = false;
  }
});
el("orders").addEventListener("change", () => selectOrder(el("orders").value));
el("count").addEventListener("input", () => {
  showError("form-error");
  renderAllocation();
});
el("tone-options").addEventListener("change", updateTone);
el("themes").addEventListener("change", () => {
  showError("form-error");
  renderAllocation();
});
el("allocation-options").addEventListener("change", () => {
  if (isManual() && !manualCounts.size) {
    const themes = chosenThemes();
    const amounts = balanced(el("count").valueAsNumber, themes.length);
    themes.forEach((theme, index) => manualCounts.set(theme.id, String(amounts[index] ?? 1)));
  }
  showError("form-error");
  renderAllocation();
});
el("add-custom").addEventListener("click", addCustomThemes);
el("custom").addEventListener("input", () => showError("custom-error"));
el("custom").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    addCustomThemes();
  }
});

async function init() {
  if (demo) demo.configure();
  try {
    const options = await api("/api/options");
    presets = options.presets;
    price = Number(options.price_usd);
    maxCount = options.max_count;
    el("count").max = String(maxCount);
    renderThemes();
    renderAllocation();
  } catch (error) {
    el("themes").setAttribute("aria-busy", "false");
    showError("form-error", error.message);
  }
  try {
    const orders = await refreshHistory();
    if (orders.length && !activeId) selectOrder(orders[0].id);
  } catch (error) {
    el("history-empty").textContent = error.message;
  }
}

init();
