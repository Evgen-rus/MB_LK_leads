import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { DayPicker, type DateRange as DayPickerRange } from 'react-day-picker';
import { ru } from 'react-day-picker/locale';
import 'react-day-picker/dist/style.css';

type PresetKey = 'today' | 'yesterday' | 'week' | 'month' | 'custom';

type DateRange = {
  from: string;
  to: string;
};

type OpenMode = 'closed' | 'menu' | 'custom';

type Props = {
  from: string;
  to: string;
  onChange: (range: DateRange) => void;
};

function formatDateInput(date: Date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function parseDateInput(value: string): Date | undefined {
  const [y, m, d] = value.split('-').map(Number);
  if (!y || !m || !d) return undefined;
  return new Date(y, m - 1, d);
}

// Вспомогательный формат для отображения в кнопке (DD-MM-YYYY)
function formatDisplayDate(value: string) {
  if (!value) return '';
  const [y, m, d] = value.split('-');
  return `${d}-${m}-${y}`;
}

function formatRangeSummary(from: string, to?: string) {
  if (!from) return 'Выберите начало и конец периода';
  if (!to) return `Начало периода: ${formatDisplayDate(from)}`;
  if (from === to) return formatDisplayDate(from);
  return `${formatDisplayDate(from)} — ${formatDisplayDate(to)}`;
}

// Применяет пресет к диапазону дат относительно сегодняшнего дня
function getPresetRange(preset: Exclude<PresetKey, 'custom'>): DateRange {
  const today = new Date();
  const from = new Date(today);
  const to = new Date(today);

  if (preset === 'yesterday') {
    from.setDate(from.getDate() - 1);
    to.setDate(to.getDate() - 1);
  } else if (preset === 'week') {
    from.setDate(from.getDate() - 6); // последние 7 дней, включая сегодня
  } else if (preset === 'month') {
    from.setDate(from.getDate() - 29); // последние 30 дней, включая сегодня
  }

  return {
    from: formatDateInput(from),
    to: formatDateInput(to),
  };
}

function detectPreset(from: string, to: string): PresetKey {
  const presets: Array<Exclude<PresetKey, 'custom'>> = ['today', 'yesterday', 'week', 'month'];
  for (const preset of presets) {
    const range = getPresetRange(preset);
    if (range.from === from && range.to === to) {
      return preset;
    }
  }
  return 'custom';
}

function DateRangeFilter({ from, to, onChange }: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [openMode, setOpenMode] = useState<OpenMode>('closed');
  const [activePreset, setActivePreset] = useState<PresetKey>(() => detectPreset(from, to));
  const [draftFrom, setDraftFrom] = useState(from);
  const [draftTo, setDraftTo] = useState(to);
  const [isMobile, setIsMobile] = useState(false);
  const isOpen = openMode !== 'closed';

  const displayRange =
    from === to ? formatDisplayDate(from) : `${formatDisplayDate(from)} — ${formatDisplayDate(to)}`;

  useEffect(() => {
    if (isOpen) return;
    setDraftFrom(from);
    setDraftTo(to);
    setActivePreset(detectPreset(from, to));
  }, [from, to, isOpen]);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const media = window.matchMedia('(max-width: 767px)');
    const applyMatch = () => setIsMobile(media.matches);
    applyMatch();
    media.addEventListener('change', applyMatch);
    return () => media.removeEventListener('change', applyMatch);
  }, []);

  useEffect(() => {
    if (!isMobile || openMode !== 'custom') return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isMobile, openMode]);

  const handleClose = useCallback(() => {
    setDraftFrom(from);
    setDraftTo(to);
    setActivePreset(detectPreset(from, to));
    setOpenMode('closed');
  }, [from, to]);

  useEffect(() => {
    if (!isMobile || openMode !== 'menu') return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      const target = event.target;
      if (!root || !(target instanceof Node)) return;
      if (root.contains(target)) return;
      handleClose();
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [handleClose, isMobile, openMode]);

  const selectedRange = useMemo<DayPickerRange | undefined>(() => {
    const parsedFrom = parseDateInput(draftFrom);
    if (!parsedFrom) return undefined;
    return {
      from: parsedFrom,
      to: parseDateInput(draftTo),
    };
  }, [draftFrom, draftTo]);

  const defaultMonth = useMemo(() => {
    return selectedRange?.from ?? parseDateInput(from) ?? new Date();
  }, [selectedRange, from]);

  const handlePresetClick = (preset: Exclude<PresetKey, 'custom'>) => {
    const range = getPresetRange(preset);
    setActivePreset(preset);
    setDraftFrom(range.from);
    setDraftTo(range.to);
    onChange(range);
    if (isMobile) {
      setOpenMode('closed');
    }
  };

  const handleCustomClick = () => {
    setActivePreset('custom');
    if (isMobile) {
      setOpenMode('custom');
    }
  };

  const handleReset = () => {
    const range = getPresetRange('today');
    setActivePreset('today');
    setDraftFrom(range.from);
    setDraftTo(range.to);
    onChange(range);
    setOpenMode('closed');
  };

  const handleApply = () => {
    if (!draftFrom || !draftTo) return;
    onChange({ from: draftFrom, to: draftTo });
    setOpenMode('closed');
  };

  const handleCalendarSelect = (range: DayPickerRange | undefined) => {
    setActivePreset('custom');
    if (!range?.from) {
      setDraftFrom('');
      setDraftTo('');
      return;
    }
    setDraftFrom(formatDateInput(range.from));
    setDraftTo(range.to ? formatDateInput(range.to) : '');
  };

  const handleToggle = () => {
    if (!isOpen) {
      setDraftFrom(from);
      setDraftTo(to);
      setActivePreset(detectPreset(from, to));
      setOpenMode('menu');
      return;
    }
    setOpenMode('closed');
  };

  const presetsContent = (
    <div className="date-filter__presets">
      <button
        type="button"
        className={`date-filter__preset${
          activePreset === 'today' ? ' date-filter__preset--active' : ''
        }`}
        onClick={() => handlePresetClick('today')}
      >
        Сегодня
      </button>
      <button
        type="button"
        className={`date-filter__preset${
          activePreset === 'yesterday' ? ' date-filter__preset--active' : ''
        }`}
        onClick={() => handlePresetClick('yesterday')}
      >
        Вчера
      </button>
      <button
        type="button"
        className={`date-filter__preset${
          activePreset === 'week' ? ' date-filter__preset--active' : ''
        }`}
        onClick={() => handlePresetClick('week')}
      >
        Неделя
      </button>
      <button
        type="button"
        className={`date-filter__preset${
          activePreset === 'month' ? ' date-filter__preset--active' : ''
        }`}
        onClick={() => handlePresetClick('month')}
      >
        Месяц
      </button>
      <button
        type="button"
        className={`date-filter__preset${
          activePreset === 'custom' ? ' date-filter__preset--active' : ''
        }`}
        onClick={handleCustomClick}
      >
        За период
      </button>
    </div>
  );

  const desktopPopoverContent = (
    <div className="date-filter__popover">
      {presetsContent}
      {activePreset === 'custom' && (
        <div className="date-filter__custom">
          <div className="date-filter__summary">
            {formatRangeSummary(draftFrom, draftTo)}
          </div>
          <div className="date-filter__hint">
            Выберите дату начала, затем дату окончания периода.
          </div>
          <div className="date-filter__calendar">
            <DayPicker
              locale={ru}
              mode="range"
              selected={selectedRange}
              onSelect={handleCalendarSelect}
              defaultMonth={defaultMonth}
              numberOfMonths={2}
              pagedNavigation
              showOutsideDays
            />
          </div>
        </div>
      )}
      <div className="date-filter__footer">
        <button type="button" className="btn" onClick={handleReset}>
          Сбросить
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={handleApply}
          disabled={activePreset === 'custom' && (!draftFrom || !draftTo)}
        >
          Применить
        </button>
      </div>
    </div>
  );

  const mobileMenuContent = (
    <div className="date-filter__popover date-filter__popover--mobile-menu">
      {presetsContent}
    </div>
  );

  const mobileCustomSheetContent = (
    <>
      <button type="button" className="date-filter__backdrop" aria-label="Закрыть выбор дат" onClick={handleClose} />
      <div className="date-filter__popover date-filter__popover--sheet">
        <div className="date-filter__sheet-handle" aria-hidden="true" />
        <div className="date-filter__custom">
          <div className="date-filter__summary">
            {formatRangeSummary(draftFrom, draftTo)}
          </div>
          <div className="date-filter__hint">
            Выберите дату начала, затем дату окончания периода.
          </div>
          <div className="date-filter__calendar">
            <DayPicker
              locale={ru}
              mode="range"
              selected={selectedRange}
              onSelect={handleCalendarSelect}
              defaultMonth={defaultMonth}
              numberOfMonths={1}
              pagedNavigation
              showOutsideDays
            />
          </div>
        </div>
        <div className="date-filter__footer">
          <button type="button" className="btn" onClick={handleReset}>
            Сбросить
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleApply}
            disabled={!draftFrom || !draftTo}
          >
            Применить
          </button>
        </div>
      </div>
    </>
  );

  return (
    <div className="date-filter" ref={rootRef}>
      <button
        type="button"
        className="date-filter__toggle"
        onClick={handleToggle}
      >
        <span>{displayRange}</span>
        <span className="date-filter__icon">📅</span>
      </button>
      {!isMobile && isOpen && desktopPopoverContent}
      {isMobile && openMode === 'menu' && mobileMenuContent}
      {isMobile && openMode === 'custom' && createPortal(mobileCustomSheetContent, document.body)}
    </div>
  );
}

export default DateRangeFilter;