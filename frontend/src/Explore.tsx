import { useCallback, useEffect, useState } from 'react'
import { request, type Repo } from './services/api'
import { btn, ghost, input, muted, page, row } from './ui'
import { Link, navigate } from './router.tsx'

export function TopBar({ loggedIn }: { loggedIn: boolean }) {
  return (
    <div style={row}>
      <Link to="/" style={{ fontSize: 22, fontWeight: 'bold', color: '#222', textDecoration: 'none' }}>bytevcs</Link>
      <span>
        <Link to="/explore" style={{ marginRight: 12, color: '#222' }}>Explore</Link>
        {loggedIn
          ? <Link to="/" style={{ color: '#222' }}>My repositories</Link>
          : <button style={btn} onClick={() => navigate('/login')}>Log in / Sign up</button>}
      </span>
    </div>
  )
}

export function RepoRow({ repo }: { repo: Repo }) {
  return (
    <div style={row}>
      <div>
        <Link to={`/${repo.owner}/${repo.name}`} style={{ fontWeight: 'bold', color: '#e5322d', textDecoration: 'none' }}>
          {repo.owner}/{repo.name}
        </Link>
        {repo.description && <div style={muted}>{repo.description}</div>}
      </div>
      <span style={muted}>{new Date(repo.created_at).toLocaleDateString()}</span>
    </div>
  )
}

export default function Explore({ loggedIn }: { loggedIn: boolean }) {
  const initial = new URLSearchParams(window.location.search).get('q') || ''
  const [q, setQ] = useState(initial)
  const [repos, setRepos] = useState<Repo[]>([])
  const [heading, setHeading] = useState('Recently published')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (term: string) => {
    setLoading(true); setError('')
    try {
      const t = term.trim()
      const data = t
        ? await request<{ repos: Repo[] }>(`/api/search?q=${encodeURIComponent(t)}`)
        : await request<{ repos: Repo[] }>('/api/explore')
      setRepos(data.repos)
      setHeading(t ? `Results for "${t}"` : 'Recently published')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load repositories.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(initial) }, [load, initial])

  const search = () => {
    const t = q.trim()
    window.history.replaceState({}, '', t ? `/explore?q=${encodeURIComponent(t)}` : '/explore')
    void load(t)
  }

  return (
    <div style={page}>
      <TopBar loggedIn={loggedIn} />
      <h2>Find repositories</h2>
      <input
        style={{ ...input, width: '60%' }}
        placeholder="Search by name, description or username"
        value={q}
        onChange={e => setQ(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') search() }}
      />
      <button style={btn} onClick={search}>Search</button>{' '}
      {q && <button style={ghost} onClick={() => { setQ(''); window.history.replaceState({}, '', '/explore'); void load('') }}>Clear</button>}
      <h3>{heading}</h3>
      {loading && <p style={muted}>Loading…</p>}
      {!loading && !error && repos.length === 0 && <p style={muted}>Nothing found. Only public repositories are listed.</p>}
      {repos.map(r => <RepoRow key={r.id} repo={r} />)}
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
    </div>
  )
}
