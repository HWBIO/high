import { clearPageCache } from './utils/pageCache'

const TOKEN_KEY = 'hh_token'
const USER_KEY = 'hh_user'

export interface AuthUser {
  id: number
  username: string
  display_name: string
  role: string
  status: string
  created_at?: string
}

export const auth = {
  token: () => localStorage.getItem(TOKEN_KEY) || '',
  user: (): AuthUser | null => {
    try {
      const raw = localStorage.getItem(USER_KEY)
      return raw ? (JSON.parse(raw) as AuthUser) : null
    } catch {
      return null
    }
  },
  save(token: string, user: AuthUser) {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
    window.dispatchEvent(new Event('auth-changed'))
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    clearPageCache()
    window.dispatchEvent(new Event('auth-changed'))
  },
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const token = auth.token()
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const resp = await fetch(url, { ...init, headers })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      const rawDetail = body.detail || body
      detail = typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return resp.json()
}

async function requestBlob(url: string, init?: RequestInit): Promise<{ blob: Blob; filename: string }> {
  const token = auth.token()
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const resp = await fetch(url, { ...init, headers })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      const rawDetail = body.detail || body
      detail = typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  const disposition = resp.headers.get('Content-Disposition') || ''
  const filenameMatch = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i)
  const rawFilename = filenameMatch ? filenameMatch[1] || filenameMatch[2] || '' : ''
  let filename = rawFilename
  try {
    filename = rawFilename ? decodeURIComponent(rawFilename) : ''
  } catch {
    filename = rawFilename
  }
  return { blob: await resp.blob(), filename }
}

export const api = {
  get: <T>(url: string) => request<T>(url),
  post: <T>(url: string, body?: unknown) =>
    request<T>(url, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(url: string, body: unknown) =>
    request<T>(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  delete: <T>(url: string) => request<T>(url, { method: 'DELETE' }),
  upload: <T>(url: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(url, { method: 'POST', body: form })
  },
  postForm: <T>(url: string, form: FormData) => request<T>(url, { method: 'POST', body: form }),
  blob: (url: string) => requestBlob(url),
}
