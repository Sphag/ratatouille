import type { RecipeFields } from './recipes';

export type Nutrition = RecipeFields['nutrition'];
export type Mode = 'ab' | 'limited';
export type Slot = 'breakfast' | 'first' | 'second' | 'dinner';
export const slots: [Slot, string][] = [
  ['breakfast', 'Завтрак'],
  ['first', 'Первое (необязательно)'],
  ['second', 'Второе'],
  ['dinner', 'Ужин'],
];
export interface Settings {
  mode: Mode;
  repeat_limit: number | null;
  targets: Nutrition;
}
export interface Menu extends Settings {
  id: string;
  plan_id: string;
  start_date: string;
  days: number;
  state: 'draft' | 'confirmed' | 'cancelled';
  number: number;
  entries: { day: number; slot: Slot; version_id: string; recipe_id: string; name: string }[];
  totals: { day: number; date: string; total: Nutrition; difference: Nutrition }[];
  problems: string[];
}
export interface Plan {
  id: string;
  start_date: string;
  days: number;
  confirmed_id: string | null;
  drafts: string[];
}
export interface Proposal {
  menu: Menu;
  positions: { day: number; slot: Slot; version_id: string }[];
  digest: string;
}
export function dateLabel(value: string): string {
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'short',
    weekday: 'short',
    timeZone: 'UTC',
  }).format(new Date(`${value}T00:00:00Z`));
}
export function rangeLabel(start: string, days: number): string {
  const end = new Date(`${start}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + days - 1);
  const endDate = end.toISOString().slice(0, 10);
  const startYear = start.slice(0, 4);
  const endYear = endDate.slice(0, 4);
  return `${dateLabel(start)}${startYear === endYear ? '' : ` ${startYear}`} — ${dateLabel(endDate)} ${endYear}`;
}
export function numberLabel(value: string, signed = false): string {
  const [whole, fraction = ''] = value.split('.');
  const trimmed = fraction.replace(/0+$/, '');
  const result = whole + (trimmed ? `,${trimmed}` : '');
  return signed && !result.startsWith('-') && /[1-9]/.test(result) ? `+${result}` : result;
}
