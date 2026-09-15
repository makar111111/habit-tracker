"""Асинхронний клієнт до API трекера.

Чому бот ходить у власне API по HTTP, а не в базу напряму
----------------------------------------------------------
Робота з базою в цьому проєкті синхронна: SQLModel блокує потік, поки
чекає відповіді. Якби бот звертався до неї прямо, кожен запит зупиняв би
весь event loop — і бот не відповідав би нікому, поки триває один запит.
Асинхронність лишилась би тільки в назві.

HTTP-запит натомість — це очікування мережі, тобто саме те, на чому
asyncio виграє: поки один запит у дорозі, бот обслуговує інших людей.

Бонусом отримуємо чисту межу: бекенд один, клієнтів у нього два
(браузер і бот), і кожен нічого не знає про внутрішній устрій іншого.
"""

import asyncio
from datetime import date

import httpx

from config import API_URL, BOT_SECRET
from bot.schedule import is_planned_on

# Межа з моделей на боці API (HabitBase.name та UserUpdate.name).
# Тримаємо копію тут, щоб бот міг підрізати значення ЗАЗДАЛЕГІДЬ і не
# отримувати 422 у відповідь. Продубльована константа — не найкраще,
# але краще, ніж число, вписане в трьох місцях по пам'яті.
MAX_NAME_LENGTH = 100


def _archived_param(include_archived: bool) -> dict[str, str]:
    """Параметр запиту лише тоді, коли він щось змінює.

    Без архіву не шлемо нічого, а не include_archived=false: так запит
    лишається тим самим, що й до появи архіву в боті, і кеш чи логи
    API не отримують нового «шуму» на кожен звичайний список.
    """
    return {"include_archived": "true"} if include_archived else {}


class ApiError(Exception):
    """Базова помилка спілкування з API."""


class ApiUnavailable(ApiError):
    """API не відповідає — найчастіше просто не запущений сервер.

    Окремий клас потрібен, щоб бот міг сказати людині зрозуміле
    «сервер недоступний, спробуй пізніше» замість того, щоб мовчки
    впасти зі стеком викликів у консоль.
    """


class HabitGone(ApiError):
    """Звички більше немає — її видалили, поки повідомлення висіло в чаті.

    Ситуація буденна саме через два інтерфейси: людина відкрила список
    у Telegram, видалила звичку у браузері, а тоді натиснула стару кнопку.
    Це не поломка, тому й повідомлення має бути спокійне, а не «помилка».
    """


