import { useCallback, useEffect, useMemo, useState } from 'react';
import DateTimeCompact from './DateTimeCompact';
import {
  type ClientTariff,
  type ClientTariffList,
  type ClientTariffOperation,
  type ClientTariffOperationList,
} from '../api';
import { preventNumberInputWheel } from '../utils/numberInput';

type NumericInputValue = number | '';

type TariffEditorState =
  | { mode: 'create' }
  | { mode: 'credit'; tariff: ClientTariff }
  | { mode: 'debit'; tariff: ClientTariff }
  | null;

type TariffManagerModalProps = {
  targetId: number;
  targetName?: string | null;
  title: string;
  onClose: () => void;
  onChanged?: () => void | Promise<void>;
  readOnly?: boolean;
  initialEditorMode?: 'create' | null;
  createOnly?: boolean;
  fetchTariffs: (targetId: number, params?: { offset?: number; limit?: number }) => Promise<ClientTariffList>;
  createTariff: (targetId: number, payload: { amount: number; comment?: string; signal1: number; signal2: number; signal3?: number | null }) => Promise<ClientTariff>;
  updateTariff: (tariffId: number, payload: { amount: number; comment?: string; signal1: number; signal2: number; signal3?: number | null }) => Promise<ClientTariff>;
  createTariffOp: (tariffId: number, payload: { amount: number; type: 'credit' | 'debit'; comment: string }) => Promise<ClientTariffOperation>;
  fetchTariffOps: (tariffId: number, params?: { offset?: number; limit?: number }) => Promise<ClientTariffOperationList>;
};

