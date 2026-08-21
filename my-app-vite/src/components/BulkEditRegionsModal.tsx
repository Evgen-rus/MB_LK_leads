import { useMemo, useState } from 'react';
import { normalizeRegionValues } from '../data/regions';
import type { Project } from '../types/project';
import type { BulkProgress } from '../utils/projectBulkUpdate';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';
import BulkEditModalFrame from './BulkEditModalFrame';
import RegionPicker from './RegionPicker';
import RegionQuickTools, { type RegionSourceProject } from './RegionQuickTools';

type RegionMode = 'include' | 'exclude';

type BulkEditRegionsModalProps = {
  selectedProjects: Project[];
  regionSourceProjects?: RegionSourceProject[];
  submitting?: boolean;
  progress?: BulkProgress | null;
  onClose: () => void;
  onSubmit: (payload: { regions: string[]; regionMode: RegionMode }) => void;
};

function BulkEditRegionsModal({
  selectedProjects,
  regionSourceProjects = [],
  submitting = false,
  progress = null,
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

  return (
    <BulkEditModalFrame
      selectedCount={selectedProjects.length}
      onClose={onClose}
      onSubmit={() => onSubmit({ regions: normalizeRegionValues(selectedRegions), regionMode: targetMode })}
      submitting={submitting}
      progress={progress}
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
          .map((project) => `${formatProjectNameForDisplay(project.name)} (id: ${project.id})`)
          .join(', ')}
        {targetProjects.length > 4 ? `, ... и еще ${targetProjects.length - 4}` : ''}
      </div>
      {skippedProjects.length > 0 && (
        <div className="sub" style={{ color: '#8a5a00' }}>
          Не будут изменены ({skippedProjects.length}, другой режим):{' '}
          {skippedProjects
            .slice(0, 4)
            .map((project) => `${formatProjectNameForDisplay(project.name)} (id: ${project.id})`)
            .join(', ')}
          {skippedProjects.length > 4 ? `, ... и еще ${skippedProjects.length - 4}` : ''}
        </div>
      )}
      <input
        type="search"
        placeholder="Поиск по регионам и округам"
        value={regionQuery}
        onChange={(e) => setRegionQuery(e.target.value)}
        disabled={submitting}
      />
      <RegionPicker
        selectedRegions={selectedRegions}
        onChange={setSelectedRegions}
        query={regionQuery}
        disabled={submitting}
      />
      <div className="sub" style={{ color: '#666' }}>
        Выбрано регионов: {selectedRegions.length}
      </div>
      <RegionQuickTools
        selectedRegions={selectedRegions}
        onChange={setSelectedRegions}
        sourceProjects={regionSourceProjects}
        disabled={submitting}
      />
    </BulkEditModalFrame>
  );
}

export default BulkEditRegionsModal;
