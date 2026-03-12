import { useEffect, useMemo, useState } from 'react';
import { createAdminClient, type AdminClientCreateResp } from '../api';

type AdminCreateClientModalProps = {
  onClose: () => void;
  onCreated?: (resp: AdminClientCreateResp) => void;
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
  return collapsed || 'client';
}

function generatePassword(length = 12): string {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let res = '';
  for (let i = 0; i < length; i += 1) {
    res += chars[Math.floor(Math.random() * chars.length)];
  }
  return res;
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

function AdminCreateClientModal({ onClose, onCreated }: AdminCreateClientModalProps) {
  const [name, setName] = useState('');
  const [inn, setInn] = useState('');
  const [phone, setPhone] = useState('');
  const [contact, setContact] = useState('');
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState(() => generatePassword());
  const [uniqueProjectNamesEnabled, setUniqueProjectNamesEnabled] = useState(false);
  const [loginEdited, setLoginEdited] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<AdminClientCreateResp | null>(null);

  const suggestedLogin = useMemo(() => translitLoginBase(name).slice(0, 40), [name]);

  useEffect(() => {
    if (!loginEdited) {
      setLogin(suggestedLogin);
    }
  }, [suggestedLogin, loginEdited]);

  function validate(): string | null {
    if (!name.trim()) return 'Укажите название клиента';
    const innDigits = normalizeDigits(inn);
    if (![10, 12].includes(innDigits.length)) return 'ИНН должен содержать 10 или 12 цифр';
    const phoneDigits = normalizeDigits(phone);
    if (phoneDigits.length < 10) return 'Телефон должен содержать минимум 10 цифр';
    if (!login.trim()) return 'Логин не может быть пустым';
    if (!password.trim()) return 'Пароль не может быть пустым';
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const resp = await createAdminClient({
        name: name.trim(),
        inn: normalizeDigits(inn),
        phone: phone.trim(),
        contact: contact.trim() || undefined,
        login: login.trim(),
        password: password.trim(),
        uniqueProjectNamesEnabled,
      });
      setCreated(resp);
      onCreated?.(resp);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось создать клиента'));
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
        zIndex: 1200,
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
          maxWidth: 520,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Новый клиент</div>
          <button type="button" className="btn btn--ghost" onClick={onClose} style={{ padding: '6px 10px' }}>
            ✕
          </button>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20, display: 'grid', gap: 12 }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Название клиента</span>
            <input
              autoFocus
              type="text"
              placeholder="ООО Ромашка"
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
              style={{ height: 34, borderRadius: 8, background: '#f7f7f9', border: '1px solid #e5e5e5', padding: '8px 10px' }}
            />
          </label>
          <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Логин (латиницей)</span>
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
                Запишите пароль: после создания клиента его можно будет только изменить.
              </span>
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
                checked={uniqueProjectNamesEnabled}
                onChange={(e) => setUniqueProjectNamesEnabled(e.target.checked)}
              />
              <span>Уникальные имена новых проектов</span>
            </label>
            <span className="sub" style={{ color: '#666' }}>
              Если включено, новые проекты клиента будут создаваться с маркером вида `B1_[MB54] Магнум`.
            </span>
          </div>

          {error && (
            <div className="sub" style={{ color: '#d00' }}>
              {error}
            </div>
          )}

          {created && (
            <div style={{ border: '1px dashed #dcdce6', borderRadius: 8, padding: 12, display: 'grid', gap: 8 }}>
              <div style={{ fontWeight: 600 }}>Клиент создан</div>
              <div className="sub">Передайте логин и пароль клиенту. Пароль отображается один раз.</div>
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
              {loading ? 'Создаём…' : created ? 'Создать ещё' : 'Создать клиента'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AdminCreateClientModal;

