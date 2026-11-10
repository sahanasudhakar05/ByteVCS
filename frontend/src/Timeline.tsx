import { useEffect, useMemo, useState } from 'react'
import { request } from './services/api'
import { btn, code, ghost, muted, row } from './ui'

type TCommit = {
  hash: string
  short_hash: string
  message: string
  timestamp?: string | null
  merge: boolean
  file_count: number
  bytes: number
  added: number
  modified: number
  deleted: number
}
type TFile = { path: string; size: number; status: 'added' | 'modified' | 'unchanged' | 'deleted' }

const W = 760
const H = 170
const PAD = 22
const COLORS = { added: '#2e9e5b', modified: '#d9a400', deleted: '#c2352f' }

function fmt(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const statusColor = (s: TFile['status']) =>
  s === 'added' ? COLORS.added : s === 'modified' ? COLORS.modified : s === 'deleted' ? COLORS.deleted : '#999'
const statusLabel = (s: TFile['status']) =>
  s === 'added' ? 'new' : s === 'modified' ? 'changed' : s === 'deleted' ? 'removed' : ''

export default function Timeline({ base }: { base: string }) {
  const [commits, setCommits] = useState<TCommit[] | null>(null)
  const [more, setMore] = useState(false)
  const [index, setIndex] = useState(0)
  const [metric, setMetric] = useState<'files' | 'bytes'>('files')
  const [tree, setTree] = useState<TFile[]>([])
  const [treeNote, setTreeNote] = useState('')
  const [playing, setPlaying] = useState(false)
  const [openPath, setOpenPath] = useState('')
  const [content, setContent] = useState('')
  const [fileError, setFileError] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setCommits(null)
    setError('')
    request<{ commits: TCommit[]; more_history: boolean }>(`${base}/timeline`)
      .then(d => {
        if (cancelled) return
        setCommits(d.commits)
        setMore(d.more_history)
        setIndex(Math.max(0, d.commits.length - 1))
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load the history.') })
    return () => { cancelled = true }
  }, [base])

  const n = commits?.length ?? 0
  const current = commits && commits[index] ? commits[index] : null

  useEffect(() => {
    if (!current) return
    let cancelled = false
    const timer = setTimeout(() => {
      request<{ files: TFile[]; truncated: boolean }>(`${base}/tree?commit=${current.hash}`)
        .then(d => {
          if (cancelled) return
          setTree(d.files)
          setTreeNote(d.truncated ? 'Showing the first 2,000 files.' : '')
        })
        .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load this moment.') })
    }, 120)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [base, current?.hash])

  useEffect(() => { setOpenPath(''); setContent(''); setFileError('') }, [index])

  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setIndex(i => Math.min(i + 1, n - 1)), 700)
    return () => clearInterval(id)
  }, [playing, n])

  useEffect(() => { if (playing && index >= n - 1) setPlaying(false) }, [playing, index, n])

  const geo = useMemo(() => {
    if (!commits || commits.length === 0) return null
    const count = commits.length
    const step = count === 1 ? 0 : (W - 2 * PAD) / (count - 1)
    const xs = commits.map((_, i) => (count === 1 ? W / 2 : PAD + i * step))
    const values = commits.map(c => (metric === 'files' ? c.file_count : c.bytes))
    const max = Math.max(1, ...values)
    const ys = values.map(v => H - PAD - (v / max) * (H - 2 * PAD))
    const line = xs.map((x, i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ')
    const area = `${line} L${xs[count - 1].toFixed(1)},${H - PAD} L${xs[0].toFixed(1)},${H - PAD} Z`
    const churnMax = Math.max(1, ...commits.map(c => c.added + c.modified + c.deleted))
    return { xs, ys, line, area, max, step, churnMax }
  }, [commits, metric])

  const openFile = async (f: TFile) => {
    if (!current || f.status === 'deleted') return
    setOpenPath(f.path)
    setContent('')
    setFileError('')
    try {
      const d = await request<{ content: string }>(
        `${base}/file-at?commit=${current.hash}&path=${encodeURIComponent(f.path)}`,
      )
      setContent(d.content)
    } catch (e) {
      setFileError(e instanceof Error ? e.message : 'Could not open this file.')
    }
  }

  const togglePlay = () => {
    if (!playing && index >= n - 1) setIndex(0)
    setPlaying(p => !p)
  }

  if (error && !commits) return <p style={{ color: 'crimson' }}>{error}</p>
  if (!commits) return <p style={muted}>Loading history…</p>
  if (commits.length === 0 || !geo || !current) return <p style={muted}>No history yet. Push a commit to see it here.</p>

  const rank = (f: TFile) => (f.status === 'unchanged' ? 1 : 0)
  const ordered = [...tree].sort((a, b) => rank(a) - rank(b) || a.path.localeCompare(b.path))

  return (
    <div>
      <p style={muted}>
        Drag through the history. Each point is a commit, and the project is shown exactly as it was at that moment.
        {more ? ` Showing the most recent ${n} commits.` : ''}
      </p>

      <div style={{ marginBottom: 8 }}>
        <button style={metric === 'files' ? btn : ghost} onClick={() => setMetric('files')}>Files</button>{' '}
        <button style={metric === 'bytes' ? btn : ghost} onClick={() => setMetric('bytes')}>Size</button>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Project growth over time">
        <text x={PAD} y={12} fontSize={11} fill="#666">
          {metric === 'files' ? `${geo.max} files` : fmt(geo.max)}
        </text>
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#ddd" />
        <path d={geo.area} fill="#e5322d" fillOpacity={0.12} />
        <path d={geo.line} fill="none" stroke="#e5322d" strokeWidth={2} />
        <line x1={geo.xs[index]} y1={PAD / 2} x2={geo.xs[index]} y2={H - PAD} stroke="#e5322d" strokeDasharray="4 3" />
        {commits.map((c, i) => (
          <g key={c.hash} onClick={() => { setPlaying(false); setIndex(i) }} style={{ cursor: 'pointer' }}>
            <rect x={geo.xs[i] - Math.max(geo.step / 2, 6)} y={0} width={Math.max(geo.step, 12)} height={H} fill="transparent" />
            {c.merge && <circle cx={geo.xs[i]} cy={geo.ys[i]} r={9} fill="none" stroke="#888" />}
            <circle cx={geo.xs[i]} cy={geo.ys[i]} r={i === index ? 6 : 3.5} fill={i === index ? '#e5322d' : '#fff'} stroke="#e5322d" strokeWidth={2} />
            <title>{`${c.short_hash}  ${c.message}`}</title>
          </g>
        ))}
      </svg>

      <svg viewBox={`0 0 ${W} 46`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Files added, changed and removed per commit">
        {commits.map((c, i) => {
          const bw = Math.max(Math.min(geo.step * 0.6, 18), 3)
          let y = 42
          const parts = [['added', c.added], ['modified', c.modified], ['deleted', c.deleted]] as const
          return (
            <g key={c.hash} onClick={() => { setPlaying(false); setIndex(i) }} style={{ cursor: 'pointer' }}>
              {parts.map(([k, v]) => {
                const h = (v / geo.churnMax) * 38
                y -= h
                return <rect key={k} x={geo.xs[i] - bw / 2} y={y} width={bw} height={h} fill={COLORS[k]} opacity={i === index ? 1 : 0.55} />
              })}
            </g>
          )
        })}
      </svg>
      <div style={{ ...muted, fontSize: 12 }}>
        <span style={{ color: COLORS.added }}>■</span> added{' '}
        <span style={{ color: COLORS.modified }}>■</span> changed{' '}
        <span style={{ color: COLORS.deleted }}>■</span> removed{' '}
        <span style={{ color: '#888' }}>◯</span> merge commit
      </div>

      <input
        type="range"
        min={0}
        max={Math.max(0, n - 1)}
        value={index}
        onChange={e => { setPlaying(false); setIndex(Number(e.target.value)) }}
        style={{ width: '100%', margin: '10px 0' }}
        aria-label="Move through history"
      />
      <div>
        <button style={ghost} disabled={index <= 0} onClick={() => { setPlaying(false); setIndex(index - 1) }}>◀</button>{' '}
        <button style={btn} onClick={togglePlay}>{playing ? 'Pause' : 'Play history'}</button>{' '}
        <button style={ghost} disabled={index >= n - 1} onClick={() => { setPlaying(false); setIndex(index + 1) }}>▶</button>{' '}
        <span style={muted}>Commit {index + 1} of {n}</span>
      </div>

      <div style={{ margin: '12px 0' }}>
        <b>{current.message || '(no message)'}</b> <span style={code}>{current.short_hash}</span>
        {current.merge && <span style={muted}> merge</span>}
        <div style={muted}>
          {current.timestamp ? new Date(current.timestamp).toLocaleString() : ''} · {current.file_count} files · {fmt(current.bytes)} ·{' '}
          <span style={{ color: COLORS.added }}>+{current.added}</span>{' '}
          <span style={{ color: COLORS.modified }}>~{current.modified}</span>{' '}
          <span style={{ color: COLORS.deleted }}>−{current.deleted}</span>
        </div>
      </div>

      {ordered.map(f => (
        <div key={`${f.status}-${f.path}`} style={row}>
          <button
            style={{ ...ghost, textDecoration: f.status === 'deleted' ? 'line-through' : 'none', cursor: f.status === 'deleted' ? 'default' : 'pointer' }}
            onClick={() => void openFile(f)}
          >
            {f.path}
          </button>
          <span>
            <span style={{ color: statusColor(f.status) }}>{statusLabel(f.status)}</span>{' '}
            <span style={muted}>{fmt(f.size)}</span>
          </span>
        </div>
      ))}
      {treeNote && <p style={muted}>{treeNote}</p>}
      {error && <p style={{ color: 'crimson' }}>{error}</p>}

      {openPath && (
        <div style={{ marginTop: 16 }}>
          <b>{openPath}</b> <span style={muted}>as of {current.short_hash}</span>
          {fileError && <p style={{ color: 'crimson' }}>{fileError}</p>}
          {!fileError && (
            <pre style={{ background: '#f8f8f8', padding: 12, borderRadius: 6, overflow: 'auto', maxHeight: 420, fontSize: 13 }}>{content}</pre>
          )}
        </div>
      )}
    </div>
  )
}