import { useEffect, useMemo, useRef, useState } from 'react';

type Option = { value: string; label: string };

type Props = {
  label: string;
  options: Option[];
  selected: string[];
  allLabel?: string;
  onApply: (values: string[]) => void;
  disabled?: boolean;
};

function FilterDropdown({ label, options, selected, allLabel = 'Все', onApply, disabled }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [localSelected, setLocalSelected] = useState<string[]>(selected);
  const [query, setQuery] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setLocalSelected(selected);
      setQuery('');
    }
  }, [isOpen, selected]);

  useEffect(() => {
    if (!isOpen) return;
    // Даем браузеру отрисовать поповер, затем ставим фокус в поиск.
    const t = window.setTimeout(() => {
      searchInputRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(t);
  }, [isOpen]);

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

  const handleSelectAll = () => {
    setLocalSelected(options.map((o) => o.value));
  };

  const handleClearAll = () => {
    setLocalSelected([]);
  };

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((opt) => opt.label.toLowerCase().includes(q));
  }, [options, query]);

  const summary =
    selected.length === options.length || options.length === 0
      ? `${label}: все`
      : `${label}: ${selected.length}`;

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
          <div style={{ padding: '8px 8px 4px' }}>
            <input
              ref={searchInputRef}
              type="search"
              value={query}
              placeholder="Поиск по ID или названию"
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: '100%' }}
            />
          </div>
          <div style={{ maxHeight: 200, overflowY: 'auto', padding: '4px 0' }}>
            {filteredOptions.map((opt) => (
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
            {options.length > 0 && filteredOptions.length === 0 && (
              <div className="export-dropdown__item" style={{ cursor: 'default', color: '#777' }}>
                По запросу ничего не найдено
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, padding: '8px', justifyContent: 'flex-end' }}>
            <button className="btn" onClick={handleClearAll} type="button">
              Снять выбор
            </button>
            <button className="btn" onClick={handleSelectAll} type="button">
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

