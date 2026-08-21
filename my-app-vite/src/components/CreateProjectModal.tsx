import { useEffect, useMemo, useRef, useState } from 'react';
import { normalizeRegionValues, regionLabelByCode } from '../data/regions';
import type { CollectionSource, ProjectMutableStatus } from '../types/project';
import { preventNumberInputWheel } from '../utils/numberInput';
import { normalizePhonesMultiline } from '../utils/phones';
import {
  RAW_SOURCE_CODES,
  formatSourceTextForDisplay,
  toDisplaySourceCode,
} from '../utils/sourceCodeDisplay';
import RegionPicker from './RegionPicker';
import RegionQuickTools, { type RegionSourceProject } from './RegionQuickTools';

type ProjectDataSourceCode = 'B1' | 'B2' | 'B3' | 'B4' | 'UNMAPPED';

type SubmitItem = {
  name: string;
  tag: string;
  collectionSource: CollectionSource;
  dataSourceCode: ProjectDataSourceCode;
  dataLimit: number;
  status: ProjectMutableStatus;
  regionMode: 'include' | 'exclude';
  regions: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  days: ('Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс')[];
};

type CreateProjectModalProps = {
  onClose: () => void;
  onSubmit?: (payloads: SubmitItem[]) => Promise<string | null> | string | null | void;
  uniqueProjectNamesEnabled?: boolean;
  pixelProjectsEnabled?: boolean;
  regionSourceProjects?: RegionSourceProject[];
  settingsStorageKey?: string;
};

// Временное ограничение выбора источников сбора в ЛК. Убрать ограничение после согласования с Prostats.
const DISABLED_COLLECTION_SOURCES = new Set<CollectionSource>([
  'Ретрозвонки',
  'Ретросайты',
  'Пересечение',
]);

const ALL_COLLECTION_SOURCES: CollectionSource[] = [
  'Звонки',
  'Сайты',
  'СМС',
  'Пиксель',
  'Ретрозвонки',
  'Ретросайты',
  'Пересечение',
];

type DayAbbrev = 'Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс';

type CreateProjectSavedSettings = {
  collectionSource?: CollectionSource;
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

const DEFAULT_CREATE_PROJECT_SETTINGS: Required<CreateProjectSavedSettings> = {
  collectionSource: 'Звонки',
  dataLimit: 100,
  status: 'Активен',
  regionMode: 'include',
  regions: [],
  days: ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'],
  b1: true,
  b2: true,
  b3: true,
  b4: true,
};

const DAY_VALUES: DayAbbrev[] = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];

function isCollectionSource(value: unknown): value is CollectionSource {
  return typeof value === 'string' && ALL_COLLECTION_SOURCES.includes(value as CollectionSource);
}

function isMutableStatus(value: unknown): value is ProjectMutableStatus {
  return value === 'Активен' || value === 'На паузе' || value === 'Удалён' || value === 'Архив';
}

function readSavedSettings(storageKey?: string): Required<CreateProjectSavedSettings> {
  if (!storageKey) return DEFAULT_CREATE_PROJECT_SETTINGS;
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return DEFAULT_CREATE_PROJECT_SETTINGS;
    const parsed = JSON.parse(raw) as CreateProjectSavedSettings;
    return {
      collectionSource: isCollectionSource(parsed.collectionSource) ? parsed.collectionSource : DEFAULT_CREATE_PROJECT_SETTINGS.collectionSource,
      dataLimit: Number.isFinite(parsed.dataLimit) ? Number(parsed.dataLimit) : DEFAULT_CREATE_PROJECT_SETTINGS.dataLimit,
      status: isMutableStatus(parsed.status) && parsed.status !== 'Удалён' && parsed.status !== 'Архив' ? parsed.status : DEFAULT_CREATE_PROJECT_SETTINGS.status,
      regionMode: parsed.regionMode === 'exclude' ? 'exclude' : 'include',
      regions: normalizeRegionValues(parsed.regions || []),
      days: Array.isArray(parsed.days)
        ? parsed.days.filter((day): day is DayAbbrev => DAY_VALUES.includes(day as DayAbbrev))
        : DEFAULT_CREATE_PROJECT_SETTINGS.days,
      b1: typeof parsed.b1 === 'boolean' ? parsed.b1 : DEFAULT_CREATE_PROJECT_SETTINGS.b1,
      b2: typeof parsed.b2 === 'boolean' ? parsed.b2 : DEFAULT_CREATE_PROJECT_SETTINGS.b2,
      b3: typeof parsed.b3 === 'boolean' ? parsed.b3 : DEFAULT_CREATE_PROJECT_SETTINGS.b3,
      b4: typeof parsed.b4 === 'boolean' ? parsed.b4 : DEFAULT_CREATE_PROJECT_SETTINGS.b4,
    };
  } catch {
    return DEFAULT_CREATE_PROJECT_SETTINGS;
  }
}

