import { supabase } from './supabase'

const API = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export type Repo = { id: string; name: string; created_at: string }

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    
    throw new Error(body.detail || body.message || `Request failed (${response.status})`)
  }
  return body as T
}

export const api = {
  listRepos: () => request<{ repos: Repo[] }>('/api/repos'),
  createRepo: (name: string) =>
    request<Repo>('/api/repos', { method: 'POST', body: JSON.stringify({ name }) }),
}