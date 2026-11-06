import { useCallback, useEffect, useState } from 'react'
import { api, type Repo } from './services/api'
import { supabase } from './services/supabase'

export default function RepoPicker({ onOpen }: { onOpen: (repo: Repo) => void }) {
  const [repos, setRepos] = useState<Repo[]>([])
  const [name, setName] = useState('')
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
    try { onOpen(await api.createRepo(name.trim())) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not create repository.') }
  }

  return (
    <div style={{ maxWidth: 520, margin: '8vh auto', padding: 24, fontFamily: 'sans-serif' }}>
      <h2>Your repositories</h2>
      {loading && <p>Loading…</p>}
      {!loading && repos.length === 0 && <p>No repositories yet. Create your first one below.</p>}
      {repos.map(r => (
        <div key={r.id} style={{ padding: '8px 0' }}>
          <button onClick={() => onOpen(r)}>{r.name}</button>
        </div>
      ))}
      <hr />
      <input placeholder="new-repository-name" value={name} onChange={e => setName(e.target.value)} />{' '}
      <button disabled={!name.trim()} onClick={() => void create()}>Create</button>
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
      <p><button onClick={() => void supabase.auth.signOut()}>Log out</button></p>
    </div>
  )
}