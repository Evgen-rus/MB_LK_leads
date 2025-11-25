// Модальное окно редактирования проекта для админа
// Включает возможность изменять deliveryStatus
import { useEffect, useMemo, useRef, useState } from 'react';
import { regions as allRegions } from '../data/regions';
import type { ProjectStatus, DeliveryStatus, CollectionSource } from '../types/project';
import { updateAdminProject, type AdminProject, type AdminProjectUpdate } from '../api';

type DayAbbrev = 'Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс';

type AdminEditProjectModalProps = {
  project: AdminProject;
  onClose: () => void;
  onSubmit?: (updated: AdminProject) => void;
};

function AdminEditProjectModal({ project, onClose, onSubmit }: AdminEditProjectModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const [name, setName] = useState(project.name);
  const [tag, setTag] = useState(project.tag);
  const [status, setStatus] = useState<ProjectStatus>(project.status);
  const [deliveryStatus, setDeliveryStatus] = useState<DeliveryStatus>(project.deliveryStatus);
  const [dataLimit, setDataLimit] = useState<number>(project.dataLimit);

  const [regionMode, setRegionMode] = useState<'include'|'exclude'>(project.regionMode || 'include');
  const [regionQuery, setRegionQuery] = useState('');
  const [regions, setRegions] = useState<string[]>(project.regions || []);

  const [sitesText, setSitesText] = useState((project.sites || []).join('\n'));
  const [phonesText, setPhonesText] = useState((project.phones || []).join('\n'));
  const [smsSenderName, setSmsSenderName] = useState(project.smsSenderName || '');

  const [days, setDays] = useState<DayAbbrev[]>(() => {
    const map: Record<string, DayAbbrev> = { 'Пн.':'Пн','Вт.':'Вт','Ср.':'Ср','Чт.':'Чт','Пт.':'Пт','Сб.':'Сб','Вс.':'Вс' };
    const parts = (project.daysReceived || '').split(/\s+/).filter(Boolean);
    const out: DayAbbrev[] = [];
    parts.forEach(p => { if (map[p]) out.push(map[p]); });
    return (out.length ? out : ['Вт','Ср','Чт','Пт','Сб']);
  });

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setTag(name); }, [name]);

  function parseList(text: string): string[] {
    return text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  }
  function uniqueList(list: string[]): string[] {
    return Array.from(new Set(list));
  }

  const sitesParsed = useMemo(() => parseList(sitesText), [sitesText]);
  const phonesParsed = useMemo(() => parseList(phonesText), [phonesText]);
  function sanitizeSites() { setSitesText(uniqueList(parseList(sitesText)).join('\n')); }
  function sanitizePhones() { setPhonesText(uniqueList(parseList(phonesText)).join('\n')); }

  const baseRegionIndex = useMemo(() => {
    const m = new Map<string, number>();
    allRegions.forEach((r, i) => m.set(r, i));
    return m;
  }, []);
  const filteredRegions = useMemo(() => {
    const q = regionQuery.trim().toLowerCase();
    if (!q) return allRegions;
    return allRegions.filter(r => r.toLowerCase().includes(q));
  }, [regionQuery]);
  const displayRegions = useMemo(() => {
    const list = filteredRegions.slice();
    list.sort((a, b) => {
      const aSel = regions.includes(a) ? 1 : 0;
      const bSel = regions.includes(b) ? 1 : 0;
      if (aSel !== bSel) return bSel - aSel;
      const ai = baseRegionIndex.get(a) ?? 0;
      const bi = baseRegionIndex.get(b) ?? 0;
      return ai - bi;
    });
    return list;
  }, [filteredRegions, regions, baseRegionIndex]);

  function toggleDay(day: DayAbbrev) {
    setDays(prev => prev.includes(day) ? prev.filter(d => d !== day) : [...prev, day]);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;

    const source: CollectionSource = project.collectionSource;
    if (source === 'СМС' || source === 'Пересечение') {
      const digits = (smsSenderName || '').replace(/\D+/g, '');
      if (!smsSenderName.trim() || digits.length >= 10) return;
    }

    const payload: AdminProjectUpdate = {
      name: name.trim(),
      tag: (tag.trim() || name.trim()),
      status,
      deliveryStatus,
      dataLimit: Number.isFinite(dataLimit) ? dataLimit : 0,
      regionMode,
      regions,
      sites: (source === 'Сайты' || source === 'Ретросайты' || source === 'Пересечение') ? uniqueList(sitesParsed) : undefined,
      phones: (source === 'Звонки' || source === 'Ретрозвонки' || source === 'Пересечение') ? uniqueList(phonesParsed) : undefined,
      smsSenderName: (source === 'СМС' || source === 'Пересечение') ? (smsSenderName.trim() || undefined) : undefined,
      days,
    };

    setSaving(true);
    setError(null);

    try {
      const updated = await updateAdminProject(project.id, payload);
      onSubmit?.(updated);
      onClose();
    } catch (e: any) {
      setError(e?.message || 'Ошибка при сохранении');
    } finally {
      setSaving(false);
    }
  }

  const bActive = (code: 'B1'|'B2'|'B3'|'B4') => project.dataSourceCode === code;
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
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
            </label>

            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Тег</span>
              <input type="text" value={tag} onChange={(e) => setTag(e.target.value)} />
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
                <input type="number" min={0} value={dataLimit} onChange={(e) => setDataLimit(Number(e.target.value))} />
              </label>
            </div>

            <div style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Источник данных</span>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(['B1','B2','B3','B4'] as const).map(code => (
                  <button key={code} type="button" aria-pressed={bActive(code)} disabled style={{ padding: '6px 10px', borderRadius: 999, border: '1px solid', borderColor: bActive(code) ? '#6a5cff' : '#dcdce6', background: bActive(code) ? '#6a5cff' : '#fff', color: bActive(code) ? '#fff' : '#1d1d1f', opacity: bActive(code) ? 1 : 0.6 }}>
                    {code}
                  </button>
                ))}
              </div>
            </div>

            {(source === 'Сайты' || source === 'Ретросайты' || source === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Список сайтов</span>
                <span className="hint">По одному в строке</span>
                <textarea rows={8} placeholder={"site.ru\nwww.site.ru\nhttps://site.ru"} value={sitesText} onChange={(e) => setSitesText(e.target.value)} onBlur={sanitizeSites} style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }} />
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Элементов: {sitesParsed.length}, уникальных: {uniqueList(sitesParsed).length}</span>
              </label>
            )}

            {(source === 'Звонки' || source === 'Ретрозвонки' || source === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Список телефонов</span>
                <span className="hint">По одному в строке</span>
                <textarea rows={8} placeholder={"Вставьте номера по одному в строке. Допустимые форматы: 79..., 7 495..., +7 ..."} value={phonesText} onChange={(e) => setPhonesText(e.target.value)} onBlur={sanitizePhones} style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }} />
                <span style={{ fontSize: '0.75rem', color: '#666' }}>Элементов: {phonesParsed.length}, уникальных: {uniqueList(phonesParsed).length}</span>
              </label>
            )}

            {(source === 'СМС' || source === 'Пересечение') && (
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Наименование отправителя (СМС)</span>
                <input type="text" placeholder="Требуется точное имя отправителя; если укажете физический номер — проект не будет запущен" value={smsSenderName} onChange={(e) => setSmsSenderName(e.target.value)} />
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
                {displayRegions.map(r => (
                  <label key={r} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <input type="checkbox" checked={regions.includes(r)} onChange={(e) => setRegions(prev => e.target.checked ? [...prev, r] : prev.filter(x => x !== r))} /> {r}
                  </label>
                ))}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Статус проекта</span>
                <select value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)}>
                  <option value="Активен">Активен</option>
                  <option value="На паузе">На паузе</option>
                </select>
              </label>

              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Статус отгрузки</span>
                <select
                  value={deliveryStatus}
                  onChange={(e) => setDeliveryStatus(e.target.value as DeliveryStatus)}
                  className={`delivery-select ${
                    deliveryStatus === 'Активна'
                      ? 'delivery-select--green'
                      : deliveryStatus === 'На модерации'
                      ? 'delivery-select--orange'
                      : 'delivery-select--gray'
                  }`}
                >
                  <option value="Активна">Активна</option>
                  <option value="На модерации">На модерации</option>
                  <option value="Отключена">Отключена</option>
                </select>
              </label>
            </div>

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
            <button type="button" className="btn" onClick={onClose} disabled={saving}>Отмена</button>
            <button type="submit" className="btn btn--primary" disabled={saving}>
              {saving ? 'Сохранение...' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AdminEditProjectModal;

