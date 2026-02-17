import React, { useMemo } from 'react';
import type { AdminChange } from '../api';
import DateTimeCompact from './DateTimeCompact';

type Props = {
  change: AdminChange;
  onClose: () => void;
};

type ProjectSnapshot = {
  name?: string;
  tag?: string;
  collectionSource?: string;
  dataSourceCode?: string;
  dataLimit?: number;
  status?: string;
  deliveryStatus?: string;
  regionMode?: 'include' | 'exclude' | string;
  regions?: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  daysReceived?: string;
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
  const after: ProjectSnapshot = (change.projectSnapshot || {}) as ProjectSnapshot;
  const before: ProjectSnapshot = (change.beforeSnapshot || {}) as ProjectSnapshot;
  const changed = useMemo(() => new Set(change.changedFields || []), [change.changedFields]);
  const afterRegions = Array.isArray(after.regions) ? after.regions : [];
  const beforeRegions = Array.isArray(before.regions) ? before.regions : [];
  const afterSites = Array.isArray(after.sites) ? after.sites : [];
  const beforeSites = Array.isArray(before.sites) ? before.sites : [];
  const afterPhones = Array.isArray(after.phones) ? after.phones : [];
  const beforePhones = Array.isArray(before.phones) ? before.phones : [];
  const afterSms = typeof after.smsSenderName === 'string' ? after.smsSenderName : '';
  const beforeSms = typeof before.smsSenderName === 'string' ? before.smsSenderName : '';
  const afterDays = typeof after.daysReceived === 'string' ? after.daysReceived : '';
  const beforeDays = typeof before.daysReceived === 'string' ? before.daysReceived : '';

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

  function renderText(_key: FieldKey, value: unknown) {
    if (Array.isArray(value)) return value.join(', ');
    if (value == null) return '—';
    return String(value);
  }

  function formatSourceLimits(): string | null {
    const limits =
      change.sourceLimits && typeof change.sourceLimits === 'object'
        ? (change.sourceLimits as Record<string, number>)
        : null;
    if (!limits) return null;
    const keys = Object.keys(limits).filter((k) => typeof limits[k] === 'number' && Number.isFinite(limits[k]));
    if (keys.length === 0) return null;
    const order = Array.isArray(change.sources) && change.sources.length ? change.sources : keys.sort();
    return order
      .filter((k) => typeof limits[k] === 'number' && Number.isFinite(limits[k]))
      .map((k) => `${k}: ${limits[k]}`)
      .join(', ');
  }

  // Удаляем префикс источника из названия (например, "B3_" или "B3 ").
  function normalizeName(raw: string) {
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
            <div className="sub" style={{ display: 'grid', gap: 2 }}>
              <span>Создано:</span>
              <DateTimeCompact value={change.createdAt} />
            </div>
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
                  value={normalizeName(after.name ?? '')}
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
                  value={formatSourceLimits() || renderText('dataLimit', after.dataLimit)}
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
                <span className="sub">Было: {renderText('status', before.status)}</span>
              )}
            </div>

            <div style={{ display: 'grid', gap: 4 }}>
              <span className="section-title">Регионы</span>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                {pill(after.regionMode === 'exclude' ? 'Исключить' : 'Включить', '#f4f4f7')}
                <span className="sub">Всего: {afterRegions.length}</span>
              </div>
              <div
                style={{
                  ...boxStyleFor('regions'),
                  padding: 8,
                  maxHeight: 140,
                  overflow: 'auto',
                }}
              >
                {afterRegions.length
                  ? afterRegions.map((r) => (
                      <span key={r} className="badge badge--secondary" style={{ margin: 4, display: 'inline-block' }}>
                        {r}
                      </span>
                    ))
                  : <span className="muted">Не заданы</span>}
              </div>
              {isChanged('regions' as FieldKey) && beforeRegions.length > 0 && (
                <span className="sub">Было: {beforeRegions.join(', ') || '—'}</span>
              )}
            </div>

            {(afterSites.length > 0 || afterPhones.length > 0 || afterSms) && (
              <div style={{ display: 'grid', gap: 12 }}>
                {afterSites.length > 0 && (
                  <label style={{ display: 'grid', gap: 4 }}>
                    <span className="section-title">Сайты</span>
                    <textarea
                      readOnly
                      rows={6}
                      value={afterSites.join('\n')}
                      style={{
                        ...(isChanged('sites' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle),
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                        borderRadius: 10,
                      }}
                    />
                    {isChanged('sites' as FieldKey) && beforeSites.length > 0 && (
                      <span className="sub">Было: {beforeSites.join(', ') || '—'}</span>
                    )}
                  </label>
                )}

                {afterPhones.length > 0 && (
                  <label style={{ display: 'grid', gap: 4 }}>
                    <span className="section-title">Телефоны</span>
                    <textarea
                      readOnly
                      rows={6}
                      value={afterPhones.join('\n')}
                      style={{
                        ...(isChanged('phones' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle),
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                        borderRadius: 10,
                      }}
                    />
                    {isChanged('phones' as FieldKey) && beforePhones.length > 0 && (
                      <span className="sub">Было: {beforePhones.join(', ') || '—'}</span>
                    )}
                  </label>
                )}

                {afterSms && (
                  <label style={{ display: 'grid', gap: 4 }}>
                    <span className="section-title">СМС отправитель</span>
                    <input
                      readOnly
                      value={afterSms}
                      style={isChanged('smsSenderName' as FieldKey) ? { ...baseInputStyle, ...highlight } : baseInputStyle}
                    />
                    {isChanged('smsSenderName' as FieldKey) && beforeSms && (
                      <span className="sub">Было: {beforeSms}</span>
                    )}
                  </label>
                )}
              </div>
            )}

            <div style={{ display: 'grid', gap: 4 }}>
              <span className="section-title">Дни получения</span>
              <div style={{ ...boxStyleFor('daysReceived'), display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {afterDays
                  .split(/\s+/)
                  .filter(Boolean)
                  .map((d) => (
                    <span key={d}>{pill(d, '#f4f4f7')}</span>
                  ))}
              </div>
              {isChanged('daysReceived' as FieldKey) && beforeDays && (
                <span className="sub">Было: {renderText('daysReceived', beforeDays)}</span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ChangeProjectDiffModal;