type TariffActionDialogProps = {
  clientName: string;
  editor: Exclude<TariffEditorState, null>;
  amount: NumericInputValue;
  signal1: NumericInputValue;
  signal2: NumericInputValue;
  signal3: NumericInputValue;
  comment: string;
  error: string | null;
  submitting: boolean;
  onAmountChange: (next: NumericInputValue) => void;
  onSignal1Change: (next: NumericInputValue) => void;
  onSignal2Change: (next: NumericInputValue) => void;
  onSignal3Change: (next: NumericInputValue) => void;
  onCommentChange: (next: string) => void;
  onClose: () => void;
  onSubmit: (e: React.FormEvent) => void;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function parseNumberInputValue(raw: string): NumericInputValue {
  if (!raw.trim()) return '';
  const next = Number(raw);
  return Number.isFinite(next) ? next : '';
}

function getInputNumber(value: NumericInputValue): number {
  return value === '' ? 0 : Number(value);
}

function TariffActionDialog({
  clientName,
  editor,
  amount,
  signal1,
  signal2,
  signal3,
  comment,
  error,
  submitting,
  onAmountChange,
  onSignal1Change,
  onSignal2Change,
  onSignal3Change,
  onCommentChange,
  onClose,
  onSubmit,
}: TariffActionDialogProps) {
  const isCreate = editor.mode === 'create';
  const isCredit = editor.mode === 'credit';
  const isDebit = editor.mode === 'debit';
  const showSignals = true;
  const showAmount = isCreate || isCredit || isDebit;
  const currentTariffAmount = editor.mode !== 'create' ? editor.tariff.currentAmount : null;
  const amountNumber = getInputNumber(amount);
  const projectedAmount = currentTariffAmount == null
    ? null
    : (isCredit
      ? currentTariffAmount + Math.max(0, amountNumber)
      : (isDebit ? currentTariffAmount - Math.max(0, amountNumber) : currentTariffAmount));
  const title = isCreate
    ? 'Создать тариф'
    : (isCredit ? 'Добавить к тарифу' : 'Списать из тарифа');
  const amountLabel = isCreate
    ? 'Тариф'
    : (isCredit ? 'Сумма пополнения' : 'Сумма списания');
  const submitLabel = isCreate
    ? 'Начислить'
    : (isCredit ? 'Добавить' : 'Списать');
  const commentPlaceholder = isCreate
    ? 'Комментарий к созданию тарифа'
    : (isCredit ? 'Зачем добавляем объём' : 'Причина списания');
  const commentRequired = isCredit || isDebit;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1500,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="modal-card"
        style={{
          background: '#fff',
          borderRadius: 12,
          width: '100%',
          maxWidth: 520,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 18px 48px rgba(0,0,0,0.22)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ display: 'grid', gap: 6 }}>
            <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>{title}</div>
            <div className="sub">Клиент</div>
            <div style={{ fontWeight: 600 }}>{clientName}</div>
          </div>
          <button type="button" className="btn btn--ghost" onClick={onClose} style={{ padding: '6px 10px' }}>
            ✕
          </button>
        </div>

        <form onSubmit={onSubmit} style={{ padding: 20, display: 'grid', gap: 14 }}>
          {!isCreate && (
            <div
              style={{
                display: 'grid',
                gap: 6,
                padding: 12,
                borderRadius: 10,
                background: '#f7f8fc',
                border: '1px solid #ececf5',
              }}
            >
              <div className="sub">Текущий тариф</div>
              <div style={{ fontSize: '1.25rem', fontWeight: 700 }}>{editor.tariff.currentAmount}</div>
              {(isCredit || isDebit) && projectedAmount != null && (
                <div className="sub">
                  После операции: <b>{projectedAmount}</b>
                </div>
              )}
            </div>
          )}

          {showAmount && (
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">{amountLabel}</span>
              <input
                autoFocus
                type="number"
                min={1}
                value={amount}
                onChange={(e) => onAmountChange(parseNumberInputValue(e.target.value))}
                onWheel={preventNumberInputWheel}
                required
              />
            </label>
          )}

          {showSignals && (
            <div style={{ display: 'grid', gap: 12 }}>
              <div className="section-title">Сигналы Telegram</div>
              <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' }}>
                <label style={{ display: 'grid', gap: 6 }}>
                  <span className="sub">Сигнал 1</span>
                  <input
                    type="number"
                    min={1}
                    value={signal1}
                    onChange={(e) => onSignal1Change(parseNumberInputValue(e.target.value))}
                    onWheel={preventNumberInputWheel}
                    required
                  />
                </label>
                <label style={{ display: 'grid', gap: 6 }}>
                  <span className="sub">Сигнал 2</span>
                  <input
                    type="number"
                    min={1}
                    value={signal2}
                    onChange={(e) => onSignal2Change(parseNumberInputValue(e.target.value))}
                    onWheel={preventNumberInputWheel}
                    required
                  />
                </label>
                <label style={{ display: 'grid', gap: 6 }}>
                  <span className="sub">Сигнал 3 (необязательно)</span>
                  <input
                    type="number"
                    min={1}
                    value={signal3}
                    onChange={(e) => onSignal3Change(parseNumberInputValue(e.target.value))}
                    onWheel={preventNumberInputWheel}
                  />
                </label>
              </div>
              <div className="sub">Правило: `Сигнал 1 &gt; Сигнал 2`. Если задан `Сигнал 3`, он должен быть меньше `Сигнала 2`. `Сигнал 1` должен быть меньше тарифа.</div>
            </div>
          )}

          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Комментарий</span>
            <textarea
              rows={3}
              value={comment}
              onChange={(e) => onCommentChange(e.target.value)}
              placeholder={commentPlaceholder}
              required={commentRequired}
            />
          </label>

          {error && (
            <div className="sub" style={{ color: '#d00' }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, borderTop: '1px solid #eee', paddingTop: 12 }}>
            <button type="button" className="btn" onClick={onClose} disabled={submitting}>
              Отмена
            </button>
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? 'Сохраняем...' : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function TariffManagerModal({
  targetId,
  targetName,
  title,
  onClose,
  onChanged,
  readOnly = false,
  initialEditorMode = null,
  createOnly = false,
  fetchTariffs,
  createTariff,
  updateTariff,
  createTariffOp,
  fetchTariffOps,
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
  const [editor, setEditor] = useState<TariffEditorState>(() => (
    createOnly && initialEditorMode === 'create' && !readOnly ? { mode: 'create' } : null
  ));
  const [amount, setAmount] = useState<NumericInputValue>('');
  const [signal1, setSignal1] = useState<NumericInputValue>('');
  const [signal2, setSignal2] = useState<NumericInputValue>('');
  const [signal3, setSignal3] = useState<NumericInputValue>('');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const isCreateOnlyFlow = createOnly && initialEditorMode === 'create';

  const selectedTariff = useMemo(
    () => (selectedTariffId != null ? tariffs.find((item) => item.id === selectedTariffId) ?? null : null),
    [selectedTariffId, tariffs],
  );
  const clientLabel = (targetName || '').trim() || `Клиент #${targetId}`;
  const totalTariffOpsPages = Math.max(1, Math.ceil(totalTariffOps / tariffOpsPageSize));

  const resetEditor = useCallback(() => {
    setEditor(null);
    setAmount('');
    setSignal1('');
    setSignal2('');
    setSignal3('');
    setComment('');
    setError(null);
  }, []);

  const openCreateDialog = useCallback(() => {
    setAmount('');
    setSignal1('');
    setSignal2('');
    setSignal3('');
    setComment('');
    setError(null);
    setEditor({ mode: 'create' });
  }, []);

  const openOperationDialog = useCallback((tariff: ClientTariff, mode: 'credit' | 'debit') => {
    setAmount('');
    setSignal1(tariff.signal1 ?? 0);
    setSignal2(tariff.signal2 ?? 0);
    setSignal3(tariff.signal3 ?? '');
    setComment('');
    setError(null);
    setEditor({ mode, tariff });
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
    if (isCreateOnlyFlow) return;
    setTariffOpsPage(1);
    void loadTariffs();
  }, [isCreateOnlyFlow, loadTariffs]);

  useEffect(() => {
    if (readOnly || initialEditorMode !== 'create') return;
    openCreateDialog();
  }, [initialEditorMode, openCreateDialog, readOnly, targetId]);

  useEffect(() => {
    if (isCreateOnlyFlow) return;
    if (selectedTariffId == null) {
      setTariffOps([]);
      setTotalTariffOps(0);
      return;
    }
    void loadTariffOps(selectedTariffId, tariffOpsPage, tariffOpsPageSize);
  }, [isCreateOnlyFlow, selectedTariffId, tariffOpsPage, tariffOpsPageSize, loadTariffOps]);

  function validateSignals(nextAmount: number) {
    const signal1Number = getInputNumber(signal1);
    const signal2Number = getInputNumber(signal2);
    const signal3Number = getInputNumber(signal3);
    if (signal1 === '' || signal2 === '') {
      return 'Заполните сигналы 1 и 2';
    }
    if (signal3 !== '' && signal3Number <= 0) {
      return 'Сигнал 3 должен быть больше нуля';
    }
    if (signal3 !== '' && signal2Number <= signal3Number) {
      return 'Сигнал 2 должен быть больше сигнала 3';
    }
    if (signal1Number <= signal2Number) {
      return 'Сигнал 1 должен быть больше сигнала 2';
    }
    if (signal1Number >= nextAmount) {
      return 'Сигнал 1 должен быть меньше тарифа';
    }
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!editor) return;
    const trimmedComment = comment.trim();
    const amountNumber = getInputNumber(amount);
    const signal1Number = getInputNumber(signal1);
    const signal2Number = getInputNumber(signal2);
    const signal3Number = getInputNumber(signal3);
    const signal3Payload = signal3 === '' ? null : signal3Number;

    try {
      setSubmitting(true);
      setError(null);

      let nextTariffId: number | null = null;
      if (editor.mode === 'create') {
        if (amountNumber <= 0) {
          setError('Укажите тариф больше нуля');
          return;
        }
        const signalError = validateSignals(amountNumber);
        if (signalError) {
          setError(signalError);
          return;
        }
        const tariff = await createTariff(targetId, {
          amount: amountNumber,
          comment: trimmedComment || undefined,
          signal1: signal1Number,
          signal2: signal2Number,
          signal3: signal3Payload,
        });
        nextTariffId = tariff.id;
      } else {
        if (amountNumber <= 0) {
          setError('Укажите сумму больше нуля');
          return;
        }
        if (!trimmedComment) {
          setError('Комментарий обязателен');
          return;
        }
        if (editor.mode === 'debit' && amountNumber > editor.tariff.currentAmount) {
          setError('Недостаточно объёма в тарифе для списания');
          return;
        }
        const nextAmount = editor.mode === 'credit'
          ? editor.tariff.currentAmount + amountNumber
          : editor.tariff.currentAmount - amountNumber;
        const signalError = validateSignals(nextAmount);
        if (signalError) {
          setError(signalError);
          return;
        }
        await createTariffOp(editor.tariff.id, {
          amount: amountNumber,
          type: editor.mode,
          comment: trimmedComment,
        });
        const signalsChanged = (editor.tariff.signal1 ?? 0) !== signal1Number
          || (editor.tariff.signal2 ?? 0) !== signal2Number
          || (editor.tariff.signal3 ?? null) !== signal3Payload;
        if (signalsChanged) {
          await updateTariff(editor.tariff.id, {
            amount: nextAmount,
            comment: undefined,
            signal1: signal1Number,
            signal2: signal2Number,
            signal3: signal3Payload,
          });
        }
        nextTariffId = editor.tariff.id;
      }

      if (!isCreateOnlyFlow) {
        const actualTariffId = await loadTariffs(nextTariffId);
        setTariffOpsPage(1);
        if (actualTariffId != null) {
          await loadTariffOps(actualTariffId, 1, tariffOpsPageSize);
        }
      }
      await onChanged?.();
      if (isCreateOnlyFlow && editor.mode === 'create') {
        onClose();
        return;
      }
      resetEditor();
    } catch (err: unknown) {
      const fallback = editor.mode === 'credit'
        ? 'Не удалось добавить объём к тарифу'
        : (editor.mode === 'debit'
          ? 'Не удалось списать объём из тарифа'
          : 'Не удалось создать тариф');
      setError(getErrorMessage(err, fallback));
    } finally {
      setSubmitting(false);
    }
  }

  if (isCreateOnlyFlow && editor?.mode === 'create') {
    return (
      <TariffActionDialog
        clientName={clientLabel}
        editor={editor}
        amount={amount}
        signal1={signal1}
        signal2={signal2}
        signal3={signal3}
        comment={comment}
        error={error}
        submitting={submitting}
        onAmountChange={setAmount}
        onSignal1Change={setSignal1}
        onSignal2Change={setSignal2}
        onSignal3Change={setSignal3}
        onCommentChange={setComment}
        onClose={onClose}
        onSubmit={handleSubmit}
      />
    );
  }

  return (
    <div className="modal-backdrop">
      <div className="modal tariff-manager">
        <div className="modal__header tariff-manager__header">
          <div>
            <div style={{ fontWeight: 600 }}>{title}</div>
            <div className="sub">
              {readOnly ? 'Режим просмотра без изменений.' : 'Тарифные операции автоматически меняют баланс.'}
            </div>
          </div>
          <button type="button" className="btn btn--ghost" onClick={onClose}>Закрыть</button>
        </div>

        <div className="modal__body tariff-manager__body">
          {error && !editor && <div className="sub" style={{ color: '#d00' }}>{error}</div>}

          <div className="table-card tariff-manager__section">
            <div className="table-toolbar">
              <div className="filters">
                <span className="sub">Тарифы</span>
              </div>
              <div className="actions tariff-manager__toolbar-actions">
                {loadingTariffs ? <span className="sub">Загрузка...</span> : <span className="sub">Всего: {totalTariffs}</span>}
                {!readOnly && (
                  <button className="btn btn--primary" type="button" onClick={openCreateDialog}>
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
                    <th>Сигналы</th>
                    <th>Комментарий</th>
                    <th>Создал</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {!loadingTariffs && tariffs.length === 0 && (
                    <tr>
                      <td colSpan={8} className="muted" style={{ padding: 16 }}>Тарифы еще не созданы.</td>
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
                      <td>{tariff.signal1 ?? '-'} / {tariff.signal2 ?? '-'} / {tariff.signal3 ?? '-'}</td>
                      <td>{tariff.comment || '-'}</td>
                      <td className="muted">{tariff.createdBy.name || tariff.createdBy.login} (id: {tariff.createdBy.id})</td>
                      <td>
                        <div className="tariff-manager__row-actions">
                          <button
                            className="btn btn--ghost"
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedTariffId(tariff.id);
                              setTariffOpsPage(1);
                            }}
                          >
                            История
                          </button>
                          {!readOnly && (
                            <>
                              <button
                                className="btn btn--secondary"
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openOperationDialog(tariff, 'credit');
                                }}
                              >
                                Добавить
                              </button>
                              <button
                                className="btn btn--secondary"
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openOperationDialog(tariff, 'debit');
                                }}
                              >
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
            <div className="table-card tariff-manager__section">
              <div className="table-toolbar">
                <div className="filters">
                  <span className="sub">История тарифа #{selectedTariff.id}</span>
                </div>
                <div className="actions tariff-manager__toolbar-actions">
                  {loadingTariffOps ? <span className="sub">Загрузка...</span> : <span className="sub">Всего: {totalTariffOps}</span>}
                </div>
              </div>
              <div className="sub tariff-manager__summary">
                Текущее значение: <b>{selectedTariff.currentAmount}</b>, базовый размер: <b>{selectedTariff.baseAmount}</b>, сигналы: <b>{selectedTariff.signal1 ?? '-'}</b> / <b>{selectedTariff.signal2 ?? '-'}</b> / <b>{selectedTariff.signal3 ?? '-'}</b>
              </div>
              <div className="table-footer table-footer--top">
                Показано {tariffOps.length} из {totalTariffOps}
                <div className="spacer" />
                <div className="pager">
                  <button
                    className="pager__btn"
                    disabled={tariffOpsPage <= 1}
                    onClick={() => {
                      const nextPage = Math.max(1, tariffOpsPage - 1);
                      setTariffOpsPage(nextPage);
                    }}
                  >
                    ‹
                  </button>
                  <span className="pager__info">{tariffOpsPage} / {totalTariffOpsPages}</span>
                  <button
                    className="pager__btn"
                    disabled={tariffOpsPage >= totalTariffOpsPages}
                    onClick={() => {
                      const nextPage = Math.min(totalTariffOpsPages, tariffOpsPage + 1);
                      setTariffOpsPage(nextPage);
                    }}
                  >
                    ›
                  </button>
                  <select
                    className="pager__size"
                    value={tariffOpsPageSize}
                    onChange={(e) => {
                      const nextSize = Number(e.target.value);
                      setTariffOpsPageSize(nextSize);
                      setTariffOpsPage(1);
                    }}
                  >
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

      {editor && (
        <TariffActionDialog
          clientName={clientLabel}
          editor={editor}
          amount={amount}
          signal1={signal1}
          signal2={signal2}
          signal3={signal3}
          comment={comment}
          error={error}
          submitting={submitting}
          onAmountChange={setAmount}
          onSignal1Change={setSignal1}
          onSignal2Change={setSignal2}
          onSignal3Change={setSignal3}
          onCommentChange={setComment}
          onClose={resetEditor}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}

export default TariffManagerModal;
