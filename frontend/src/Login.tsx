import { useState } from 'react'
import { supabase } from './services/supabase'
const btn = { padding: '8px 14px', background: '#e5322d', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' } as const
export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setError(''); setInfo(''); setBusy(true)
    try {
      if (mode === 'login') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      } else {
        const { data, error } = await supabase.auth.signUp({ email, password })
        if (error) throw error
        if (!data.session) setInfo('Check your email to confirm your account, then log in.')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    setError(''); setInfo('')
    if (!email) { setError('Enter your email first.'); return }
    const { error } = await supabase.auth.resetPasswordForEmail(email)
    if (error) setError(error.message)
    else setInfo('If that email has an account, a reset link is on its way.')
  }

  return (
    <div style={{ maxWidth: 380, margin: '10vh auto', padding: 24, fontFamily: 'sans-serif',color: '#222', background: '#fff', borderRadius: 8}}>
      <h1>bytevcs</h1>
      <p>{mode === 'login' ? 'Log in to your account' : 'Create an account'}</p>
      <input style={{ display: 'block', width: '100%', marginBottom: 8, padding: 8 }}
        type="email" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} />
      <input style={{ display: 'block', width: '100%', marginBottom: 8, padding: 8 }}
        type="password" placeholder="Password (12+ characters)" value={password}
        onChange={e => setPassword(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') void submit() }} />
      <button style={btn} disabled={busy || !email || !password} onClick={() => void submit()}>
        {busy ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Sign up'}
      </button>{' '}
      <button onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError(''); setInfo('') }}>
        {mode === 'login' ? 'Need an account?' : 'Have an account?'}
      </button>
      {mode === 'login' && <p><a href="#" onClick={e => { e.preventDefault(); void reset() }}>Forgot password?</a></p>}
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
      {info && <p style={{ color: 'seagreen' }}>{info}</p>}
    </div>
  )
}