class HabitsAPI:
    """Усе спілкування бота з трекером зібране в одному місці.

    Обробники команд не знають ні про httpx, ні про адреси, ні про
    заголовки — вони працюють зі звичними методами на кшталт check_in().
    Якщо API колись зміниться, правити доведеться лише тут.
    """

    def __init__(self, base_url: str = API_URL, secret: str = BOT_SECRET):
        # ОДИН клієнт на весь час життя бота, а не новий на кожен запит.
        # httpx тримає всередині пул відкритих зʼєднань: наступний запит
        # до того самого сервера піде вже готовим каналом, без повторного
        # рукостискання TCP. Створювати клієнт щоразу — це викидати
        # цю економію і платити за зʼєднання знову й знову.
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._secret = secret

    async def close(self) -> None:
        """Закрити зʼєднання. Викликається при зупинці бота."""
        await self._client.aclose()

    # ---------- внутрішня кухня ----------

    def _headers(self, telegram_id: int | None) -> dict[str, str]:
        """Заголовки, якими бот доводить, кого саме він обслуговує.

        X-Telegram-Id сам по собі нічого не вартий — його міг би надіслати
        будь-хто. Довіру дає X-Bot-Secret: спільний пароль, який знають
        лише бот і API. Перевірку робить auth.get_current_user.

        telegram_id=None — для запитів, що стосуються ВСІХ користувачів
        одразу (список для розсилки нагадувань), де немає «від імені
        кого»: X-Telegram-Id тоді просто не додається, і на боці API
        такий запит перевіряє auth.require_bot, а не get_current_user.
        """
        headers = {"X-Bot-Secret": self._secret}
        if telegram_id is not None:
            headers["X-Telegram-Id"] = str(telegram_id)
        return headers

    async def _request(
        self, method: str, url: str, telegram_id: int | None, **kwargs
    ) -> httpx.Response:
        """Один запит до API з автентифікацією та зрозумілими помилками."""
        try:
            return await self._client.request(
                method, url, headers=self._headers(telegram_id), **kwargs
            )
        except httpx.ConnectError as error:
            raise ApiUnavailable(
                "Не вдалося зʼєднатися з API. Чи запущений uvicorn?"
            ) from error
        except httpx.TimeoutException as error:
            raise ApiUnavailable("API не відповів вчасно.") from error
        except httpx.TransportError as error:
            # Спільний предок для решти бід із мережею: обрив зʼєднання,
            # зіпсована відповідь, вичерпаний пул. Ловимо ОСТАННІМ, бо
            # два випадки вище — його нащадки, і їм є що сказати
            # конкретніше. Без цієї гілки рідкісний обрив вилетів би
            # як «щось пішло не так» без жодної підказки.
            raise ApiUnavailable(f"Збій звʼязку з API: {error}") from error

    @staticmethod
    def _ok(response: httpx.Response) -> httpx.Response:
        """Переконатись, що відповідь успішна, інакше кинути ApiError.

        Окремий метод, бо httpx-івський raise_for_status кидає власний
        виняток, а обробникам зручніше ловити один наш тип.
        """
        if response.is_success:
            return response

        # 401 і 503 означають, що не збігається BOT_SECRET або його немає
        # в .env. Найчастіша помилка при налаштуванні — тому окремий текст.
        if response.status_code in (401, 503):
            raise ApiError(
                "API не приймає секрет бота. Перевір BOT_SECRET у .env — "
                "він має бути однаковий для бота й для сервера."
            )

        raise ApiError(f"API відповів {response.status_code}: {response.text[:200]}")

    # ---------- користувач ----------

    async def today(self, telegram_id: int) -> date:
        """Поточний день власника за його часовим поясом на боці API."""
        response = await self._request("GET", "/users/me/today", telegram_id)
        return date.fromisoformat(self._ok(response).json()["day"])

    async def list_reminder_targets(self) -> list[dict]:
        """Увімкнені нагадування: користувач, часовий пояс, година й останній день."""
        response = await self._request("GET", "/reminder-targets", None)
        return self._ok(response).json()

    async def mark_reminder_sent(self, telegram_id: int, day: date) -> None:
        """Зберегти день лише після успішного надсилання в Telegram."""
        response = await self._request(
            "POST",
            f"/reminder-targets/{telegram_id}/sent",
            None,
            json={"day": day.isoformat()},
        )
        self._ok(response)

    async def set_name(self, telegram_id: int, name: str) -> None:
        """Зберегти імʼя людини на боці API.

        Імʼя йде тілом JSON, а не заголовком: HTTP-заголовки за стандартом
        однобайтові, і «Олена» в них не влазить. Це не теорія — на цьому
        падали тести, поки заголовок не замінили на тіло запиту.

        Імʼя підрізаємо самі. Telegram дозволяє по 64 символи на імʼя
        та прізвище, тобто full_name сягає 129 — а модель на боці API
        приймає до 100. Без підрізання людина з довгим імʼям отримала б
        на /start не привітання, а дамп помилки валідації.
        """
        response = await self._request(
            "PATCH", "/users/me", telegram_id, json={"name": name[:MAX_NAME_LENGTH]}
        )
        self._ok(response)

    async def get_me(self, telegram_id: int) -> dict:
        """Профіль людини: ім'я, часовий пояс, година й стан нагадувань."""
        response = await self._request("GET", "/users/me", telegram_id)
        return self._ok(response).json()

    async def update_settings(
        self,
        telegram_id: int,
        *,
        timezone: str | None = None,
        reminder_hour: int | None = None,
        reminders_enabled: bool | None = None,
    ) -> dict:
        """Змінити налаштування нагадувань. Повертає оновлений профіль.

        Як і в update_habit, у тіло йдуть ЛИШЕ передані поля: API
        оновлює через exclude_unset, тож зміна години не зачепить пояс.
        """
        payload: dict[str, str | int | bool] = {}
        if timezone is not None:
            payload["timezone"] = timezone
        if reminder_hour is not None:
            payload["reminder_hour"] = reminder_hour
        if reminders_enabled is not None:
            payload["reminders_enabled"] = reminders_enabled

        response = await self._request("PATCH", "/users/me", telegram_id, json=payload)
        return self._ok(response).json()

    async def confirm_login(self, telegram_id: int, token: str) -> None:
        """Підтвердити код входу у вебверсію від імені цієї людини.

        Обидва заголовки тут обов'язкові й доповнюють один одного:
        секрет доводить, що запит справді від бота, а X-Telegram-Id
        каже, кому саме прив'язати сесію.
        """
        response = await self._request(
            "POST", "/auth/confirm", telegram_id, json={"token": token}
        )
        self._ok(response)

    async def list_telegram_users(self) -> list[int]:
        """Усі telegram_id, кому бот може писати. Для розсилки нагадувань.

        telegram_id=None у запиті: тут немає "від імені кого" — питання
        не "хто ти", а "дай мені всіх". API перевіряє це auth.require_bot,
        не auth.get_current_user.
        """
        response = await self._request("GET", "/telegram-users", None)
        return self._ok(response).json()

    # ---------- звички ----------

    async def list_habits(
        self, telegram_id: int, *, include_archived: bool = False
    ) -> list[dict]:
        """Звички людини: id, назва, опис.

        Архівні API за замовчуванням не віддає — саме тому список відміток
        і нагадування їх не бачать без жодного фільтра на боці бота.
        """
        response = await self._request(
            "GET", "/habits", telegram_id, params=_archived_param(include_archived)
        )
        return self._ok(response).json()

    async def list_stats(
        self, telegram_id: int, *, include_archived: bool = False
    ) -> list[dict]:
        """Показники всіх звичок: серії, всього днів, чи зроблено сьогодні."""
        response = await self._request(
            "GET", "/stats", telegram_id, params=_archived_param(include_archived)
        )
        return self._ok(response).json()

    async def habits_with_stats(
        self, telegram_id: int, *, include_archived: bool = False
    ) -> list[dict]:
        """Звички разом із їхніми показниками — усе для клавіатури одразу.

        Назви лежать у /habits, а серії — у /stats, тож потрібні обидва
        запити. І ось тут асинхронність нарешті дає реальний виграш:
        asyncio.gather запускає їх ОДНОЧАСНО і чекає обидва.

        Послідовно це коштувало б час(A) + час(B).
        Паралельно — max(час(A), час(B)), тобто майже вдвічі менше.
        Саме заради таких місць бот і робиться асинхронним.
        """
        # Той самий день користувача потрібний для розкладу, що й для відміток.
        # Годинник процесу бота може бути в зовсім іншому часовому поясі.
        today = await self.today(telegram_id)
        habits, stats = await asyncio.gather(
            self.list_habits(telegram_id, include_archived=include_archived),
            self.list_stats(telegram_id, include_archived=include_archived),
        )

        # Показники приходять списком; перекладаємо у словник за habit_id,
        # щоб пошук був миттєвий, а не перебором на кожну звичку.
        stats_by_id = {row["habit_id"]: row for row in stats}

        return [
            habit
            | {
                "stats": stats_by_id.get(habit["id"], {}),
                "due_today": is_planned_on(habit, today),
            }
            for habit in habits
        ]

    async def create_habit(
        self,
        telegram_id: int,
        name: str,
        description: str = "",
        *,
        weekdays: list[int] | None = None,
    ) -> dict:
        """Створити звичку. Власника API візьме з автентифікації.

        Без weekdays API ставить «щодня» — саме так бот раніше й створював
        УСІ звички, мовчки: поле необов'язкове, тож запит проходив успішно.
        """
        body: dict = {"name": name, "description": description}
        if weekdays is not None:
            body["weekdays"] = weekdays
        response = await self._request("POST", "/habits", telegram_id, json=body)
        return self._ok(response).json()

    async def update_habit(
        self,
        telegram_id: int,
        habit_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        archived: bool | None = None,
    ) -> dict:
        """Змінити назву, опис звички або перенести її в архів і назад.

        archived=True ховає звичку з активного списку, зберігаючи всю
        історію відміток; archived=False повертає її. Повторний
        archived=True безпечний: API не зсуває вже збережену дату архіву.

        Аргументи keyword-only (після `*`) навмисно: update_habit(id, "Йога")
        без назви поля читалося б двозначно — це нова назва чи опис?
        Тепер плутанина неможлива, доводиться писати name= або description=.

        У тіло запиту кладемо ЛИШЕ передані поля. На боці API працює
        exclude_unset: чого немає в тілі, того він і не чіпає. Тому
        зміна лише назви не затирає опис порожнім рядком.
        """
        payload: dict[str, str | bool] = {}
        if name is not None:
            payload["name"] = name[:MAX_NAME_LENGTH]
        if description is not None:
            payload["description"] = description
        if archived is not None:
            payload["archived"] = archived

        response = await self._request(
            "PATCH", f"/habits/{habit_id}", telegram_id, json=payload
        )
        self._raise_if_gone(response)
        return self._ok(response).json()

    async def delete_habit(self, telegram_id: int, habit_id: int) -> None:
        """Видалити звичку разом з усіма її відмітками."""
        response = await self._request("DELETE", f"/habits/{habit_id}", telegram_id)
        self._raise_if_gone(response)
        self._ok(response)

    @staticmethod
    def _raise_if_gone(response: httpx.Response) -> None:
        """Перекласти 404 у зрозумілий виняток.

        Викликається ЛИШЕ там, де 404 не має інших значень, окрім
        «звички немає»: у запитах, адресованих самій звичці.

        Розрізняти два сенси 404 за текстом поля detail було б крихко —
        досить комусь переформулювати повідомлення на боці API, і бот
        мовчки почне поводитись інакше. Тому сенс визначає не текст,
        а те, ЯКИЙ саме запит ми зробили. Там, де 404 неоднозначний
        (видалення відмітки), цей метод просто не викликається.
        """
        if response.status_code == 404:
            raise HabitGone(
                "Цієї звички вже немає — можливо, ти видалив її у браузері. "
                "Онови список: /habits"
            )

    # ---------- відмітки ----------

    async def check_in(
        self, telegram_id: int, habit_id: int, day: date | None = None
    ) -> bool:
        """Відмітити звичку за сьогодні або за вказаний день.

        Повертає True, якщо відмітка створена, і False, якщо цей день
        уже був відмічений раніше.

        Без day день обирає СЕРВЕР: ми надсилаємо порожнє тіло, і він
        підставляє поточну дату в часовому поясі власника. day передає
        той, хто знає контекст краще за «зараз» — наприклад, кнопка
        нагадування, яке прийшло ввечері, а натиснуте вже після опівночі.

        409 тут навмисно НЕ вважається помилкою: з погляду API це
        конфлікт, а з погляду людини — просто «вже зроблено».
        Перекладати технічний код у побутовий сенс — робота клієнта.
        """
        body = {"day": day.isoformat()} if day else {}
        response = await self._request(
            "POST", f"/habits/{habit_id}/checkins", telegram_id, json=body
        )

        if response.status_code == 409:
            return False

        self._raise_if_gone(response)
        self._ok(response)
        return True

    async def is_checked_in(self, telegram_id: int, habit_id: int, day: date) -> bool:
        """Чи є відмітка звички за конкретний день.

        Показники (/stats) знають лише «сьогодні», тож для будь-якого
        іншого дня питаємо відмітки за період рівно з одного дня.
        """
        response = await self._request(
            "GET",
            f"/habits/{habit_id}/checkins",
            telegram_id,
            params={"since": day.isoformat(), "until": day.isoformat()},
        )
        self._raise_if_gone(response)
        return bool(self._ok(response).json())

    async def undo_check_in(self, telegram_id: int, habit_id: int, day: date) -> bool:
        """Зняти відмітку за конкретний день. False — знімати не було чого.

        День тут ОБОВʼЯЗКОВИЙ, без значення за замовчуванням. Причина
        не в тому, що «сьогодні» важко вгадати, а в тому, що вибір дня —
        рішення, яке має ухвалювати той, хто викликає: у нього є контекст,
        а тут його немає.

        404 на цьому запиті НЕ перекладається у виняток, бо він
        неоднозначний: сервер віддає той самий код і коли немає звички,
        і коли немає відмітки. Другий випадок — буденний («знімати
        нічого»), тому обидва зводимо до False. Екран усе одно
        перемальовується з бази, тож користувач побачить правду
        незалежно від того, який саме з двох випадків стався.
        """
        response = await self._request(
            "DELETE", f"/habits/{habit_id}/checkins/{day.isoformat()}", telegram_id
        )

        if response.status_code == 404:
            return False

        self._ok(response)
        return True
