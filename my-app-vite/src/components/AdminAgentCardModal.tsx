import { useState } from 'react';
import {
  updateAdminAgent,
  type AdminAgentSummaryItem,
  type AdminAgentUpdateResp,
} from '../api';
import DateTimeCompact from './DateTimeCompact';

type AdminAgentCardModalProps = {
  agent: AdminAgentSummaryItem;
  onClose: () => void;
  onUpdated?: (resp: AdminAgentUpdateResp) => void;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function copyToClipboard(text: string) {
  try {
    navigator.clipboard?.writeText(text);
  } catch {
    /* ignore */
  }
}

function AdminAgentCardModal({ agent, onClose, onUpdated }: AdminAgentCardModalProps) {
  const [name, setName] = useState(agent.user.name || agent.user.login);
  const [login, setLogin] = useState(agent.user.login);
  const [password, setPassword] = useState('');
  const [isDisabled, setIsDisabled] = useState(Boolean(agent.user.isDisabled));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatedPassword, setUpdatedPassword] = useState<string | null>(null);

  const hasCredChanges = login.trim() !== agent.user.login || password.trim() !== '';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError('Укажите имя агента');
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const resp = await updateAdminAgent(agent.user.id, {
        name: name.trim(),
        login: login.trim() || undefined,
        password: password.trim() || undefined,
        isDisabled,
      });
      setUpdatedPassword(resp.password ?? null);
      onUpdated?.(resp);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось сохранить агента'));
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
          maxWidth: 580,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'grid', gap: 6 }}>
            <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Карточка агента</div>
            <div className="sub">
              ID: {agent.user.id}
              {' · '}
              <DateTimeCompact value={agent.createdAt} />
            </div>
          </div>
          <button type="button" className="btn btn--ghost" onClick={onClose} style={{ padding: '6px 10px' }}>
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ padding: 20, display: 'grid', gap: 12 }}>
          <div style={{ display: 'grid', gap: 10, padding: 12, border: '1px solid #eee', borderRadius: 8, background: '#fafbff' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span className={isDisabled ? 'badge badge--orange' : 'badge badge--green'}>
                {isDisabled ? 'Отключён' : 'Активен'}
              </span>
              <span className="sub">Логин: {login || '—'}</span>
            </div>
            <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' }}>
              <div>
                <div className="sub">Клиентов</div>
                <div style={{ fontWeight: 600, fontSize: '1.1rem' }}>{agent.clientCount}</div>
              </div>
              <div>
                <div className="sub">Баланс</div>
                <div style={{ fontWeight: 600, fontSize: '1.1rem' }}>{agent.balance}</div>
              </div>
              <div>
                <div className="sub">Начислено</div>
                <div style={{ fontWeight: 600, fontSize: '1.1rem' }}>{agent.credited}</div>
              </div>
              <div>
                <div className="sub">Списано</div>
                <div style={{ fontWeight: 600, fontSize: '1.1rem' }}>{agent.debited}</div>
              </div>
            </div>
          </div>

          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Имя агента</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
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
              <span className="section-title">Новый пароль</span>
              <input
                type="text"
                placeholder="Оставьте пустым, если без смены"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          </div>

          <div
            style={{
              display: 'grid',
              gap: 8,
              padding: 12,
              border: '1px solid #eee',
              borderRadius: 8,
              background: '#fafbff',
            }}
          >
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={isDisabled}
                onChange={(e) => setIsDisabled(e.target.checked)}
              />
              <span>Агент отключён</span>
            </label>
            <span className="sub" style={{ color: '#666' }}>
              При отключении агент не сможет войти в систему, а редактирование проектов его клиентов будет заблокировано.
            </span>
          </div>

          {hasCredChanges && (
            <div className="sub" style={{ color: '#d67', fontWeight: 500 }}>
              Внимание: при смене логина или пароля нужно передать агенту новые данные.
            </div>
          )}

          {error && (
            <div className="sub" style={{ color: '#d00' }}>
              {error}
            </div>
          )}

          {updatedPassword && (
            <div style={{ border: '1px dashed #dcdce6', borderRadius: 8, padding: 12, display: 'grid', gap: 8 }}>
              <div style={{ fontWeight: 600 }}>Учётные данные обновлены</div>
              <div className="sub">Новый пароль показывается один раз.</div>
              <div style={{ display: 'grid', gap: 6 }}>
                <div>
                  <div className="sub">Логин</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <code>{login}</code>
                    <button type="button" className="btn btn--ghost" onClick={() => copyToClipboard(login)}>
                      Копировать
                    </button>
                  </div>
                </div>
                <div>
                  <div className="sub">Новый пароль</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <code>{updatedPassword}</code>
                    <button type="button" className="btn btn--ghost" onClick={() => copyToClipboard(updatedPassword)}>
                      Копировать
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, borderTop: '1px solid #eee', paddingTop: 12 }}>
            <button type="button" className="btn" onClick={onClose}>Отмена</button>
            <button type="submit" className="btn btn--primary" disabled={loading}>
              {loading ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AdminAgentCardModal;
