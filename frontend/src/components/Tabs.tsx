import { useRef } from "react";

export interface TabItem<T extends string> {
  value: T;
  label: string;
}

interface Props<T extends string> {
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Для чого цей набір вкладок — читає екранний диктор. */
  label: string;
}

/**
 * Вкладки за правилами ARIA.
 *
 * Спершу тут стояли звичайні кнопки з `aria-selected`, і це не
 * працювало взагалі: за специфікацією ARIA цей атрибут дозволений лише
 * на ролях `tab`, `option`, `row`, `gridcell` і `treeitem`. У кнопки
 * неявна роль `button`, тож атрибут просто ігнорувався — вкладка була
 * підсвічена візуально, але для екранного диктора нічим не
 * відрізнялася від сусідньої.
 *
 * Роль `tab` тягне за собою обов'язки, і вони виконані тут усі:
 *
 *   • `role="tablist"` навколо і `role="tabpanel"` на вмісті
 *     (див. App.tsx) — інакше роль `tab` ні на що не вказує;
 *   • стрілки ← → перемикають вкладки. Для вкладок це не прикраса,
 *     а очікувана поведінка: диктор оголошує їх як набір, і людина
 *     чекає, що він гортається стрілками;
 *   • у табуляцію потрапляє ЛИШЕ активна вкладка (`tabIndex`).
 *     Це називають roving tabindex: Tab заводить у набір і виводить
 *     із нього, а всередині рухаються стрілками. Без цього довелося б
 *     протискати табом кожну вкладку, щоб дістатися до вмісту.
 */
export function Tabs<T extends string>({ items, value, onChange, label }: Props<T>) {
  // Посилання на кнопки потрібні, щоб ПЕРЕВЕСТИ фокус на нову вкладку.
  // Змінити стан мало: фокус лишився б на старій кнопці, і наступна
  // стрілка рахувалася б від неї.
  const buttons = useRef(new Map<T, HTMLButtonElement | null>());

  /** Перейти на вкладку за номером і перевести на неї фокус. */
  function select(index: number) {
    const next = items[index];
    onChange(next.value);
    buttons.current.get(next.value)?.focus();
  }

  /** Зсув відносно поточної вкладки, із замиканням у кільце. */
  function move(offset: number) {
    const index = items.findIndex((item) => item.value === value);
    // Додаємо довжину перед відсотком, бо в JavaScript -1 % 2 дорівнює
    // -1, а не 1, і з першої вкладки стрілка вліво дала б -1.
    select((index + offset + items.length) % items.length);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    // Home і End — АБСОЛЮТНІ переходи, і виражати їх через зсув не
    // можна. Спершу тут стояло move(-items.length): із двох вкладок
    // такий зсув кратний довжині, тобто повний оберт, і Home лишав
    // усе на місці. Тест на це і впав.
    if (event.key === "ArrowRight") move(1);
    else if (event.key === "ArrowLeft") move(-1);
    else if (event.key === "Home") select(0);
    else if (event.key === "End") select(items.length - 1);
    else return;

    // Стрілки в межах вкладок не мають ще й прокручувати сторінку.
    event.preventDefault();
  }

  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item) => {
        const selected = item.value === value;
        return (
          <button
            key={item.value}
            ref={(node) => {
              buttons.current.set(item.value, node);
            }}
            type="button"
            role="tab"
            className="tab"
            id={`tab-${item.value}`}
            aria-selected={selected}
            aria-controls={`panel-${item.value}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.value)}
            onKeyDown={onKeyDown}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
