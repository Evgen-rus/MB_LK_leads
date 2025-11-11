import { useEffect, useRef } from 'react';

type HelpMenuProps = {
  onClose: () => void;
};

function HelpMenu({ onClose }: HelpMenuProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [onClose]);

  return (
    <div
      ref={ref}
      style={{
        position: 'absolute',
        right: 0,
        top: '100%',
        marginTop: 6,
        background: '#fff',
        border: '1px solid #eee',
        borderRadius: 10,
        boxShadow: '0 12px 40px rgba(0,0,0,0.15)',
        minWidth: 220,
        zIndex: 100,
        overflow: 'hidden',
      }}
    >
      <button
        className="btn"
        style={{ width: '100%', textAlign: 'left', border: 'none', borderRadius: 0 }}
        onClick={() => {
          window.dispatchEvent(new CustomEvent('open-support-chat'));
          onClose();
        }}
      >
        Чат с техподдержкой
      </button>
      <a
        className="btn"
        style={{ display: 'block', width: '100%', textAlign: 'left', border: 'none', borderRadius: 0, textDecoration: 'none' }}
        href="#training"
        onClick={onClose}
      >
        Обучение
      </a>
    </div>
  );
}

export default HelpMenu;


