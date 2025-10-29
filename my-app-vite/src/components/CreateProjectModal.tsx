import { useEffect, useMemo, useRef, useState } from 'react';
import { regions as allRegions } from '../data/regions';
import type { CollectionSource, ProjectStatus } from '../types/project';

type SubmitItem = {
  name: string;
  tag: string;
  collectionSource: CollectionSource;
  dataSourceCode: 'B1' | 'B2' | 'B3' | 'B4';
  dataLimit: number;
  status: ProjectStatus;
  regionMode: 'include' | 'exclude';
  regions: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  days: ('Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс')[];
};

type CreateProjectModalProps = {
  onClose: () => void;
  onSubmit?: (payloads: SubmitItem[]) => void;
};

function CreateProjectModal({ onClose, onSubmit }: CreateProjectModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const [name, setName] = useState('');
  const [tag, setTag] = useState('');
  const [collectionSource, setCollectionSource] = useState<CollectionSource>('Звонки');
  const [dataLimit, setDataLimit] = useState<number>(100);
  const [status, setStatus] = useState<ProjectStatus>('Активен');

  const [b1, setB1] = useState(true);
  const [b2, setB2] = useState(true);
  const [b3, setB3] = useState(true);
  const [b4, setB4] = useState(false);

  const [regionMode, setRegionMode] = useState<'include'|'exclude'>('include');
  const [regionQuery, setRegionQuery] = useState('');
  const [regions, setRegions] = useState<string[]>([]);

  const [sitesText, setSitesText] = useState('');
  const [phonesText, setPhonesText] = useState('');
  const [smsSenderName, setSmsSenderName] = useState('');

  const [days, setDays] = useState<('Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс')[]>(['Вт','Ср','Чт','Пт','Сб']);

  useEffect(() => {
    setTag(name);
  }, [name]);

  // Закрытие по Esc и по клику вне отключено: закрываем только кнопками

  const filteredRegions = useMemo(() => {
    const q = regionQuery.trim().toLowerCase();
    if (!q) return allRegions;
    return allRegions.filter(r => r.toLowerCase().includes(q));
  }, [regionQuery]);

  useEffect(() => {
    // Ограничения по B-кодам в зависимости от источника сбора
    if (collectionSource === 'Звонки') {
      setB1(true);
      setB2(true);
      setB3(true);
      setB4(false);
    } else if (collectionSource === 'СМС') {
      setB1(false);
      setB2(true);
      setB3(true);
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
    const unique = uniqueList(parseList(phonesText));
    setPhonesText(unique.join('\n'));
  }

  function allowedCodesForSource(src: CollectionSource): ('B1'|'B2'|'B3'|'B4')[] {
    if (src === 'Сайты' || src === 'Звонки') return ['B1','B2','B3','B4'];
    if (src === 'СМС') return ['B2','B3'];
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
    return effectiveCodesPreview.map((_, idx) => idx < rem ? base + 1 : base);
  }, [dataLimit, effectiveCodesPreview]);

  function toggleDay(day: 'Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс') {
    setDays(prev => prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day]);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    if (collectionSource === 'СМС') {
      if (!smsSenderName.trim()) return;
      if (isLikelyPhone(smsSenderName)) return;
    }
    // B-коды для генерации проектов
    const allowedCodes: ('B1'|'B2'|'B3'|'B4')[] =
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
    const phones = collectionSource === 'Звонки' || collectionSource === 'Ретрозвонки' || collectionSource === 'Пересечение'
      ? parseList(phonesText)
      : undefined;

    const totalLimit = Number.isFinite(dataLimit) ? dataLimit : 0;
    const n = effectiveCodes.length;
    const base = Math.floor(totalLimit / n);
    const remainder = totalLimit % n;
    const perCodeLimits = effectiveCodes.map((_, idx) => (idx < remainder ? base + 1 : base));

    const items: SubmitItem[] = effectiveCodes.map((code, idx) => ({
      name: `${code}_${name.trim()}`,
      tag: `${code}_${(tag.trim() || name.trim())}`,
      collectionSource,
      dataSourceCode: code,
      dataLimit: perCodeLimits[idx],
      status,
      regionMode,
      regions,
      sites,
      phones,
      smsSenderName: collectionSource === 'СМС' || collectionSource === 'Пересечение' ? (smsSenderName.trim() || undefined) : undefined,
      days,
    }));

    onSubmit?.(items);
    onClose();
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
          <div style={{ fontSize: 18, fontWeight: 600 }}>Создать проект</div>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20 }}>
          <div style={{ display: 'grid', gap: 12 }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 12, color: '#666' }}>Название</span>
              <input
                autoFocus
                type="text"
                placeholder="Например, [LR172] тест6"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>

            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 12, color: '#666' }}>Тег</span>
              <input
                type="text"
                placeholder="По умолчанию как название"
                value={tag}
                onChange={(e) => setTag(e.target.value)}
              />
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 12, color: '#666' }}>Источник сбора</span>
                <select value={collectionSource} onChange={(e) => setCollectionSource(e.target.value as CollectionSource)}>
                  <option value="Звонки">Звонки</option>
                  <option value="Сайты">Сайты</option>
                  <option value="СМС">СМС</option>
                  <option value="Ретрозвонки">Ретрозвонки</option>
                  <option value="Ретросайты">Ретросайты</option>
                  <option value="Пересечение">Пересечение</option>
                </select>
              </label>

              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 12, color: '#666' }}>Лимит</span>
                <input
                  type="number"
                  min={0}
                  value={dataLimit}
                  onChange={(e) => setDataLimit(Number(e.target.value))}
                />
              </label>
            </div>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Источник данных</span>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(['B1','B2','B3','B4'] as const).map(code => {
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
                      {code}
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: 12, color: '#666' }}>
                {effectiveCodesPreview.length > 0
                  ? `Будет создано: ${effectiveCodesPreview.length} — ` + effectiveCodesPreview.map((c, i) => `${c}:${previewLimits[i]}`).join(', ')
                  : 'Выберите источники данных'}
              </div>
            </div>

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
                <span style={{ fontSize: 12, color: '#666' }}>Элементов: {sitesParsed.length}, уникальных: {uniqueList(sitesParsed).length}</span>
              </label>
            )}

            {(collectionSource === 'Звонки' || collectionSource === 'Ретрозвонки' || collectionSource === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Список телефонов</span>
                <span className="hint">По одному в строке</span>
                <textarea
                  rows={8}
                  placeholder={"Вставьте номера по одному в строке. Допустимые форматы: 79..., 7 495..., +7 ..."}
                  value={phonesText}
                  onChange={(e) => setPhonesText(e.target.value)}
                  onBlur={sanitizePhones}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
                />
                <span style={{ fontSize: 12, color: '#666' }}>Элементов: {phonesParsed.length}, уникальных: {uniqueList(phonesParsed).length}</span>
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
              </label>
            )}

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Регионы</span>
              <div className="radio-row" style={{ alignItems: 'center' }}>
                <label><input type="radio" name="regionMode" checked={regionMode==='include'} onChange={() => setRegionMode('include')} /> Включить</label>
                <label><input type="radio" name="regionMode" checked={regionMode==='exclude'} onChange={() => setRegionMode('exclude')} /> Исключить</label>
                <input type="search" placeholder="Поиск по регионам" value={regionQuery} onChange={(e) => setRegionQuery(e.target.value)} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6, maxHeight: 160, overflow: 'auto', padding: 6, border: '1px solid #eee', borderRadius: 8 }}>
                {filteredRegions.map(r => (
                  <label key={r} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <input type="checkbox" checked={regions.includes(r)} onChange={(e) => setRegions(prev => e.target.checked ? [...prev, r] : prev.filter(x => x !== r))} /> {r}
                  </label>
                ))}
              </div>
            </div>

            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 12, color: '#666' }}>Статус проекта</span>
              <select value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)}>
                <option value="Активен">Активен</option>
                <option value="На паузе">На паузе</option>
              </select>
            </label>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title" title="Сбор данных не осуществляется в те дни, которые не отмечены галочкой">Дни получения номеров</span>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {(['Пн','Вт','Ср','Чт','Пт','Сб','Вс'] as const).map(d => (
                  <label key={d}>
                    <input type="checkbox" checked={days.includes(d)} onChange={() => toggleDay(d)} /> {d}
                  </label>
                ))}
              </div>
            </div>
          </div>

          <div style={{ position: 'sticky', bottom: 0, background: '#fff', paddingTop: 12, borderTop: '1px solid #eee', display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <button type="button" className="btn" onClick={onClose}>Отмена</button>
            <button type="submit" className="btn btn--primary">Создать</button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default CreateProjectModal;


