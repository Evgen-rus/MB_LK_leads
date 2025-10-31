import { useState } from 'react';
import { login } from '../api';

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
    } catch (e: any) {
      setError(e?.message || 'Ошибка входа');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{display:'flex',alignItems:'center',justifyContent:'center',minHeight:'100vh'}}>
      <form onSubmit={handleSubmit} className="modal-card" style={{padding:24, width:360, background:'#fff', borderRadius:8, boxShadow:'0 10px 30px rgba(0,0,0,0.2)'}}>
        <div style={{fontSize:18,fontWeight:600,marginBottom:12}}>Вход</div>
        <label style={{display:'grid',gap:6,marginBottom:10}}>
          <span style={{fontSize:12,color:'#666'}}>Логин</span>
          <input type="text" value={username} onChange={(e)=>setUsername(e.target.value)} autoFocus />
        </label>
        <label style={{display:'grid',gap:6,marginBottom:10}}>
          <span style={{fontSize:12,color:'#666'}}>Пароль</span>
          <input type="password" value={password} onChange={(e)=>setPassword(e.target.value)} />
        </label>
        {error && <div style={{color:'#b00020',fontSize:12,marginBottom:8}}>{error}</div>}
        <div style={{display:'flex',justifyContent:'flex-end',gap:8}}>
          <button type="submit" className="btn btn--primary" disabled={loading}>{loading?'Входим...':'Войти'}</button>
        </div>
      </form>
    </div>
  );
}

export default Login;


