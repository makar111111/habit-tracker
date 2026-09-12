"""Чи не розійшлися типи фронтенду з моделями бекенду.

Проблема, яку тут закрито
-------------------------
Фронтенд написаний на TypeScript, і його типи (`frontend/src/api/types.ts`)
— це РУЧНИЙ переклад pydantic-моделей. Ручний переклад має відому ваду:
хтось додає поле в `models.py`, фронтенд про нього не дізнається, і
розбіжність спливає вже в браузері — або не спливає взагалі, бо поле
просто мовчки не використовується.

Цей тест читає файл типів як текст і звіряє набір полів. Він не
перевіряє самі ТИПИ полів (для цього потрібен був би розбір TypeScript),
але ловить головне: поле з'явилось, зникло або його перейменували.

Чому тест живе на боці Python, а не серед тестів Vitest: pydantic-моделі
— єдине джерело правди, і вони тут. Щоб зробити те саме з боку
фронтенду, довелося б спершу якось доставити туди схему.
"""

import re
from pathlib import Path

import pytest

from models import Checkin, HabitPublic, HabitStats, UserPublic

TYPES_FILE = Path(__file__).parent / "frontend" / "src" / "api" / "types.ts"


def parse_interface(source: str, name: str) -> set[str]:
    """Витягнути назви полів з `export interface Name { ... }`.

    Регулярка навмисно проста: у файлі типів немає ні вкладених
    об'єктів, ні методів — лише плоскі поля. Щойно там з'явиться щось
    складніше, цей розбір треба буде замінити, і краще про це знати.
    """
    match = re.search(
        rf"export interface {name} \{{(.*?)\n\}}",
        source,
        re.DOTALL,
    )
    if match is None:
        pytest.fail(f"У {TYPES_FILE.name} немає інтерфейсу {name}")

    body = match.group(1)

    # Прибираємо коментарі, щоб слово всередині /** ... */ не зійшло
    # за назву поля.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    body = re.sub(r"//.*", "", body)

    # Поле виглядає як `name: type;` або `name?: type;` на початку рядка.
    return set(re.findall(r"^\s*(\w+)\??:", body, re.MULTILINE))


@pytest.fixture(scope="module")
def source() -> str:
    if not TYPES_FILE.exists():
        pytest.skip(f"Немає {TYPES_FILE} — фронтенд не на місці")
    return TYPES_FILE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "model, interface_name",
    [
        (HabitPublic, "Habit"),
        (HabitStats, "HabitStats"),
        (UserPublic, "User"),
        (Checkin, "Checkin"),
    ],
)
def test_fields_match(source: str, model, interface_name: str) -> None:
    """Набір полів у TypeScript збігається з набором полів моделі."""
    expected = set(model.model_fields)
    actual = parse_interface(source, interface_name)

    missing = expected - actual
    extra = actual - expected

    assert not missing, (
        f"{interface_name} у types.ts не знає про поля {sorted(missing)}, "
        f"які є в {model.__name__}. Додай їх туди."
    )
    assert not extra, (
        f"{interface_name} у types.ts описує поля {sorted(extra)}, "
        f"яких немає в {model.__name__}. Прибери або виправ назву."
    )


def test_habit_stats_has_no_leftovers(source: str) -> None:
    """Окремо про HabitStats — його поля найлегше зламати.

    Саме на них тримається кнопка відмітки й оптимістичне оновлення
    в `hooks.ts`: помилка в назві `done_today` не впаде ніде, просто
    галочка перестане вмикатися.
    """
    fields = parse_interface(source, "HabitStats")
    assert "done_today" in fields
    assert "current_streak" in fields
