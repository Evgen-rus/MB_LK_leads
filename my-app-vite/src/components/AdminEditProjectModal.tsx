// Модальное окно редактирования проекта для админа
// Включает возможность изменять deliveryStatus
import { useMemo, useRef, useState } from 'react';
import { regions as allRegions, normalizeRegionValues, regionLabelByCode } from '../data/regions';
import type { ProjectStatus, CollectionSource } from '../types/project';
import { updateAdminProject, type AdminProject, type AdminProjectUpdate } from '../api';
import { preventNumberInputWheel } from '../utils/numberInput';
import { normalizePhonesMultiline } from '../utils/phones';
import {
  RAW_SOURCE_CODES,
  formatProjectNameForDisplay,
  formatProjectNameForSubmit,
  formatSourceTextForDisplay,
  getDisplayProjectPrefix,
  getRawProjectPrefix,
  toDisplaySourceCode,
  type RawSourceCode,
} from '../utils/sourceCodeDisplay';

type DayAbbrev = 'Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс';

type AdminEditProjectModalProps = {
  project: AdminProject;
  onClose: () => void;
  onSubmit?: (updated: AdminProject) => void;
  readOnly?: boolean;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return formatSourceTextForDisplay(msg);
  }
  return formatSourceTextForDisplay(fallback);
}

