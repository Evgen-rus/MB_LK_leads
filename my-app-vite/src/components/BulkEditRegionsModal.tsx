import { useMemo, useState } from 'react';
import { regions as allRegions, normalizeRegionValues } from '../data/regions';
import BulkEditModalFrame from './BulkEditModalFrame';

type BulkEditRegionsModalProps = {
  selectedCount: number;
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (regions: string[]) => void;
};

function BulkEditRegionsModal({
  selectedCount,
  submitting = false,
  onClose,
  onSubmit,
}: BulkEditRegionsModalProps) {
  const [regionQuery, setRegionQuery] = useState('');
  const [selectedRegions, setSelectedRegions] = useState<string[]>([]);

  const filteredRegions = useMemo(() => {
    const q = regionQuery.trim().toLowerCase();
    if (!q) return allRegions;
    return allRegions.filter(
      (item) => item.name.toLowerCase().includes(q) || item.code.includes(q),
    );
  }, [regionQuery]);

  return (
    <BulkEditModalFrame
      selectedCount={selectedCount}
      onClose={onClose}
      onSubmit={() => onSubmit(normalizeRegionValues(selectedRegions))}
      submitting={submitting}
    >
      <div className="section-title">Регионы</div>
      <div className="hint">
        Список регионов будет полностью заменён. Пустой список = вся РФ.
      </div>
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
