"""Готові звички для новачка — дані без жодної логіки.

Окремим файлом, а не всередині new_habit.py чи views.py, щоб уникнути
циклічного імпорту: views малює екран шаблонів, new_habit створює з них
звички, а views уже імпортується в new_habit. Модуль лише з даними
нічого з бота не імпортує, тож цикл тут неможливий.

Порожній список звичок — момент, коли людина найчастіше закриває бота:
«а що саме додавати?». Шаблон прибирає це питання одним дотиком.
"""

from dataclasses import dataclass

from bot.schedule import EVERY_DAY


@dataclass(frozen=True)
class HabitTemplate:
    # key — у callback_data ("tpl:water"), тож короткий латиницею: 64 байти
    # на всю кнопку, а кирилиця й емодзі займають по 2–4 байти на символ.
    key: str
    name: str
    description: str
    weekdays: tuple[int, ...] = tuple(EVERY_DAY)


# Прості, щоденні й такі, що не потребують підготовки: перша звичка має
# бути легкою, інакше людина зірветься в перший же день. Розклад «щодня»
# навмисно — новачкові незрозуміле «💤 сьогодні не заплановано» у вихідні.
TEMPLATES: tuple[HabitTemplate, ...] = (
    HabitTemplate("water", "💧 Пити воду", "Склянка води одразу після пробудження"),
    HabitTemplate("read", "📚 Читати 10 хвилин", "Навіть кілька сторінок рахуються"),
    HabitTemplate("move", "🏃 Зарядка", "10 хвилин руху зранку"),
    HabitTemplate("walk", "🚶 Прогулянка", "20 хвилин на свіжому повітрі"),
    HabitTemplate("sleep", "😴 Лягти до 23:00", "Телефон — поза ліжком"),
)

TEMPLATES_BY_KEY: dict[str, HabitTemplate] = {t.key: t for t in TEMPLATES}
