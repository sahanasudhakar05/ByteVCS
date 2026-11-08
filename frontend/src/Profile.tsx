import { useEffect, useState } from 'react'
import { request, type Repo } from './services/api'
import { muted, page } from './ui'
import { RepoRow, TopBar } from './Explore.tsx'

export default function Profile({ owner, loggedIn }: { owner: string; loggedIn: boolean }) {
  const [repos, setRepos] = useState<Repo[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setRepos(null); setError('')
    request<{ repos: Repo[] }>(`/api/u/${encodeURIComponent(owner)}`)
      .then(d => setRepos(d.repos))
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load this profile.'))
  }, [owner])

  return (
    <div style={page}>
      <TopBar loggedIn={loggedIn} />
      <h2>{owner}</h2>
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
      {!error && repos === null && <p style={muted}>Loading…</p>}
      {repos && repos.length === 0 && <p style={muted}>No public repositories yet.</p>}
      {repos && repos.map(r => <RepoRow key={r.id} repo={r} />)}
    </div>
  )
}
