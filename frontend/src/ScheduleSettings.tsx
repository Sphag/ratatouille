import { useEffect, useState } from 'react';
import { request } from './recipes';

type Kind = 'cooking' | 'thawing';
interface Item {
  kind: Kind;
  weekday: number;
  at: string;
  enabled: boolean;
}
interface View {
  items: Item[];
  digest: string;
  timezone: string;
}
const days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

export function ScheduleSettings({
  active,
  onConfigured,
}: {
  active: boolean;
  onConfigured: (configured: boolean) => void;
}) {
  const [view, setView] = useState<View | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  async function load() {
    setBusy(true);
    setError('');
    try {
      const value = await request<View>('menu/schedule');
      setView(value);
      setItems(value.items);
      onConfigured(value.items.length > 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось загрузить расписание.');
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    let current = true;
    void request<View>('menu/schedule')
      .then((value) => {
        if (!current) return;
        setView(value);
        setItems(value.items);
        onConfigured(value.items.length > 0);
      })
      .catch((reason: unknown) => {
        if (current)
          setError(reason instanceof Error ? reason.message : 'Не удалось загрузить расписание.');
      });
    return () => {
      current = false;
    };
  }, [onConfigured]);
  function change(index: number, field: Partial<Item>) {
    setItems((current) => current.map((item, i) => (i === index ? { ...item, ...field } : item)));
    setNotice('');
  }
  async function save() {
    if (!view) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const next = await request<View>('menu/schedule', 'PUT', {
        items,
        expected_digest: view.digest,
      });
      setView(next);
      setItems(next.items);
      onConfigured(next.items.length > 0);
      setNotice('Расписание сохранено.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось сохранить расписание.');
    } finally {
      setBusy(false);
    }
  }
  if (!active) return null;
  return (
    <section className="menu-workspace">
      <h1>Напоминания</h1>
      <p>
        Дни и время — по Москве (Europe/Moscow). До сохранения настроек напоминания выключены. Они
        относятся только к подтверждённым планам.
      </p>
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
      {!view && !error && <p role="status">Загрузка расписания…</p>}
      <button className="secondary" disabled={busy} onClick={() => void load()}>
        Перечитать расписание
      </button>
      {view && (
        <>
          <fieldset disabled={busy}>
            {(['cooking', 'thawing'] as const).map((kind) => (
              <section className="menu-panel" key={kind}>
                <h2>{kind === 'cooking' ? 'Готовка' : 'Разморозка'}</h2>
                {items.map((item, index) =>
                  item.kind === kind ? (
                    <div key={index}>
                      <label>
                        День недели
                        <select
                          value={item.weekday}
                          onChange={(e) => change(index, { weekday: Number(e.target.value) })}
                        >
                          {days.map((day, i) => (
                            <option value={i} key={i}>
                              {day}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Время
                        <input
                          type="time"
                          required
                          value={item.at}
                          onChange={(e) => change(index, { at: e.target.value })}
                        />
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={item.enabled}
                          onChange={(e) => change(index, { enabled: e.target.checked })}
                        />
                        Напоминание включено
                      </label>
                      <button
                        className="secondary"
                        onClick={() => setItems((current) => current.filter((_, i) => i !== index))}
                      >
                        Удалить время
                      </button>
                    </div>
                  ) : null,
                )}
                <button
                  className="secondary"
                  disabled={items.filter((i) => i.kind === kind).length >= 7}
                  onClick={() => {
                    const weekday = days.findIndex(
                      (_, i) => !items.some((item) => item.kind === kind && item.weekday === i),
                    );
                    if (weekday >= 0)
                      setItems((current) => [
                        ...current,
                        { kind, weekday, at: '18:00', enabled: false },
                      ]);
                  }}
                >
                  Добавить время {kind === 'cooking' ? 'готовки' : 'разморозки'}
                </button>
              </section>
            ))}
          </fieldset>
          <button className="primary" disabled={busy} onClick={() => void save()}>
            Сохранить расписание
          </button>
        </>
      )}
      <p className="hint">
        Разморозка — общий сигнал проверить продукты вручную. Время и правила хранения автоматически
        не вычисляются. При неопределённом результате отправки сообщение не повторяется, чтобы
        избежать дубликата.
      </p>
    </section>
  );
}
