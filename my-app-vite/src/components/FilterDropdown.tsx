import { useEffect, useRef, useState } from 'react';

type Option = { value: string; label: string };

type Props = {
  label: string;
  options: Option[];
  selected: string[];
  placeholder?: string;
  allLabel?: string;
  onApply: (values: string[]) => void;
  disabled?: boolean;
};

function FilterDropdown({ label, options, selected, placeholder = 'Пусто = все', allLabel = 'Все', onApply, disabled }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [localSelected, setLocalSelected] = useState<string[]>(selected);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isOpen) {
      setLocalSelected(selected);
    }
  }, [isOpen, selected]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const toggleValue = (val: string) => {
    setLocalSelected((prev) =>
      prev.includes(val) ? prev.filter((v) => v !== val) : [...prev, val],
    );
  };

  const handleApply = () => {
    onApply(localSelected);
    setIsOpen(false);
  };

  const handleClear = () => {
    setLocalSelected([]);
  };

  const summary = selected.length ? `${label}: ${selected.length}` : `${label}: все`;

  return (
    <div className="export-dropdown" ref={dropdownRef} style={{ opacity: disabled ? 0.6 : 1 }}>
      <button
        className="btn export-dropdown__button"
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
      >
        {summary}
        <span className={`export-dropdown__arrow ${isOpen ? 'open' : ''}`}>▼</span>
      </button>
      {isOpen && (
        <div className="export-dropdown__menu" style={{ minWidth: 240 }}>
          <div className="sub" style={{ padding: '6px 8px' }}>{placeholder}</div>
          <div style={{ maxHeight: 200, overflowY: 'auto', padding: '4px 0' }}>
            {options.map((opt) => (
              <label
                key={opt.value}
                className="export-dropdown__item"
                style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
              >
                <input
                  type="checkbox"
                  checked={localSelected.includes(opt.value)}
                  onChange={() => toggleValue(opt.value)}
                  style={{ cursor: 'pointer' }}
                />
                <span>{opt.label}</span>
              </label>
            ))}
            {options.length === 0 && (
              <div className="export-dropdown__item" style={{ cursor: 'default', color: '#777' }}>
                Нет данных
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, padding: '8px', justifyContent: 'flex-end' }}>
            <button className="btn" onClick={handleClear} type="button">
              {allLabel}
            </button>
            <button className="btn" onClick={handleApply} type="button">
              Сохранить
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default FilterDropdown;

