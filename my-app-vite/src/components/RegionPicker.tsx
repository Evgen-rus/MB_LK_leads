import { useLayoutEffect, useMemo, useRef } from 'react';
import {
  federalDistricts,
  normalizeRegionValues,
  regionsInDistrict,
  type FederalDistrict,
  type RegionOption,
} from '../data/regions';

type RegionPickerProps = {
  selectedRegions: string[];
  onChange: (regions: string[]) => void;
  query?: string;
  disabled?: boolean;
};

type DistrictGroup = {
  district: FederalDistrict;
  regions: RegionOption[];
  selectedCount: number;
  allSelected: boolean;
  someSelected: boolean;
};

function DistrictCheckbox({
  checked,
  indeterminate,
  disabled,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled?: boolean;
  onChange: () => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  useLayoutEffect(() => {
    if (inputRef.current) {
      inputRef.current.indeterminate = indeterminate;
    }
  }, [indeterminate]);

  return (
    <input
      ref={inputRef}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      aria-checked={indeterminate ? 'mixed' : checked}
      onChange={onChange}
    />
  );
}

function matchesQuery(text: string, query: string): boolean {
  return text.toLowerCase().includes(query);
}

function RegionPicker({
  selectedRegions,
  onChange,
  query = '',
  disabled = false,
}: RegionPickerProps) {
  const selectedSet = useMemo(() => new Set(selectedRegions), [selectedRegions]);

  const groups = useMemo<DistrictGroup[]>(() => {
    const q = query.trim().toLowerCase();

    return federalDistricts.flatMap((district) => {
      const districtRegions = regionsInDistrict(district);
      const districtHit = !q
        || matchesQuery(district.name, q)
        || matchesQuery(district.shortName, q);
      const visibleRegions = districtHit
        ? districtRegions
        : districtRegions.filter((region) => (
          matchesQuery(region.name, q) || region.code.includes(q)
        ));
      if (visibleRegions.length === 0) return [];

      const selectedCount = districtRegions.filter((region) => selectedSet.has(region.code)).length;
      return [{
        district,
        regions: visibleRegions,
        selectedCount,
        allSelected: selectedCount === districtRegions.length && districtRegions.length > 0,
        someSelected: selectedCount > 0 && selectedCount < districtRegions.length,
      }];
    });
  }, [query, selectedSet]);

  function toggleRegion(code: string, checked: boolean) {
    if (disabled) return;
    if (checked) {
      onChange(normalizeRegionValues([...selectedRegions, code]));
      return;
    }
    onChange(selectedRegions.filter((item) => item !== code));
  }

  function toggleDistrict(district: FederalDistrict) {
    if (disabled) return;
    const selectedInDistrict = district.codes.filter((code) => selectedSet.has(code)).length;
    if (selectedInDistrict === district.codes.length) {
      const remove = new Set(district.codes);
      onChange(selectedRegions.filter((code) => !remove.has(code)));
      return;
    }
    onChange(normalizeRegionValues([...selectedRegions, ...district.codes]));
  }

  if (groups.length === 0) {
    return (
      <div className="region-picker">
        <div className="hint" style={{ color: '#666' }}>Ничего не найдено</div>
      </div>
    );
  }

  return (
    <div className="region-picker">
      {groups.map((group) => (
        <div key={group.district.id} className="region-picker__district">
          <label className="region-picker__district-title">
            <DistrictCheckbox
              checked={group.allSelected}
              indeterminate={group.someSelected}
              disabled={disabled}
              onChange={() => toggleDistrict(group.district)}
            />
            <span>{group.district.name}</span>
            <span className="region-picker__short">{group.district.shortName}</span>
            <span className="region-picker__count">
              {group.selectedCount}/{group.district.codes.length}
            </span>
          </label>
          <div className="region-picker__regions">
            {group.regions.map((region) => (
              <label key={region.code} className="region-picker__region">
                <input
                  type="checkbox"
                  checked={selectedSet.has(region.code)}
                  disabled={disabled}
                  onChange={(e) => toggleRegion(region.code, e.target.checked)}
                />
                {region.name}
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export default RegionPicker;
