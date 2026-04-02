import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

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
  const [emptyMeansAllInUi, setEmptyMeansAllInUi] = useState(false);
  const [query, setQuery] = useState('');
  const [isMobile, setIsMobile] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const media = window.matchMedia('(max-width: 767px)');
    const applyMatch = () => setIsMobile(media.matches);
    applyMatch();
    media.addEventListener('change', applyMatch);
    return () => media.removeEventListener('change', applyMatch);
  }, []);

  useEffect(() => {
    if (isOpen) {
      setLocalSelected(selected);
      setEmptyMeansAllInUi(selected.length === 0);
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
    if (!isMobile || !isOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isMobile, isOpen]);

  useEffect(() => {
    if (isMobile) return undefined;
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isMobile]);

  const toggleValue = (val: string) => {
    setLocalSelected((prev) => {
      // Пустой выбор трактуется как "все", поэтому при первом клике
      // берем за основу весь список опций только если это "виртуальное все"
      // (а не результат кнопки "Очистить выбор").
      const effective =
        prev.length === 0 && emptyMeansAllInUi
          ? options.map((o) => o.value)
          : prev;
      setEmptyMeansAllInUi(false);
      return effective.includes(val)
        ? effective.filter((v) => v !== val)
        : [...effective, val];
    });
  };

  const handleApply = () => {
    onApply(localSelected);
    setIsOpen(false);
  };

  const handleSelectAll = () => {
    setLocalSelected(options.map((o) => o.value));
    setEmptyMeansAllInUi(false);
  };

  const handleClearAll = () => {
    setLocalSelected([]);
    setEmptyMeansAllInUi(false);
  };

  const filteredOptions = useMemo(() => {
    const normalize = (value: string) => value.toLowerCase().replace(/\s+/g, ' ').trim();
    const q = normalize(query);
    if (!q) return options;
    return options.filter((opt) => {
      const label = normalize(opt.label);
      const value = normalize(opt.value);
      return label.includes(q) || value.includes(q);
    });
  }, [options, query]);

  // Для UI: если localSelected пустой, показываем как выбранные все опции.
  const localSelectedForUi = useMemo(
    () =>
      localSelected.length === 0 && emptyMeansAllInUi
        ? options.map((o) => o.value)
        : localSelected,
    [localSelected, options, emptyMeansAllInUi],
  );

  const summary =
    selected.length === 0 || selected.length === options.length || options.length === 0
      ? `${label}: все`
      : `${label}: ${selected.length}`;

  const mobileTitle =
    label === 'Проекты'
      ? 'Выбор проектов'
      : label === 'Каналы'
        ? 'Выбор каналов'
        : label;

  const dropdownContent = (
    <div className={`export-dropdown__menu filter-dropdown__menu${isMobile ? ' filter-dropdown__menu--mobile' : ''}`} style={{ minWidth: 240 }}>
      {isMobile && (
        <div className="filter-dropdown__header">
          <div className="filter-dropdown__title">{mobileTitle}</div>
          <button
            type="button"
            className="filter-dropdown__close"
            aria-label={`Закрыть фильтр ${label}`}
            onClick={() => setIsOpen(false)}
          >
            <span>×</span>
          </button>
        </div>
      )}
      <div className="filter-dropdown__search" style={{ padding: '8px 8px 4px' }}>
        <input
          ref={searchInputRef}
          type="search"
          value={query}
          placeholder="Поиск по названию"
          onChange={(e) => setQuery(e.target.value)}
          style={{ width: '100%' }}
        />
      </div>
      <div className="filter-dropdown__list" style={{ maxHeight: 200, overflowY: 'auto', padding: '4px 0' }}>
        {filteredOptions.map((opt) => (
          <label
            key={opt.value}
            className="export-dropdown__item"
            style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
          >
            <input
              type="checkbox"
              checked={localSelectedForUi.includes(opt.value)}
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
      <div className="filter-dropdown__actions-panel">
        <div className="sub filter-dropdown__hint" style={{ padding: '0 8px 8px' }}>
          Подсказка: Пустой выбор = все значения.
        </div>
        <div className="filter-dropdown__footer" style={{ display: 'flex', gap: 8, padding: '8px', justifyContent: 'flex-end' }}>
          <button className="btn" onClick={handleClearAll} type="button">
            Очистить выбор
          </button>
          <button className="btn" onClick={handleSelectAll} type="button">
            {allLabel}
          </button>
          <button className="btn" onClick={handleApply} type="button">
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );

  const mobileDropdown = isMobile && isOpen
    ? createPortal(
        <>
          <button
            type="button"
            className="filter-dropdown__backdrop"
            aria-label={`Закрыть фильтр ${label}`}
            onClick={() => setIsOpen(false)}
          />
          {dropdownContent}
        </>,
        document.body,
      )
    : null;

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
      {!isMobile && isOpen && dropdownContent}
      {mobileDropdown}
    </div>
  );
}

export default FilterDropdown;

