import { useState } from 'react';
import { sendSupportMessage } from '../api';

// Страница "Интеграции" + виджет чата (Telegram)
function Integrations() {
  const [chatOpen, setChatOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [phone, setPhone] = useState('');
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedPhone = phone.trim();
    const trimmedText = text.trim();
    if (!trimmedPhone || !trimmedText) return;
    try {
      setSending(true);
      setError(null);
      setSuccess(null);
      await sendSupportMessage({ phone: trimmedPhone, text: trimmedText });
      setSuccess('Сообщение отправлено. Мы свяжемся с вами по указанному телефону.');
      setPhone('');
      setText('');
      setFormOpen(false);
    } catch (e: any) {
      setError(e?.message || 'Не удалось отправить сообщение. Попробуйте позже.');
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <div className="card" style={{ padding: 16 }}>
        <h3 style={{ marginTop: 0, marginBottom: 12 }}>Интеграции</h3>
        <p style={{ margin: 0, lineHeight: 1.6 }}>
          Для подключения интеграций с CRM (amoCRM и др.) обратитесь в поддержку.
          Мы подскажем оптимальную схему и поможем с настройкой.
        </p>
        {success && (
          <p style={{ marginTop: 16, fontSize: '0.9rem', color: '#2e7d32' }}>{success}</p>
        )}
        {error && (
          <p style={{ marginTop: 16, fontSize: '0.9rem', color: '#c62828' }}>{error}</p>
        )}
      </div>

      {/* Плавающая кнопка чата — только на этой вкладке */}
      <div
        style={{
          position: 'fixed',
          right: 24,
          bottom: 24,
          zIndex: 1100,
        }}
      >
        {/* Панель с иконкой Telegram */}
        {chatOpen && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              marginBottom: 8,
              gap: 8,
            }}
          >
            <button
              type="button"
              onClick={() => setFormOpen(true)}
              style={{
                width: 44,
                height: 44,
                borderRadius: 22,
                border: 'none',
                background: '#2AABEE',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 4px 10px rgba(0,0,0,0.15)',
                cursor: 'pointer',
                color: '#fff',
              }}
              title="Написать в Telegram"
            >
              {/* Иконка Telegram (простая SVG) */}
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
                <path
                  d="M9.04 13.93L8.86 17.07C9.14 17.07 9.26 16.95 9.41 16.8L10.72 15.54L13.81 17.79C14.38 18.11 14.79 17.95 14.94 17.28L16.96 7.8C17.16 6.99 16.69 6.64 16.14 6.85L5.65 10.88C4.86 11.19 4.87 11.63 5.51 11.82L8.21 12.65L14.39 8.76C14.68 8.57 14.95 8.68 14.74 8.87L9.04 13.93Z"
                  fill="white"
                />
              </svg>
            </button>
          </div>
        )}

        {/* Кнопка открытия/закрытия чата */}
        <button
          type="button"
          onClick={() => {
            setChatOpen(v => !v);
            if (!chatOpen) {
              setFormOpen(false);
            }
          }}
          style={{
            width: 52,
            height: 52,
            borderRadius: 26,
            border: 'none',
            background: '#234b9b',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 6px 16px rgba(0,0,0,0.2)',
            cursor: 'pointer',
            color: '#fff',
          }}
          title="Написать в поддержку"
        >
          {/* Иконка чата */}
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none">
            <path
              d="M4 6.5C4 5.12 5.12 4 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-3.5 3.5V16H6.5A2.5 2.5 0 0 1 4 13.5v-7Z"
              stroke="white"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="9" cy="10" r="1" fill="white" />
            <circle cx="12.5" cy="10" r="1" fill="white" />
            <circle cx="16" cy="10" r="1" fill="white" />
          </svg>
        </button>
      </div>

      {/* Маленькая форма (модалка) для телефона и вопроса */}
      {formOpen && (
        <div
          style={{
            position: 'fixed',
            right: 90,
            bottom: 90,
            width: 320,
            maxWidth: '90vw',
            background: '#fff',
            borderRadius: 12,
            boxShadow: '0 10px 30px rgba(0,0,0,0.25)',
            padding: 16,
            zIndex: 1200,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>Написать в поддержку</div>
            <button
              type="button"
              onClick={() => setFormOpen(false)}
              style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 18, lineHeight: 1 }}
              aria-label="Закрыть форму"
            >
              ×
            </button>
          </div>
          <form onSubmit={handleSubmit} style={{ display: 'grid', gap: 8 }}>
            <label style={{ display: 'grid', gap: 4, fontSize: '0.85rem' }}>
              Напишите Ваш телефон
              <input
                type="tel"
                placeholder="+7 ..."
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </label>
            <label style={{ display: 'grid', gap: 4, fontSize: '0.85rem' }}>
              Вопрос
              <textarea
                rows={4}
                placeholder="Опишите вопрос или проблему, мы ответим в Telegram / по телефону"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={sending || !phone.trim() || !text.trim()}
            >
              {sending ? 'Отправка…' : 'Отправить'}
            </button>
          </form>
        </div>
      )}
    </>
  );
}

export default Integrations;


