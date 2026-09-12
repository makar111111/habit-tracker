import type { HabitWithStats } from "../api/types";

interface Props {
  items: HabitWithStats[];
}

/**
 * «Скільки я вже зробив сьогодні» — головне питання, заради якого
 * відкривають трекер. Відповідь має бути вище за список, щоб її
 * не довелося збирати очима самому.
 */
export function ProgressPanel({ items }: Props) {
  if (items.length === 0) return null;

  const done = items.filter((item) => item.stats.done_today).length;
  const total = items.length;
  const percent = Math.round((done / total) * 100);
  const complete = done === total;

  return (
    <section className="progress">
      <div className="progress-head">
        <span className="progress-count">
          {done} з {total} сьогодні
        </span>
        <span className="progress-note">{note(done, total)}</span>
      </div>

      {/*
        role="progressbar" з трьома aria-атрибутами перетворює два
        порожні <div> на зрозумілий для екранного диктора індикатор.
        Без них він побачив би два блоки без тексту й промовчав би.
      */}
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label="Прогрес за сьогодні"
      >
        <div
          className={complete ? "progress-fill complete" : "progress-fill"}
          style={{ width: `${percent}%` }}
        />
      </div>
    </section>
  );
}

function note(done: number, total: number): string {
  if (done === total) return "усе зроблено 🎉";
  if (done === 0) return "ще нічого";
  return `лишилось ${total - done}`;
}
