import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'
import { request, type Repo } from './services/api'
import { supabase } from './services/supabase'
import { navigate } from './router.tsx'

type Commit = { hash: string; short_hash: string; message: string; timestamp?: string; parent?: string | null; merge_parent?: string | null }
type FileItem = { path: string; tracked: boolean; staged: boolean; status: string }
type Branch = { name: string; current: boolean }
type StatusData = Record<string, string[]>

function App({ repo, onBack }: { repo: Repo; onBack: () => void }) {
  const base = `/api/repos/${repo.id}`
  const [repository, setRepository] = useState<any>(null)
  const [commits, setCommits] = useState<Commit[]>([])
  const [files, setFiles] = useState<FileItem[]>([])
  const [branches, setBranches] = useState<Branch[]>([])
  const [changes, setChanges] = useState<StatusData>({})
  const [diff, setDiff] = useState('')
  const [message, setMessage] = useState('')
  const [commitMessage, setCommitMessage] = useState('')
  const [newFile, setNewFile] = useState('')
  const [editPath, setEditPath] = useState('')
  const [editContent, setEditContent] = useState('')
  const [newBranch, setNewBranch] = useState('')
  const [active, setActive] = useState('Overview')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [r, c, f, b, s, d] = await Promise.all([
        request<any>(base), request<any>(`${base}/commits`), request<any>(`${base}/files`),
        request<any>(`${base}/branches`), request<any>(`${base}/status`), request<any>(`${base}/diff`),
      ])
      setRepository(r); setCommits(c.commits || []); setFiles(f.files || []); setBranches(b.branches || [])
      setChanges(s.changes || {}); setDiff(d.diff || '')
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not connect to ByteVCS API.') }
    finally { setLoading(false) }
  }, [base])

  useEffect(() => { void refresh() }, [refresh])
  const changedCount = useMemo(() => Object.values(changes).reduce((n, list) => n + list.length, 0), [changes])

  const act = async (path: string, body: object) => {
    setMessage(''); setError('')
    try { const result = await request<any>(`${base}${path}`, { method: 'POST', body: JSON.stringify(body) }); setMessage(result.message || 'Operation completed.'); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : 'Operation failed.') }
  }

  const saveFile = async () => {
    setMessage(''); setError('')
    try {
      const result = await request<any>(`${base}/file`, { method: 'PUT', body: JSON.stringify({ path: editPath.trim(), content: editContent }) })
      setMessage(result.message || 'File saved.'); setNewFile(editPath.trim()); await refresh()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save file.') }
  }

  const allChanges = Object.entries(changes).flatMap(([kind, paths]) => paths.map(path => ({ kind, path })))

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">B</div><div><b>bytevcs<span>.</span></b><small>CODE, IN CONTROL</small></div></div>
      <p className="eyebrow">WORKSPACE</p>
      {['Overview', 'Commits', 'Branches', 'Changes', 'Diff'].map((item) => <button key={item} className={`nav-button ${active === item ? 'selected' : ''}`} onClick={() => setActive(item)}>{item}</button>)}
      {repo.owner && <button className="nav-button" onClick={() => navigate(`/${repo.owner}/${repo.name}`)}>Time machine</button>}
      <button className="nav-button" onClick={onBack}>All repositories</button>
      <button className="nav-button" onClick={() => void supabase.auth.signOut()}>Log out</button>
      <div className="side-repo"><span className="status-dot"/> <b>{repository?.name || repo.name}</b><small>Hosted repository</small><small className="muted">{repository?.branch || 'Loading branch…'}</small></div>
      <div className="sidebar-bottom">BYTEVCS WEB · 0.2.0<br/>Python-powered version control</div>
    </aside>
    <main className="main-area">
      <header className="topbar"><div><span>Workspace</span><i>/</i><b>{active}</b></div><button className="secondary" onClick={() => void refresh()}>{loading ? 'Refreshing…' : '↻ Refresh'}</button></header>
      <div className="content">
        <div className="hero"><div><p className="eyebrow red">YOUR DEVELOPMENT SPACE</p><h1>Good work takes <em>iterations.</em></h1><p className="subhead">A live view of your code, your changes, and everything in between.</p></div><div className="branch-pill">⑂ {repository?.branch || '—'}</div></div>
        {error && <div className="alert error">{error} <button onClick={() => setError('')}>×</button></div>}
        {message && <div className="alert success">{message} <button onClick={() => setMessage('')}>×</button></div>}
        {active === 'Overview' && <>
          <div className="stats"><Stat label="Commits" value={commits.length}/><Stat label="Branches" value={branches.length}/><Stat label="Tracked files" value={files.filter(f => f.tracked).length}/><Stat label="Pending changes" value={changedCount}/></div>
          <div className="two-col"><section className="panel"><PanelTitle title="Recent commits" subtitle="Real history from your repository"/><CommitList commits={commits.slice(0, 6)}/></section><section className="panel"><PanelTitle title="Working tree" subtitle="Current file status"/><ChangeList items={allChanges.slice(0, 8)}/></section></div>
          <div className="panel"><PanelTitle title="Add or edit a file" subtitle="Write a file into this repository, then stage and commit it"/>
            <div className="action-grid"><div>
              <label>File path</label><input value={editPath} onChange={e => setEditPath(e.target.value)} placeholder="src/main.py"/>
              <label>Content</label><textarea rows={6} style={{ width: '100%' }} value={editContent} onChange={e => setEditContent(e.target.value)} placeholder="File contents (text only, up to 1 MB)"/>
              <button onClick={() => void saveFile()} disabled={!editPath.trim()}>Save file</button>
            </div></div>
          </div>
          <div className="panel"><PanelTitle title="Quick actions" subtitle="Run actual ByteVCS operations"/><ActionBar newFile={newFile} setNewFile={setNewFile} onStage={() => act('/stage', { path: newFile })} commitMessage={commitMessage} setCommitMessage={setCommitMessage} onCommit={() => act('/commits', { message: commitMessage })}/></div>
        </>}
        {active === 'Commits' && <section className="panel"><PanelTitle title="Commit history" subtitle="Follow the current branch's parent chain"/><CommitList commits={commits}/></section>}
        {active === 'Branches' && <section className="panel"><PanelTitle title="Branches" subtitle="Create and switch branches"/><div className="branch-list">{branches.map(b=><div className="branch-row" key={b.name}><span>⑂ {b.name}</span>{b.current ? <b className="tag">CURRENT</b> : <button className="secondary" onClick={() => act('/checkout', { name: b.name })}>Checkout</button>}</div>)}</div><div className="inline-form"><input value={newBranch} onChange={e=>setNewBranch(e.target.value)} placeholder="feature/new-feature"/><button onClick={() => { void act('/branches', { name: newBranch }); setNewBranch('') }}>Create branch</button></div><p className="muted small">Merge another branch into the current branch:</p><MergeForm branches={branches.filter(b=>!b.current)} onMerge={name=>act('/merge',{name})}/></section>}
        {active === 'Changes' && <section className="panel"><PanelTitle title="Changed files" subtitle="Stage, unstage or remove files"/><ChangeList items={allChanges}/><div className="file-table">{files.map(f=><div className="file-row" key={f.path}><code>{f.path}</code><span>{f.status.replaceAll('_',' ')}</span><div>{f.staged ? <button className="secondary" onClick={() => act('/unstage',{path:f.path})}>Unstage</button> : <button className="secondary" onClick={() => act('/stage',{path:f.path})}>Stage</button>}</div></div>)}</div></section>}
        {active === 'Diff' && <section className="panel"><PanelTitle title="Working diff" subtitle="Changes between working tree and staged/committed content"/><pre className="diff">{diff || 'No text differences to display.'}</pre></section>}
        <section className="integration-row"><div className="integration"><b>API server</b><span className="online">● Connected when data loads</span><small>FastAPI · Uvicorn</small></div><div className="integration"><b>Access</b><span className="online">● Private to your account</span><small>Supabase Auth · owner-checked routes</small></div></section>
        <footer>BYTEVCS · A PYTHON-BASED VERSION CONTROL SYSTEM <span>Hosted repositories · Supabase Auth</span></footer>
      </div>
    </main>
  </div>
}
function Stat({label,value}:{label:string;value:number}) { return <div className="stat"><small>{label}</small><strong>{value}</strong><span>Live repository data</span></div> }
function PanelTitle({title,subtitle}:{title:string;subtitle:string}) { return <div className="panel-title"><div><h2>{title}</h2><p>{subtitle}</p></div></div> }
function CommitList({commits}:{commits:Commit[]}) { return <div className="commit-list">{commits.length ? commits.map(c=><div className="commit-row" key={c.hash}><span className="commit-node"/><div><b>{c.message}</b><small><code>{c.short_hash}</code> · {c.timestamp ? new Date(c.timestamp).toLocaleString() : 'Timestamp unavailable'}</small></div></div>) : <p className="empty">No commits yet. Stage a file and create your first commit.</p>}</div> }
function ChangeList({items}:{items:{kind:string;path:string}[]}) { return <div className="change-list">{items.length ? items.map((item,i)=><div className="change-row" key={`${item.kind}-${item.path}-${i}`}><code>{item.path}</code><span>{item.kind.replaceAll('_',' ')}</span></div>) : <p className="empty">Working tree is clean.</p>}</div> }
function ActionBar(props:{newFile:string;setNewFile:(s:string)=>void;onStage:()=>void;commitMessage:string;setCommitMessage:(s:string)=>void;onCommit:()=>void}) { return <div className="action-grid"><div><label>File path to stage</label><input value={props.newFile} onChange={e=>props.setNewFile(e.target.value)} placeholder="src/main.py"/><button onClick={props.onStage} disabled={!props.newFile.trim()}>Stage file</button></div><div><label>Commit message</label><input value={props.commitMessage} onChange={e=>props.setCommitMessage(e.target.value)} placeholder="Describe your changes"/><button onClick={props.onCommit} disabled={!props.commitMessage.trim()}>Create commit</button></div></div> }
function MergeForm({branches,onMerge}:{branches:Branch[];onMerge:(s:string)=>void}) { const [value,setValue]=useState(''); return <div className="inline-form"><select value={value} onChange={e=>setValue(e.target.value)}><option value="">Select branch to merge</option>{branches.map(b=><option key={b.name} value={b.name}>{b.name}</option>)}</select><button disabled={!value} onClick={()=>onMerge(value)}>Merge</button></div> }
export default App