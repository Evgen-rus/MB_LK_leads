import { useCallback, useEffect, useMemo, useState } from 'react';
import DateTimeCompact from './DateTimeCompact';
import {
  type ClientTariff,
  type ClientTariffList,
  type ClientTariffOperation,
  type ClientTariffOperationList,
} from '../api';
import { preventNumberInputWheel } from '../utils/numberInput';

type TariffEditorState =
  | { mode: 'create' }
  | { mode: 'credit' | 'debit'; tariff: ClientTariff }
  | null;

type TariffManagerModalProps = {
  targetId: number;
  title: string;
  onClose: () => void;
  onChanged?: () => void | Promise<void>;
  readOnly?: boolean;
  fetchTariffs: (targetId: number, params?: { offset?: number; limit?: number }) => Promise<ClientTariffList>;
  createTariff: (targetId: number, payload: { amount: number; comment?: string }) => Promise<ClientTariff>;
  fetchTariffOps: (tariffId: number, params?: { offset?: number; limit?: number }) => Promise<ClientTariffOperationList>;
  createTariffOp: (tariffId: number, payload: { amount: number; type: 'credit' | 'debit'; comment: string }) => Promise<ClientTariffOperation>;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function TariffManagerModal({
  targetId,
  title,
  onClose,
  onChanged,
  readOnly = false,
  fetchTariffs,
  createTariff,
  fetchTariffOps,
  createTariffOp,
}: TariffManagerModalProps) {
  const [tariffs, setTariffs] = useState<ClientTariff[]>([]);
  const [totalTariffs, setTotalTariffs] = useState(0);
  const [selectedTariffId, setSelectedTariffId] = useState<number | null>(null);
  const [tariffOps, setTariffOps] = useState<ClientTariffOperation[]>([]);
  const [totalTariffOps, setTotalTariffOps] = useState(0);
  const [tariffOpsPage, setTariffOpsPage] = useState(1);
  const [tariffOpsPageSize, setTariffOpsPageSize] = useState(10);
  const [loadingTariffs, setLoadingTariffs] = useState(false);
  const [loadingTariffOps, setLoadingTariffOps] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<TariffEditorState>(null);
  const [amount, setAmount] = useState<number>(0);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const selectedTariff = useMemo(
    () => (selectedTariffId != null ? tariffs.find((item) => item.id === selectedTariffId) ?? null : null),
    [selectedTariffId, tariffs],
  );

  const totalTariffOpsPages = Math.max(1, Math.ceil(totalTariffOps / tariffOpsPageSize));

  const resetEditor = useCallback(() => {
    setEditor(null);
    setAmount(0);
    setComment('');
  }, []);

  const loadTariffs = useCallback(async (preferredTariffId?: number | null) => {
    try {
      setLoadingTariffs(true);
      setError(null);
      const resp = await fetchTariffs(targetId, { offset: 0, limit: 100 });
      setTariffs(resp.items);
      setTotalTariffs(resp.total);
      const nextTariffId = preferredTariffId != null && resp.items.some((item) => item.id === preferredTariffId)
        ? preferredTariffId
        : (resp.items[0]?.id ?? null);
      setSelectedTariffId(nextTariffId);
      return nextTariffId;
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить тарифы'));
      setTariffs([]);
      setTotalTariffs(0);
      setSelectedTariffId(null);
      return null;
    } finally {
      setLoadingTariffs(false);
    }
  }, [fetchTariffs, targetId]);

  const loadTariffOps = useCallback(async (tariffId: number, page: number, pageSize = tariffOpsPageSize) => {
    try {
      setLoadingTariffOps(true);
      setError(null);
      const offset = (page - 1) * pageSize;
      const resp = await fetchTariffOps(tariffId, { offset, limit: pageSize });
      setTariffOps(resp.items);
      setTotalTariffOps(resp.total);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить историю тарифа'));
      setTariffOps([]);
      setTotalTariffOps(0);
    } finally {
      setLoadingTariffOps(false);
    }
  }, [fetchTariffOps, tariffOpsPageSize]);

  useEffect(() => {
    setTariffOpsPage(1);
    void loadTariffs();
  }, [loadTariffs]);

  useEffect(() => {
    if (selectedTariffId == null) {
      setTariffOps([]);
      setTotalTariffOps(0);
      return;
    }
    void loadTariffOps(selectedTariffId, tariffOpsPage, tariffOpsPageSize);
  }, [selectedTariffId, tariffOpsPage, tariffOpsPageSize, loadTariffOps]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!editor) return;
    if (amount <= 0) {
      setError('Укажите положительное число');
      return;
    }
    const isCreate = editor.mode === 'create';
    if (!isCreate && !comment.trim()) {
      setError('Комментарий обязателен');
      return;
    }
    try {
      setSubmitting(true);
      setError(null);
      let nextTariffId: number | null = null;
      if (isCreate) {
        const tariff = await createTariff(targetId, { amount, comment: comment.trim() || undefined });
        nextTariffId = tariff.id;
      } else {
        await createTariffOp(editor.tariff.id, {
          amount,
          type: editor.mode,
          comment: comment.trim(),
        });
        nextTariffId = editor.tariff.id;
      }
      const actualTariffId = await loadTariffs(nextTariffId);
      setTariffOpsPage(1);
      if (actualTariffId != null) {
        await loadTariffOps(actualTariffId, 1, tariffOpsPageSize);
      }
      resetEditor();
      await onChanged?.();
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось сохранить изменение тарифа'));
    } finally {
      setSubmitting(false);
    }
  }

  const editorTitle = !editor
    ? null
    : editor.mode === 'create'
      ? 'Создание тарифа'
      : editor.mode === 'credit'
        ? `Добавление к тарифу #${editor.tariff.id}`
        : `Списание из тарифа #${editor.tariff.id}`;

  return (
    <div className="modal-backdrop">
      <div className="modal" style={{ maxWidth: 1120, width: 'min(1120px, calc(100vw - 32px))', borderRadius: 12, padding: 0, overflow: 'hidden' }}>
        <div className="modal__header" style={{ padding: '14px 16px', borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <div style={{ fontWeight: 600 }}>{title}</div>
            <div className="sub">
              {readOnly ? 'Режим просмотра без изменений.' : 'Тарифные операции автоматически меняют баланс.'}
            </div>
          </div>
          <button type="button" className="btn btn--ghost" onClick={onClose}>Закрыть</button>
        </div>

        <div className="modal__body" style={{ display: 'grid', gap: 16, padding: 16, maxHeight: '80vh', overflow: 'auto' }}>
          {error && <div className="sub" style={{ color: '#d00' }}>{error}</div>}

          {!readOnly && editor && (
            <form onSubmit={handleSubmit} className="table-card" style={{ display: 'grid', gap: 12, padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{editorTitle}</div>
                  {editor.mode !== 'create' && (
                    <div className="sub">Текущее значение тарифа: {editor.tariff.currentAmount}</div>
                  )}
                </div>
                <button type="button" className="btn btn--ghost" onClick={resetEditor}>Скрыть форму</button>
              </div>

              <label style={{ display: 'grid', gap: 6 }}>
                <span className="sub">{editor.mode === 'create' ? 'Размер тарифа' : 'Количество'}</span>
                <input
                  type="number"
                  min={1}
                  value={amount}
                  onChange={(e) => setAmount(Number(e.target.value))}
                  onWheel={preventNumberInputWheel}
                  required
                />
              </label>

              <label style={{ display: 'grid', gap: 6 }}>
                <span className="sub">{editor.mode === 'create' ? 'Комментарий (необязательно)' : 'Комментарий (обязательно)'}</span>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  rows={3}
                  required={editor.mode !== 'create'}
                />
              </label>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button type="button" className="btn btn--ghost" onClick={resetEditor}>Отмена</button>
                <button type="submit" className="btn btn--primary" disabled={submitting}>
                  {submitting ? 'Сохранение…' : 'Сохранить'}
                </button>
              </div>
            </form>
          )}

          <div className="table-card" style={{ minWidth: 940, padding: '0 8px 8px' }}>
            <div className="table-toolbar">
              <div className="filters">
                <span className="sub">Тарифы</span>
              </div>
              <div className="actions" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {loadingTariffs ? <span className="sub">Загрузка…</span> : <span className="sub">Всего: {totalTariffs}</span>}
                {!readOnly && (
                  <button className="btn btn--primary" type="button" onClick={() => {
                    setAmount(0);
                    setComment('');
                    setEditor({ mode: 'create' });
                  }}>
                    Создать тариф
                  </button>
                )}
              </div>
            </div>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Создан</th>
                    <th>База</th>
                    <th>Текущее значение</th>
                    <th>Комментарий</th>
                    <th>Создал</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {!loadingTariffs && tariffs.length === 0 && (
                    <tr>
                      <td colSpan={7} className="muted" style={{ padding: 16 }}>Тарифы ещё не созданы.</td>
                    </tr>
                  )}
                  {tariffs.map((tariff) => (
                    <tr
                      key={tariff.id}
                      style={{ backgroundColor: selectedTariffId === tariff.id ? '#f7f8fc' : undefined, cursor: 'pointer' }}
                      onClick={() => {
                        setSelectedTariffId(tariff.id);
                        setTariffOpsPage(1);
                      }}
                    >
                      <td>{tariff.id}</td>
                      <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={tariff.createdAt} /></td>
                      <td>{tariff.baseAmount}</td>
                      <td style={{ fontWeight: 600 }}>{tariff.currentAmount}</td>
                      <td>{tariff.comment || '—'}</td>
                      <td className="muted">{tariff.createdBy.name || tariff.createdBy.login} (id: {tariff.createdBy.id})</td>
                      <td>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          <button className="btn btn--ghost" type="button" onClick={(e) => {
                            e.stopPropagation();
                            setSelectedTariffId(tariff.id);
                            setTariffOpsPage(1);
                          }}>
                            История
                          </button>
                          {!readOnly && (
                            <>
                              <button className="btn btn--secondary" type="button" onClick={(e) => {
                                e.stopPropagation();
                                setAmount(0);
                                setComment('');
                                setEditor({ mode: 'credit', tariff });
                              }}>
                                Добавить
                              </button>
                              <button className="btn btn--secondary" type="button" onClick={(e) => {
                                e.stopPropagation();
                                setAmount(0);
                                setComment('');
                                setEditor({ mode: 'debit', tariff });
                              }}>
                                Списать
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {selectedTariff && (
            <div className="table-card" style={{ minWidth: 900, padding: '0 8px 8px' }}>
              <div className="table-toolbar">
                <div className="filters">
                  <span className="sub">История тарифа #{selectedTariff.id}</span>
                </div>
                <div className="actions">
                  {loadingTariffOps ? <span className="sub">Загрузка…</span> : <span className="sub">Всего: {totalTariffOps}</span>}
                </div>
              </div>
              <div style={{ padding: '0 8px 8px' }} className="sub">
                Текущее значение: <b>{selectedTariff.currentAmount}</b>, базовый размер: <b>{selectedTariff.baseAmount}</b>
              </div>
              <div className="table-footer table-footer--top">
                Показано {tariffOps.length} из {totalTariffOps}
                <div className="spacer" />
                <div className="pager">
                  <button className="pager__btn" disabled={tariffOpsPage <= 1} onClick={() => {
                    const nextPage = Math.max(1, tariffOpsPage - 1);
                    setTariffOpsPage(nextPage);
                  }}>‹</button>
                  <span className="pager__info">{tariffOpsPage} / {totalTariffOpsPages}</span>
                  <button className="pager__btn" disabled={tariffOpsPage >= totalTariffOpsPages} onClick={() => {
                    const nextPage = Math.min(totalTariffOpsPages, tariffOpsPage + 1);
                    setTariffOpsPage(nextPage);
                  }}>›</button>
                  <select className="pager__size" value={tariffOpsPageSize} onChange={(e) => {
                    const nextSize = Number(e.target.value);
                    setTariffOpsPageSize(nextSize);
                    setTariffOpsPage(1);
                  }}>
                    <option value={10}>10</option>
                    <option value={25}>25</option>
                    <option value={50}>50</option>
                  </select>
                </div>
              </div>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Дата</th>
                      <th>Тип</th>
                      <th>Количество</th>
                      <th>Комментарий</th>
                      <th>Создал</th>
                    </tr>
                  </thead>
                  <tbody>
                    {!loadingTariffOps && tariffOps.length === 0 && (
                      <tr>
                        <td colSpan={5} className="muted" style={{ padding: 16 }}>Изменений по тарифу нет.</td>
                      </tr>
                    )}
                    {tariffOps.map((op) => (
                      <tr key={op.id}>
                        <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={op.createdAt} /></td>
                        <td>
                          <span className={op.type === 'credit' ? 'badge badge--green' : 'badge badge--orange'}>
                            {op.type === 'credit' ? 'Добавление' : 'Списание'}
                          </span>
                        </td>
                        <td>{op.amount}</td>
                        <td>{op.comment}</td>
                        <td className="muted">{op.createdBy.name || op.createdBy.login} (id: {op.createdBy.id})</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default TariffManagerModal;
