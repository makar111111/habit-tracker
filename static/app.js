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

  // Відмітити звичку — це ТА САМА дія, заради якої відкривають трекер.
  // Тому не стандартний дрібний чекбокс, а велика кнопка: у неї легко
  // влучити пальцем на телефоні, і натискати її приємно.
  //
  // Це саме <button> з aria-pressed, а не <div> з обробником: кнопку
  // видно з клавіатури, вона реагує на пробіл і Enter без жодного
  // коду, а читач екрана каже, натиснута вона чи ні.
  const check = document.createElement("button");
  check.type = "button";
  check.className = "check";
  check.setAttribute("aria-pressed", String(Boolean(stats.done_today)));
  // Без цього підпису читач екрана сказав би просто «кнопка»: людина
  // не дізналася б, яку саме звичку відмічає.
  check.setAttribute(
    "aria-label",
    `${stats.done_today ? "Зняти відмітку" : "Відмітити"}: ${habit.name}`
  );
  check.textContent = stats.done_today ? "✓" : "";
  check.addEventListener("click", () => toggleToday(habit.id, !stats.done_today));

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

  body.append(renderStats(stats));

  const remove = document.createElement("button");
  remove.className = "delete";
  remove.title = "Видалити звичку";
  remove.setAttribute("aria-label", `Видалити звичку: ${habit.name}`);
  remove.textContent = "×";
  remove.addEventListener("click", () => deleteHabit(habit, stats));

  // Верхній поверх картки: кнопка відмітки, текст, кнопка видалення.
  const row = document.createElement("div");
  row.className = "habit-row";
  row.append(check, body, remove);

  // Нижній поверх — календар. Розгорнутий ОДРАЗУ, а не за посиланням:
  // це найінформативніша частина картки, і ховати її означало б
  // показувати людині найменш цікаве, а найцікавіше — за кліком.
  // Ховати можна те, чого зазвичай не потребують; тут навпаки.
  const calendarBox = document.createElement("div");
  if (stats.total > 0) {
    showCalendar(habit.id, calendarBox).catch(() => {
      // Календар — прикраса поверх головного. Якщо він не завантажився,
      // це не привід ламати весь список: картка лишається робочою.
      calendarBox.replaceChildren();
    });
  }

  item.append(row, calendarBox);
  return item;
}

/**
 * Показники звички: серія великим, решта — дрібним.
 *
 * Раніше всі три числа стояли в один сірий рядок однаковою вагою, і
 * найважливіше — поточна серія — губилося серед них. А коли серії не
 * було, рядок починався з «серії немає»: заперечення там, де людина
 * шукає привід продовжити.
 */
function renderStats(stats) {
  const box = document.createElement("div");
  box.className = "habit-stats";

  if (stats.total === 0) {
    box.textContent = "ще жодної відмітки";
    return box;
  }

  const streak = document.createElement("span");
  streak.className = stats.current_streak > 0 ? "streak alive" : "streak";
  streak.textContent =
    stats.current_streak > 0
      ? `🔥 ${stats.current_streak} ${plural(stats.current_streak)} поспіль`
      : "почни серію сьогодні";
  box.append(streak);

  const rest = document.createElement("span");
  rest.className = "habit-stats-rest";
  rest.textContent = `рекорд ${stats.longest_streak} · усього ${stats.total}`;
  box.append(rest);

  return box;
}

/**
 * Панель «скільки зроблено сьогодні».
 *
 * Це головне питання, заради якого відкривають трекер, і воно має
 * мати відповідь до того, як людина почне читати список. Бот таке
 * вміє давно («Сьогодні відмічено: 0 з 3»), а вебверсія — ні.
 */
