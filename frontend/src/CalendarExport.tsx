import { useState } from 'react';
import { telegramData } from './telegram';

export function CalendarExport({ planId }: { planId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function download() {
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`/api/menu/plans/${encodeURIComponent(planId)}/calendar.ics`, {
        headers: {
          'X-Ratatouille-Request': '1',
          ...(telegramData() ? { Authorization: `tma ${telegramData()}` } : {}),
        },
      });
      if (!response.ok) {
        const body = (await response.json()) as { detail?: unknown };
        throw new Error(
          typeof body.detail === 'string' ? body.detail : 'Не удалось экспортировать календарь.',
        );
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'ratatouille.ics';
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось скачать календарь.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="menu-panel">
      <h2>Календарь готовки</h2>
      <p>
        Экспортируются включённые события готовки и разморозки по Москве. Приёмы пищи не
        добавляются.
      </p>
      <button className="secondary" disabled={busy} onClick={() => void download()}>
        {busy ? 'Подготовка файла…' : 'Скачать ICS'}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <p className="hint">
        Создайте отдельный календарь для этого плана. Чтобы обновить его, удалите прежний календарь
        и импортируйте актуальный файл в новый: повторный импорт поверх старого зависит от
        календарного приложения и может создать дубликаты. Файл содержит названия ваших блюд.
      </p>
    </section>
  );
}
