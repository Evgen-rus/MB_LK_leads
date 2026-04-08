import { useEffect, useMemo, useState } from 'react';
import { createAdminAgent, type AdminAgentCreateResp } from '../api';

type AdminCreateAgentModalProps = {
  onClose: () => void;
  onCreated?: (resp: AdminAgentCreateResp) => void;
};

function translitLoginBase(text: string): string {
  const map: Record<string, string> = {
    а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z',
    и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r',
    с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh',
    щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
  };
  const cleaned = Array.from(text.toLowerCase())
    .map((ch) => {
      if (/[a-z0-9]/.test(ch)) return ch;
      if (map[ch]) return map[ch];
      if (/[а-яё]/.test(ch)) return map[ch] ?? '';
      if (/[ _.-]/.test(ch)) return '-';
      return '';
    })
    .join('');
  const collapsed = cleaned.replace(/-+/g, '-').replace(/^-+|-+$/g, '');
  return collapsed || 'agent';
}

function generatePassword(length = 12): string {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let result = '';
  for (let i = 0; i < length; i += 1) {
    result += chars[Math.floor(Math.random() * chars.length)];
  }
  return result;
}

function normalizeDigits(value: string): string {
  return value.replace(/\D+/g, '');
}

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

function AdminCreateAgentModal({ onClose, onCreated }: AdminCreateAgentModalProps) {
  const [name, setName] = useState('');
  const [inn, setInn] = useState('');
  const [phone, setPhone] = useState('');
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState(() => generatePassword());
  const [loginEdited, setLoginEdited] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<AdminAgentCreateResp | null>(null);

  const suggestedLogin = useMemo(() => translitLoginBase(name).slice(0, 40), [name]);

  useEffect(() => {
    if (!loginEdited) {
      setLogin(suggestedLogin);
    }
  }, [suggestedLogin, loginEdited]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError('Укажите имя агента');
      return;
    }
    const innDigits = normalizeDigits(inn);
    if (![10, 12].includes(innDigits.length)) {
      setError('ИНН должен содержать 10 или 12 цифр');
      return;
    }
    const phoneDigits = normalizeDigits(phone);
    if (phoneDigits.length < 10) {
      setError('Телефон должен содержать минимум 10 цифр');
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const resp = await createAdminAgent({
        name: name.trim(),
        inn: innDigits,
        phone: phone.trim(),
        login: login.trim() || undefined,
        password: password.trim() || undefined,
      });
      setCreated(resp);
      onCreated?.(resp);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось создать агента'));
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
          maxWidth: 540,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Новый агент</div>
          <button type="button" className="btn btn--ghost" onClick={onClose} style={{ padding: '6px 10px' }}>
            ✕
          </button>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20, display: 'grid', gap: 12 }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Имя агента</span>
            <input
              autoFocus
              type="text"
              placeholder="Иван Петров"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
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
              <span className="section-title">Телефон</span>
              <input
                type="tel"
                placeholder="+7 999 123-45-67"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </label>
          </div>

          <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Логин</span>
              <input
                type="text"
                value={login}
                onChange={(e) => {
                  setLoginEdited(true);
                  setLogin(e.target.value);
                }}
              />
              <span className="hint">Предложение: {suggestedLogin}</span>
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Пароль</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  type="text"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => setPassword(generatePassword())}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  Сгенерировать
                </button>
              </div>
              <span className="hint" style={{ color: '#666' }}>
                После создания покажем итоговые логин и пароль из backend.
              </span>
            </label>
          </div>

          {error && (
            <div className="sub" style={{ color: '#d00' }}>
              {error}
            </div>
          )}

          {created && (
            <div style={{ border: '1px dashed #dcdce6', borderRadius: 8, padding: 12, display: 'grid', gap: 8 }}>
              <div style={{ fontWeight: 600 }}>Агент создан</div>
              <div className="sub">Передайте агенту учётные данные. Пароль отображается один раз.</div>
              <div style={{ display: 'grid', gap: 6 }}>
                <div>
                  <div className="sub">Логин</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <code>{created.login}</code>
                    <button type="button" className="btn btn--ghost" onClick={() => copyToClipboard(created.login)}>
                      Копировать
                    </button>
                  </div>
                </div>
                <div>
                  <div className="sub">Пароль</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <code>{created.password}</code>
                    <button type="button" className="btn btn--ghost" onClick={() => copyToClipboard(created.password)}>
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
              {loading ? 'Создаём…' : 'Создать агента'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AdminCreateAgentModal;
