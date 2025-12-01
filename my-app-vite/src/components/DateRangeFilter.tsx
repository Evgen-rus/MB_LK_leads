import { useState } from 'react';

type PresetKey = 'today' | 'yesterday' | 'week' | 'month' | 'custom';

type DateRange = {
  from: string;
  to: string;
};

type Props = {
  from: string;
  to: string;
  onChange: (range: DateRange) => void;
};

// Вспомогательный формат для отображения в кнопке (DD-MM-YYYY)
function formatDisplayDate(value: string) {
  if (!value) return '';
  const [y, m, d] = value.split('-');
  return `${d}-${m}-${y}`;
}

// Применяет пресет к диапазону дат относительно сегодняшнего дня
function getPresetRange(preset: Exclude<PresetKey, 'custom'>): DateRange {
  const today = new Date();
  let from = new Date(today);
  let to = new Date(today);

  if (preset === 'yesterday') {
    from.setDate(from.getDate() - 1);
    to.setDate(to.getDate() - 1);
  } else if (preset === 'week') {
    from.setDate(from.getDate() - 6); // последние 7 дней, включая сегодня
  } else if (preset === 'month') {
    from.setDate(from.getDate() - 29); // последние 30 дней, включая сегодня
  }

  const toStr = to.toISOString().slice(0, 10);
  const fromStr = from.toISOString().slice(0, 10);
  return { from: fromStr, to: toStr };
}

function DateRangeFilter({ from, to, onChange }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [activePreset, setActivePreset] = useState<PresetKey>('today');

  const displayRange =
    from === to ? formatDisplayDate(from) : `${formatDisplayDate(from)} — ${formatDisplayDate(to)}`;

  const handlePresetClick = (preset: Exclude<PresetKey, 'custom'>) => {
    const range = getPresetRange(preset);
    setActivePreset(preset);
    onChange(range);
  };

  const handleCustomClick = () => {
    setActivePreset('custom');
  };

  const handleReset = () => {
    const range = getPresetRange('today');
    setActivePreset('today');
    onChange(range);
    setIsOpen(false);
  };

  return (
    <div className="date-filter">
      <button
        type="button"
        className="date-filter__toggle"
        onClick={() => setIsOpen((v) => !v)}
      >
        <span>{displayRange}</span>
        <span className="date-filter__icon">📅</span>
      </button>
      {isOpen && (
        <div className="date-filter__popover">
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
          {activePreset === 'custom' && (
            <div className="date-filter__inputs">
              <label>
                с
                <input
                  type="date"
                  value={from}
                  onChange={(e) => onChange({ from: e.target.value, to })}
                />
              </label>
              <label>
                по
                <input
                  type="date"
                  value={to}
                  onChange={(e) => onChange({ from, to: e.target.value })}
                />
              </label>
            </div>
          )}
          <div className="date-filter__footer">
            <button type="button" className="btn" onClick={handleReset}>
              Сбросить
            </button>
            <button type="button" className="btn btn--primary" onClick={() => setIsOpen(false)}>
              Применить
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default DateRangeFilter;


