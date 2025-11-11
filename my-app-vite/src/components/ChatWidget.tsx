import { useEffect, useRef, useState } from 'react';

function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const handler = () => setOpen(true);
    window.addEventListener('open-support-chat', handler as any);
    return () => window.removeEventListener('open-support-chat', handler as any);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) {
    return (
      <button
        title="Чат поддержки"
        onClick={() => setOpen(true)}
        style={{
          position: 'fixed',
          right: 24,
          bottom: 24,
          width: 56,
          height: 56,
          borderRadius: '50%',
          background: 'linear-gradient(135deg, #6a5cff, #7b68ff)',
          color: '#fff',
          border: 'none',
          boxShadow: '0 8px 24px rgba(0,0,0,0.2)',
          cursor: 'pointer',
          zIndex: 1000,
        }}
      >
        💬
      </button>
    );
  }

  return (
    <div
      style={{
        position: 'fixed',
        right: 16,
        bottom: 16,
        width: 360,
        maxWidth: '92vw',
        height: 420,
        background: '#fff',
        borderRadius: 16,
        boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 1000,
      }}
    >
      <div style={{ padding: '10px 12px', borderBottom: '1px solid #eee', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontWeight: 600 }}>Напишите ваше сообщение</div>
        <button className="icon-btn" onClick={() => setOpen(false)}>✕</button>
      </div>
      <div style={{ padding: 12, overflowY: 'auto', flex: 1, display: 'grid', gap: 8 }}>
        {messages.length === 0 && (
          <div style={{ display: 'grid', gap: 6 }}>
            {['Здравствуйте!', 'Мне нужна помощь', 'Вы можете мне помочь?'].map((t) => (
              <button
                key={t}
                className="btn"
                style={{ justifySelf: 'start', background: '#eff0ff', borderColor: '#dedeff' }}
                onClick={() => setMessages((prev) => [...prev, t])}
              >
                {t}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ alignSelf: 'flex-end', background: '#e8f9ee', color: '#175b33', border: '1px solid #c9f0d6', padding: '6px 10px', borderRadius: 12, maxWidth: '80%' }}>
            {m}
          </div>
        ))}
      </div>
      <div style={{ padding: 10, borderTop: '1px solid #eee', display: 'flex', gap: 8 }}>
        <input
          ref={inputRef}
          type="text"
          placeholder="Введите сообщение"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const value = (e.target as HTMLInputElement).value.trim();
              if (!value) return;
              setMessages((prev) => [...prev, value]);
              (e.target as HTMLInputElement).value = '';
            }
          }}
          style={{ flex: 1, border: '1px solid #e0e0ea', borderRadius: 10, padding: '8px 10px' }}
        />
        <button className="btn btn--primary" onClick={() => {
          const el = inputRef.current;
          if (!el) return;
          const value = el.value.trim();
          if (!value) return;
          setMessages((prev) => [...prev, value]);
          el.value = '';
        }}>Отправить</button>
      </div>
    </div>
  );
}

export default ChatWidget;


