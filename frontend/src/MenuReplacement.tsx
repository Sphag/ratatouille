import { useState } from 'react';
import { request, units, type Recipe } from './recipes';
import { dateLabel, numberLabel, slots, type Menu, type Slot } from './menus';
import { MenuProposal } from './MenuProposal';

interface Preview {
  expected_number: number;
  menu: Menu;
  changes: { day: number; slot: Slot; version_id: string | null }[];
  digest: string;
}
export function MenuReplacement({
  menu,
  recipes,
  busy,
  act,
  update,
  back,
}: {
  menu: Menu;
  recipes: Recipe[];
  busy: boolean;
  act: (action: () => Promise<void>) => Promise<void>;
  update: (menu: Menu) => void;
  back: () => void;
}) {
  const [day, setDay] = useState(0);
  const [slot, setSlot] = useState<Slot>('breakfast');
  const [version, setVersion] = useState('');
  const [automatic, setAutomatic] = useState(false);
  const [scenario, setScenario] = useState('keep');
  const [options, setOptions] = useState<Menu[] | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const body = {
    expected_number: menu.number,
    day,
    slot,
    version_id: version,
    scenario,
    automatic,
  };
  function clear() {
    setOptions(null);
    setVersion('');
  }
  if (preview)
    return (
      <>
        <section className="menu-panel">
          <h2>Что изменится</h2>
          <p>
            {scenario === 'keep'
              ? 'Остальные блюда сохраняются.'
              : 'Предложены изменения только выбранной недели. Выбранное блюдо зафиксировано.'}{' '}
            Порции остаются целыми.
          </p>
          <ul>
            {preview.changes.map((c) => (
              <li key={`${c.day}-${c.slot}`}>
                {dateLabel(menu.totals[c.day].date)} · {slots.find(([s]) => s === c.slot)?.[1]}:{' '}
                {menu.entries.find((e) => e.day === c.day && e.slot === c.slot)?.name ??
                  'Нет блюда'}{' '}
                →{' '}
                {preview.menu.entries.find((e) => e.day === c.day && e.slot === c.slot)?.name ??
                  'Без первого блюда'}
              </li>
            ))}
          </ul>
          <h3>Покупки после замены</h3>
          <ul>
            {preview.menu.shopping
              .filter((s) => s.week === Math.floor(day / 7))
              .map((s) => (
                <li key={`${s.ingredient_id}-${units[s.unit]}`}>
                  {s.name}: {numberLabel(s.quantity)} {units[s.unit]}
                </li>
              ))}
          </ul>
        </section>
        <MenuProposal
          proposal={{ menu: preview.menu, positions: [], digest: preview.digest }}
          busy={busy}
          note="Просмотр замены не изменяет черновик. Применение сохранит показанные изменения; план нужно подтвердить отдельно."
          admissionNote="При ручном выборе допуск не обязателен. Автоматические варианты и изменения остальных блюд используют только допущенные рецепты."
          onDiscard={() => setPreview(null)}
          onApply={() =>
            act(async () =>
              update(
                await request<Menu>(`menu/revisions/${menu.id}/replacement`, 'PUT', {
                  ...body,
                  digest: preview.digest,
                }),
              ),
            )
          }
        />
      </>
    );
  return (
    <section className="menu-panel" aria-label="Замена блюда">
      <button className="text-button" disabled={busy} onClick={back}>
        ← Вернуться к меню
      </button>
      <h1>Замена блюда</h1>
      <p>
        В A/B замена меняет выбранный слот во всех днях A либо B той же недели. Другие недели
        сохраняются.
      </p>
      <label>
        День замены
        <select
          value={day}
          disabled={busy}
          onChange={(e) => {
            setDay(Number(e.target.value));
            clear();
          }}
        >
          {menu.totals.map((d) => (
            <option key={d.day} value={d.day}>
              {dateLabel(d.date)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Слот замены
        <select
          value={slot}
          disabled={busy}
          onChange={(e) => {
            setSlot(e.target.value as Slot);
            clear();
          }}
        >
          {slots.map(([s, label]) => (
            <option key={s} value={s}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <button
        className="secondary"
        disabled={busy}
        onClick={() =>
          void act(async () => {
            setOptions(
              await request<Menu[]>(`menu/revisions/${menu.id}/replacement-options`, 'POST', {
                expected_number: menu.number,
                day,
                slot,
              }),
            );
          })
        }
      >
        Подобрать варианты замены
      </button>
      {options && (
        <div>
          <p>
            {options.length
              ? 'Допущенные варианты по близости КБЖУ:'
              : 'Подходящих допущенных вариантов нет. Можно выбрать рецепт вручную либо проверить допуск и лимит.'}
          </p>
          {options.map((o) => {
            const e = o.entries.find((e) => e.day === day && e.slot === slot)!;
            return (
              <button
                key={e.version_id}
                disabled={busy}
                className="text-button"
                onClick={() => {
                  setVersion(e.version_id);
                  setAutomatic(true);
                }}
              >
                {e.name}
              </button>
            );
          })}
        </div>
      )}
      <label>
        Рецепт замены
        <select
          value={version}
          disabled={busy}
          onChange={(e) => {
            setVersion(e.target.value);
            setAutomatic(false);
          }}
        >
          <option value="">Выберите рецепт</option>
          {recipes
            .filter((r) => !r.archived)
            .map((r) => (
              <option key={r.version_id} value={r.version_id}>
                {r.name}
              </option>
            ))}
        </select>
      </label>
      <fieldset disabled={busy}>
        <legend>Остальные блюда</legend>
        <label>
          <input
            type="radio"
            name="replacement-scenario"
            checked={scenario === 'keep'}
            onChange={() => setScenario('keep')}
          />
          Оставить без изменений
        </label>
        <label>
          <input
            type="radio"
            name="replacement-scenario"
            checked={scenario === 'rebalance'}
            onChange={() => setScenario('rebalance')}
          />
          Предложить изменения для приближения к целям
        </label>
      </fieldset>
      <button
        className="primary"
        disabled={busy || !version}
        onClick={() =>
          void act(async () =>
            setPreview(
              await request<Preview>(`menu/revisions/${menu.id}/replacement`, 'POST', body),
            ),
          )
        }
      >
        Посмотреть замену
      </button>
    </section>
  );
}
