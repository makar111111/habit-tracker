"use strict";

// ---------- допоміжне ----------

/** Дата у форматі YYYY-MM-DD — саме такий чекає сервер. */
function toISO(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * Обгортка над fetch. Робить три речі, які інакше довелось би
 * повторювати в кожному виклику:
 *   1) додає заголовок Content-Type для запитів з тілом;
 *   2) перетворює помилковий статус (404, 409, 422) на виняток;
 *   3) віддає розібраний JSON, а на 204 — нічого.
 */
async function api(path, { method = "GET", body } = {}) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }

  const response = await fetch(path, options);

  if (!response.ok) {
    // Сервер надсилає пояснення в полі detail — те саме, яке ти
    // бачив у /docs. Витягуємо його, щоб показати людині.
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Помилка ${response.status}`);
  }

  return response.status === 204 ? null : response.json();
}

function showError(message) {
  const box = document.getElementById("error");
  box.textContent = message;
  box.hidden = false;
}

function clearError() {
  document.getElementById("error").hidden = true;
}

// ---------- відображення ----------

/** Зібрати один рядок списку. Повертає готовий елемент <li>. */
function renderHabit(habit, stats) {
  const item = document.createElement("li");
  item.className = stats.done_today ? "habit done" : "habit";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = stats.done_today;
  checkbox.addEventListener("change", () => toggleToday(habit.id, checkbox.checked));

  const body = document.createElement("div");
  body.className = "habit-body";

  const name = document.createElement("div");
  name.className = "habit-name";
  // textContent, а не innerHTML. Назву вводить користувач, і якщо
  // вставити її як HTML, то назва <script>...</script> виконається.
  // textContent завжди трактує рядок як текст — і цієї діри немає.
  name.textContent = habit.name;
  body.append(name);

  if (habit.description) {
    const desc = document.createElement("div");
    desc.className = "habit-desc";
    desc.textContent = habit.description;
    body.append(desc);
  }

  const info = document.createElement("div");
  info.className = "habit-stats";
  info.append(describeStats(stats));
  body.append(info);

  const remove = document.createElement("button");
  remove.className = "delete";
  remove.title = "Видалити звичку";
  remove.textContent = "×";
  remove.addEventListener("click", () => deleteHabit(habit));

  // Верхній поверх картки: галочка, текст, кнопка видалення.
  const row = document.createElement("div");
  row.className = "habit-row";
  row.append(checkbox, body, remove);

  // Нижній поверх: сюди потрапить календар, коли його попросять.
  const calendarBox = document.createElement("div");

  const toggle = document.createElement("button");
  toggle.className = "toggle-cal";
  toggle.textContent = "показати графік";
  toggle.addEventListener("click", () => toggleCalendar(habit, toggle, calendarBox));
  body.append(toggle);

  item.append(row, calendarBox);
  return item;
}

/** Показати або сховати календар звички. */
async function toggleCalendar(habit, toggle, container) {
  // Календар уже відкритий — просто прибираємо його.
  if (container.hasChildNodes()) {
    container.replaceChildren();
    toggle.textContent = "показати графік";
    return;
  }

  toggle.textContent = "завантаження…";
  try {
    clearError();
    await showCalendar(habit.id, container);
    toggle.textContent = "сховати графік";
  } catch (error) {
    toggle.textContent = "показати графік";
    showError(error.message);
  }
}

/** Текст на кшталт "серія: 4 дні · найдовша: 7 · усього: 12". */
function describeStats(stats) {
  const fragment = document.createDocumentFragment();

  if (stats.current_streak > 0) {
    const streak = document.createElement("span");
    streak.className = "streak";
    streak.textContent = `серія: ${stats.current_streak} ${plural(stats.current_streak)}`;
    fragment.append(streak);
    fragment.append(` · найдовша: ${stats.longest_streak} · усього: ${stats.total}`);
  } else if (stats.total > 0) {
    fragment.append(`серії немає · найдовша була: ${stats.longest_streak} · усього: ${stats.total}`);
  } else {
    fragment.append("ще жодної відмітки");
  }

  return fragment;
}

/** Правильна форма слова: 1 день, 2 дні, 5 днів. */
function plural(n) {
  const last = n % 10;
  const lastTwo = n % 100;
  if (lastTwo >= 11 && lastTwo <= 14) return "днів";
  if (last === 1) return "день";
  if (last >= 2 && last <= 4) return "дні";
  return "днів";
}

// ---------- календар ----------

const WEEKS = 12;
const MONTH_NAMES = ["січ", "лют", "бер", "кві", "тра", "чер",
                     "лип", "сер", "вер", "жов", "лис", "гру"];
const WEEKDAY_LABELS = ["Пн", "", "Ср", "", "Пт", "", "Нд"];

function addDays(date, days) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

/**
 * Перший день сітки — понеділок, такий, щоб сітка закінчувалась
 * неділею поточного тижня і мала рівно WEEKS стовпців.
 */
function gridStart(today) {
  const weekday = (today.getDay() + 6) % 7;   // getDay(): 0=Нд. Нам треба Пн=0.
  const sunday = addDays(today, 6 - weekday); // кінець поточного тижня
  return addDays(sunday, -(WEEKS * 7 - 1));
}

/** Побудувати блок календаря за множиною відмічених днів. */
function renderCalendar(doneDays, today) {
  const start = gridStart(today);
  const todayIso = toISO(today);

  const calendar = document.createElement("div");
  calendar.className = "calendar";

  const corner = document.createElement("div");
  corner.className = "cal-corner";

  // --- підписи місяців: ставимо там, де починається новий місяць
  const months = document.createElement("div");
  months.className = "cal-months";
  let previousMonth = null;
  for (let week = 0; week < WEEKS; week++) {
    const monday = addDays(start, week * 7);
    const label = document.createElement("span");
    if (monday.getMonth() !== previousMonth) {
      label.textContent = MONTH_NAMES[monday.getMonth()];
      previousMonth = monday.getMonth();
    }
    months.append(label);
  }

  // --- підписи днів тижня
  const weekdays = document.createElement("div");
  weekdays.className = "cal-weekdays";
  for (const name of WEEKDAY_LABELS) {
    const cell = document.createElement("div");
    cell.textContent = name;
    weekdays.append(cell);
  }

  // --- самі клітинки
  // grid-auto-flow: column у CSS означає, що елементи заповнюють
  // спершу стовпець згори вниз, потім наступний. Оскільки перший
  // день — понеділок, кожен стовпець природно стає тижнем.
  const grid = document.createElement("div");
  grid.className = "cal-grid";
  for (let i = 0; i < WEEKS * 7; i++) {
    const date = addDays(start, i);
    const iso = toISO(date);
    const cell = document.createElement("div");

    cell.className = "cell";
    if (iso > todayIso) {
      cell.classList.add("future");          // майбутнє не малюємо
    } else {
      if (doneDays.has(iso)) cell.classList.add("done");
      if (iso === todayIso) cell.classList.add("today");
      // title показує підказку при наведенні мишею
      cell.title = doneDays.has(iso) ? `${iso} — зроблено` : `${iso} — пропуск`;
    }

    grid.append(cell);
  }

  // --- легенда
  const legend = document.createElement("div");
  legend.className = "cal-legend";
  const emptyBox = document.createElement("span");
  emptyBox.className = "cell";
  const doneBox = document.createElement("span");
  doneBox.className = "cell done";
  legend.append("пропуск", emptyBox, doneBox, "зроблено");

  calendar.append(corner, months, weekdays, grid, legend);
  return calendar;
}

/** Завантажити відмітки за період сітки й показати календар. */
async function showCalendar(habitId, container) {
  const today = new Date();
  const since = toISO(gridStart(today));

  // since — параметр рядка запиту. Просимо лише потрібний період,
  // а не всю історію звички.
  const checkins = await api(`/habits/${habitId}/checkins?since=${since}`);
  const doneDays = new Set(checkins.map((c) => c.day));

  container.replaceChildren(renderCalendar(doneDays, today));
}

// ---------- дії ----------

/** Завантажити все з сервера й перемалювати список. */
async function load() {
  try {
    clearError();
    // Два запити — і байдуже, скільки в базі звичок.
    // Раніше тут було 1 + N: окремий /stats на кожну звичку.
    // Promise.all запускає обидва одночасно, тож чекаємо повільніший,
    // а не суму двох.
    const [habits, statsList] = await Promise.all([
      api("/habits"),
      api("/stats"),
    ]);

    // Map дає миттєвий пошук статистики за id звички.
    const statsById = new Map(statsList.map((item) => [item.habit_id, item]));

    const list = document.getElementById("habits");
    list.replaceChildren(
      ...habits.map((habit) => renderHabit(habit, statsById.get(habit.id)))
    );

    document.getElementById("empty").hidden = habits.length > 0;
  } catch (error) {
    showError(`Не вдалося завантажити: ${error.message}`);
  }
}

async function toggleToday(habitId, checked) {
  try {
    clearError();
    if (checked) {
      // Порожнє тіло — сервер підставить сьогоднішній день сам.
      await api(`/habits/${habitId}/checkins`, { method: "POST", body: {} });
    } else {
      await api(`/habits/${habitId}/checkins/${toISO(new Date())}`, { method: "DELETE" });
    }
  } catch (error) {
    showError(error.message);
  }
  // Перемальовуємо в будь-якому разі: після помилки галочка
  // має повернутись у стан, який справді записаний на сервері.
  load();
}

async function deleteHabit(habit) {
  if (!confirm(`Видалити «${habit.name}» разом з усіма відмітками?`)) return;

  try {
    clearError();
    await api(`/habits/${habit.id}`, { method: "DELETE" });
  } catch (error) {
    showError(error.message);
  }
  load();
}

async function addHabit(event) {
  event.preventDefault();  // інакше браузер перезавантажить сторінку

  const nameField = document.getElementById("name");
  const descField = document.getElementById("description");

  try {
    clearError();
    await api("/habits", {
      method: "POST",
      body: { name: nameField.value.trim(), description: descField.value.trim() },
    });
    nameField.value = "";
    descField.value = "";
    nameField.focus();
  } catch (error) {
    showError(error.message);
  }
  load();
}

// ---------- старт ----------

document.getElementById("today").textContent = toISO(new Date());
document.getElementById("add-form").addEventListener("submit", addHabit);
load();
