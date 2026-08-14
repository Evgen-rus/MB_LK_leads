import { useMemo, useRef, useState } from 'react';
import type { CreateProjectItem, CreateProjectsResp } from '../api';
import { regions as allRegions, normalizeRegionValues, regionLabelByCode } from '../data/regions';
import type { ProjectMutableStatus } from '../types/project';
import { preventNumberInputWheel } from '../utils/numberInput';
import { parseBulkProjectLines, BULK_CREATE_MAX_LINES } from '../utils/bulkProjectLines';
import {
  RAW_SOURCE_CODES,
  formatSourceTextForDisplay,
  toDisplaySourceCode,
} from '../utils/sourceCodeDisplay';
import type { BulkProgress } from '../utils/projectBulkUpdate';
import RegionQuickTools, { type RegionSourceProject } from './RegionQuickTools';

type DayAbbrev = 'Пн' | 'Вт' | 'Ср' | 'Чт' | 'Пт' | 'Сб' | 'Вс';
type SourceCode = 'B1' | 'B2' | 'B3' | 'B4';

type SavedSettings = {
  dataLimit?: number;
  status?: ProjectMutableStatus;
  regionMode?: 'include' | 'exclude';
  regions?: string[];
  days?: DayAbbrev[];
  b1?: boolean;
  b2?: boolean;
  b3?: boolean;
  b4?: boolean;
};

const DEFAULT_SETTINGS: Required<SavedSettings> = {
  dataLimit: 100,
  status: 'Активен',
  regionMode: 'include',
  regions: [],
  days: ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'],
  b1: true,
  b2: true,
  b3: true,
  b4: true,
};

const DAY_VALUES: DayAbbrev[] = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const CALLS_CODES: SourceCode[] = ['B1', 'B2', 'B3', 'B4'];

type BulkCreateProjectModalProps = {
  onClose: () => void;
  onCreateBatch: (items: CreateProjectItem[]) => Promise<CreateProjectsResp>;
  uniqueProjectNamesEnabled?: boolean;
  regionSourceProjects?: RegionSourceProject[];
  settingsStorageKey?: string;
};

function isMutableStatus(value: unknown): value is ProjectMutableStatus {
  return value === 'Активен' || value === 'На паузе' || value === 'Удалён';
}

