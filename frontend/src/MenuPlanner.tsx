import { useEffect, useState, type FormEvent } from 'react';
import { decimal, nutrients, request, type Recipe } from './recipes';
import {
  dateLabel,
  numberLabel,
  rangeLabel,
  slots,
  type Menu,
  type Nutrition,
  type Plan,
  type Settings,
  type Slot,
} from './menus';

function NutritionInputs({
  values,
  onChange,
  disabled,
}: {
  values: Nutrition;
  onChange: (value: Nutrition) => void;
  disabled: boolean;
}) {
  return (
    <div className="nutrition-fields">
      {nutrients.map(([key, label]) => (
        <label key={key}>
          {label}
          <input
            inputMode="decimal"
            required
            disabled={disabled}
            value={values[key]}
            onChange={(e) => onChange({ ...values, [key]: e.target.value })}
          />
        </label>
      ))}
    </div>
  );
}
function normalized(values: Nutrition): Nutrition {
  const result = { ...values };
  for (const [key] of nutrients) {
    result[key] = decimal(values[key]);
    if (!/^\d{1,30}(\.\d{1,20})?$/.test(result[key]))
      throw new Error('КБЖУ задаются неотрицательными числами. Используйте запятую или точку.');
  }
  return result;
}
function ModeInputs({
  values,
  setValues,
  disabled,
}: {
  values: Settings;
  setValues: (value: Settings) => void;
  disabled: boolean;
}) {
  return (
    <div className="menu-settings">
      <label>
        Режим повторов
        <select
          value={values.mode}
          disabled={disabled}
          onChange={(e) =>
            setValues({
              ...values,
              mode: e.target.value as Settings['mode'],
              repeat_limit: e.target.value === 'limited' ? 3 : null,
            })
          }
        >
          <option value="ab">A/B — 4 дня A, 3 дня B</option>
          <option value="limited">Ограничение повторов</option>
        </select>
      </label>
      {values.mode === 'limited' && (
        <label>
          Лимит на блюдо за неделю
          <input
            type="number"
            min="1"
            max={Number.MAX_SAFE_INTEGER}
            step="1"
            required
            value={values.repeat_limit ?? 3}
            disabled={disabled}
            onChange={(e) => setValues({ ...values, repeat_limit: Number(e.target.value) })}
          />
        </label>
      )}
    </div>
  );
}

