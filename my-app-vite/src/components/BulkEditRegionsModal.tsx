import { useMemo, useState } from 'react';
import { regions as allRegions, normalizeRegionValues } from '../data/regions';
import type { Project } from '../types/project';
import BulkEditModalFrame from './BulkEditModalFrame';

type RegionMode = 'include' | 'exclude';

type BulkEditRegionsModalProps = {
  selectedProjects: Project[];
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (payload: { regions: string[]; regionMode: RegionMode }) => void;
};

function BulkEditRegionsModal({
  selectedProjects,
  submitting = false,
  onClose,
  onSubmit,
}: BulkEditRegionsModalProps) {
  const [regionQuery, setRegionQuery] = useState('');
  const [selectedRegions, setSelectedRegions] = useState<string[]>([]);
  const includeProjects = useMemo(
    () => selectedProjects.filter((project) => (project.regionMode || 'include') === 'include'),
    [selectedProjects],
  );
  const excludeProjects = useMemo(
    () => selectedProjects.filter((project) => (project.regionMode || 'include') === 'exclude'),
    [selectedProjects],
  );

  const [targetMode, setTargetMode] = useState<RegionMode>(
    includeProjects.length > 0 ? 'include' : 'exclude',
  );
  const mixedModes = includeProjects.length > 0 && excludeProjects.length > 0;
  const targetProjects = targetMode === 'include' ? includeProjects : excludeProjects;
  const skippedProjects = targetMode === 'include' ? excludeProjects : includeProjects;

  const filteredRegions = useMemo(() => {
    const q = regionQuery.trim().toLowerCase();
    if (!q) return allRegions;
    return allRegions.filter(
      (item) => item.name.toLowerCase().includes(q) || item.code.includes(q),
    );
  }, [regionQuery]);

  return (
    <BulkEditModalFrame
      selectedCount={selectedProjects.length}
      onClose={onClose}
      onSubmit={() => onSubmit({ regions: normalizeRegionValues(selectedRegions), regionMode: targetMode })}
      submitting={submitting}
    >
      <div className="section-title">Регионы</div>
      <div className="hint">
        Список регионов будет полностью заменён. Пустой список = вся РФ. Режим региона не изменяется.
      </div>
      {mixedModes && (
        <div style={{ display: 'grid', gap: 8 }}>
          <div className="section-title">Выберите группу для массового изменения</div>
          <label>
            <input
              type="radio"
              name="bulk-regions-mode"
              checked={targetMode === 'include'}
              onChange={() => setTargetMode('include')}
              disabled={submitting}
            />{' '}
            Включить регионы - {includeProjects.length} проекта(ов)
          </label>
          <label>
            <input
              type="radio"
              name="bulk-regions-mode"
              checked={targetMode === 'exclude'}
              onChange={() => setTargetMode('exclude')}
              disabled={submitting}
            />{' '}
            Исключить регионы - {excludeProjects.length} проекта(ов)
          </label>
        </div>
      )}
      <div className="sub" style={{ color: '#666' }}>
        Будут изменены ({targetProjects.length}):{' '}
        {targetProjects
          .slice(0, 4)
          .map((project) => `${project.name} (id: ${project.id})`)
          .join(', ')}
        {targetProjects.length > 4 ? `, ... и еще ${targetProjects.length - 4}` : ''}
      </div>
      {skippedProjects.length > 0 && (
        <div className="sub" style={{ color: '#8a5a00' }}>
          Не будут изменены ({skippedProjects.length}, другой режим):{' '}
          {skippedProjects
            .slice(0, 4)
            .map((project) => `${project.name} (id: ${project.id})`)
            .join(', ')}
          {skippedProjects.length > 4 ? `, ... и еще ${skippedProjects.length - 4}` : ''}
        </div>
      )}
      <input
        type="search"
        placeholder="Поиск по регионам"
        value={regionQuery}
        onChange={(e) => setRegionQuery(e.target.value)}
        disabled={submitting}
      />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
          gap: 6,
          maxHeight: 220,
          overflow: 'auto',
          padding: 6,
          border: '1px solid #eee',
          borderRadius: 8,
        }}
      >
        {filteredRegions.map((item) => (
          <label key={item.code} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              checked={selectedRegions.includes(item.code)}
              disabled={submitting}
              onChange={(e) =>
                setSelectedRegions((prev) =>
                  e.target.checked
                    ? [...prev, item.code]
                    : prev.filter((code) => code !== item.code),
                )
              }
            />
            {item.name}
          </label>
        ))}
      </div>
      <div className="sub" style={{ color: '#666' }}>
        Выбрано регионов: {selectedRegions.length}
      </div>
    </BulkEditModalFrame>
  );
}

export default BulkEditRegionsModal;
