import { useState } from 'react';
import { updateAdminClient, impersonateClient, type AdminClientUpdateResp } from '../api';

type AdminClientCardModalProps = {
  clientId: number;
  initialName: string;
  initialInn?: string;
  initialPhone?: string;
  initialContact?: string;
  initialLogin: string;
  onClose: () => void;
  onUpdated?: (resp: AdminClientUpdateResp) => void;
};

const env = import.meta.env as Record<string, unknown>;

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function AdminClientCardModal({
  clientId,
  initialName,
  initialInn,
  initialPhone,
  initialContact,
  initialLogin,
  onClose,
  onUpdated,
}: AdminClientCardModalProps) {
  const [name, setName] = useState(initialName);
  const [inn, setInn] = useState(initialInn || '');
  const [phone, setPhone] = useState(initialPhone || '');
  const [contact, setContact] = useState(initialContact || '');
  const [login, setLogin] = useState(initialLogin);
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState<string | null>(null);

  const clientCabinetBase =
    typeof env.VITE_CLIENT_PORTAL_URL === 'string' && env.VITE_CLIENT_PORTAL_URL
      ? (env.VITE_CLIENT_PORTAL_URL as string)
      : '/';

  const hasCredChanges = login.trim() !== initialLogin || password.trim() !== '';

  async function handleOpenClientCabinet() {
    if (!clientId) return;
    setError(null);
    setLoading(true);
    try {
      // 1) Запрашиваем короткий токен имперсонации
      const resp = await impersonateClient(clientId);

      // 2) Сохраняем токен сразу, чтобы новая вкладка увидела его из localStorage и cookies
      try {
        localStorage.setItem('access_token', resp.access_token);
        sessionStorage.setItem('access_token', resp.access_token);
        document.cookie = `access_token=${resp.access_token}; path=/; secure; samesite=strict`;
      } catch {
        /* ignore */
      }

      // 3) Формируем URL портала
      const targetUrl = new URL(clientCabinetBase, window.location.origin).toString();

      // 4) Пытаемся открыть в новой вкладке; если блокируется — уходим в эту же вкладку
      const newWindow = window.open(targetUrl, '_blank', 'noopener');
      if (!newWindow) {
        window.location.href = targetUrl;
      }
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось открыть ЛК клиента'));
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const resp = await updateAdminClient(clientId, {
        name: name.trim(),
        inn: inn.trim(),
        phone: phone.trim(),
        contact: contact.trim(),
        login: login.trim(),
        password: password.trim() || undefined,
      });
      setNewPassword(resp.password ?? null);
      onUpdated?.(resp);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось сохранить'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1300,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="modal-card"
        style={{
          background: '#fff',
          borderRadius: 10,
          width: '100%',
          maxWidth: 560,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Карточка клиента</div>
          <button type="button" className="btn btn--ghost" onClick={onClose} style={{ padding: '6px 10px' }}>
            ✕
          </button>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20, display: 'grid', gap: 12 }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Название / имя клиента</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">ИНН</span>
            <input
              type="text"
              inputMode="numeric"
              placeholder="10 или 12 цифр"
              value={inn}
              onChange={(e) => setInn(e.target.value)}
            />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Контактное лицо</span>
            <input
              type="text"
              placeholder="ФИО"
              value={contact}
              onChange={(e) => setContact(e.target.value)}
            />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Телефон</span>
            <input
              type="tel"
              placeholder="+7 999 123-45-67"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </label>
          <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Логин</span>
              <input
                type="text"
                value={login}
                onChange={(e) => setLogin(e.target.value)}
              />
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Новый пароль (опционально)</span>
              <input
                type="text"
                placeholder="Оставьте пустым, если без смены"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          </div>

          {hasCredChanges && (
            <div className="sub" style={{ color: '#d67', fontWeight: 500 }}>
              Внимание: при смене логина или пароля нужно передать новые данные клиенту.
            </div>
          )}

          {error && (
            <div className="sub" style={{ color: '#d00' }}>
              {error}
            </div>
          )}

          {newPassword && (
            <div style={{ border: '1px dashed #dcdce6', borderRadius: 8, padding: 12, display: 'grid', gap: 6 }}>
              <div style={{ fontWeight: 600 }}>Учётные данные обновлены</div>
              <div className="sub">Пароль показывается один раз — передайте клиенту.</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <code>{login}</code>
                <code>{newPassword}</code>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, borderTop: '1px solid #eee', paddingTop: 12 }}>
            <button
              type="button"
              className="btn btn--secondary"
              onClick={handleOpenClientCabinet}
              disabled={loading || !clientId}
            >
              Перейти в ЛК
            </button>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" className="btn" onClick={onClose}>Отмена</button>
              <button type="submit" className="btn btn--primary" disabled={loading}>
                {loading ? 'Сохраняем…' : 'Сохранить'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AdminClientCardModal;