function AdminEditProjectModal({ project, onClose, onSubmit, readOnly = false }: AdminEditProjectModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const [name, setName] = useState(formatProjectNameForDisplay(project.name));
  const [status, setStatus] = useState<ProjectStatus>(project.status);
  const [dataLimit, setDataLimit] = useState<number>(project.dataLimit);

  const [regionMode] = useState<'include'|'exclude'>(project.regionMode || 'include');
  const [regionQuery, setRegionQuery] = useState('');
  const [regions, setRegions] = useState<string[]>(normalizeRegionValues(project.regions || []));
  const [regionsOpen, setRegionsOpen] = useState(false);

  const [sitesText, setSitesText] = useState((project.sites || []).join('\n'));
  const [phonesText, setPhonesText] = useState((project.phones || []).join('\n'));
  const [sitesError, setSitesError] = useState<string | null>(null);
  const [smsSenderName, setSmsSenderName] = useState(project.smsSenderName || '');
  const [phonesError, setPhonesError] = useState<string | null>(null);

  const initialDays = useMemo<DayAbbrev[]>(() => {
    const map: Record<string, DayAbbrev> = { 'Пн.':'Пн','Вт.':'Вт','Ср.':'Ср','Чт.':'Чт','Пт.':'Пт','Сб.':'Сб','Вс.':'Вс' };
    const parts = (project.daysReceived || '').split(/\s+/).filter(Boolean);
    const out: DayAbbrev[] = [];
    parts.forEach(p => { if (map[p]) out.push(map[p]); });
    return out;
  }, [project.daysReceived]);
  const originalDaysRef = useRef<DayAbbrev[]>(initialDays);
  const [days, setDays] = useState<DayAbbrev[]>(initialDays);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uniqueNameApplied = project.name.startsWith(
    getRawProjectPrefix(project.dataSourceCode, { projectId: project.id, uniqueNameApplied: true }),
  );
  const protectedNamePrefix = getDisplayProjectPrefix(project.dataSourceCode, {
    projectId: project.id,
    uniqueNameApplied,
  });
  const protectedNameHint = uniqueNameApplied
    ? `Технический префикс "${protectedNamePrefix}" обязателен и не редактируется.`
    : `Префикс источника "${protectedNamePrefix}" обязателен и не должен удаляться.`;

  function parseList(text: string): string[] {
    return text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  }
  function uniqueList(list: string[]): string[] {
    return Array.from(new Set(list));
  }

  const sitesParsed = useMemo(() => parseList(sitesText), [sitesText]);
  const phonesParsed = useMemo(() => parseList(phonesText), [phonesText]);
  function sanitizeSites() { setSitesText(uniqueList(parseList(sitesText)).join('\n')); }
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
      if (aSel !== bSel) return bSel - aSel;
      const ai = baseRegionIndex.get(a.code) ?? 0;
      const bi = baseRegionIndex.get(b.code) ?? 0;
      return ai - bi;
    });
    return list;
  }, [filteredRegions, regions, baseRegionIndex]);

  function toggleDay(day: DayAbbrev) {
    if (readOnly) return;
    setDays(prev => prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day]);
  }

  const weekOrder: Record<DayAbbrev, number> = { 'Пн': 1, 'Вт': 2, 'Ср': 3, 'Чт': 4, 'Пт': 5, 'Сб': 6, 'Вс': 7 };

  function normalizeString(value: string | undefined | null): string {
    return (value ?? '').trim();
  }

  function normalizeOptionalString(value: string | undefined | null): string | undefined {
    const v = normalizeString(value);
    return v ? v : undefined;
  }

  function normalizeStringArray(value: unknown): string[] {
    const arr = Array.isArray(value) ? (value as unknown[]) : [];
    const out = arr
      .map((x) => normalizeString(typeof x === 'string' ? x : String(x ?? '')))
      .filter(Boolean);
    return Array.from(new Set(out)).sort();
  }

  function normalizeRegionsForCompare(values: string[] | undefined | null): string[] {
    const normalized = normalizeRegionValues(values || []);
    return Array.from(new Set(normalized)).sort();
  }

  function normalizeDays(value: DayAbbrev[]): DayAbbrev[] {
    const unique = Array.from(new Set(value));
    unique.sort((a, b) => (weekOrder[a] ?? 999) - (weekOrder[b] ?? 999));
    return unique;
  }

  function arraysEqual(a: string[], b: string[]): boolean {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) {
      if (a[i] !== b[i]) return false;
    }
    return true;
  }

  function daysEqual(a: DayAbbrev[], b: DayAbbrev[]): boolean {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) {
      if (a[i] !== b[i]) return false;
    }
    return true;
  }

  const isDirty = useMemo(() => {
    const sourceNow: CollectionSource = project.collectionSource;

    const original = {
      name: normalizeString(formatProjectNameForDisplay(project.name)),
      status: project.status,
      dataLimit: Number.isFinite(project.dataLimit) ? project.dataLimit : 0,
      regionMode: (project.regionMode || 'include') as 'include' | 'exclude',
      regions: normalizeRegionsForCompare(project.regions || []),
      sites: (sourceNow === 'Сайты' || sourceNow === 'Ретросайты' || sourceNow === 'Пересечение')
        ? normalizeStringArray(project.sites || [])
        : [],
      phones: (sourceNow === 'Звонки' || sourceNow === 'Ретрозвонки' || sourceNow === 'Пересечение')
        ? normalizeStringArray(project.phones || [])
        : [],
      smsSenderName: (sourceNow === 'СМС' || sourceNow === 'Пересечение')
        ? normalizeOptionalString(project.smsSenderName)
        : undefined,
      // исходный набор дней (фикс isDirty для чекбоксов)
      days: normalizeDays(originalDaysRef.current),
    };

    let phonesNow: string[] = [];
    if (sourceNow === 'Звонки' || sourceNow === 'Ретрозвонки' || sourceNow === 'Пересечение') {
      const res = normalizePhonesMultiline(phonesText);
      phonesNow = normalizeStringArray(res.normalized);
    }

    const current = {
      name: normalizeString(name),
      status,
      dataLimit: Number.isFinite(dataLimit) ? dataLimit : 0,
      regionMode,
      regions: normalizeRegionsForCompare(regions),
      sites: (sourceNow === 'Сайты' || sourceNow === 'Ретросайты' || sourceNow === 'Пересечение')
        ? normalizeStringArray(uniqueList(sitesParsed))
        : [],
      phones: phonesNow,
      smsSenderName: (sourceNow === 'СМС' || sourceNow === 'Пересечение')
        ? normalizeOptionalString(smsSenderName)
        : undefined,
      days: normalizeDays(days),
    };

    if (original.name !== current.name) return true;
    if (original.status !== current.status) return true;
    if (original.dataLimit !== current.dataLimit) return true;
    if (original.regionMode !== current.regionMode) return true;
    if (!arraysEqual(original.regions, current.regions)) return true;
    if (!arraysEqual(original.sites, current.sites)) return true;
    if (!arraysEqual(original.phones, current.phones)) return true;
    if (original.smsSenderName !== current.smsSenderName) return true;
    if (!daysEqual(original.days, current.days)) return true;
    return false;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, status, dataLimit, regionMode, regions, sitesParsed, phonesText, smsSenderName, days, project.collectionSource]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (readOnly) return;
    if (!name.trim()) return;
    if (!isDirty) return;
    const normalizedName = name.trim();
    if (!normalizedName.startsWith(protectedNamePrefix)) {
      setError(protectedNameHint);
      return;
    }
    if (!normalizedName.slice(protectedNamePrefix.length).trim()) {
      setError('Название проекта после технического префикса не может быть пустым.');
      return;
    }

    const source: CollectionSource = project.collectionSource;
    if (source === 'СМС' || source === 'Пересечение') {
      const digits = (smsSenderName || '').replace(/\D+/g, '');
      if (!smsSenderName.trim() || digits.length >= 10) return;
    }

    if (source === 'Сайты' || source === 'Ретросайты' || source === 'Пересечение') {
      const normalizedSites = uniqueList(sitesParsed);
      if (normalizedSites.length === 0) {
        setSitesError('Укажите минимум 1 сайт.');
        return;
      }
      setSitesError(null);
    } else {
      setSitesError(null);
    }

    let phones: string[] | undefined;
    if (source === 'Звонки' || source === 'Ретрозвонки' || source === 'Пересечение') {
      const res = normalizePhonesMultiline(phonesText);
      setPhonesText(res.displayText);
      if (res.errors.length > 0) {
        const examples = res.errors.slice(0, 5).map((er) => `строка ${er.lineNumber}: "${er.raw}" (${er.reason})`);
        const suffix = res.errors.length > 5 ? `\n… и ещё ${res.errors.length - 5}` : '';
        setPhonesError(`Некорректные номера. Нужно: 11 цифр и первая — 7 или 8.\n${examples.join('\n')}${suffix}`);
        return;
      }
      if (res.normalized.length === 0) {
        setPhonesError('Укажите минимум 1 номер телефона.');
        return;
      }
      setPhonesError(null);
      phones = res.normalized;
    }

    if (status === 'Блокировка оператора') {
      setError('Чтобы сохранить изменения, выберите статус «Активен» или «На паузе».');
      return;
    }

    const payload: AdminProjectUpdate = {
      name: formatProjectNameForSubmit(normalizedName),
      tag: project.tag,
      status,
      // Статус отгрузки больше не редактируем в модалке — отправляем текущее значение
      deliveryStatus: project.deliveryStatus,
      dataLimit: Number.isFinite(dataLimit) ? dataLimit : 0,
      regionMode,
      regions: normalizeRegionValues(regions),
      sites: (source === 'Сайты' || source === 'Ретросайты' || source === 'Пересечение') ? uniqueList(sitesParsed) : undefined,
      phones,
      smsSenderName: (source === 'СМС' || source === 'Пересечение') ? (smsSenderName.trim() || undefined) : undefined,
      days,
    };

    setSaving(true);
    setError(null);

    try {
      const result = await updateAdminProject(project.id, payload);
      onSubmit?.(result.project);
      if (result.warning) {
        window.dispatchEvent(new CustomEvent('app-toast', { detail: formatSourceTextForDisplay(result.warning) }));
      }
      onClose();
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Ошибка при сохранении'));
    } finally {
      setSaving(false);
    }
  }

  const bActive = (code: RawSourceCode) => project.dataSourceCode === code;
  const source: CollectionSource = project.collectionSource;

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16 }}
    >
      <div ref={dialogRef} role="dialog" aria-modal="true" className="modal-card" style={{ background: '#fff', borderRadius: 8, width: '100%', maxWidth: 560, maxHeight: '90vh', overflowY: 'auto', boxShadow: '0 10px 30px rgba(0,0,0,0.2)' }}>
        <div style={{ padding: 20, borderBottom: '1px solid #eee' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>
            Редактировать проект (Админ)
          </div>
          <div style={{ fontSize: '0.875rem', color: '#666', marginTop: 4 }}>
            Клиент: {project.user.login} (id: {project.user.id})
          </div>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20 }}>
          {error && (
            <div style={{ color: '#d00', marginBottom: 12, padding: 8, background: '#fee', borderRadius: 4 }}>
              {error}
            </div>
          )}
          <div style={{ display: 'grid', gap: 12 }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Название</span>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} disabled={readOnly} />
              <span className="hint" style={{ color: '#666' }}>
                {protectedNameHint}
              </span>
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Источник сбора</span>
                <select value={source} disabled>
                  <option value={source}>{source}</option>
                </select>
              </label>

              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Лимит</span>
                <input type="number" min={0} value={dataLimit} onChange={(e) => setDataLimit(Number(e.target.value))} onWheel={preventNumberInputWheel} disabled={readOnly} />
              </label>
            </div>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Источник данных</span>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {RAW_SOURCE_CODES.map(code => (
                  <button key={code} type="button" aria-pressed={bActive(code)} disabled style={{ padding: '6px 10px', borderRadius: 999, border: '1px solid', borderColor: bActive(code) ? '#6a5cff' : '#dcdce6', background: bActive(code) ? '#6a5cff' : '#fff', color: bActive(code) ? '#fff' : '#1d1d1f', opacity: bActive(code) ? 1 : 0.6 }}>
                    {toDisplaySourceCode(code)}
                  </button>
                ))}
              </div>
            </div>

            {(source === 'Сайты' || source === 'Ретросайты' || source === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Список сайтов</span>
                <span className="hint">По одному в строке</span>
                <textarea
                  rows={8}
                  placeholder={"site.ru\nwww.site.ru\nhttps://site.ru"}
                  value={sitesText}
                  onChange={(e) => {
                    setSitesText(e.target.value);
                    setSitesError(null);
                  }}
                  onBlur={sanitizeSites}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
                  disabled={readOnly}
                />
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Элементов: {sitesParsed.length}, уникальных: {uniqueList(sitesParsed).length}</span>
                {sitesError && (
                  <div className="sub" style={{ color: '#d00', whiteSpace: 'pre-line' }}>
                    {sitesError}
                  </div>
                )}
              </label>
            )}

            {(source === 'Звонки' || source === 'Ретрозвонки' || source === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Список телефонов</span>
                <span className="hint">По одному номеру в строке, строго 11 цифр, начинаем с 7 или 8</span>
                <textarea rows={8} placeholder={"79231234567\n74951234567"} value={phonesText} onChange={(e) => setPhonesText(e.target.value)} onBlur={sanitizePhones} style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }} disabled={readOnly} />
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Элементов: {phonesParsed.length}, уникальных: {uniqueList(phonesParsed).length}</span>
                {phonesError && (
                  <div className="sub" style={{ color: '#d00', whiteSpace: 'pre-line' }}>
                    {phonesError}
                  </div>
                )}
              </label>
            )}

            {(source === 'СМС' || source === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Наименование отправителя (СМС)</span>
                <input type="text" placeholder="Требуется точное имя отправителя; если укажете физический номер — проект не будет запущен" value={smsSenderName} onChange={(e) => setSmsSenderName(e.target.value)} disabled={readOnly} />
              </label>
            )}

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Регионы</span>
              <div className="hint">Если ничего не выбрано, сбор идет по всей РФ. Чтобы выбрать регион — кликните в поле поиска.</div>
              <div className="radio-row" style={{ alignItems: 'center' }}>
                <label><input type="radio" name="regionMode" checked={regionMode==='include'} disabled /> Включить</label>
                <label><input type="radio" name="regionMode" checked={regionMode==='exclude'} disabled /> Исключить</label>
                <input
                  type="search"
                  placeholder="Поиск по регионам"
                  value={regionQuery}
                  onFocus={() => { if (!readOnly) setRegionsOpen(true); }}
                  onClick={() => { if (!readOnly) setRegionsOpen(true); }}
                  onChange={(e) => {
                    if (readOnly) return;
                    setRegionsOpen(true);
                    setRegionQuery(e.target.value);
                  }}
                  disabled={readOnly}
                />
              </div>
              <div className="hint" style={{ color: '#666' }}>
                Менять можно список регионов. Режим (включить/исключить) - только при создании проекта.
              </div>
              {regionsOpen ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6, maxHeight: 160, overflow: 'auto', padding: 6, border: '1px solid #eee', borderRadius: 8 }}>
                  {displayRegions.map((r) => (
                    <label key={r.code} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input
                        type="checkbox"
                        checked={regions.includes(r.code)}
                        onChange={(e) => setRegions((prev) => e.target.checked ? [...prev, r.code] : prev.filter(x => x !== r.code))}
                        disabled={readOnly}
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
            </div>

            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Статус проекта</span>
              <select value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)} disabled={readOnly}>
                {status === 'Блокировка оператора' && (
                  <option value="Блокировка оператора" disabled>Блокировка оператора</option>
                )}
                <option value="Активен">Активен</option>
                <option value="На паузе">На паузе</option>
                <option value="Удалён">Удалён</option>
              </select>
            </label>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title" title="Сбор данных не осуществляется в те дни, которые не отмечены галочкой">Дни получения номеров</span>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {(['Пн','Вт','Ср','Чт','Пт','Сб','Вс'] as const).map(d => (
                  <label key={d}>
                    <input type="checkbox" checked={days.includes(d)} onChange={() => toggleDay(d)} disabled={readOnly} /> {d}
                  </label>
                ))}
              </div>
            </div>
          </div>

          <div style={{ position: 'sticky', bottom: 0, background: '#fff', paddingTop: 12, borderTop: '1px solid #eee', display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            {saving && (
              <div style={{ marginRight: 'auto', color: '#8a5a00', fontSize: '0.9rem' }}>
                Ждём ответ от провайдера…
              </div>
            )}
            <button type="button" className="btn" onClick={onClose} disabled={saving}>Закрыть</button>
            {readOnly ? (
              <span className="sub" style={{ alignSelf: 'center', color: '#666' }}>
                Только просмотр (редактировать через ЛК клиента)
              </span>
            ) : (
              <>
                {!isDirty && (
                  <span className="sub" style={{ alignSelf: 'center', color: '#666', marginRight: 8 }}>
                    Нет изменений
                  </span>
                )}
                <button type="submit" className="btn btn--primary" disabled={!isDirty || saving}>
                  {saving ? 'Сохранение...' : 'Сохранить'}
                </button>
              </>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

export default AdminEditProjectModal;

