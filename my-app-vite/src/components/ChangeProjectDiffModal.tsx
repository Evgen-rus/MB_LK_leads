import React, { useMemo } from 'react';
import type { AdminChange } from '../api';

type Props = {
  change: AdminChange;
  onClose: () => void;
};

type FieldKey =
  | 'name'
  | 'tag'
  | 'collectionSource'
  | 'dataSourceCode'
  | 'dataLimit'
  | 'status'
  | 'deliveryStatus'
  | 'regionMode'
  | 'regions'
  | 'sites'
  | 'phones'
  | 'smsSenderName'
  | 'daysReceived';

const fieldLabels: Record<FieldKey, string> = {
  name: 'Название',
  tag: 'Тег',
  collectionSource: 'Источник сбора',
  dataSourceCode: 'Источник данных',
  dataLimit: 'Лимит',
  status: 'Статус проекта',
  deliveryStatus: 'Статус отгрузки',
  regionMode: 'Режим регионов',
  regions: 'Регионы',
  sites: 'Сайты',
  phones: 'Телефоны',
  smsSenderName: 'СМС отправитель',
  daysReceived: 'Дни получения',
};

function ChangeProjectDiffModal({ change, onClose }: Props) {
  const after = (change.projectSnapshot || {}) as Record<string, any>;
  const before = (change.beforeSnapshot || {}) as Record<string, any>;
  const changed = useMemo(() => new Set(change.changedFields || []), [change.changedFields]);

  const changedList = useMemo(
    () => (change.changedFields && change.changedFields.length ? change.changedFields : []),
    [change.changedFields],
  );

  const isChanged = (key: FieldKey) => changed.has(key);

  const pill = (text: string, color = '#ececff', fg = '#363568') => (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 10px',
        borderRadius: 999,
        background: color,
        color: fg,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {text}
    </span>
  );

  function renderText(_key: FieldKey, value: any) {
    if (Array.isArray(value)) return value.join(', ');
    if (value == null) return '—';
    return String(value);
  }

  // Удаляем префикс источника из названия (например, "B3_" или "B3 ").
  function normalizeName(raw: any) {
    if (typeof raw !== 'string') return renderText('name' as FieldKey, raw);
    return raw.replace(/^B[1-4][\s_-]*/i, '');
  }

  const baseInputStyle: React.CSSProperties = {
    borderRadius: 10,
    border: '1px solid #dcdce6',
    padding: '10px 12px',
    background: '#f9f9ff',
  };

  const highlight: React.CSSProperties = {
    borderColor: '#f05b6c',
    background: '#fff1f3',
    boxShadow: '0 0 0 2px rgba(240,91,108,0.2)',
  };

  // Унифицированная подсветка изменённых полей: в одном цвете для всех типов контролов.
  const baseBoxStyle: React.CSSProperties = {
    borderRadius: 10,
    border: '1px solid #dcdce6',
    padding: '10px 12px',
    background: '#f9f9ff',
  };

  function boxStyleFor(key: FieldKey): React.CSSProperties {
    return isChanged(key) ? { ...baseBoxStyle, ...highlight } : baseBoxStyle;
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1100,
        padding: 16,
      }}
    >
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        style={{
          background: '#fff',
          borderRadius: 10,
          width: '100%',
          maxWidth: 720,
          maxHeight: '90vh',
          overflow: 'hidden',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{ padding: 16, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
          <div style={{ display: 'grid', gap: 4 }}>
            <div style={{ fontSize: '1.05rem', fontWeight: 700 }}>
              Карточка проекта ({change.action === 'create' ? 'Создание' : change.action === 'delete' ? 'Удаление' : 'Изменение'})
            </div>
            <div className="sub">
              {change.projectName || 'Проект'}
              {change.projectId ? ` (id: ${change.projectId})` : ''}
            </div>
            <div className="sub">Создано: {change.createdAt}</div>
            {changedList.length > 0 && (
              <div className="sub">
                Изменений: {changedList.length}{' '}
                {changedList.length ? '— ' + changedList.map((f) => fieldLabels[f as FieldKey] || f).join(', ') : ''}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <button
              className="btn"
              type="button"
              onClick={() => {
                try {
                  navigator.clipboard?.writeText(JSON.stringify(change.projectSnapshot || {}, null, 2));
                } catch (e) {
                  console.error(e);
                }
              }}
            >
              Копировать JSON
            </button>
            <button className="btn btn--secondary" onClick={onClose} type="button">
              Закрыть
            </button>
          </div>
        </div>

        <div style={{ padding: 16, overflowY: 'auto' }}>
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'grid', gap: 8 }}>
              <label style={{ display: 'grid', gap: 4 }}>
                <span className="section-title">Название</span>
                <input
                  readOnly
                  value={normalizeName(after.name)}
                  style={isChanged('name' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle}
                />
                {isChanged('name' as FieldKey) && before.name && (
                  <span className="sub">Было: {normalizeName(before.name)}</span>
                )}
              </label>
              {/* Тег скрываем — на фронте не используется */}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={{ display: 'grid', gap: 4 }}>
                <span className="section-title">Источник сбора</span>
                <input
                  readOnly
                  value={renderText('collectionSource', after.collectionSource)}
                  style={isChanged('collectionSource' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle}
                />
              </label>
              <label style={{ display: 'grid', gap: 4 }}>
                <span className="section-title">Лимит</span>
                <input
                  readOnly
                  value={renderText('dataLimit', after.dataLimit)}
                  style={isChanged('dataLimit' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle}
                />
                {isChanged('dataLimit' as FieldKey) && before.dataLimit != null && (
                  <span className="sub">Было: {renderText('dataLimit', before.dataLimit)}</span>
                )}
              </label>
            </div>

            <div style={{ display: 'grid', gap: 4 }}>
              <span className="section-title">Источник данных</span>
              <div style={{ ...boxStyleFor('dataSourceCode'), display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(['B1', 'B2', 'B3', 'B4'] as const).map((code) => {
                  const activeList = Array.isArray(change.sources) && change.sources.length > 0 ? change.sources : null;
                  const active = activeList ? activeList.includes(code) : after.dataSourceCode === code;
                  return (
                    <span
                      key={code}
                      style={{
                        padding: '6px 10px',
                        borderRadius: 999,
                        border: '1px solid',
                        borderColor: active ? '#6a5cff' : '#dcdce6',
                        background: active ? '#6a5cff' : '#fff',
                        color: active ? '#fff' : '#1d1d1f',
                        opacity: active ? 1 : 0.6,
                      }}
                    >
                      {code}
                    </span>
                  );
                })}
              </div>
              {isChanged('dataSourceCode' as FieldKey) && before.dataSourceCode && (
                <span className="sub">Было: {renderText('dataSourceCode', before.dataSourceCode)}</span>
              )}
            </div>

            <div style={{ display: 'grid', gap: 4 }}>
              <span className="section-title">Статус проекта</span>
              <div style={{ ...boxStyleFor('status'), display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {pill(
                  renderText('status', after.status),
                  '#f4f4f7',
                  '#24223f',
                )}
              </div>
              {isChanged('status' as FieldKey) && (
                <span className="sub">Было: {before.status || '—'}</span>
              )}
            </div>

            <div style={{ display: 'grid', gap: 4 }}>
              <span className="section-title">Регионы</span>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                {pill(after.regionMode === 'exclude' ? 'Исключить' : 'Включить', '#f4f4f7')}
                <span className="sub">Всего: {(after.regions || []).length}</span>
              </div>
              <div
                style={{
                  ...boxStyleFor('regions'),
                  padding: 8,
                  maxHeight: 140,
                  overflow: 'auto',
                }}
              >
                {(after.regions || []).length
                  ? (after.regions as string[]).map((r: string) => (
                      <span key={r} className="badge badge--secondary" style={{ margin: 4, display: 'inline-block' }}>
                        {r}
                      </span>
                    ))
                  : <span className="muted">Не заданы</span>}
              </div>
              {isChanged('regions' as FieldKey) && (before.regions || []).length > 0 && (
                <span className="sub">Было: {(before.regions as string[]).join(', ') || '—'}</span>
              )}
            </div>

            {(after.sites || after.phones || after.smsSenderName) && (
              <div style={{ display: 'grid', gap: 12 }}>
                {after.sites && (
                  <label style={{ display: 'grid', gap: 4 }}>
                    <span className="section-title">Сайты</span>
                    <textarea
                      readOnly
                      rows={6}
                      value={(after.sites as string[]).join('\n')}
                      style={{
                        ...(isChanged('sites' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle),
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                        borderRadius: 10,
                      }}
                    />
                    {isChanged('sites' as FieldKey) && before.sites && (
                      <span className="sub">Было: {(before.sites as string[]).join(', ') || '—'}</span>
                    )}
                  </label>
                )}

                {after.phones && (
                  <label style={{ display: 'grid', gap: 4 }}>
                    <span className="section-title">Телефоны</span>
                    <textarea
                      readOnly
                      rows={6}
                      value={(after.phones as string[]).join('\n')}
                      style={{
                        ...(isChanged('phones' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle),
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                        borderRadius: 10,
                      }}
                    />
                    {isChanged('phones' as FieldKey) && before.phones && (
                      <span className="sub">Было: {(before.phones as string[]).join(', ') || '—'}</span>
                    )}
                  </label>
                )}

                {after.smsSenderName != null && (
                  <label style={{ display: 'grid', gap: 4 }}>
                    <span className="section-title">СМС отправитель</span>
                    <input
                      readOnly
                      value={renderText('smsSenderName', after.smsSenderName)}
                      style={isChanged('smsSenderName' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle}
                    />
                    {isChanged('smsSenderName' as FieldKey) && before.smsSenderName && (
                      <span className="sub">Было: {renderText('smsSenderName', before.smsSenderName)}</span>
                    )}
                  </label>
                )}
              </div>
            )}

            <div style={{ display: 'grid', gap: 4 }}>
              <span className="section-title">Дни получения</span>
              <div style={{ ...boxStyleFor('daysReceived'), display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(after.daysReceived || '').split(/\s+/).filter(Boolean).map((d: string) => pill(d, '#f4f4f7'))}
              </div>
              {isChanged('daysReceived' as FieldKey) && before.daysReceived && (
                <span className="sub">Было: {renderText('daysReceived', before.daysReceived)}</span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ChangeProjectDiffModal;

