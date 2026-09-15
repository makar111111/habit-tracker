"""Обмеження частоти запитів: не більше N спроб за вікно часу з однієї адреси.

Навіщо: POST /auth/login-code доступний без входу і щоразу пише рядок
у базу. Без ліміту будь-хто міг би засипати SQLite тисячами кодів —
а SQLite на час запису блокує всю базу, тож гальмували б усі.

Лічильник живе в пам'яті процесу, а не в базі: інакше захист від
зайвих записів сам робив би запис на кожен запит. Ціна — після
перезапуску сервера лічильники обнуляються, для захисту від флуду
це прийнятно.
"""

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Ковзне вікно: пам'ятаємо моменти останніх спроб кожного ключа.

    Ковзне, а не «скидання щохвилини»: при фіксованих хвилинах можна
    зробити N запитів о 12:00:59 і ще N о 12:01:00 — подвійний ліміт
    за секунду. Тут рахуються спроби саме за останні window_seconds.
    """

    # Поріг, після якого прибираємо ключі без свіжих спроб. Без цього
    # словник ріс би з кожною новою адресою, яка колись приходила.
    _SWEEP_THRESHOLD = 10_000

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        # monotonic, а не time.time(): системний годинник може перевести
        # NTP чи людина, і тоді вікно «стрибнуло» б. Монотонний — ні.
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        # Синхронні ендпоінти FastAPI виконуються в пулі потоків, тож два
        # запити можуть прийти одночасно. Без замка обидва побачили б
        # «ще є місце» і разом перевищили б ліміт.
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Зарахувати спробу.

        Повертає None, якщо спробу дозволено, або кількість секунд,
        через яку з'явиться вільне місце, якщо ліміт вичерпано.
        Відхилена спроба НЕ зараховується: інакше той, хто стукає без
        упину, ніколи не дочекався б вільного вікна.
        """
        with self._lock:
            now = self._clock()
            if len(self._hits) > self._SWEEP_THRESHOLD:
                self._sweep(now)

            hits = self._hits.setdefault(key, deque())
            self._drop_old(hits, now)

            if len(hits) >= self.limit:
                return hits[0] + self.window_seconds - now

            hits.append(now)
            return None

    def reset(self) -> None:
        """Забути всі лічильники (для тестів)."""
        with self._lock:
            self._hits.clear()

    def _drop_old(self, hits: deque[float], now: float) -> None:
        while hits and hits[0] <= now - self.window_seconds:
            hits.popleft()

    def _sweep(self, now: float) -> None:
        for key in list(self._hits):
            self._drop_old(self._hits[key], now)
            if not self._hits[key]:
                del self._hits[key]
