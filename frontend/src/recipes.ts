export type Unit = 'g' | 'kg' | 'ml' | 'l' | 'piece';
export const units: Record<Unit, string> = { g: 'г', kg: 'кг', ml: 'мл', l: 'л', piece: 'шт.' };
export const nutrients = [
  ['calories', 'Калории, ккал'],
  ['protein', 'Белки, г'],
  ['fat', 'Жиры, г'],
  ['carbs', 'Углеводы, г'],
] as const;
export interface Ingredient {
  ingredient_id?: string | null;
  name: string;
  quantity: string;
  unit: Unit;
}
export interface RecipeFields {
  name: string;
  instructions: string;
  yield_portions: number;
  nutrition: Record<(typeof nutrients)[number][0], string>;
  ingredients: Ingredient[];
}
export interface Recipe extends RecipeFields {
  recipe_id: string;
  version_id: string;
  eligible: boolean;
  archived: boolean;
}
export interface StarterPreview {
  digest: string;
  imported: boolean;
  library: { id: string; approved: boolean; recipes: (RecipeFields & { source_id: string })[] };
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Ratatouille-Request': '1' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error('Не удалось связаться с сервером. Проверьте соединение и попробуйте снова.');
  }
  let value: T & { detail?: unknown };
  try {
    value = (await response.json()) as T & { detail?: unknown };
  } catch {
    throw new Error('Сервер вернул некорректный ответ. Попробуйте снова.');
  }
  if (!response.ok) {
    throw new ApiError(
      typeof value.detail === 'string' ? value.detail : 'Не удалось выполнить действие.',
      response.status,
    );
  }
  return value;
}
export const blankRecipe = (): RecipeFields => ({
  name: '',
  instructions: '',
  yield_portions: 1,
  nutrition: { calories: '', protein: '', fat: '', carbs: '' },
  ingredients: [{ name: '', quantity: '', unit: 'g' }],
});
export function decimal(value: string): string {
  return value.trim().replace(',', '.');
}
