"use strict";

const photoTelDemo = (() => {
  const orders = new Map();
  const config = JSON.parse(document.getElementById("demo-options").textContent);
  const duration = 4200;
  const examples = {
    lake: "Горное озеро у скального хребта",
    tram: "Оранжевый трамвай на вечерней улице",
    jellyfish: "Полупрозрачная медуза в глубокой воде"
  };

  function configure() {
    el("hero-description").textContent = "Соберите свою коллекцию для экрана блокировки и посмотрите, как проходит заказ в демо.";
    el("price-label").textContent = "Оценка реального заказа";
    el("submit").firstElementChild.textContent = "Запустить демо";
    el("history-empty").textContent = "Здесь появятся прогресс демо и примеры обоев.";
    document.querySelector('label[for="generated"]').textContent = "Генерация";
    document.querySelector('label[for="uploaded"]').textContent = "Подготовка папки и CSV";

    const notice = document.createElement("p");
    notice.id = "demo-notice";
    notice.className = "hint demo-notice";
    notice.textContent = "Демонстрационный режим. Этапы имитируются в браузере, без списаний и загрузки на Google Диск.";
    el("submit").before(notice);
    el("submit").setAttribute("aria-describedby", notice.id);

    const section = document.createElement("div");
    section.id = "demo-examples";
    section.className = "demo-examples";
    section.hidden = true;
    const caption = document.createElement("p");
    caption.className = "hint";
    caption.textContent = "Примеры из готовой коллекции. Они одинаковы для всех запусков демо.";
    const gallery = document.createElement("div");
    gallery.className = "demo-gallery";
    for (const [key, description] of Object.entries(examples)) {
      const image = document.createElement("img");
      image.src = "./examples/" + key + ".webp";
      image.alt = description;
      image.width = 256;
      image.height = 556;
      image.loading = "lazy";
      gallery.append(image);
    }
    section.append(caption, gallery);
    el("progress").append(section);
  }

  function progress(order) {
    const ms = Math.min(duration, performance.now() - order.started);
    const stage = (count, delay, finish) =>
      Math.floor(count * Math.max(0, Math.min(1, (ms - delay) / (finish - delay))));
    return {
      ...order,
      status: ms >= duration ? "done" : "running",
      elapsed_seconds: ms / 1000,
      generated: stage(order.count, 0, 2500),
      jpeg_ready: stage(order.count, 500, 3200),
      uploaded: stage(order.count + 1, 1300, duration)
    };
  }

  function request(path, options) {
    if (path === "/api/options") return config;
    if (path === "/api/orders") return Array.from(orders.values(), progress).reverse();

    const id = path.split("/").at(-1);
    if (options?.method === "PUT" && !orders.has(id)) {
      const selection = JSON.parse(options.body);
      const names = selection.presets.map(key => config.presets[key]).concat(selection.custom);
      const counts = selection.quantities ?? balanced(selection.count, names.length);
      orders.set(id, {
        id, selection, count: selection.count,
        created_at: new Date().toISOString(),
        started: performance.now(),
        themes: Object.fromEntries(names.map((name, index) => [name, counts[index]]))
      });
    }
    const order = orders.get(id);
    if (!order) throw new Error("Демонстрационный заказ не найден.");
    return progress(order);
  }

  return {
    configure,
    request,
    pollInterval: 350,
    statuses: { running: "Имитация заказа", done: "Демо завершено" }
  };
})();
