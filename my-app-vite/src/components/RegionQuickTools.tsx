import { useMemo, useState } from 'react';
import { normalizeRegionValues, regions as allRegions, regionLabelByCode } from '../data/regions';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';

export type RegionSourceProject = {
  id: number;
  name: string;
  regions?: string[] | null;
};

type RegionQuickToolsProps = {
  selectedRegions: string[];
  onChange: (regions: string[]) => void;
  sourceProjects?: RegionSourceProject[];
  currentProjectId?: number;
  disabled?: boolean;
};

function normalizeRegionSearchValue(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[.,()"']/g, ' ')
    .replace(/\bобласть\b/g, 'обл')
    .replace(/\s+/g, ' ');
}

function buildRegionLookup(): Map<string, string> {
  const lookup = new Map<string, string>();
  allRegions.forEach((region) => {
    const keys = [
      region.code,
      region.name,
      region.name.replace(/^г\.\s*/i, ''),
      region.name.replace(/\bобл\.\b/i, 'область'),
    ];
    keys.forEach((key) => {
      const normalized = normalizeRegionSearchValue(key);
      if (normalized) lookup.set(normalized, region.code);
    });
  });
  return lookup;
}

const REGION_LOOKUP = buildRegionLookup();

function parseRegionLines(text: string): string[] {
  return text
    .split(/[\r\n,;]+/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function resolveRegionsFromText(text: string): { found: string[]; notFound: string[] } {
  const found: string[] = [];
  const notFound: string[] = [];
  const seenFound = new Set<string>();
  const seenMissing = new Set<string>();

  parseRegionLines(text).forEach((raw) => {
    const key = normalizeRegionSearchValue(raw);
    const code = REGION_LOOKUP.get(key);
    if (code) {
      if (!seenFound.has(code)) {
        seenFound.add(code);
        found.push(code);
      }
      return;
    }
    if (!seenMissing.has(raw)) {
      seenMissing.add(raw);
      notFound.push(raw);
    }
  });

  return { found, notFound };
}

function RegionQuickTools({
  selectedRegions,
  onChange,
  sourceProjects = [],
  currentProjectId,
  disabled = false,
}: RegionQuickToolsProps) {
  const [bulkText, setBulkText] = useState('');
  const [notFound, setNotFound] = useState<string[]>([]);
  const [sourceProjectId, setSourceProjectId] = useState('');

  const sourceOptions = useMemo(
    () => sourceProjects
      .filter((project) => project.id !== currentProjectId)
      .filter((project) => normalizeRegionValues(project.regions || []).length > 0),
    [sourceProjects, currentProjectId],
  );

  function addFromText() {
    const { found, notFound: missing } = resolveRegionsFromText(bulkText);
    setNotFound(missing);
    if (found.length === 0) return;
    const next = normalizeRegionValues([...selectedRegions, ...found]);
    onChange(next);
  }

  function copyFromProject() {
    const id = Number(sourceProjectId);
    const source = sourceOptions.find((project) => project.id === id);
    if (!source) return;
    onChange(normalizeRegionValues(source.regions || []));
    setNotFound([]);
  }

  return (
    <div className="region-quick-tools">
      <div className="region-quick-tools__block">
        <span className="section-title">Вставить список регионов</span>
        <textarea
          rows={4}
          placeholder="Каждый регион с новой строки, через запятую или точку с запятой"
          value={bulkText}
          onChange={(e) => setBulkText(e.target.value)}
          disabled={disabled}
        />
        <div className="region-quick-tools__actions">
          <button type="button" className="btn btn--secondary" onClick={addFromText} disabled={disabled || !bulkText.trim()}>
            Добавить из списка
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => { setBulkText(''); setNotFound([]); }} disabled={disabled || (!bulkText && notFound.length === 0)}>
            Очистить ввод
          </button>
        </div>
        {notFound.length > 0 && (
          <div className="sub" style={{ color: '#d00' }}>
            Не найдено: {notFound.join(', ')}
          </div>
        )}
      </div>

      {sourceOptions.length > 0 && (
        <div className="region-quick-tools__block">
          <span className="section-title">Скопировать регионы из проекта</span>
          <div className="region-copy-row">
            <select
              value={sourceProjectId}
              onChange={(e) => setSourceProjectId(e.target.value)}
              disabled={disabled}
            >
              <option value="">Выберите проект</option>
              {sourceOptions.map((project) => (
                <option key={project.id} value={project.id}>
                  {formatProjectNameForDisplay(project.name)} (id: {project.id}) - {normalizeRegionValues(project.regions || []).map(regionLabelByCode).join(', ')}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn--secondary" onClick={copyFromProject} disabled={disabled || !sourceProjectId}>
              Скопировать
            </button>
          </div>
          <div className="hint">Копируется только список регионов. Режим включить/исключить не меняется.</div>
        </div>
      )}
    </div>
  );
}

export default RegionQuickTools;
