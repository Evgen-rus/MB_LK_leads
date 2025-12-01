import { useState } from 'react';
import { sendSupportMessage } from '../api';

function Support() {
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
      setSuccess('Заявка отправлена. Мы свяжемся с вами по указанному телефону.');
      setPhone('');
      setText('');
    } catch (e: any) {
      setError(e?.message || 'Не удалось отправить сообщение. Попробуйте позже.');
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="table-card" style={{ maxWidth: 760 }}>
      <div style={{ padding: 20, borderBottom: '1px solid #ececf2' }}>
        <h3 style={{ margin: 0, marginBottom: 8 }}>Техподдержка</h3>
        <p className="sub" style={{ margin: 0 }}>
          Если у вас есть вопросы по интеграции, отчётам или идентификациям — напишите нам удобным способом.
        </p>
      </div>

      <div style={{ padding: 20, display: 'grid', gap: 16 }}>
        <div>
          <div className="sub" style={{ marginBottom: 6 }}>
            Быстрый контакт
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <a
              href="https://t.me/EvgeniiRa"
              target="_blank"
              rel="noreferrer"
              className="btn btn--primary"
            >
              Написать в Telegram
            </a>
            <a
              href="https://t.me/EvgeniiRa"
              target="_blank"
              rel="noreferrer"
              className="btn btn--secondary"
            >
              Написать в WhatsApp
            </a>
          </div>
        </div>

        <div>
          <div className="sub" style={{ marginBottom: 6 }}>
            Заявка на звонок
          </div>
          <form onSubmit={handleSubmit} style={{ display: 'grid', gap: 10 }}>
            <label style={{ display: 'grid', gap: 4, fontSize: '0.85rem' }}>
              Ваш телефон
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
                placeholder="Кратко опишите вопрос, задачу или проблему"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={sending || !phone.trim() || !text.trim()}
            >
              {sending ? 'Отправка…' : 'Отправить заявку'}
            </button>
          </form>
          {success && (
            <p style={{ marginTop: 8, fontSize: '0.85rem', color: '#1d7a45' }}>{success}</p>
          )}
          {error && (
            <p style={{ marginTop: 8, fontSize: '0.85rem', color: '#c62828' }}>{error}</p>
          )}
        </div>
      </div>
    </div>
  );
}

export default Support;


