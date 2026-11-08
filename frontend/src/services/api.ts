import { supabase } from './supabase'

const configured = import.meta.env.VITE_API_URL as string | undefined
if (import.meta.env.PROD && !configured) {
  throw new Error('VITE_API_URL is not set. Set it to your API address before building the site.')
}
const API = (configured || 'http://127.0.0.1:8000').replace(/\/$/, '')
export const API_BASE = API

export type Repo = { id: string; owner: string | null; name: string; description: string; is_private: boolean; created_at: string }
export type Me = { id: string; email: string; username: string | null }
export type TokenInfo = { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null }

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
  me: () => request<Me>('/api/me'),
  setUsername: (username: string) =>
    request<{ username: string }>('/api/me/username', { method: 'PUT', body: JSON.stringify({ username }) }),
  listRepos: () => request<{ repos: Repo[] }>('/api/repos'),
  createRepo: (name: string, isPrivate: boolean) =>
    request<Repo>('/api/repos', { method: 'POST', body: JSON.stringify({ name, is_private: isPrivate }) }),
  updateRepo: (id: string, patch: { is_private?: boolean; description?: string }) =>
    request<Repo>(`/api/repos/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  listTokens: () => request<{ tokens: TokenInfo[] }>('/api/tokens'),
  createToken: (name: string) =>
    request<{ id: string; name: string; token: string }>('/api/tokens', { method: 'POST', body: JSON.stringify({ name }) }),
  revokeToken: (id: string) => request<{ message: string }>(`/api/tokens/${id}`, { method: 'DELETE' }),
}