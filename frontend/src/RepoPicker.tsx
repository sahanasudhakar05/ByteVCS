import { useCallback, useEffect, useState } from 'react'
import { api, type Me, type Repo } from './services/api'
import { supabase } from './services/supabase'
import { btn, code, ghost, input, muted, page, row } from './ui'
import { navigate } from './router.tsx'

export default function RepoPicker({ me, onOpen, onSettings }: { me: Me; onOpen: (repo: Repo) => void; onSettings: () => void }) {
  const [repos, setRepos] = useState<Repo[]>([])
  const [name, setName] = useState('')
  const [isPublic, setIsPublic] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { setRepos((await api.listRepos()).repos) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load repositories.') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const create = async () => {
    setError('')
    try { onOpen(await api.createRepo(name.trim(), !isPublic)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not create repository.') }
  }

  const toggle = async (repo: Repo) => {
    setError('')
    try { await api.updateRepo(repo.id, { is_private: !repo.is_private }); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not change visibility.') }
  }

  return (
    <div style={page}>
      <h2>Your repositories <span style={muted}>({me.username})</span></h2>
      {loading && <p>Loading…</p>}
      {!loading && repos.length === 0 && <p style={muted}>No repositories yet. Create one below, or run <span style={code}>bytevcs push</span> inside a project folder.</p>}
      {repos.map(r => (
        <div key={r.id} style={row}>
          <div>
            <button style={ghost} onClick={() => onOpen(r)}>{r.name}</button>{' '}
            <span style={muted}>{r.is_private ? 'Private' : 'Public'}</span>
          </div>
          <button style={ghost} onClick={() => void toggle(r)}>Make {r.is_private ? 'public' : 'private'}</button>
        </div>
      ))}
      <hr />
      <input style={input} placeholder="new-repository-name" value={name} onChange={e => setName(e.target.value)} />
      <label style={{ marginRight: 8 }}><input type="checkbox" checked={isPublic} onChange={e => setIsPublic(e.target.checked)} /> Public</label>
      <button style={btn} disabled={!name.trim()} onClick={() => void create()}>Create</button>
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
      <p>
        <button style={ghost} onClick={() => navigate('/explore')}>Explore</button>{' '}
        <button style={ghost} onClick={onSettings}>Settings and tokens</button>{' '}
        <button style={ghost} onClick={() => void supabase.auth.signOut()}>Log out</button>
      </p>
    </div>
  )
}

