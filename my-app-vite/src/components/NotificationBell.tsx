import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchClientActivityEvents, type ActivityEvent } from '../api';
import DateTimeCompact from './DateTimeCompact';
import DateRangeCompact from './DateRangeCompact';
import { formatSourceTextForDisplay } from '../utils/sourceCodeDisplay';

type Props = {
  onOpenFullHistory: () => void;
};

function NotificationBell({ onOpenFullHistory }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<ActivityEvent[]>([]);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const hasItems = items.length > 0;
  const subtitle = useMemo(() => {
    if (loading) return 'Загрузка…';
    if (error) return error;
    return hasItems ? `Последние действия: ${items.length}` : 'Действий пока нет';
  }, [loading, error, hasItems, items.length]);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (rootRef.current && !rootRef.current.contains(target)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  async function loadLatest(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchClientActivityEvents({ offset: 0, limit: 20 });
      setItems(resp.items);
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'message' in e && typeof (e as { message?: unknown }).message === 'string') {
        setError((e as { message: string }).message);
      } else {
        setError('Не удалось загрузить историю');
      }
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  const handleToggle = () => {
    setOpen((prev) => {
      const next = !prev;
      if (next) {
        void loadLatest();
      }
      return next;
    });
  };

  const entityLabel = (entity: ActivityEvent['entity']) =>
    entity === 'project'
      ? 'Проект'
      : entity === 'balance'
      ? 'Баланс'
      : entity === 'report'
      ? 'Отчёт'
      : 'ЧС';

  return (
    <div className="notif" ref={rootRef}>
      <button
        type="button"
        className={`icon-btn notif__btn${open ? ' notif__btn--open' : ''}`}
        title="История действий"
        aria-label="История действий"
        onClick={handleToggle}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5" />
          <path d="M10 17a2 2 0 0 0 4 0" />
        </svg>
      </button>
      {open && (
        <>
          <button
            type="button"
            className="notif__backdrop"
            aria-label="Закрыть историю действий"
            onClick={() => setOpen(false)}
          />
          <div className="notif__menu">
            <div className="notif__head">
              <div style={{ fontWeight: 600 }}>История действий</div>
              <div className="sub">{subtitle}</div>
            </div>
            <div className="notif__list">
              {!loading && !error && !hasItems && (
                <div className="sub" style={{ padding: 10 }}>
                  Действий пока нет.
                </div>
              )}
              {items.map((item) => (
                <div key={item.eventId} className="notif__item">
                  <div className="notif__meta">
                    <span className="sub">{item.eventId}</span>
                    <span className={item.outcome === 'failed' ? 'badge badge--orange' : 'badge badge--green'} style={{ fontWeight: 500 }}>
                      {item.outcome === 'failed' ? 'Ошибка' : 'Успешно'}
                    </span>
                    <span className="badge badge--gray" style={{ fontWeight: 500 }}>
                      {entityLabel(item.entity)}
                    </span>
                  </div>
                  <div style={{ marginTop: 4 }}>
                    <DateTimeCompact value={item.createdAt} />
                  </div>
                  <div style={{ marginTop: 4 }}>{formatSourceTextForDisplay(item.description)}</div>
                  {item.entity === 'report' && item.periodFrom && item.periodTo && (
                    <div className="sub" style={{ marginTop: 4 }}>
                      Период: <DateRangeCompact from={item.periodFrom} to={item.periodTo} />
                    </div>
                  )}
                  <div className="sub" style={{ marginTop: 4 }}>
                    {item.actor ? `${item.actor.login} (id: ${item.actor.id})` : 'Система'}
                  </div>
                </div>
              ))}
            </div>
            <div className="notif__footer">
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => {
                  setOpen(false);
                  onOpenFullHistory();
                }}
              >
                Показать всю историю
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default NotificationBell;
