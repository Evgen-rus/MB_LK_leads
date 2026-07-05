import { useEffect, useMemo, useRef, useState } from 'react';
import { regions as allRegions, normalizeRegionValues, regionLabelByCode } from '../data/regions';
import type { CollectionSource, ProjectMutableStatus } from '../types/project';
import { preventNumberInputWheel } from '../utils/numberInput';
import { normalizePhonesMultiline } from '../utils/phones';
import {
  RAW_SOURCE_CODES,
  formatSourceTextForDisplay,
  toDisplaySourceCode,
} from '../utils/sourceCodeDisplay';
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

function CreateProjectModal({
  onClose,
  onSubmit,
  uniqueProjectNamesEnabled = false,
  pixelProjectsEnabled = false,
  regionSourceProjects = [],
}: CreateProjectModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [name, setName] = useState('');
  const [collectionSource, setCollectionSource] = useState<CollectionSource>('Звонки');
  const [dataLimit, setDataLimit] = useState<number>(100);
  const [status, setStatus] = useState<ProjectMutableStatus>('Активен');

  const [b1, setB1] = useState(true);
  const [b2, setB2] = useState(true);
  const [b3, setB3] = useState(true);
  const [b4, setB4] = useState(true);

  const [regionMode, setRegionMode] = useState<'include'|'exclude'>('include');
  const [regionQuery, setRegionQuery] = useState('');
  const [regions, setRegions] = useState<string[]>([]);
  const [regionsOpen, setRegionsOpen] = useState(false);

  const [sitesText, setSitesText] = useState('');
  const [phonesText, setPhonesText] = useState('');
  const [phonesError, setPhonesError] = useState<string | null>(null);
  const [smsSenderName, setSmsSenderName] = useState('');

  const [days, setDays] = useState<('Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс')[]>(['Пн','Вт','Ср','Чт','Пт','Сб','Вс']);

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

  useEffect(() => {
    if (!availableSources.includes(collectionSource)) {
      setCollectionSource(availableSources[0] || 'Звонки');
    }
  }, [collectionSource, availableSources]);

  const baseRegionIndex = useMemo(() => {
    const m = new Map<string, number>();
    allRegions.forEach((r, i) => m.set(r.code, i));
    return m;
  }, []);

  const filteredRegions = useMemo(() => {
    const q = regionQuery.trim().toLowerCase();
    if (!q) return allRegions;
    return allRegions.filter((r) => (
      r.name.toLowerCase().includes(q) || r.code.includes(q)
    ));
  }, [regionQuery]);

  const displayRegions = useMemo(() => {
    const list = filteredRegions.slice();
    list.sort((a, b) => {
      const aSel = regions.includes(a.code) ? 1 : 0;
      const bSel = regions.includes(b.code) ? 1 : 0;
      if (aSel !== bSel) return bSel - aSel; // выбранные — наверх
      const ai = baseRegionIndex.get(a.code) ?? 0;
      const bi = baseRegionIndex.get(b.code) ?? 0;
      return ai - bi; // сохраняем исходный порядок внутри групп
    });
    return list;
  }, [filteredRegions, regions, baseRegionIndex]);

  useEffect(() => {
    // Ограничения по B-кодам в зависимости от источника сбора
    if (collectionSource === 'Звонки') {
      setB1(true);
      setB2(true);
      setB3(true);
      setB4(true);
    } else if (collectionSource === 'СМС') {
      setB1(false);
      setB2(true);
      setB3(true);
      setB4(false);
    } else if (collectionSource === 'Пиксель') {
      setB1(false);
      setB2(false);
      setB3(false);
      setB4(false);
    } else if (
      collectionSource === 'Ретросайты' ||
      collectionSource === 'Ретрозвонки' ||
      collectionSource === 'Пересечение'
    ) {
      setB1(false);
      setB2(true);
      setB3(false);
      setB4(false);
    }
  }, [collectionSource]);

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
                  <select value={collectionSource} onChange={(e) => setCollectionSource(e.target.value as CollectionSource)}>
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
                        onChange={(e) => setRegions((prev) => e.target.checked ? [...prev, r.code] : prev.filter(x => x !== r.code))}
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


