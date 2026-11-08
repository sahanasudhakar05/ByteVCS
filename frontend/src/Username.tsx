import { useState } from 'react'
import { api } from './services/api'
import { btn, input, muted, page } from './ui'

export default function Username({ onDone }: { onDone: (username: string) => void }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const name = value.trim().toLowerCase()
  const valid = /^[a-z0-9](?:[a-z0-9-]{1,37}[a-z0-9])$/.test(name)

  const submit = async () => {
    setError(''); setBusy(true)
    try { onDone((await api.setUsername(name)).username) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save username.') }
    finally { setBusy(false) }
  }

  return (
    <div style={page}>
      <h2>Choose your username</h2>
      <p style={muted}>It appears in your repository links (for example <b>{name || 'yourname'}/my-project</b>) and cannot be changed later. Your email is never shown publicly.</p>
      <input style={input} placeholder="username" value={value} onChange={e => setValue(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && valid) void submit() }} />
      <button style={btn} disabled={!valid || busy} onClick={() => void submit()}>{busy ? 'Saving…' : 'Continue'}</button>
      <p style={muted}>3-39 characters: lowercase letters, digits and hyphens (not at the ends).</p>
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
    </div>
  )
}
