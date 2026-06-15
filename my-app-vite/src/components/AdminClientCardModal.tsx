import { useState } from 'react';
import { updateAdminClient, impersonateClient, type AdminClientUpdateResp } from '../api';

type AdminClientCardModalProps = {
  clientId: number;
  managerRole?: 'admin' | 'agent';
  initialName: string;
  initialInn?: string;
  initialPhone?: string;
  initialContact?: string;
  initialTelegramNotificationsChatId?: string;
  initialTelegramAutoPauseEnabled?: boolean;
  initialUniqueProjectNamesEnabled?: boolean;
  initialInternalClientId?: string;
  initialTableUrl?: string;
  initialPixelTableUrl?: string;
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
  managerRole = 'admin',
  initialName,
  initialInn,
  initialPhone,
  initialContact,
  initialTelegramNotificationsChatId,
  initialTelegramAutoPauseEnabled,
  initialUniqueProjectNamesEnabled,
  initialInternalClientId,
  initialTableUrl,
  initialPixelTableUrl,
  initialLogin,
  onClose,
  onUpdated,
}: AdminClientCardModalProps) {
  const isAgentManager = managerRole === 'agent';
  const [name, setName] = useState(initialName);
  const [inn, setInn] = useState(initialInn || '');
  const [phone, setPhone] = useState(initialPhone || '');
  const [contact, setContact] = useState(initialContact || '');
  const [telegramNotificationsChatId, setTelegramNotificationsChatId] = useState(initialTelegramNotificationsChatId || '');
  const [telegramAutoPauseEnabled, setTelegramAutoPauseEnabled] = useState(Boolean(initialTelegramAutoPauseEnabled));
  const [uniqueProjectNamesEnabled, setUniqueProjectNamesEnabled] = useState(Boolean(initialUniqueProjectNamesEnabled));
  const [internalClientId, setInternalClientId] = useState(initialInternalClientId || '');
  const [tableUrl, setTableUrl] = useState(initialTableUrl || '');
  const [pixelTableUrl, setPixelTableUrl] = useState(initialPixelTableUrl || '');
  const [login, setLogin] = useState(initialLogin);
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);

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
        // В dev по HTTP secure-cookie не отправляется браузером, поэтому не форсируем secure.
        // Также Strict может ломать часть сценариев — Lax достаточно.
        const parts = [`access_token=${encodeURIComponent(resp.access_token)}`, 'path=/', 'samesite=lax'];
        if (window.location.protocol === 'https:') parts.push('secure');
        document.cookie = parts.join('; ');
      } catch {
        /* ignore */
      }

      // 3) Формируем URL портала
      const targetUrl = new URL(clientCabinetBase, window.location.origin).toString();

      // 4) Переходим в этой же вкладке, чтобы не было дубля вкладок
      window.location.href = targetUrl;
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось открыть ЛК клиента'));
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaveNotice(null);
    setLoading(true);
    try {
      const resp = await updateAdminClient(clientId, {
        name: name.trim(),
        inn: inn.trim(),
        phone: phone.trim(),
        contact: contact.trim(),
        telegramNotificationsChatId: telegramNotificationsChatId.trim(),
        telegramAutoPauseEnabled,
        uniqueProjectNamesEnabled,
        internalClientId: internalClientId.trim(),
        tableUrl: tableUrl.trim(),
        pixelTableUrl: pixelTableUrl.trim(),
        login: login.trim(),
        password: password.trim() || undefined,
      });
      setNewPassword(resp.password ?? null);
      setSaveNotice(
        resp.telegramTestStatus === 'queued'
          ? 'Клиент сохранён. Тестовое Telegram-сообщение поставлено в очередь отправки.'
          : 'Клиент сохранён.'
      );
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
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Редактирование карточки клиента</div>
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
              placeholder="79991234567"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </label>
          {!isAgentManager && (
            <div style={{ display: 'grid', gap: 10 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Таблица клиента: остальные источники</span>
                <input
                  type="text"
                  placeholder="https://docs.google.com/spreadsheets/d/..."
                  value={tableUrl}
                  onChange={(e) => setTableUrl(e.target.value)}
                />
                <span className="hint">Текущая таблица для обычных provider-идентификаций</span>
              </label>
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Таблица клиента: Пиксель</span>
                <input
                  type="text"
                  placeholder="https://docs.google.com/spreadsheets/d/..."
                  value={pixelTableUrl}
                  onChange={(e) => setPixelTableUrl(e.target.value)}
                />
                <span className="hint">Отдельная таблица для выгрузки идентификаций Пикселя</span>
              </label>
            </div>
          )}
          {!isAgentManager && (
            <div
              style={{
                display: 'grid',
                gap: 10,
                padding: 12,
                border: '1px solid #eee',
                borderRadius: 8,
                background: '#fafbff',
              }}
            >
              <div style={{ fontWeight: 600 }}>Telegram для уведомлений</div>
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Telegram chat ID</span>
                <input
                  type="text"
                  placeholder="-1001234567890"
                  value={telegramNotificationsChatId}
                  onChange={(e) => setTelegramNotificationsChatId(e.target.value)}
                />
                <span className="hint">
                  Для персональных тарифных уведомлений создайте Telegram-группу клиента, добавьте в неё бота
                  {' '}
                  <code>@MB_Info_LK_Bot</code> и укажите chat ID группы.
                </span>
                <span className="sub" style={{ fontSize: 12, color: '#6b7280' }}>
                  Если chat ID не задан или маршрут выключен, тарифные уведомления будут уходить в общий чат.
                  Технические уведомления об автопаузе и блокировке оператора всегда отправляются в общий чат.
                </span>
              </label>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <input
                  type="checkbox"
                  checked={telegramAutoPauseEnabled}
                  onChange={(e) => setTelegramAutoPauseEnabled(e.target.checked)}
                />
                <span>Использовать этот чат для уведомлений</span>
              </label>
            </div>
          )}
          {!isAgentManager && (
            <div
              style={{
                display: 'grid',
                gap: 10,
                padding: 12,
                border: '1px solid #eee',
                borderRadius: 8,
                background: '#fafbff',
              }}
            >
              <div style={{ fontWeight: 600 }}>Имена новых проектов</div>
              <label style={{ display: 'grid', gap: 6 }}>
                <span className="section-title">Внутренний идентификатор клиента</span>
                <input
                  type="text"
                  placeholder="[LR135] Название клиента"
                  value={internalClientId}
                  onChange={(e) => setInternalClientId(e.target.value)}
                />
              </label>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <input
                  type="checkbox"
                  checked={uniqueProjectNamesEnabled}
                  onChange={(e) => setUniqueProjectNamesEnabled(e.target.checked)}
                />
                <span>Добавлять внутренний идентификатор клиента в новые проекты</span>
              </label>
              <span className="sub" style={{ fontSize: 12, color: '#6b7280' }}>
                Если включено и идентификатор заполнен, новые проекты будут называться, например:
                {' '}
                <code>A_[LR135] Название клиента_Магнум</code>.
                Клиент в ЛК будет видеть обычное имя без внутреннего идентификатора.
              </span>
            </div>
          )}
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

          {saveNotice && (
            <div className="sub" style={{ color: '#1d7a45', fontWeight: 500 }}>
              {saveNotice}
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

