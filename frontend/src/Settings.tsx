import { useCallback, useEffect, useState } from 'react'
import { api, API_BASE, type Me, type TokenInfo } from './services/api'
import { btn, code, ghost, input, muted, page, row } from './ui'

export default function Settings({ me, onBack }: { me: Me; onBack: () => void }) {
  const [tokens, setTokens] = useState<TokenInfo[]>([])
  const [name, setName] = useState('')
  const [fresh, setFresh] = useState('')
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try { setTokens((await api.listTokens()).tokens) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load tokens.') }
  }, [])
  useEffect(() => { void load() }, [load])

  const create = async () => {
    setError('')
    try {
      const t = await api.createToken(name.trim())
      setFresh(t.token); setCopied(false); setName('')
      await load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not create token.') }
  }

  const revoke = async (id: string) => {
    if (!window.confirm('Revoke this token? Anything using it will stop working.')) return
    setError('')
    try { await api.revokeToken(id); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not revoke token.') }
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(fresh); setCopied(true) }
    catch { setError('Could not copy automatically. Select the token and copy it by hand.') }
  }

  return (
    <div style={page}>
      <p><button style={ghost} onClick={onBack}>← Back</button></p>
      <h2>Settings</h2>
      <p>Signed in as <b>{me.username}</b> <span style={muted}>({me.email})</span></p>

      <h3>Command-line access</h3>
      <p style={muted}>
        Create a token, then run <span style={code}>bytevcs login --server {API_BASE}</span> and paste it when asked.
        After that, <span style={code}>bytevcs push</span> inside any project folder uploads it as <b>{me.username}/folder-name</b> (private unless you add <span style={code}>--public</span>).
      </p>

      <input style={input} placeholder="Token name, e.g. my laptop" value={name} onChange={e => setName(e.target.value)} />
      <button style={btn} disabled={!name.trim()} onClick={() => void create()}>Create token</button>

      {fresh && (
        <div style={{ margin: '16px 0', padding: 12, background: '#fff8e1', borderRadius: 6 }}>
          <p><b>Copy your new token now. It will not be shown again.</b></p>
          <p style={code}>{fresh}</p>
          <button style={btn} onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</button>{' '}
          <button style={ghost} onClick={() => setFresh('')}>I have saved it</button>
        </div>
      )}

      <h3>Your tokens</h3>
      {tokens.length === 0 && <p style={muted}>No tokens yet.</p>}
      {tokens.map(t => (
        <div key={t.id} style={row}>
          <div>
            <b>{t.name}</b> <span style={code}>{t.prefix}…</span>
            <div style={muted}>Created {new Date(t.created_at).toLocaleDateString()} · {t.last_used_at ? `last used ${new Date(t.last_used_at).toLocaleDateString()}` : 'never used'}</div>
          </div>
          <button style={ghost} onClick={() => void revoke(t.id)}>Revoke</button>
        </div>
      ))}
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
    </div>
  )
}
