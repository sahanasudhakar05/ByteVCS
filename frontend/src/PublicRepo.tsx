import { useEffect, useState } from 'react'
import { API_BASE, request, type Repo } from './services/api'
import { btn, code, ghost, muted, page, row } from './ui'
import { Link } from './router.tsx'
import { TopBar } from './Explore.tsx'
import Timeline from './Timeline.tsx'

type Info = Repo & { branch: string; head: string | null }
type CommitItem = { hash: string; short_hash: string; message: string; timestamp?: string | null }
const MAX_SHOWN = 100_000

export default function PublicRepo({ owner, name, loggedIn }: { owner: string; name: string; loggedIn: boolean }) {
  const base = `/api/u/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`
  const [info, setInfo] = useState<Info | null>(null)
  const [commits, setCommits] = useState<CommitItem[]>([])
  const [files, setFiles] = useState<string[]>([])
  const [tab, setTab] = useState<'files' | 'commits' | 'timeline'>('files')
  const [openPath, setOpenPath] = useState('')
  const [content, setContent] = useState('')
  const [fileError, setFileError] = useState('')
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setInfo(null); setError(''); setOpenPath(''); setContent('')
    Promise.all([
      request<Info>(base),
      request<{ commits: CommitItem[] }>(`${base}/commits`),
      request<{ files: { path: string }[] }>(`${base}/files`),
    ])
      .then(([i, c, f]) => { setInfo(i); setCommits(c.commits); setFiles(f.files.map(x => x.path)) })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load this repository.'))
  }, [base])

  const openFile = async (path: string) => {
    setOpenPath(path); setContent(''); setFileError('')
    try {
      const data = await request<{ content: string }>(`${base}/file?path=${encodeURIComponent(path)}`)
      setContent(data.content.length > MAX_SHOWN ? data.content.slice(0, MAX_SHOWN) + '\n\n… (truncated)' : data.content)
    } catch (e) {
      setFileError(e instanceof Error ? e.message : 'Could not open this file.')
    }
  }

  const cloneCommand = `bytevcs clone ${API_BASE}/${owner}/${name}`
  const copy = async () => {
    try { await navigator.clipboard.writeText(cloneCommand); setCopied(true) } catch { setCopied(false) }
  }

  return (
    <div style={{ ...page, maxWidth: 820 }}>
      <TopBar loggedIn={loggedIn} />
      {error && <p style={{ color: 'crimson' }}>{error} It may not exist, or it may be private.</p>}
      {!error && !info && <p style={muted}>Loading…</p>}
      {info && (
        <>
          <h2>
            <Link to={`/${owner}`} style={{ color: '#222' }}>{owner}</Link> / {name}{' '}
            <span style={muted}>{info.is_private ? 'Private' : 'Public'}</span>
          </h2>
          {info.description && <p>{info.description}</p>}
          <p style={muted}>Branch: <b>{info.branch}</b></p>

          <div style={{ padding: 12, background: '#f4f4f4', borderRadius: 6, margin: '12px 0' }}>
            <div style={muted}>Get a copy on your computer:</div>
            <span style={code}>{cloneCommand}</span>{' '}
            <button style={btn} onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</button>
          </div>

          <p>
            <button style={tab === 'files' ? btn : ghost} onClick={() => setTab('files')}>Files ({files.length})</button>{' '}
            <button style={tab === 'commits' ? btn : ghost} onClick={() => setTab('commits')}>Commits ({commits.length})</button>{' '}
            <button style={tab === 'timeline' ? btn : ghost} onClick={() => setTab('timeline')}>Time machine</button>
          </p>

          {tab === 'files' && (
            <>
              {files.length === 0 && <p style={muted}>No files yet.</p>}
              {files.map(f => (
                <div key={f} style={row}>
                  <button style={{ ...ghost, background: f === openPath ? '#ddd' : '#eee' }} onClick={() => void openFile(f)}>{f}</button>
                </div>
              ))}
              {openPath && (
                <div style={{ marginTop: 16 }}>
                  <b>{openPath}</b>
                  {fileError && <p style={{ color: 'crimson' }}>{fileError}</p>}
                  {!fileError && (
                    <pre style={{ background: '#f8f8f8', padding: 12, borderRadius: 6, overflow: 'auto', maxHeight: 420, fontSize: 13 }}>{content}</pre>
                  )}
                </div>
              )}
            </>
          )}

          {tab === 'timeline' && <Timeline base={base} />}

          {tab === 'commits' && (
            <>
              {commits.length === 0 && <p style={muted}>No commits yet.</p>}
              {commits.map(c => (
                <div key={c.hash} style={row}>
                  <div><b>{c.message}</b> <span style={code}>{c.short_hash}</span></div>
                  <span style={muted}>{c.timestamp ? new Date(c.timestamp).toLocaleString() : ''}</span>
                </div>
              ))}
            </>
          )}
        </>
      )}
    </div>
  )
}