function MenuEditor({
  menu,
  recipes,
  busy,
  update,
  act,
  back,
  week,
  setWeek,
}: {
  menu: Menu;
  recipes: Recipe[];
  busy: boolean;
  update: (menu: Menu) => void;
  act: (action: () => Promise<void>) => Promise<void>;
  back: () => void;
  week: number;
  setWeek: (week: number) => void;
}) {
  const initial = { mode: menu.mode, repeat_limit: menu.repeat_limit, targets: menu.targets };
  const [settings, setSettings] = useState<Settings>(initial);
  const [confirming, setConfirming] = useState(false);
  const dirty = JSON.stringify(settings) !== JSON.stringify(initial);
  const draft = menu.state === 'draft';
  const frozen = busy || dirty || !draft;
  const days = menu.totals.slice(week * 7, week * 7 + 7);
  async function mutate(path: string, body: unknown, method = 'POST') {
    update(await request<Menu>(`menu/revisions/${menu.id}/${path}`, method, body));
  }
  function pick(dayNumbers: number[], slot: Slot, version: string) {
    void act(() =>
      mutate(
        'entries',
        {
          expected_number: menu.number,
          positions: dayNumbers.map((day) => ({ day, slot, version_id: version || null })),
        },
        'PUT',
      ),
    );
  }
  function selection(dayNumbers: number[], slot: Slot) {
    const present = dayNumbers.map((day) =>
      menu.entries.find((e) => e.day === day && e.slot === slot),
    );
    const versions = new Set(present.map((e) => e?.version_id ?? ''));
    const selected = present[0];
    const mixed = versions.size > 1;
    return (
      <label key={slot}>
        {slots.find(([key]) => key === slot)?.[1]}
        <select
          value={mixed ? '__mixed' : (selected?.version_id ?? '')}
          disabled={frozen}
          onChange={(e) => pick(dayNumbers, slot, e.target.value)}
        >
          {mixed && (
            <option value="__mixed" disabled>
              Разные блюда — выберите общее
            </option>
          )}
          <option value="">{slot === 'first' ? 'Без первого блюда' : 'Выберите блюдо'}</option>
          {selected && !recipes.some((r) => r.version_id === selected.version_id) && (
            <option value={selected.version_id}>{selected.name} · сохранённая версия</option>
          )}
          {recipes.map((r) => (
            <option key={r.version_id} value={r.version_id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
    );
  }
  function leave() {
    if (
      !dirty ||
      window.confirm('Уйти без сохранения изменений параметров? Блюда черновика сохранятся.')
    )
      back();
  }
  return (
    <>
      <button className="text-button" disabled={busy} onClick={leave}>
        ← Все планы
      </button>
      <div className="title-row">
        <div>
          <p className="eyebrow">{draft ? 'Черновик' : 'Подтверждённое меню'}</p>
          <h1>{menu.days === 7 ? 'Меню на неделю' : 'Меню на четыре недели'}</h1>
          <p>{rangeLabel(menu.start_date, menu.days)}</p>
        </div>
      </div>
      {draft && (
        <p className="notice">
          Блюда сохраняются в черновике. Подтвердите меню, чтобы сохранить план.
        </p>
      )}
      <form
        className="menu-panel"
        onSubmit={(e) => {
          e.preventDefault();
          void act(async () => {
            await mutate(
              'settings',
              { ...settings, targets: normalized(settings.targets), expected_number: menu.number },
              'PUT',
            );
          });
        }}
      >
        <h2>Параметры и дневные цели этого плана</h2>
        <ModeInputs values={settings} setValues={setSettings} disabled={busy || !draft} />
        <NutritionInputs
          values={settings.targets}
          disabled={busy || !draft}
          onChange={(targets) => setSettings({ ...settings, targets })}
        />
        <p className="hint">
          Отклонения от целей не запрещают подтверждение. Каждое выбранное блюдо — одна порция.
        </p>
        {draft && (
          <div className="actions">
            <button className="secondary" disabled={busy || !dirty}>
              Сохранить параметры
            </button>
            {dirty && (
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() => setSettings(initial)}
              >
                Отменить правки параметров
              </button>
            )}
          </div>
        )}
      </form>
      {dirty && (
        <p className="notice">Сохраните параметры или отмените их правки, чтобы выбирать блюда.</p>
      )}
      <nav className="tabs" aria-label="Недели меню">
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
      <p className="hint">{rangeLabel(days[0].date, 7)} · Первое блюдо необязательно.</p>
      {menu.mode === 'ab' ? (
        <div className="menu-templates">
          {[0, 1].map((parity) => {
            const group = days.filter((d) => (d.day % 7) % 2 === parity);
            return (
              <section className="menu-panel" key={parity}>
                <h2>Меню {parity === 0 ? 'A' : 'B'}</h2>
                <p className="hint">{group.map((d) => dateLabel(d.date)).join(' · ')}</p>
                {slots.map(([slot]) =>
                  selection(
                    group.map((d) => d.day),
                    slot,
                  ),
                )}
              </section>
            );
          })}
        </div>
      ) : (
        <div className="menu-days">
          {days.map((day) => (
            <section className="menu-panel" key={day.day}>
              <h2>{dateLabel(day.date)}</h2>
              {slots.map(([slot]) => selection([day.day], slot))}
            </section>
          ))}
        </div>
      )}
      <section className="menu-panel">
        <h2>КБЖУ по дням</h2>
        <p className="hint">
          Фактическое меню и отклонение от дневных целей: «+» — превышение, «−» — недобор.
        </p>
        <div className="daily-totals">
          {days.map((day) => (
            <article key={day.day}>
              <h3>{dateLabel(day.date)}</h3>
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
            </article>
          ))}
        </div>
      </section>
      {draft && (
        <>
          {menu.problems.length > 0 && (
            <div className="notice">
              <p>Перед подтверждением:</p>
              <ul>
                {menu.problems.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="actions">
            <button
              className="primary"
              disabled={frozen || menu.problems.length > 0}
              onClick={() => setConfirming(true)}
            >
              Подтвердить меню
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    'Отменить весь черновик? Подтверждённый план останется без изменений.',
                  )
                )
                  void act(async () => {
                    await mutate('cancel', { expected_number: menu.number });
                    back();
                  });
              }}
            >
              Отменить черновик
            </button>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  if (dirty && !window.confirm('Перечитать меню и отменить правки параметров?'))
                    return;
                  update(await request<Menu>(`menu/revisions/${menu.id}`));
                })
              }
            >
              Перечитать меню
            </button>
          </div>
          {confirming && (
            <section className="menu-panel" role="region" aria-label="Подтверждение меню">
              <h2>Сохранить этот план?</h2>
              <p>
                {rangeLabel(menu.start_date, menu.days)} · {menu.entries.length} порций.
              </p>
              <p>Сохранится меню всех недель и его дневные цели.</p>
              <div className="actions">
                <button
                  className="primary"
                  disabled={busy || dirty || menu.problems.length > 0}
                  onClick={() =>
                    void act(() => mutate('confirm', { expected_number: menu.number }))
                  }
                >
                  Да, сохранить план
                </button>
                <button className="secondary" disabled={busy} onClick={() => setConfirming(false)}>
                  Продолжить редактирование
                </button>
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}

export function MenuPlanner({ active }: { active: boolean }) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [goals, setGoals] = useState<Nutrition | null>(null);
  const [goalEdit, setGoalEdit] = useState<Nutrition | null>(null);
  const [start, setStart] = useState('');
  const [days, setDays] = useState(7);
  const [creating, setCreating] = useState(false);
  const [settings, setSettings] = useState<Settings>({
    mode: 'ab',
    repeat_limit: null,
    targets: { calories: '0', protein: '0', fat: '0', carbs: '0' },
  });
  const [menu, setMenu] = useState<Menu | null>(null);
  const [editorKey, setEditorKey] = useState(0);
  const [week, setWeek] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    if (!active) return;
    let alive = true;
    void Promise.all([
      request<Plan[]>('menu/plans'),
      request<Recipe[]>('recipes'),
      request<Nutrition>('menu/goals'),
      request<{ start_date: string }>('menu/defaults'),
    ])
      .then(([p, r, g, defaults]) => {
        if (alive) {
          setPlans(p);
          setRecipes(r);
          setGoals(g);
          setStart((value) => value || defaults.start_date);
        }
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : 'Не удалось загрузить меню.');
      });
    return () => {
      alive = false;
    };
  }, [active]);
  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось выполнить действие.');
    } finally {
      setBusy(false);
    }
  }
  function update(value: Menu) {
    if (value.state === 'cancelled') {
      setNotice('Черновик отменён. Подтверждённое меню не изменилось.');
      setMenu(null);
      setWeek(0);
      void request<Plan[]>('menu/plans')
        .then(setPlans)
        .catch((e: unknown) => {
          setError(e instanceof Error ? e.message : 'Не удалось обновить планы.');
        });
      return;
    }
    if (value.state === 'confirmed') setNotice('Меню подтверждено и сохранено.');
    setMenu(value);
    setEditorKey((key) => key + 1);
  }
  function back() {
    setMenu(null);
    setWeek(0);
    void request<Plan[]>('menu/plans')
      .then(setPlans)
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Не удалось обновить планы.');
      });
  }
  async function open(id: string) {
    update(await request<Menu>(`menu/revisions/${id}`));
    setWeek(0);
  }
  async function create(e: FormEvent) {
    e.preventDefault();
    await act(async () => {
      update(
        await request<Menu>('menu/plans', 'POST', {
          ...settings,
          targets: normalized(settings.targets),
          start_date: start,
          days,
        }),
      );
      setCreating(false);
    });
  }
  return (
    <div className="menu-workspace">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {menu ? (
        <MenuEditor
          key={editorKey}
          menu={menu}
          recipes={recipes}
          busy={busy}
          update={update}
          act={act}
          back={back}
          week={week}
          setWeek={setWeek}
        />
      ) : (
        <>
          <div className="title-row">
            <div>
              <p className="eyebrow">План питания</p>
              <h1>Ваше меню</h1>
            </div>
            <button
              className="primary"
              disabled={busy || !goals || creating || !!goalEdit}
              onClick={() => {
                setCreating(true);
                setSettings({ mode: 'ab', repeat_limit: null, targets: goals! });
              }}
            >
              + Новый план
            </button>
          </div>
          {!goals && <p>Загружаем планы…</p>}
          <section className="menu-panel">
            <h2>Дневные цели для новых планов</h2>
            {goalEdit ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    const saved = await request<Nutrition>(
                      'menu/goals',
                      'PUT',
                      normalized(goalEdit),
                    );
                    setGoals(saved);
                    setGoalEdit(null);
                    setNotice('Цели сохранены для новых планов.');
                  });
                }}
              >
                <NutritionInputs values={goalEdit} onChange={setGoalEdit} disabled={busy} />
                <div className="actions">
                  <button className="primary" disabled={busy}>
                    Сохранить цели
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => setGoalEdit(null)}
                  >
                    Отмена
                  </button>
                </div>
              </form>
            ) : (
              goals && (
                <>
                  <p className="nutrition-summary">
                    {numberLabel(goals.calories)} ккал · Б {numberLabel(goals.protein)} · Ж{' '}
                    {numberLabel(goals.fat)} · У {numberLabel(goals.carbs)} г
                  </p>
                  <button
                    className="text-button"
                    disabled={busy || creating}
                    onClick={() => setGoalEdit(goals)}
                  >
                    Изменить цели
                  </button>
                </>
              )
            )}
            <p className="hint">Задайте цели вручную. Уже сохранённые планы сохраняют свои цели.</p>
          </section>
          {creating && (
            <form className="menu-panel" onSubmit={(e) => void create(e)}>
              <h2>Новый план</h2>
              <div className="menu-settings">
                <label>
                  Дата начала
                  <input
                    type="date"
                    required
                    value={start}
                    disabled={busy}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label>
                  Длительность
                  <select
                    value={days}
                    disabled={busy}
                    onChange={(e) => setDays(Number(e.target.value))}
                  >
                    <option value={7}>Неделя — 7 дней</option>
                    <option value={28}>Четыре недели — 28 дней</option>
                  </select>
                </label>
              </div>
              <ModeInputs values={settings} setValues={setSettings} disabled={busy} />
              <h3>Дневные цели этого плана</h3>
              <NutritionInputs
                values={settings.targets}
                disabled={busy}
                onChange={(targets) => setSettings({ ...settings, targets })}
              />
              <div className="actions">
                <button className="primary" disabled={busy}>
                  Создать черновик
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => setCreating(false)}
                >
                  Отмена
                </button>
              </div>
            </form>
          )}
          {!creating && goals && plans.length === 0 && (
            <section className="menu-panel">
              <h2>Пока нет планов</h2>
              <p>Создайте неделю или цикл и выберите блюда из библиотеки.</p>
            </section>
          )}
          {!creating && (
            <div className="plan-list">
              {plans.map((plan) => (
                <section className="menu-panel" key={plan.id}>
                  <h2>{plan.days === 7 ? 'Неделя' : 'Четыре недели'}</h2>
                  <p>{rangeLabel(plan.start_date, plan.days)}</p>
                  <div className="actions">
                    {plan.confirmed_id && (
                      <>
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() => void act(() => open(plan.confirmed_id!))}
                        >
                          Открыть сохранённое
                        </button>
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() =>
                            void act(async () => {
                              update(await request<Menu>(`menu/plans/${plan.id}/draft`, 'POST'));
                            })
                          }
                        >
                          Редактировать меню
                        </button>
                      </>
                    )}
                    {plan.drafts.map((id, i) => (
                      <button
                        className="primary"
                        key={id}
                        disabled={busy}
                        onClick={() => void act(() => open(id))}
                      >
                        Продолжить черновик{plan.drafts.length > 1 ? ` ${i + 1}` : ''}
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
          {error && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  setPlans(await request<Plan[]>('menu/plans'));
                  setRecipes(await request<Recipe[]>('recipes'));
                  setGoals(await request<Nutrition>('menu/goals'));
                  setStart((await request<{ start_date: string }>('menu/defaults')).start_date);
                })
              }
            >
              Попробовать снова
            </button>
          )}
        </>
      )}
    </div>
  );
}
