import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import type { Session } from '@supabase/supabase-js'
import './index.css'
import App from './App.tsx'
import Explore from './Explore.tsx'
import Login from './Login.tsx'
import Profile from './Profile.tsx'
import PublicRepo from './PublicRepo.tsx'
import RepoPicker from './RepoPicker.tsx'
import Settings from './Settings.tsx'
import Username from './Username.tsx'
import { navigate, usePath } from './router.tsx'
import { supabase } from './services/supabase'
import { api, type Me, type Repo } from './services/api'
import { btn, muted, page } from './ui'

const RESERVED = new Set(['login', 'explore', 'api'])

function safeDecode(part: string): string {
  try { return decodeURIComponent(part) } catch { return part }
}

function Root() {
  const [session, setSession] = useState<Session | null>(null)
  const [ready, setReady] = useState(false)
  const [me, setMe] = useState<Me | null>(null)
  const [meError, setMeError] = useState('')
  const [view, setView] = useState<'repos' | 'settings'>('repos')
  const [repo, setRepo] = useState<Repo | null>(null)
  const path = usePath()
  const pathname = path.split('?')[0]
  const parts = pathname.split('/').filter(Boolean).map(safeDecode)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setReady(true)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s)
      if (!s) { setRepo(null); setMe(null); setView('repos') }
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  const userId = session?.user.id
  const loadMe = () => {
    setMeError('')
    api.me().then(setMe).catch(e => setMeError(e instanceof Error ? e.message : 'Could not reach the server.'))
  }
  useEffect(() => { if (userId) loadMe(); else setMe(null) }, [userId])

  useEffect(() => {
    if (session && pathname === '/login') navigate('/')
  }, [session, pathname])

  if (!ready) return null

  // Pages anyone can open
  if (parts[0] === 'login' && !session) return <Login />
  if (parts[0] === 'explore') return <Explore loggedIn={!!session} />
  if (parts.length === 2 && !RESERVED.has(parts[0])) {
    return <PublicRepo owner={parts[0]} name={parts[1]} loggedIn={!!session} />
  }
  if (parts.length === 1 && !RESERVED.has(parts[0])) {
    return <Profile owner={parts[0]} loggedIn={!!session} />
  }
  if (!session) return <Explore loggedIn={false} />

  // Signed-in home
  if (meError) return (
    <div style={page}>
      <p style={{ color: 'crimson' }}>{meError}</p>
      <button style={btn} onClick={loadMe}>Try again</button>{' '}
      <button style={btn} onClick={() => void supabase.auth.signOut()}>Log out</button>
    </div>
  )
  if (!me) return <div style={page}><p style={muted}>Loading…</p></div>
  if (!me.username) return <Username onDone={u => setMe({ ...me, username: u })} />
  if (repo) return <App repo={repo} onBack={() => setRepo(null)} />
  if (view === 'settings') return <Settings me={me} onBack={() => setView('repos')} />
  return <RepoPicker me={me} onOpen={setRepo} onSettings={() => setView('settings')} />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
