import { useState } from 'react';
import { login } from '../api';
import './Login.css';

type Props = {
  onSuccess: () => void;
};

function Login({ onSuccess }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      onSuccess();
    } catch (e: unknown) {
      // Обработка различных типов ошибок
      let errorMessage = 'Ошибка входа';
      
      if (e && typeof e === 'object' && 'isNetworkError' in e && (e as { isNetworkError?: boolean }).isNetworkError) {
        // Сетевая ошибка
        errorMessage = 'Нет соединения с сервером. Проверьте подключение к интернету.';
      } else if (e && typeof e === 'object' && 'status' in e && (e as { status?: number }).status === 0) {
        errorMessage = 'Нет соединения с сервером. Проверьте подключение к интернету.';
      } else if (e && typeof e === 'object' && 'status' in e && (e as { status?: number }).status === 401) {
        // Неверные учетные данные - стандартное сообщение для безопасности
        errorMessage = 'Неверный логин или пароль';
      } else if (e && typeof e === 'object' && 'status' in e && (e as { status?: number }).status === 403) {
        errorMessage = 'Доступ запрещен';
      } else if (e && typeof e === 'object' && 'status' in e && (e as { status?: number }).status === 404) {
        errorMessage = 'Сервис не найден';
      } else if (e && typeof e === 'object' && 'status' in e && typeof (e as { status?: number }).status === 'number' && (e as { status: number }).status >= 500) {
        errorMessage = 'Ошибка сервера. Попробуйте позже.';
      } else if (e && typeof e === 'object' && 'message' in e && typeof (e as { message?: unknown }).message === 'string') {
        // Пытаемся извлечь понятное сообщение
        const msg = (e as { message: string }).message;
        // Если это JSON с detail, извлекаем его
        if (msg.includes('Bad credentials') || msg.includes('detail')) {
          errorMessage = 'Неверный логин или пароль';
        } else if (msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
          errorMessage = 'Нет соединения с сервером. Проверьте подключение к интернету.';
        } else {
          // Для других случаев используем сообщение как есть, но ограничиваем длину
          errorMessage = msg.length > 100 ? 'Произошла ошибка при входе' : msg;
        }
      }
      
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-container">
      <div className="login-card">
        <header className="login-header">
          <h1 className="login-brand">
            <span className="login-brand__name">Mad Boss</span>
            <span className="login-brand__pill">leads</span>
          </h1>
          <p className="login-subtitle">Войдите в личный кабинет</p>
        </header>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="login-field">
            <label htmlFor="username" className="login-label">
              Логин
            </label>
            <input
              id="username"
              type="text"
              className="login-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Введите логин"
              autoFocus
              disabled={loading}
            />
          </div>

          <div className="login-field">
            <label htmlFor="password" className="login-label">
              Пароль
            </label>
            <input
              id="password"
              type="password"
              className="login-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Введите пароль"
              disabled={loading}
            />
          </div>

          {error && (
            <div className="login-error">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
              </svg>
              {error}
            </div>
          )}

          <button
            type="submit"
            className="login-button"
            disabled={loading || !username || !password}
          >
            {loading ? (
              <>
                <svg className="login-spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12a9 9 0 1 1-6.219-8.56"></path>
                </svg>
                Входим...
              </>
            ) : (
              'Войти'
            )}
          </button>
        </form>
      </div>
    </div>
  );
}

export default Login;