function readSavedSettings(storageKey?: string): Required<SavedSettings> {
  if (!storageKey) return DEFAULT_SETTINGS;
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as SavedSettings;
    return {
      dataLimit: Number.isFinite(parsed.dataLimit) ? Number(parsed.dataLimit) : DEFAULT_SETTINGS.dataLimit,
      status: isMutableStatus(parsed.status) && parsed.status !== 'Удалён' ? parsed.status : DEFAULT_SETTINGS.status,
      regionMode: parsed.regionMode === 'exclude' ? 'exclude' : 'include',
      regions: normalizeRegionValues(parsed.regions || []),
      days: Array.isArray(parsed.days)
        ? parsed.days.filter((day): day is DayAbbrev => DAY_VALUES.includes(day as DayAbbrev))
        : DEFAULT_SETTINGS.days,
      b1: typeof parsed.b1 === 'boolean' ? parsed.b1 : DEFAULT_SETTINGS.b1,
      b2: typeof parsed.b2 === 'boolean' ? parsed.b2 : DEFAULT_SETTINGS.b2,
      b3: typeof parsed.b3 === 'boolean' ? parsed.b3 : DEFAULT_SETTINGS.b3,
      b4: typeof parsed.b4 === 'boolean' ? parsed.b4 : DEFAULT_SETTINGS.b4,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function BulkCreateProjectModal({
  onClose,
  onCreateBatch,
  uniqueProjectNamesEnabled = false,
  regionSourceProjects = [],
  settingsStorageKey,
}: BulkCreateProjectModalProps) {
  const initialSettingsRef = useRef<Required<SavedSettings>>(readSavedSettings(settingsStorageKey));

  const [namesText, setNamesText] = useState('');
  const [dataLimit, setDataLimit] = useState<number>(initialSettingsRef.current.dataLimit);
  const [status, setStatus] = useState<ProjectMutableStatus>(initialSettingsRef.current.status);
  const [b1, setB1] = useState(initialSettingsRef.current.b1);
  const [b2, setB2] = useState(initialSettingsRef.current.b2);
  const [b3, setB3] = useState(initialSettingsRef.current.b3);
  const [b4, setB4] = useState(initialSettingsRef.current.b4);
  const [regionMode, setRegionMode] = useState<'include' | 'exclude'>(initialSettingsRef.current.regionMode);
  const [regionQuery, setRegionQuery] = useState('');
  const [regions, setRegions] = useState<string[]>(initialSettingsRef.current.regions);
  const [regionsOpen, setRegionsOpen] = useState(false);
  const [days, setDays] = useState<DayAbbrev[]>(initialSettingsRef.current.days);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDone, setIsDone] = useState(false);
  const [progress, setProgress] = useState<BulkProgress | null>(null);
  const [resultMessages, setResultMessages] = useState<string[]>([]);

  const parsed = useMemo(() => parseBulkProjectLines(namesText), [namesText]);
  const selectedCodes = useMemo(
    () => CALLS_CODES.filter((code) => (code === 'B1' ? b1 : code === 'B2' ? b2 : code === 'B3' ? b3 : b4)),
    [b1, b2, b3, b4],
  );
  const hasValidList = parsed.errors.length === 0 && parsed.lines.length > 0 && selectedCodes.length > 0;
  const plannedCount = parsed.lines.length * selectedCodes.length;
  const perProjectLimit = Number.isFinite(dataLimit) ? dataLimit : 0;

  const baseRegionIndex = useMemo(() => {
    const m = new Map<string, number>();
    allRegions.forEach((r, i) => m.set(r.code, i));
    return m;
  }, []);

  const displayRegions = useMemo(() => {
    const q = regionQuery.trim().toLowerCase();
    const filtered = q
      ? allRegions.filter((r) => r.name.toLowerCase().includes(q) || r.code.includes(q))
      : allRegions;
    const list = filtered.slice();
    list.sort((a, b) => {
      const aSel = regions.includes(a.code) ? 1 : 0;
      const bSel = regions.includes(b.code) ? 1 : 0;
      if (aSel !== bSel) return bSel - aSel;
      return (baseRegionIndex.get(a.code) ?? 0) - (baseRegionIndex.get(b.code) ?? 0);
    });
    return list;
  }, [regionQuery, regions, baseRegionIndex]);

  const progressPercent =
    progress && progress.total > 0
      ? Math.max(0, Math.min(100, Math.round((progress.done / progress.total) * 100)))
      : 0;

  function toggleDay(day: DayAbbrev) {
    setDays((prev) => (prev.includes(day) ? prev.filter((item) => item !== day) : [...prev, day]));
  }

  function saveCurrentSettings() {
    if (!settingsStorageKey) return;
    let previous: Record<string, unknown> = {};
    try {
      const raw = localStorage.getItem(settingsStorageKey);
      if (raw) previous = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      previous = {};
    }
    const settings = {
      ...previous,
      dataLimit: perProjectLimit,
      status,
      regionMode,
      regions: normalizeRegionValues(regions),
      days,
      b1,
      b2,
      b3,
      b4,
    };
    try {
      localStorage.setItem(settingsStorageKey, JSON.stringify(settings));
    } catch {
      /* ignore */
    }
  }

  function resetSavedSettings() {
    try {
      if (settingsStorageKey) localStorage.removeItem(settingsStorageKey);
    } catch {
      /* ignore */
    }
    setDataLimit(DEFAULT_SETTINGS.dataLimit);
    setStatus(DEFAULT_SETTINGS.status);
    setB1(DEFAULT_SETTINGS.b1);
    setB2(DEFAULT_SETTINGS.b2);
    setB3(DEFAULT_SETTINGS.b3);
    setB4(DEFAULT_SETTINGS.b4);
    setRegionMode(DEFAULT_SETTINGS.regionMode);
    setRegions(DEFAULT_SETTINGS.regions);
    setRegionQuery('');
    setRegionsOpen(false);
    setDays(DEFAULT_SETTINGS.days);
  }

  function buildItemsForLine(line: { name: string; phone: string }): CreateProjectItem[] {
    return selectedCodes.map((code) => {
      const name = `${code}_${line.name}`;
      return {
        name,
        tag: name,
        collectionSource: 'Звонки',
        dataSourceCode: code,
        dataLimit: perProjectLimit,
        status,
        regionMode,
        regions: normalizeRegionValues(regions),
        phones: [line.phone],
        days,
      };
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (isSubmitting || isDone || !hasValidList) return;

    const workLines = parsed.lines;
    const total = workLines.length * selectedCodes.length;
    let created = 0;
    let failed = 0;
    let done = 0;
    const messages: string[] = [];

    const emitProgress = () => {
      setProgress({
        total,
        done,
        updated: created,
        skipped: 0,
        failed,
      });
    };

    setIsSubmitting(true);
    setIsDone(false);
    setResultMessages([]);
    emitProgress();
    saveCurrentSettings();

    for (const line of workLines) {
      const items = buildItemsForLine(line);
      try {
        const result = await onCreateBatch(items);
        created += result.items.length;
        failed += Math.max(0, items.length - result.items.length);
        if (result.warning?.trim()) {
          messages.push(formatSourceTextForDisplay(result.warning));
        }
      } catch (err: unknown) {
        failed += items.length;
        const message = err instanceof Error && err.message.trim()
          ? formatSourceTextForDisplay(err.message)
          : `Не удалось создать проекты для строки ${line.lineNumber}.`;
        messages.push(message);
      }
      done += items.length;
      emitProgress();
    }

    setResultMessages(messages);
    setIsSubmitting(false);
    setIsDone(true);
  }

  const errorPreview = parsed.errors.slice(0, 8);
  const extraErrorCount = Math.max(0, parsed.errors.length - errorPreview.length);

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="modal-card"
        style={{
          background: '#fff',
          borderRadius: 8,
          width: '100%',
          maxWidth: 640,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Массовое создание проектов</div>
          <div className="sub" style={{ marginTop: 4, color: '#666' }}>
            Источник сбора: Звонки. Лимит ставится каждому каналу целиком, без деления.
          </div>
          {(isSubmitting || isDone) && progress && (
            <div className="bulk-progress-card">
              <div className="bulk-progress-card__head">
                <div className="bulk-progress-card__status">
                  {isSubmitting && <span className="bulk-progress-card__dot" />}
                  <span>{isDone ? 'Создание завершено' : 'Идёт создание проектов'}</span>
                </div>
                <div className="bulk-progress-card__counter">
                  {progress.done}/{progress.total}
                </div>
              </div>
              <div
                className="bulk-progress-card__bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={progress.total}
                aria-valuenow={progress.done}
                aria-valuetext={`${progress.done} из ${progress.total}`}
              >
                <div
                  className="bulk-progress-card__bar-fill"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              <div className="bulk-progress-card__meta">
                <span>{progressPercent}% выполнено</span>
                <span>Создано: {progress.updated}</span>
                <span>Не создано: {progress.failed}</span>
              </div>
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} style={{ padding: 20 }}>
          <fieldset
            disabled={isSubmitting}
            style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 12 }}
          >
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: '0.75rem', color: '#666' }}>Мультиназвание</span>
              <span className="hint">
                По одной строке, не больше {BULK_CREATE_MAX_LINES}. Вся строка станет названием,
                а телефон после последнего «_» уйдёт в звонки этого проекта.
              </span>
              <textarea
                autoFocus
                rows={10}
                placeholder={'[LR221] ЛИДЕР БЕТОН_gkm-beton.ru_78123132543\n[LR221] ЛИДЕР БЕТОН_udarnik.spb.ru_78123334466'}
                value={namesText}
                onChange={(e) => {
                  setNamesText(e.target.value);
                  setIsDone(false);
                  setProgress(null);
                  setResultMessages([]);
                }}
                style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
              />
              <span style={{ fontSize: '0.75rem', color: '#666' }}>
                Строк: {parsed.totalNonEmpty}
                {selectedCodes.length > 0 && parsed.lines.length > 0 && parsed.errors.length === 0
                  ? ` · будет создано ${plannedCount} проектов`
                  : ''}
              </span>
              {parsed.errors.length > 0 && (
                <div className="sub" style={{ color: '#d00', whiteSpace: 'pre-wrap' }}>
                  {errorPreview.map((item) => (
                    item.lineNumber > 0
                      ? `Строка ${item.lineNumber}: ${item.reason}`
                      : item.reason
                  )).join('\n')}
                  {extraErrorCount > 0 ? `\n… и ещё ${extraErrorCount}` : ''}
                </div>
              )}
              {uniqueProjectNamesEnabled && parsed.lines.length > 0 && parsed.errors.length === 0 && (
                <span className="hint" style={{ color: '#666' }}>
                  Внутренний идентификатор клиента будет добавлен автоматически. В ЛК проект останется виден с обычным названием.
                </span>
              )}
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Источник сбора</span>
                <input type="text" value="Звонки" disabled />
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Лимит для каждого проекта и канала</span>
                <input
                  type="number"
                  min={0}
                  value={dataLimit}
                  onChange={(e) => setDataLimit(Number(e.target.value))}
                  onWheel={preventNumberInputWheel}
                />
              </label>
            </div>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Источник данных</span>
              <div className="sub" style={{ color: '#666' }}>
                Для каждой строки списка будет создан отдельный проект на каждый выбранный канал. Лимит {perProjectLimit} ставится каждому.
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {RAW_SOURCE_CODES.map((code) => {
                  const active = code === 'B1' ? b1 : code === 'B2' ? b2 : code === 'B3' ? b3 : b4;
                  const setActive = (val: boolean) => {
                    if (code === 'B1') setB1(val);
                    else if (code === 'B2') setB2(val);
                    else if (code === 'B3') setB3(val);
                    else setB4(val);
                  };
                  return (
                    <button
                      key={code}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setActive(!active)}
                      style={{
                        padding: '6px 10px',
                        borderRadius: 999,
                        border: '1px solid',
                        borderColor: active ? '#6a5cff' : '#dcdce6',
                        background: active ? '#6a5cff' : '#fff',
                        color: active ? '#fff' : '#1d1d1f',
                        cursor: 'pointer',
                      }}
                    >
                      {toDisplaySourceCode(code)}
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: '0.75rem', color: '#666' }}>
                {selectedCodes.length > 0
                  ? `Каналы: ${selectedCodes.map((code) => toDisplaySourceCode(code)).join(', ')}. Лимит ${perProjectLimit} — каждому.`
                  : 'Выберите источники данных'}
              </div>
            </div>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Регионы</span>
              <div className="hint">Если ничего не выбрано, сбор идет по всей РФ. Чтобы выбрать регион — кликните в поле поиска.</div>
              <div className="radio-row" style={{ alignItems: 'center' }}>
                <label><input type="radio" name="bulkRegionMode" checked={regionMode === 'include'} onChange={() => setRegionMode('include')} /> Включить</label>
                <label><input type="radio" name="bulkRegionMode" checked={regionMode === 'exclude'} onChange={() => setRegionMode('exclude')} /> Исключить</label>
                <input
                  type="search"
                  placeholder="Поиск по регионам"
                  value={regionQuery}
                  onFocus={() => setRegionsOpen(true)}
                  onClick={() => setRegionsOpen(true)}
                  onChange={(e) => {
                    setRegionsOpen(true);
                    setRegionQuery(e.target.value);
                  }}
                />
              </div>
              {regionsOpen ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6, maxHeight: 160, overflow: 'auto', padding: 6, border: '1px solid #eee', borderRadius: 8 }}>
                  {displayRegions.map((r) => (
                    <label key={r.code} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input
                        type="checkbox"
                        checked={regions.includes(r.code)}
                        onChange={(e) => setRegions((prev) => (e.target.checked ? [...prev, r.code] : prev.filter((x) => x !== r.code)))}
                      /> {r.name}
                    </label>
                  ))}
                </div>
              ) : (
                <div className="hint" style={{ color: '#666' }}>Список скрыт. Нажмите в поле поиска, чтобы открыть.</div>
              )}
              <div className="sub" style={{ color: '#666' }}>
                {regions.length === 0
                  ? 'Итог: Вся РФ'
                  : regionMode === 'exclude'
                    ? `Итог: Вся РФ, исключая: ${regions.map(regionLabelByCode).join(', ')}`
                    : `Итог: Только: ${regions.map(regionLabelByCode).join(', ')}`}
              </div>
              <RegionQuickTools
                selectedRegions={regions}
                onChange={setRegions}
                sourceProjects={regionSourceProjects}
              />
            </div>

            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: '0.75rem', color: '#666' }}>Статус проекта</span>
              <select value={status} onChange={(e) => setStatus(e.target.value as ProjectMutableStatus)}>
                <option value="Активен">Активен</option>
                <option value="На паузе">На паузе</option>
              </select>
            </label>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Дни сбора</span>
              <div className="hint">
                Галочки - это дни поступления данных. Данные приходят за предыдущий день (пример: Вт включен → во Вт получите данные за Пн).
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {DAY_VALUES.map((d) => (
                  <label key={d}>
                    <input type="checkbox" checked={days.includes(d)} onChange={() => toggleDay(d)} /> {d}
                  </label>
                ))}
              </div>
            </div>
          </fieldset>

          {resultMessages.length > 0 && (
            <div
              className="sub"
              role="status"
              style={{
                marginTop: 12,
                color: '#8a5a00',
                background: '#fff7e6',
                border: '1px solid #f2d59c',
                padding: '10px 12px',
                borderRadius: 8,
                whiteSpace: 'pre-wrap',
                maxHeight: 180,
                overflow: 'auto',
              }}
            >
              {resultMessages.join('\n\n')}
            </div>
          )}

          <div style={{ position: 'sticky', bottom: 0, background: '#fff', paddingTop: 12, borderTop: '1px solid #eee', display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <button type="button" className="btn btn--ghost" onClick={resetSavedSettings} disabled={isSubmitting}>
              Сбросить настройки
            </button>
            <button type="button" className="btn" onClick={onClose} disabled={isSubmitting}>
              {isDone ? 'Закрыть' : 'Отмена'}
            </button>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={!hasValidList || isSubmitting || isDone}
            >
              {isSubmitting ? 'Создание...' : isDone ? 'Готово' : 'Создать'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default BulkCreateProjectModal;
