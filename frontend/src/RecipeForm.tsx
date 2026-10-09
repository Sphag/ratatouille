import { useState, type FormEvent } from 'react';
import {
  blankRecipe,
  decimal,
  nutrients,
  units,
  type Recipe,
  type RecipeFields,
  type Unit,
} from './recipes';

interface Props {
  recipe?: Recipe;
  products: { ingredient_id: string; name: string }[];
  busy: boolean;
  error: string;
  onSave: (
    data: RecipeFields & { eligible: boolean; expected_version_id?: string },
  ) => Promise<void>;
  onCancel: () => void;
}
export function RecipeForm({ recipe, products, busy, error, onSave, onCancel }: Props) {
  const [data, setData] = useState<RecipeFields>(() => recipe ?? blankRecipe());
  // A changed recipe requires a fresh explicit admission, even if the previous version was eligible.
  const [eligible, setEligible] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [localError, setLocalError] = useState('');
  function change(next: RecipeFields) {
    setData(next);
    setDirty(true);
    setEligible(false);
  }
  function cancel() {
    if (!dirty || window.confirm('Отменить изменения рецепта?')) onCancel();
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = {
      name: data.name.trim(),
      instructions: data.instructions.trim(),
      yield_portions: Number(data.yield_portions),
      nutrition: Object.fromEntries(
        Object.entries(data.nutrition).map(([key, value]) => [key, decimal(value)]),
      ) as RecipeFields['nutrition'],
      ingredients: data.ingredients.map((item) => ({
        ...item,
        name: item.name.trim(),
        quantity: decimal(item.quantity),
      })),
      eligible,
      ...(recipe
        ? {
            expected_version_id: recipe.version_id,
            expected_eligible: recipe.eligible,
            expected_archived: recipe.archived,
          }
        : {}),
    };
    const number = /^\d{1,30}(\.\d{1,20})?$/;
    if (
      !normalized.name ||
      !Number.isSafeInteger(normalized.yield_portions) ||
      normalized.yield_portions < 1 ||
      normalized.yield_portions > 10000 ||
      Object.values(normalized.nutrition).some((value) => !number.test(value)) ||
      normalized.ingredients.some(
        (item) => !item.name || !number.test(item.quantity) || !/[1-9]/.test(item.quantity),
      )
    ) {
      setLocalError(
        'Заполните название, КБЖУ и ингредиенты. Выход — целое число порций, количества — больше нуля.',
      );
      return;
    }
    setLocalError('');
    await onSave(normalized);
  }
  return (
    <section className="editor" aria-labelledby="editor-title">
      <button type="button" className="text-button" onClick={cancel} disabled={busy}>
        ← К библиотеке
      </button>
      <h2 id="editor-title">{recipe ? 'Изменить рецепт' : 'Новый рецепт'}</h2>
      <form
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <fieldset disabled={busy}>
          <label>
            Название
            <input
              autoFocus
              required
              maxLength={200}
              value={data.name}
              onChange={(event) => change({ ...data, name: event.target.value })}
            />
          </label>
          <label>
            Выход рецепта, порций
            <input
              type="number"
              required
              min="1"
              max="10000"
              step="1"
              value={data.yield_portions || ''}
              onChange={(event) => change({ ...data, yield_portions: Number(event.target.value) })}
            />
          </label>
          <h3>КБЖУ на одну порцию</h3>
          <p className="hint">Введите значения вручную. По ингредиентам они не рассчитываются.</p>
          <div className="nutrition-fields">
            {nutrients.map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  required
                  inputMode="decimal"
                  maxLength={51}
                  value={data.nutrition[key]}
                  onChange={(event) =>
                    change({ ...data, nutrition: { ...data.nutrition, [key]: event.target.value } })
                  }
                />
              </label>
            ))}
          </div>
          <h3>Ингредиенты на весь выход рецепта</h3>
          <p className="hint">Например, для двух порций укажите продукты на обе порции.</p>
          <datalist id="products">
            {products.map((item) => (
              <option key={item.ingredient_id} value={item.name} />
            ))}
          </datalist>
          {data.ingredients.map((item, index) => (
            <div className="ingredient-row" key={index}>
              <label>
                Продукт {index + 1}
                <input
                  list="products"
                  required
                  maxLength={200}
                  value={item.name}
                  onChange={(event) =>
                    change({
                      ...data,
                      ingredients: data.ingredients.map((value, i) =>
                        i === index
                          ? {
                              ...value,
                              name: event.target.value,
                              ingredient_id: products.find((p) => p.name === event.target.value)
                                ?.ingredient_id,
                            }
                          : value,
                      ),
                    })
                  }
                />
              </label>
              <label>
                Количество {index + 1}
                <input
                  required
                  inputMode="decimal"
                  maxLength={51}
                  value={item.quantity}
                  onChange={(event) =>
                    change({
                      ...data,
                      ingredients: data.ingredients.map((value, i) =>
                        i === index ? { ...value, quantity: event.target.value } : value,
                      ),
                    })
                  }
                />
              </label>
              <label>
                Единица {index + 1}
                <select
                  value={item.unit}
                  onChange={(event) =>
                    change({
                      ...data,
                      ingredients: data.ingredients.map((value, i) =>
                        i === index ? { ...value, unit: event.target.value as Unit } : value,
                      ),
                    })
                  }
                >
                  {Object.entries(units).map(([key, label]) => (
                    <option value={key} key={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="remove-button"
                aria-label={`Удалить продукт ${index + 1}`}
                disabled={data.ingredients.length === 1}
                onClick={() =>
                  change({ ...data, ingredients: data.ingredients.filter((_, i) => i !== index) })
                }
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className="secondary"
            disabled={data.ingredients.length >= 200}
            onClick={() =>
              change({
                ...data,
                ingredients: [...data.ingredients, { name: '', quantity: '', unit: 'g' }],
              })
            }
          >
            + Добавить ингредиент
          </button>
          <label className="instructions">
            Приготовление
            <textarea
              rows={5}
              maxLength={10000}
              value={data.instructions}
              onChange={(event) => change({ ...data, instructions: event.target.value })}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={eligible}
              onChange={(event) => {
                setEligible(event.target.checked);
                setDirty(true);
              }}
            />
            Подтверждаю допустимость этого рецепта для автоматического подбора
          </label>
          {recipe?.eligible && (
            <p className="hint">После изменения состава подтвердите допуск заново.</p>
          )}
        </fieldset>
        {(localError || error) && (
          <p role="alert" className="error">
            {localError || error}
          </p>
        )}
        <div className="actions">
          <button className="primary" disabled={busy}>
            {busy ? 'Сохраняем…' : 'Сохранить рецепт'}
          </button>
          <button type="button" className="secondary" onClick={cancel} disabled={busy}>
            Отмена
          </button>
        </div>
      </form>
    </section>
  );
}
