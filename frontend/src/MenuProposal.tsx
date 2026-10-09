import { useState } from 'react';
import { nutrients } from './recipes';
import { dateLabel, numberLabel, rangeLabel, slots, type Proposal } from './menus';

export function MenuProposal({
  proposal,
  busy,
  onApply,
  onDiscard,
  note,
  admissionNote,
}: {
  proposal: Proposal;
  busy: boolean;
  onApply: () => Promise<void>;
  onDiscard: () => void;
  note?: string;
  admissionNote?: string;
}) {
  const [week, setWeek] = useState(0);
  const menu = proposal.menu;
  return (
    <section aria-label="Предложение меню">
      <button className="text-button" disabled={busy} onClick={onDiscard}>
        ← Вернуться к черновику
      </button>
      <p className="eyebrow">Автоматический подбор</p>
      <h1>Предложение меню</h1>
      <p>{rangeLabel(menu.start_date, menu.days)}</p>
      <p className="notice">
        {note ??
          'Черновик пока не изменён. Применение заменит блюда всех недель. Подтверждённый план сохранится до отдельного подтверждения меню.'}
      </p>
      <p>
        {menu.mode === 'ab'
          ? 'A/B: 4 дня A и 3 дня B в каждой неделе.'
          : `Лимит: ${menu.repeat_limit} появления блюда за неделю по всем слотам.`}
      </p>
      <p className="hint">
        {admissionNote ??
          'Использованы только активные рецепты с вашим явным допуском. Каждое блюдо — одна порция. Проверьте распределение по приёмам пищи.'}
      </p>
      <nav className="tabs" aria-label="Недели предложения">
        {Array.from({ length: menu.days / 7 }, (_, i) => (
          <button
            key={i}
            className={week === i ? 'tab active' : 'tab'}
            aria-current={week === i ? 'page' : undefined}
            onClick={() => setWeek(i)}
          >
            Неделя {i + 1}
          </button>
        ))}
      </nav>
      <div className="menu-days">
        {menu.totals.slice(week * 7, week * 7 + 7).map((day) => (
          <article className="menu-panel" key={day.day}>
            <h2>
              {dateLabel(day.date)}
              {menu.mode === 'ab' ? ` · ${(day.day % 7) % 2 === 0 ? 'A' : 'B'}` : ''}
            </h2>
            <dl className="proposal-dishes">
              {slots.map(([slot, label]) => (
                <div key={slot}>
                  <dt>{label}</dt>
                  <dd>
                    {menu.entries.find((e) => e.day === day.day && e.slot === slot)?.name ??
                      'Без первого блюда'}
                  </dd>
                </div>
              ))}
            </dl>
            <div className="daily-totals">
              <dl>
                {nutrients.map(([key, label]) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd>
                      {numberLabel(day.total[key])}
                      <span>Δ {numberLabel(day.difference[key], true)}</span>
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </article>
        ))}
      </div>
      <p className="hint">
        «+» — превышение дневной цели, «−» — недобор. Отклонения не запрещают сохранение.
      </p>
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => void onApply()}>
          Применить к черновику
        </button>
        <button className="secondary" disabled={busy} onClick={onDiscard}>
          Отклонить предложение
        </button>
      </div>
    </section>
  );
}
