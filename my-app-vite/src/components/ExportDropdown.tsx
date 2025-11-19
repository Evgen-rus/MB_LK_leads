// Компонент выпадающего меню для экспорта данных
import { useState, useRef, useEffect } from 'react';

type ExportFormat = 'csv' | 'xlsx';

interface ExportDropdownProps {
  onExport: (format: ExportFormat) => void;
  buttonText?: string;
}

function ExportDropdown({ onExport, buttonText = 'Экспорт отчета' }: ExportDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Закрываем меню при клике вне его
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleExport = (format: ExportFormat) => {
    onExport(format);
    setIsOpen(false);
  };

  return (
    <div className="export-dropdown" ref={dropdownRef}>
      <button
        className="btn export-dropdown__button"
        onClick={() => setIsOpen(!isOpen)}
      >
        {buttonText}
        <span className={`export-dropdown__arrow ${isOpen ? 'open' : ''}`}>▼</span>
      </button>
      {isOpen && (
        <div className="export-dropdown__menu">
          <button
            className="export-dropdown__item"
            onClick={() => handleExport('csv')}
          >
            CSV
          </button>
          <button
            className="export-dropdown__item"
            onClick={() => handleExport('xlsx')}
          >
            XLSX
          </button>
        </div>
      )}
    </div>
  );
}

export default ExportDropdown;