function renderProgress(habits, statsById) {
  const panel = document.getElementById("progress");

  if (habits.length === 0) {
    panel.hidden = true;
    return;
  }

  const done = habits.filter(
    (habit) => statsById.get(habit.id)?.done_today
  ).length;
  const percent = Math.round((done / habits.length) * 100);

  document.getElementById("progress-count").textContent =
    `${done} з ${habits.length}`;

  document.getElementById("progress-note").textContent =
    done === habits.length ? "усе на сьогодні ✨" : "лишилось на сьогодні";

  const fill = document.getElementById("progress-fill");
  fill.style.width = `${percent}%`;
  // Клас, а не інлайновий колір: правило «усе зроблено виглядає інакше»
  // лишається у CSS, поруч із рештою оформлення.
  fill.classList.toggle("full", done === habits.length);

  const bar = document.getElementById("progress-bar");
  bar.setAttribute("aria-valuenow", String(percent));
  // Читач екрана вимовляє саме цей текст, а не «45 відсотків»:
  // «2 з 3 звичок» людині зрозуміліше.
  bar.setAttribute("aria-valuetext", `${done} з ${habits.length} звичок`);

  panel.hidden = false;
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

    renderProgress(habits, statsById);
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

async function deleteHabit(habit, stats) {
  // Називаємо ЧИСЛО відміток, а не просто «з усіма». Саме воно змушує
  // зупинитись: «видалити звичку» звучить дешево, «разом із 38 днями» —
  // уже ні. Бот так робить давно, веб відставав.
  const count = stats?.total ?? 0;
  const cost =
    count > 0
      ? ` Разом із нею зникнуть ${count} ${plural(count)} відміток.`
      : "";

  if (!confirm(`Видалити «${habit.name}»?${cost} Це незворотно.`)) return;

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

// ---------- вхід через Telegram ----------
//
// Браузер не знає, хто його власник у Telegram, а бот знає напевно.
// Тому: просимо в сервера одноразовий код -> показуємо посилання на
// бота -> людина підтверджує там -> опитуємо сервер, поки він не
// віддасть сесію. Код при обміні згорає.

let pollTimer = null;

/** Показати екран входу або основний. */
function showScreen(name) {
  document.getElementById("login-screen").hidden = name !== "login";
  document.getElementById("app-screen").hidden = name !== "app";
}

/** Почати вхід: узяти код і чекати підтвердження в боті. */
async function startLogin() {
  const button = document.getElementById("login-button");
  const errorBox = document.getElementById("login-error");

  button.disabled = true;
  errorBox.hidden = true;

  let data;
  try {
    data = await api("/auth/login-code", { method: "POST" });
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    button.disabled = false;
    return;
  }

  const link = document.getElementById("login-link");
  link.href = data.url;
  document.getElementById("login-wait").hidden = false;

  // Відкриваємо бота одразу — щоб не змушувати робити зайвий клік.
  // Якщо браузер заблокує спливаюче вікно, посилання лишається видимим
  // і людина натисне його сама.
  window.open(data.url, "_blank", "noopener");

  waitForConfirmation(data.token, data.expires_in);
}

/**
 * Опитувати сервер, доки бот не підтвердить код.
 *
 * Опитування (poll) — найпростіший спосіб дізнатися про подію на
 * сервері. Складніші (WebSocket, SSE) тут зайві: чекати доводиться
 * секунди, і одне запитання на дві секунди нікого не навантажить.
 */
function waitForConfirmation(token, expiresIn) {
  clearInterval(pollTimer);
  const deadline = Date.now() + expiresIn * 1000;

  pollTimer = setInterval(async () => {
    if (Date.now() > deadline) {
      clearInterval(pollTimer);
      failLogin("Час вийшов. Спробуй ще раз.");
      return;
    }

    let data;
    try {
      data = await api(`/auth/login-code/${token}`);
    } catch (error) {
      // 404 чи 410 означають, що код уже недійсний — далі питати марно.
      clearInterval(pollTimer);
      failLogin(error.message);
      return;
    }

    if (data.status === "confirmed") {
      clearInterval(pollTimer);
      // Cookie сервер уже поставив у відповіді — далі просто працюємо.
      showScreen("app");
      load();
    }
  }, 2000);
}

function failLogin(message) {
  const errorBox = document.getElementById("login-error");
  errorBox.textContent = message;
  errorBox.hidden = false;
  document.getElementById("login-wait").hidden = true;
  document.getElementById("login-button").disabled = false;
}

async function logout() {
  await api("/auth/logout", { method: "POST" });
  clearInterval(pollTimer);
  document.getElementById("login-wait").hidden = true;
  document.getElementById("login-button").disabled = false;
  showScreen("login");
}

// ---------- старт ----------

document.getElementById("today").textContent = toISO(new Date());
document.getElementById("add-form").addEventListener("submit", addHabit);
document.getElementById("login-button").addEventListener("click", startLogin);
document.getElementById("logout-button").addEventListener("click", logout);

/**
 * З чого починати: з екрана входу чи одразу зі списку.
 *
 * Перевіряємо найпростішим способом — пробуємо завантажити дані.
 * Якщо сервер відповів 401, сесії немає; будь-яка інша помилка —
 * це вже справжня біда, і її треба показати, а не мовчки просити
 * увійти ще раз.
 */
async function start() {
  try {
    await api("/users/me");
  } catch (error) {
    showScreen("login");
    return;
  }
  showScreen("app");
  load();
}

start();
