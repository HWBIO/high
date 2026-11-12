import { useEffect, useState } from 'react'
import { api, auth, AuthUser } from '../api'
import { canManageBusiness } from '../utils/authz'

interface TempResumeStats {
  retention_days: number
  temp_candidates: number
  with_resume_file: number
  with_resume_text: number
  eligible_for_cleanup: number
}

interface TempResumeCleanupResult {
  retention_days: number
  temp_candidates_cleaned: number
  resume_files_deleted: number
  stats: TempResumeStats
}

export default function AccountPage() {
  const [user, setUser] = useState<AuthUser | null>(auth.user())
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [consultantName, setConsultantName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [tempStats, setTempStats] = useState<TempResumeStats | null>(null)
  const [maintenanceMessage, setMaintenanceMessage] = useState('')
  const [maintenanceBusy, setMaintenanceBusy] = useState(false)

  useEffect(() => {
    // 校验浏览器会话 token 是否仍有效
    if (auth.token()) {
      api
        .get<AuthUser>('/api/auth/me')
        .then(setUser)
        .catch(() => {
          auth.clear()
          setUser(null)
        })
    }
  }, [])

  useEffect(() => {
    if (!user || !canManageBusiness(user)) return
    api
      .get<TempResumeStats>('/api/maintenance/temp-resumes')
      .then(setTempStats)
      .catch(() => setTempStats(null))
  }, [user])

  async function submit() {
    setError('')
    setBusy(true)
    try {
      const path = mode === 'login' ? '/api/auth/login' : '/api/auth/register'
      const body: Record<string, string> = { username: consultantName, password }
      const result = await api.post<{ token: string; user: AuthUser }>(path, body)
      auth.save(result.token, result.user)
      setUser(result.user)
      if (mode === 'register') {
        window.dispatchEvent(new Event('hh-match-users-updated'))
      }
      setConsultantName('')
      setPassword('')
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setBusy(false)
    }
  }

  async function logout() {
    try {
      await api.post('/api/auth/logout')
    } catch {
      /* 浏览器会话清除即可 */
    }
    auth.clear()
    setUser(null)
  }

  async function cleanupTempResumes() {
    const confirmed = window.confirm('确认清理过期临时简历原件和解析文本吗？历史匹配报告会保留。')
    if (!confirmed) return
    setMaintenanceBusy(true)
    setMaintenanceMessage('')
    try {
      const result = await api.post<TempResumeCleanupResult>('/api/maintenance/temp-resumes/cleanup')
      setTempStats(result.stats)
      setMaintenanceMessage(`已清理 ${result.temp_candidates_cleaned} 个临时候选人，删除 ${result.resume_files_deleted} 个简历原件。`)
    } catch (e) {
      setMaintenanceMessage(String((e as Error).message))
    } finally {
      setMaintenanceBusy(false)
    }
  }

  if (user) {
    return (
      <div>
        <h2>账号</h2>
        <div className="card">
          <h3>当前登录</h3>
          <table style={{ maxWidth: 480 }}>
            <tbody>
              <tr><th style={{ width: 120 }}>姓名/顾问名</th><td>{user.display_name}</td></tr>
              {user.created_at && <tr><th>注册时间</th><td>{user.created_at}</td></tr>}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="secondary" onClick={logout}>退出登录</button>
          </div>
        </div>
        <div className="card">
          <h3>业务归属与分成</h3>
          <p className="muted">
            候选人看板中的「联系人」记录每单业务的负责人。候选人成功入职后，将按联系人归属进行收入分成统计（分成规则与报表功能规划中）。
          </p>
        </div>
        {canManageBusiness(user) && (
          <div className="card">
            <h3>临时简历数据清理</h3>
            {tempStats ? (
              <>
                <table style={{ maxWidth: 560 }}>
                  <tbody>
                    <tr><th style={{ width: 180 }}>保留天数</th><td>{tempStats.retention_days} 天</td></tr>
                    <tr><th>临时候选人</th><td>{tempStats.temp_candidates}</td></tr>
                    <tr><th>仍保留原件</th><td>{tempStats.with_resume_file}</td></tr>
                    <tr><th>仍保留解析文本</th><td>{tempStats.with_resume_text}</td></tr>
                    <tr><th>可清理</th><td>{tempStats.eligible_for_cleanup}</td></tr>
                  </tbody>
                </table>
                <div className="row" style={{ marginTop: 16 }}>
                  <button onClick={cleanupTempResumes} disabled={maintenanceBusy || tempStats.eligible_for_cleanup === 0}>
                    {maintenanceBusy ? '清理中…' : '清理过期临时简历'}
                  </button>
                </div>
              </>
            ) : (
              <p className="muted">暂无临时简历统计。</p>
            )}
            {maintenanceMessage && <p className="muted">{maintenanceMessage}</p>}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="auth-shell">
      <section className="auth-panel">
        <h2>登录 / 注册</h2>
        <div className="auth-tabs" role="tablist" aria-label="账号操作">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            className={mode === 'register' ? 'auth-tab active' : 'auth-tab'}
            onClick={() => setMode('register')}
          >
            注册
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            className={mode === 'login' ? 'auth-tab active' : 'auth-tab'}
            onClick={() => setMode('login')}
          >
            登录
          </button>
        </div>
        <div className={mode === 'register' ? 'auth-form-card register-mode' : 'auth-form-card login-mode'}>
          <input
            type="text"
            placeholder="姓名/顾问名"
            value={consultantName}
            onChange={(e) => setConsultantName(e.target.value)}
          />
          <input
            type="password"
            placeholder="密码（至少 6 位）"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
          />
          <div className="auth-actions">
            <button className="auth-submit-button" onClick={submit} disabled={busy || !consultantName.trim() || !password}>
              {busy ? '提交中…' : mode === 'login' ? '登录' : '注册并登录'}
            </button>
          </div>
          {error && <p className="error">{error}</p>}
        </div>
      </section>
    </div>
  )
}