function CreateProjectModal({
  onClose,
  onSubmit,
  uniqueProjectNamesEnabled = false,
  pixelProjectsEnabled = false,
  regionSourceProjects = [],
  settingsStorageKey,
}: CreateProjectModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const initialSettingsRef = useRef<Required<CreateProjectSavedSettings>>(readSavedSettings(settingsStorageKey));
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [name, setName] = useState('');
  const [collectionSource, setCollectionSource] = useState<CollectionSource>(initialSettingsRef.current.collectionSource);
  const [dataLimit, setDataLimit] = useState<number>(initialSettingsRef.current.dataLimit);
  const [status, setStatus] = useState<ProjectMutableStatus>(initialSettingsRef.current.status);

  const [b1, setB1] = useState(initialSettingsRef.current.b1);
  const [b2, setB2] = useState(initialSettingsRef.current.b2);
  const [b3, setB3] = useState(initialSettingsRef.current.b3);
  const [b4, setB4] = useState(initialSettingsRef.current.b4);

  const [regionMode, setRegionMode] = useState<'include'|'exclude'>(initialSettingsRef.current.regionMode);
  const [regionQuery, setRegionQuery] = useState('');
  const [regions, setRegions] = useState<string[]>(initialSettingsRef.current.regions);
  const [regionsOpen, setRegionsOpen] = useState(false);

  const [sitesText, setSitesText] = useState('');
  const [phonesText, setPhonesText] = useState('');
  const [phonesError, setPhonesError] = useState<string | null>(null);
  const [smsSenderName, setSmsSenderName] = useState('');

  const [days, setDays] = useState<DayAbbrev[]>(initialSettingsRef.current.days);

  // Закрытие по Esc и по клику вне отключено: закрываем только кнопками

  const hasName = name.trim().length > 0;
  const trimmedSmsSender = smsSenderName.trim();
  const isSmsSenderValid =
    collectionSource !== 'СМС' || (trimmedSmsSender.length > 0 && !isLikelyPhone(trimmedSmsSender));
  const isPixel = collectionSource === 'Пиксель';

  const availableSources = useMemo(() => (
    ALL_COLLECTION_SOURCES.filter((src) => (
      !DISABLED_COLLECTION_SOURCES.has(src) &&
      (src !== 'Пиксель' || pixelProjectsEnabled)
    ))
  ), [pixelProjectsEnabled]);

  function applySourceDefaults(src: CollectionSource) {
    if (src === 'Звонки' || src === 'Сайты') {
      setB1(true);
      setB2(true);
      setB3(true);
      setB4(true);
    } else if (src === 'СМС') {
      setB1(false);
      setB2(true);
      setB3(true);
      setB4(false);
    } else if (src === 'Пиксель') {
      setB1(false);
      setB2(false);
      setB3(false);
      setB4(false);
    } else {
      setB1(false);
      setB2(true);
      setB3(false);
      setB4(false);
    }
  }

  function changeCollectionSource(src: CollectionSource) {
    setCollectionSource(src);
    applySourceDefaults(src);
  }

  function resetSavedSettings() {
    try {
      if (settingsStorageKey) localStorage.removeItem(settingsStorageKey);
    } catch {
      /* ignore */
    }
    setCollectionSource(DEFAULT_CREATE_PROJECT_SETTINGS.collectionSource);
    setDataLimit(DEFAULT_CREATE_PROJECT_SETTINGS.dataLimit);
    setStatus(DEFAULT_CREATE_PROJECT_SETTINGS.status);
    setB1(DEFAULT_CREATE_PROJECT_SETTINGS.b1);
    setB2(DEFAULT_CREATE_PROJECT_SETTINGS.b2);
    setB3(DEFAULT_CREATE_PROJECT_SETTINGS.b3);
    setB4(DEFAULT_CREATE_PROJECT_SETTINGS.b4);
    setRegionMode(DEFAULT_CREATE_PROJECT_SETTINGS.regionMode);
    setRegions(DEFAULT_CREATE_PROJECT_SETTINGS.regions);
    setRegionQuery('');
    setRegionsOpen(false);
    setDays(DEFAULT_CREATE_PROJECT_SETTINGS.days);
  }

  function saveCurrentSettings() {
    if (!settingsStorageKey) return;
    const settings: Required<CreateProjectSavedSettings> = {
      collectionSource,
      dataLimit: Number.isFinite(dataLimit) ? dataLimit : DEFAULT_CREATE_PROJECT_SETTINGS.dataLimit,
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

  useEffect(() => {
    if (!availableSources.includes(collectionSource)) {
      const nextSource = availableSources[0] || 'Звонки';
      setCollectionSource(nextSource);
      applySourceDefaults(nextSource);
    }
  }, [collectionSource, availableSources]);

  function isLikelyPhone(input: string) {
    const digits = input.replace(/\D+/g, '');
    return digits.length >= 10; // простая проверка, чтобы не указывать реальный номер как имя отправителя
  }

  function parseList(text: string): string[] {
    return text
      .split(/\r?\n/)
      .map(s => s.trim())
      .filter(Boolean);
  }

  function uniqueList(list: string[]): string[] {
    return Array.from(new Set(list));
  }

  const sitesParsed = useMemo(() => parseList(sitesText), [sitesText]);
  const phonesParsed = useMemo(() => parseList(phonesText), [phonesText]);

  function sanitizeSites() {
    const unique = uniqueList(parseList(sitesText));
    setSitesText(unique.join('\n'));
  }

  function sanitizePhones() {
    const res = normalizePhonesMultiline(phonesText);
    setPhonesText(res.displayText);

    if (res.errors.length > 0) {
      const examples = res.errors.slice(0, 5).map((e) => `строка ${e.lineNumber}: "${e.raw}" (${e.reason})`);
      const suffix = res.errors.length > 5 ? `\n… и ещё ${res.errors.length - 5}` : '';
      setPhonesError(
        `Некорректные номера. Нужно: 11 цифр и первая — 7 или 8.\n${examples.join('\n')}${suffix}`,
      );
    } else {
      setPhonesError(null);
    }
  }

  function allowedCodesForSource(src: CollectionSource): Array<'B1' | 'B2' | 'B3' | 'B4'> {
    if (src === 'Сайты' || src === 'Звонки') return ['B1','B2','B3','B4'];
    if (src === 'СМС') return ['B2','B3'];
    if (src === 'Пиксель') return [];
    return ['B2'];
  }

  const allowedCodes = useMemo(() => allowedCodesForSource(collectionSource), [collectionSource]);
  const selectedCodes = useMemo(() => {
    return allowedCodes.filter(c => (c === 'B1' ? b1 : c === 'B2' ? b2 : c === 'B3' ? b3 : b4));
  }, [allowedCodes, b1, b2, b3, b4]);
  const effectiveCodesPreview = useMemo(() => {
    return selectedCodes.length > 0 ? selectedCodes : (allowedCodes.length === 1 ? allowedCodes : []);
  }, [selectedCodes, allowedCodes]);
  const previewLimits = useMemo(() => {
    const total = Number.isFinite(dataLimit) ? dataLimit : 0;
    const n = effectiveCodesPreview.length || 1;
    const base = Math.floor(total / n);
    const rem = total % n;
    // Остаток отдаём "в конец списка" выбранных источников (последним rem элементам).
    return effectiveCodesPreview.map((_, idx) => idx >= (n - rem) ? base + 1 : base);
  }, [dataLimit, effectiveCodesPreview]);

  function toggleDay(day: 'Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс') {
    setDays(prev => prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day]);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    if (isSubmitting) return;
    if (collectionSource === 'СМС') {
      if (!smsSenderName.trim()) return;
      if (isLikelyPhone(smsSenderName)) return;
    }
    if (isPixel) {
      const domain = name.trim();
      const items: SubmitItem[] = [{
        name: domain,
        tag: domain,
        collectionSource,
        dataSourceCode: 'UNMAPPED',
        dataLimit: Number.isFinite(dataLimit) ? dataLimit : 0,
        status,
        regionMode,
        regions: normalizeRegionValues(regions),
        sites: [domain],
        phones: undefined,
        smsSenderName: undefined,
        days,
      }];
      if (!onSubmit) {
        onClose();
        return;
      }
      setIsSubmitting(true);
      setSubmitError(null);
      try {
        const result = await onSubmit(items);
        if (typeof result === 'string' && result.trim()) {
          setSubmitError(result);
          return;
        }
        saveCurrentSettings();
        onClose();
      } finally {
        setIsSubmitting(false);
      }
      return;
    }

    // B-коды для генерации проектов
    const allowedCodes: Array<'B1' | 'B2' | 'B3' | 'B4'> =
      collectionSource === 'Сайты' || collectionSource === 'Звонки'
        ? ['B1','B2','B3','B4']
        : collectionSource === 'СМС'
        ? ['B2','B3']
        : ['B2']; // ретро и пересечение — только B2

    const selectedCodes = allowedCodes.filter(c => (c === 'B1' ? b1 : c === 'B2' ? b2 : c === 'B3' ? b3 : b4));
    const effectiveCodes = selectedCodes.length > 0 ? selectedCodes : allowedCodes.length === 1 ? allowedCodes : [];
    if (effectiveCodes.length === 0) return;

    const sites = collectionSource === 'Сайты' || collectionSource === 'Ретросайты' || collectionSource === 'Пересечение'
      ? parseList(sitesText)
      : undefined;

    let phones: string[] | undefined;
    if (collectionSource === 'Звонки' || collectionSource === 'Ретрозвонки' || collectionSource === 'Пересечение') {
      const res = normalizePhonesMultiline(phonesText);
      setPhonesText(res.displayText);
      if (res.errors.length > 0) {
        const examples = res.errors.slice(0, 5).map((er) => `строка ${er.lineNumber}: "${er.raw}" (${er.reason})`);
        const suffix = res.errors.length > 5 ? `\n… и ещё ${res.errors.length - 5}` : '';
        setPhonesError(`Некорректные номера. Нужно: 11 цифр и первая — 7 или 8.\n${examples.join('\n')}${suffix}`);
        return;
      }
      setPhonesError(null);
      phones = res.normalized.length > 0 ? res.normalized : undefined;
    }

    const totalLimit = Number.isFinite(dataLimit) ? dataLimit : 0;
    const n = effectiveCodes.length;
    const base = Math.floor(totalLimit / n);
    const remainder = totalLimit % n;
    // Остаток отдаём "в конец списка" выбранных источников (последним remainder элементам).
    const perCodeLimits = effectiveCodes.map((_, idx) => (idx >= (n - remainder) ? base + 1 : base));

    const items: SubmitItem[] = effectiveCodes.map((code, idx) => ({
      name: `${code}_${name.trim()}`,
      tag: `${code}_${name.trim()}`,
      collectionSource,
      dataSourceCode: code,
      dataLimit: perCodeLimits[idx],
      status,
      regionMode,
      regions: normalizeRegionValues(regions),
      sites,
      phones,
      smsSenderName: collectionSource === 'СМС' || collectionSource === 'Пересечение' ? (smsSenderName.trim() || undefined) : undefined,
      days,
    }));

    if (!onSubmit) {
      onClose();
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const result = await onSubmit(items);
      if (typeof result === 'string' && result.trim()) {
        setSubmitError(result);
        return;
      }
      saveCurrentSettings();
      onClose();
    } finally {
      setIsSubmitting(false);
    }
  }

  const isWarning = submitError ? submitError.includes('создан') : false;

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
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        className="modal-card"
        style={{
          background: '#fff',
          borderRadius: 8,
          width: '100%',
          maxWidth: 560,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Создать проект</div>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20 }}>
          <div style={{ display: 'grid', gap: 12 }}>
            {submitError && (
              <div
                className="sub"
                role="alert"
                style={{
                  position: 'sticky',
                  top: 0,
                  zIndex: 2,
                  color: isWarning ? '#8a5a00' : '#b00020',
                  background: isWarning ? '#fff7e6' : '#fff4f4',
                  border: `1px solid ${isWarning ? '#f2d59c' : '#f3c6c6'}`,
                  padding: '10px 12px',
                  borderRadius: 8,
                  whiteSpace: 'pre-wrap',
                  marginBottom: 4,
                }}
              >
                {formatSourceTextForDisplay(submitError)}
              </div>
            )}
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: '0.75rem', color: '#666' }}>
                {isPixel ? 'Домен сайта' : 'Название'}
              </span>
              <input
                autoFocus
                type="text"
                placeholder={isPixel ? 'live.vyshka.su' : 'Название проекта'}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              {!hasName && (
                <span className="hint" style={{ color: '#666' }}>
                  {isPixel
                    ? 'Введите домен сайта клиента.'
                    : 'Сначала введите название проекта — затем станут доступны остальные настройки.'}
                </span>
              )}
              {hasName && uniqueProjectNamesEnabled && (
                <span className="hint" style={{ color: '#666' }}>
                  Внутренний идентификатор клиента будет добавлен автоматически. В ЛК проект останется виден с обычным названием.
                </span>
              )}
            </label>

            <fieldset
              disabled={!hasName}
              style={{
                border: 0,
                padding: 0,
                margin: 0,
                display: 'grid',
                gap: 12,
                opacity: hasName ? 1 : 0.55,
              }}
              aria-disabled={!hasName}
            >
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <label style={{ display: 'grid', gap: 6 }}>
                  <span style={{ fontSize: '0.75rem', color: '#666' }}>Источник сбора</span>
                  <select value={collectionSource} onChange={(e) => changeCollectionSource(e.target.value as CollectionSource)}>
                    {availableSources.map((src) => (
                      <option key={src} value={src}>{src}</option>
                    ))}
                  </select>
                </label>

                <label style={{ display: 'grid', gap: 6 }}>
                  <span style={{ fontSize: '0.75rem', color: '#666' }}>Лимит</span>
                  <input
                    type="number"
                    min={0}
                    value={dataLimit}
                    onChange={(e) => setDataLimit(Number(e.target.value))}
                    onWheel={preventNumberInputWheel}
                  />
                </label>
              </div>

              {!isPixel && (
              <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Источник данных</span>
              <div className="sub" style={{ color: '#666' }}>
                Можно выбрать одного или нескольких поставщиков. Для каждого выбранного будет создан отдельный проект с префиксом поставщика (A/B/C/D).
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {RAW_SOURCE_CODES.map(code => {
                  const allowed = allowedCodes.includes(code);
                  const singleForced = allowedCodes.length === 1 && allowedCodes[0] === 'B2' && code === 'B2';
                  const active = code === 'B1' ? b1 : code === 'B2' ? b2 : code === 'B3' ? b3 : b4;
                  const setActive = (val: boolean) => {
                    if (code === 'B1') setB1(val); else if (code === 'B2') setB2(val); else if (code === 'B3') setB3(val); else setB4(val);
                  };
                  return (
                    <button
                      key={code}
                      type="button"
                      aria-pressed={active}
                      disabled={!allowed || singleForced}
                      onClick={() => setActive(!active)}
                      style={{
                        padding: '6px 10px',
                        borderRadius: 999,
                        border: '1px solid',
                        borderColor: active ? '#6a5cff' : '#dcdce6',
                        background: active ? '#6a5cff' : '#fff',
                        color: active ? '#fff' : '#1d1d1f',
                        opacity: allowed ? 1 : 0.5,
                        cursor: (!allowed || singleForced) ? 'not-allowed' : 'pointer',
                      }}
                    >
                      {toDisplaySourceCode(code)}
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: '0.75rem', color: '#666' }}>
                {effectiveCodesPreview.length > 0
                  ? `Будет создано ${effectiveCodesPreview.length} проектов. Лимит ${dataLimit} / день распределится между ними: ` + effectiveCodesPreview.map((c, i) => `${toDisplaySourceCode(c)}:${previewLimits[i]}`).join(', ')
                  : 'Выберите источники данных'}
              </div>
              </div>
              )}

              {(collectionSource === 'Сайты' || collectionSource === 'Ретросайты' || collectionSource === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Список сайтов</span>
                <span className="hint">По одному в строке</span>
                <textarea
                  rows={8}
                  placeholder={"site.ru\nwww.site.ru\nhttps://site.ru"}
                  value={sitesText}
                  onChange={(e) => setSitesText(e.target.value)}
                  onBlur={sanitizeSites}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
                />
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Элементов: {sitesParsed.length}, уникальных: {uniqueList(sitesParsed).length}</span>
              </label>
            )}

              {(collectionSource === 'Звонки' || collectionSource === 'Ретрозвонки' || collectionSource === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Телефоны конкурентов/целевых компаний</span>
                <span className="hint">По одному номеру в строке, строго 11 цифр, начинаем с 7 или 8</span>
                <textarea
                  rows={8}
                  placeholder={"79231234567\n74951234567"}
                  value={phonesText}
                  onChange={(e) => {
                    setPhonesText(e.target.value);
                    setPhonesError(null);
                  }}
                  onBlur={sanitizePhones}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
                />
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Элементов: {phonesParsed.length}, уникальных: {uniqueList(phonesParsed).length}</span>
                {phonesError && (
                  <div className="sub" style={{ color: '#d00', whiteSpace: 'pre-line' }}>
                    {phonesError}
                  </div>
                )}
              </label>
            )}

              {(collectionSource === 'СМС' || collectionSource === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Наименование отправителя (СМС)</span>
                <input
                  type="text"
                  placeholder="Требуется точное имя отправителя; если укажете физический номер — проект не будет запущен"
                  value={smsSenderName}
                  onChange={(e) => setSmsSenderName(e.target.value)}
                />
                {!isSmsSenderValid && (
                  <div className="sub" style={{ color: '#d00' }}>
                    Для СМС обязателен корректный sender (не пустой и не номер).
                  </div>
                )}
              </label>
            )}

              <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Регионы</span>
              <div className="hint">Если ничего не выбрано, сбор идет по всей РФ. Чтобы выбрать регион — кликните в поле поиска.</div>
              <div className="radio-row" style={{ alignItems: 'center' }}>
                <label><input type="radio" name="regionMode" checked={regionMode==='include'} onChange={() => setRegionMode('include')} /> Включить</label>
                <label><input type="radio" name="regionMode" checked={regionMode==='exclude'} onChange={() => setRegionMode('exclude')} /> Исключить</label>
                <input
                  type="search"
                  placeholder="Поиск по регионам и округам"
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
                <RegionPicker
                  selectedRegions={regions}
                  onChange={setRegions}
                  query={regionQuery}
                />
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
                {(['Пн','Вт','Ср','Чт','Пт','Сб','Вс'] as const).map(d => (
                  <label key={d}>
                    <input type="checkbox" checked={days.includes(d)} onChange={() => toggleDay(d)} /> {d}
                  </label>
                ))}
              </div>
              </div>
            </fieldset>
          </div>

          <div style={{ position: 'sticky', bottom: 0, background: '#fff', paddingTop: 12, borderTop: '1px solid #eee', display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <button type="button" className="btn btn--ghost" onClick={resetSavedSettings} disabled={isSubmitting}>
              Сбросить настройки
            </button>
            <button type="button" className="btn" onClick={onClose} disabled={isSubmitting}>Отмена</button>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={!hasName || !isSmsSenderValid || isSubmitting}
            >
              {isSubmitting
                ? (uniqueProjectNamesEnabled ? 'Создаём и присваиваем уникальные имена...' : 'Создание...')
                : 'Создать'}
            </button>
          </div>
          {isSubmitting && uniqueProjectNamesEnabled && (
            <div className="sub" style={{ marginTop: 10 }}>
              Создаём проекты и подтверждаем уникальные имена у поставщика. Это может занять несколько секунд.
            </div>
          )}
        </form>
      </div>
    </div>
  );
}

export default CreateProjectModal;


