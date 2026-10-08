import { useEffect, useState } from 'react';
import icon from '../../assets/icon.png';
import { RecipeForm } from './RecipeForm';
import {
  ApiError,
  request,
  units,
  type Recipe,
  type RecipeFields,
  type StarterPreview,
} from './recipes';

type Tab = 'active' | 'archive' | 'starter';
type Editor = { recipe?: Recipe } | null;
interface Data {
  active: Recipe[];
  archive: Recipe[];
  products: { ingredient_id: string; name: string }[];
  starter: StarterPreview;
}
async function load(): Promise<Data> {
  const [active, archive, products, starter] = await Promise.all([
    request<Recipe[]>('recipes'),
    request<Recipe[]>('recipes?archived=true'),
    request<Data['products']>('ingredients'),
    request<StarterPreview>('starter-library'),
  ]);
  return { active, archive, products, starter };
}
function Summary({ recipe }: { recipe: RecipeFields }) {
  return (
    <p className="nutrition-summary">
      {recipe.nutrition.calories} ккал{' '}
      <span>
        Б {recipe.nutrition.protein} · Ж {recipe.nutrition.fat} · У {recipe.nutrition.carbs} г
      </span>
    </p>
  );
}
function Details({ recipe }: { recipe: RecipeFields }) {
  return (
    <div className="recipe-details">
      <Summary recipe={recipe} />
      <p className="hint">КБЖУ на порцию · Выход: {recipe.yield_portions} порц.</p>
      <h3>Ингредиенты</h3>
      <ul>
        {recipe.ingredients.map((item, i) => (
          <li key={i}>
            <span>{item.name}</span>
            <strong>
              {item.quantity} {units[item.unit]}
            </strong>
          </li>
        ))}
      </ul>
      <h3>Приготовление</h3>
      <p className="instruction-text">{recipe.instructions || 'Инструкция пока не добавлена.'}</p>
    </div>
  );
}
export function App() {
  const [data, setData] = useState<Data | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [tab, setTab] = useState<Tab>('active');
  const [editor, setEditor] = useState<Editor>(null);
  const [selected, setSelected] = useState<Recipe | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    let active = true;
    void request<{ recipes: boolean }>('capabilities')
      .then(async (capabilities) => {
        if (!active) return;
        if (!capabilities.recipes) {
          setUnavailable(true);
          return;
        }
        const result = await load();
        if (active) setData(result);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 404) setUnavailable(true);
        else
          setError(reason instanceof Error ? reason.message : 'Не удалось загрузить библиотеку.');
      });
    return () => {
      active = false;
    };
  }, []);
  async function reload() {
    setError('');
    try {
      setData(await load());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось обновить библиотеку.');
    }
  }
  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось выполнить действие.');
    } finally {
      setBusy(false);
    }
  }
  async function save(values: RecipeFields & { eligible: boolean; expected_version_id?: string }) {
    await act(async () => {
      const id = editor?.recipe?.recipe_id;
      const recipe = await request<Recipe>(
        id ? `recipes/${id}` : 'recipes',
        id ? 'PUT' : 'POST',
        values,
      );
      // Reflect a successful mutation even if the subsequent list refresh is interrupted.
      setData(
        (current) =>
          current && {
            ...current,
            active: [
              ...current.active.filter((item) => item.recipe_id !== recipe.recipe_id),
              recipe,
            ],
          },
      );
      setEditor(null);
      setSelected(recipe);
      setTab('active');
      setNotice('Рецепт сохранён.');
      setData(await load());
    });
  }
  async function flags(recipe: Recipe, eligible: boolean, archived: boolean) {
    await act(async () => {
      const updated = await request<Recipe>(`recipes/${recipe.recipe_id}/flags`, 'PATCH', {
        expected_version_id: recipe.version_id,
        expected_eligible: recipe.eligible,
        expected_archived: recipe.archived,
        eligible,
        archived,
      });
      setData(
        (current) =>
          current && {
            ...current,
            active: [
              ...current.active.filter((item) => item.recipe_id !== recipe.recipe_id),
              ...(archived ? [] : [updated]),
            ],
            archive: [
              ...current.archive.filter((item) => item.recipe_id !== recipe.recipe_id),
              ...(archived ? [updated] : []),
            ],
          },
      );
      setSelected(null);
      setNotice(
        archived ? 'Рецепт в архиве. Сохранённые планы не изменились.' : 'Изменения сохранены.',
      );
      setData(await load());
    });
  }
  if (unavailable)
    return (
      <main className="welcome">
        <img className="welcome__icon" src={icon} alt="Крыса-повар Ratatouille" />
        <p className="welcome__eyebrow">Готовим неделю заранее</p>
        <h1>Ratatouille</h1>
        <p className="welcome__description">Меню, покупки и готовка на неделю — в Telegram.</p>
        <p className="welcome__status">Приложение готовится к запуску.</p>
      </main>
    );
  return (
    <main className="library">
      <header className="brand">
        <img src={icon} alt="" />
        <div>
          <p>Ratatouille</p>
          <span>Готовим неделю заранее</span>
        </div>
      </header>
      {!data ? (
        <section aria-live="polite">
          <h1>Библиотека рецептов</h1>
          {error ? (
            <>
              <p role="alert" className="error">
                {error}
              </p>
              <button
                className="secondary"
                onClick={() => {
                  void reload();
                }}
              >
                Попробовать снова
              </button>
            </>
          ) : (
            <p>Загружаем рецепты…</p>
          )}
        </section>
      ) : editor ? (
        <RecipeForm
          key={editor.recipe?.version_id ?? 'new'}
          recipe={editor.recipe}
          products={data.products}
          busy={busy}
          error={error}
          onSave={save}
          onCancel={() => {
            setEditor(null);
            setError('');
          }}
        />
      ) : (
        <>
          <div className="title-row">
            <div>
              <p className="eyebrow">Ваши блюда</p>
              <h1>Библиотека рецептов</h1>
            </div>
            <button
              className="primary"
              disabled={busy}
              onClick={() => {
                setEditor({});
                setSelected(null);
                setError('');
              }}
            >
              + Новый рецепт
            </button>
          </div>
          <nav className="tabs" aria-label="Разделы библиотеки">
            {(
              [
                ['active', 'Рецепты'],
                ['archive', 'Архив'],
                ['starter', 'Стартовые 18'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                className={tab === value ? 'tab active' : 'tab'}
                aria-current={tab === value ? 'page' : undefined}
                disabled={busy}
                onClick={() => {
                  setTab(value);
                  setSelected(null);
                  setError('');
                }}
              >
                {label}
              </button>
            ))}
          </nav>
          {error && (
            <p role="alert" className="error">
              {error}{' '}
              <button
                className="text-button"
                disabled={busy}
                onClick={() => {
                  setSelected(null);
                  void reload();
                }}
              >
                Обновить список
              </button>
            </p>
          )}
          {notice && (
            <p className="notice" role="status">
              {notice}
            </p>
          )}
          {tab === 'starter' ? (
            <section>
              <h2>Стартовая библиотека</h2>
              <p className="intro">
                18 рецептов с составом и КБЖУ на одну порцию. Проверьте карточки перед добавлением.
                Допуск к автоматическому подбору для каждого блюда подтверждается отдельно.
              </p>
              <p className="hint">
                Крупы — в сухом виде, мясо и рыба — до приготовления. Вода и специи без количества в
                покупки не входят. Некоторые инструкции в источнике неполные: их можно дополнить
                после импорта.
              </p>
              {data.starter.imported ? (
                <p className="notice">
                  Библиотека уже добавлена. Повторный импорт не создаёт копии.
                </p>
              ) : (
                <div className="import-panel">
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={confirmed}
                      disabled={busy}
                      onChange={(event) => setConfirmed(event.target.checked)}
                    />
                    Я проверил состав и КБЖУ всех 18 карточек
                  </label>
                  <button
                    className="primary"
                    disabled={busy || !confirmed || !data.starter.library.approved}
                    onClick={() => {
                      void act(async () => {
                        const result = await request<{ imported: number }>(
                          'starter-library/import',
                          'POST',
                          { confirmed, digest: data.starter.digest },
                        );
                        setData(await load());
                        setNotice(`Добавлено рецептов: ${result.imported}.`);
                        setTab('active');
                      });
                    }}
                  >
                    {busy ? 'Добавляем…' : 'Добавить 18 рецептов'}
                  </button>
                  {!data.starter.library.approved && (
                    <p className="hint">Карточки ещё ожидают проверки владельцем проекта.</p>
                  )}
                </div>
              )}
              <div className="starter-list">
                {data.starter.library.recipes.map((recipe) => (
                  <details key={recipe.source_id}>
                    <summary>
                      {recipe.name}
                      <Summary recipe={recipe} />
                    </summary>
                    <Details recipe={recipe} />
                  </details>
                ))}
              </div>
            </section>
          ) : selected ? (
            <section className="detail-panel">
              <button className="text-button" onClick={() => setSelected(null)}>
                ← К списку
              </button>
              <h2>{selected.name}</h2>
              <Details recipe={selected} />
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={selected.eligible}
                  disabled={busy || selected.archived}
                  onChange={(event) => {
                    void flags(selected, event.target.checked, selected.archived);
                  }}
                />
                Допускаю для автоматического подбора
              </label>
              <div className="actions">
                {selected.archived ? (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => {
                      void flags(selected, selected.eligible, false);
                    }}
                  >
                    Восстановить рецепт
                  </button>
                ) : (
                  <>
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() => {
                        setEditor({ recipe: selected });
                        setError('');
                      }}
                    >
                      Изменить рецепт
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(
                            'Убрать рецепт в архив? Сохранённые планы останутся прежними.',
                          )
                        )
                          void flags(selected, selected.eligible, true);
                      }}
                    >
                      В архив
                    </button>
                  </>
                )}
              </div>
            </section>
          ) : (
            <>
              <p className="hint">
                {tab === 'archive'
                  ? 'Архивные рецепты скрыты из подбора. Их можно восстановить; сохранённые планы остаются прежними.'
                  : 'Для подбора используются только рецепты с вашим явным допуском.'}
              </p>
              {(tab === 'archive' ? data.archive : data.active).length === 0 ? (
                <div className="empty">
                  <h2>{tab === 'archive' ? 'Архив пуст' : 'Начните с любимых блюд'}</h2>
                  <p>
                    {tab === 'archive'
                      ? 'Здесь появятся рецепты, которые вы уберёте из библиотеки.'
                      : 'Добавьте свой рецепт или проверьте стартовую библиотеку из 18 блюд.'}
                  </p>
                </div>
              ) : (
                <div className="recipe-grid">
                  {(tab === 'archive' ? data.archive : data.active).map((recipe) => (
                    <button
                      className="recipe-card"
                      key={recipe.recipe_id}
                      disabled={busy}
                      onClick={() => {
                        setSelected(recipe);
                        setError('');
                      }}
                    >
                      <span className="badge">
                        {recipe.archived
                          ? 'В архиве'
                          : recipe.eligible
                            ? 'Допущен к подбору'
                            : 'Допуск не подтверждён'}
                      </span>
                      <h2>{recipe.name}</h2>
                      <Summary recipe={recipe} />
                      <span className="hint">
                        Ингредиенты: {recipe.ingredients.length} · {recipe.yield_portions} порц.
                      </span>
                      <span className="card-link">Открыть рецепт →</span>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </main>
  );
}
