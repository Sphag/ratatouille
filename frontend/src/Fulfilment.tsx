import { useEffect, useState } from 'react';
import { request, units, type Unit } from './recipes';
import { numberLabel, type Menu } from './menus';
interface Shopping {
  week: number;
  ingredient_id: string;
  name: string;
  unit: Unit;
  quantity: string;
  checked: boolean;
}
interface Batch {
  week: number;
  version_id: string;
  name: string;
  portions: number;
  ingredients: Omit<Shopping, 'checked'>[];
  checked: boolean;
}
interface View {
  revision_id: string;
  state: Menu['state'];
  shopping: Shopping[];
  cooking: Batch[];
}
export function Fulfilment({
  menu,
  week,
  busy,
  act,
}: {
  menu: Menu;
  week: number;
  busy: boolean;
  act: (action: () => Promise<void>) => Promise<void>;
}) {
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState('');
  async function reload() {
    setData(await request<View>(`menu/revisions/${menu.id}/fulfilment`));
    setError('');
  }
  useEffect(() => {
    let alive = true;
    void request<View>(`menu/revisions/${menu.id}/fulfilment`)
      .then((v) => {
        if (alive) setData(v);
      })
      .catch((e: unknown) => {
        if (alive)
          setError(e instanceof Error ? e.message : 'Не удалось загрузить покупки и готовку.');
      });
    return () => {
      alive = false;
    };
  }, [menu.id, menu.number]);
  if (!data)
    return (
      <section className="menu-panel">
        <p>{error || 'Загружаем покупки и готовку…'}</p>
        <button disabled={busy} onClick={() => void act(reload)}>
          Повторить загрузку
        </button>
      </section>
    );
  return (
    <section aria-label="Покупки и готовка">
      <p className="hint">
        Неделя {week + 1}.{' '}
        {data.state === 'confirmed'
          ? 'Отметки относятся к этой сохранённой версии плана.'
          : 'Это расчёт черновика. Отметки станут доступны после подтверждения.'}
      </p>
      <button className="text-button" disabled={busy} onClick={() => void act(reload)}>
        Перечитать покупки и готовку
      </button>
      <section className="menu-panel">
        <h2>Покупки этой недели</h2>
        {data.shopping.filter((s) => s.week === week).length === 0 && (
          <p>Добавьте блюда в меню, чтобы увидеть продукты.</p>
        )}
        <ul className="checklist">
          {data.shopping
            .filter((s) => s.week === week)
            .map((s) => (
              <li key={`${s.ingredient_id}-${s.unit}`}>
                <label>
                  <input
                    type="checkbox"
                    checked={s.checked}
                    disabled={busy || data.state !== 'confirmed'}
                    onChange={(e) =>
                      void act(async () =>
                        setData(
                          await request<View>(`menu/revisions/${menu.id}/shopping-check`, 'PUT', {
                            week,
                            ingredient_id: s.ingredient_id,
                            unit: s.unit,
                            expected_checked: s.checked,
                            checked: e.target.checked,
                          }),
                        ),
                      )
                    }
                  />
                  <span>
                    {s.name}: {numberLabel(s.quantity)} {units[s.unit]} · куплено
                  </span>
                </label>
              </li>
            ))}
        </ul>
      </section>
      <section className="menu-panel">
        <h2>План готовки</h2>
        <p className="hint">
          Партии рецептов и целые порции за неделю. Это перечень, без расписания параллельной
          готовки.
        </p>
        {data.cooking.filter((b) => b.week === week).length === 0 && (
          <p>Партии появятся после выбора блюд.</p>
        )}
        {data.cooking
          .filter((b) => b.week === week)
          .map((b) => (
            <article key={b.version_id} className="cooking-batch">
              <h3>
                {b.name} · {b.portions} порц.
              </h3>
              <label>
                <input
                  type="checkbox"
                  checked={b.checked}
                  disabled={busy || data.state !== 'confirmed'}
                  onChange={(e) =>
                    void act(async () =>
                      setData(
                        await request<View>(`menu/revisions/${menu.id}/cooking-check`, 'PUT', {
                          week,
                          version_id: b.version_id,
                          expected_checked: b.checked,
                          checked: e.target.checked,
                        }),
                      ),
                    )
                  }
                />
                Приготовлено
              </label>
              <ul>
                {b.ingredients.map((s) => (
                  <li key={`${s.ingredient_id}-${s.unit}`}>
                    {s.name}: {numberLabel(s.quantity)} {units[s.unit]}
                  </li>
                ))}
              </ul>
            </article>
          ))}
      </section>
    </section>
  );
}
