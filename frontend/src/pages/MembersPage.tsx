import { useEffect, useState } from 'react'
import { api, auth, AuthUser } from '../api'

interface Member extends AuthUser {
  created_at: string
}

const ROLE_LABELS: Record<string, string> = {
  super_admin: '技术管理员',
  business_admin: '管理员',
  consultant: '顾问',
}

export default function MembersPage() {
  const currentUser = auth.user()
  const [members, setMembers] = useState<Member[]>([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [updatingId, setUpdatingId] = useState<number | null>(null)

  async function loadMembers() {
    setLoading(true)
    setError('')
    try {
      const data = await api.get<{ items: Member[] }>('/api/auth/admin/users')
      setMembers(data.items)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadMembers()
  }, [])

  async function updateMember(member: Member, patch: Partial<Pick<Member, 'role' | 'status'>>) {
    setUpdatingId(member.id)
    setMessage('')
    setError('')
    try {
      const updated = await api.put<Member>(`/api/auth/admin/users/${member.id}`, patch)
      setMembers((prev) => prev.map((item) => (item.id === updated.id ? updated : item)))
      setMessage(`已更新 ${updated.display_name || updated.username}`)
      window.dispatchEvent(new Event('hh-match-users-updated'))
      if (currentUser?.id === updated.id) {
        const fresh = await api.get<AuthUser>('/api/auth/me')
        auth.save(auth.token(), fresh)
      }
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      setUpdatingId(null)
    }
  }

  return (
    <div>
      <div className="folder-page-title">
        <h2>账号管理</h2>
      </div>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>成员列表</h3>
          <button className="secondary" onClick={loadMembers} disabled={loading}>
            {loading ? '刷新中...' : '刷新'}
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>姓名/顾问名</th>
              <th>角色</th>
              <th>状态</th>
              <th>注册时间</th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => {
              const isSuperAdmin = member.role === 'super_admin'
              const isBusy = updatingId === member.id
              const roleDisabled = isBusy || (isSuperAdmin && currentUser?.role !== 'super_admin')
              const statusDisabled = isBusy || currentUser?.id === member.id
              return (
                <tr key={member.id}>
                  <td>
                    <strong>{member.display_name || member.username}</strong>
                    <br />
                    <span className="muted">{member.username}</span>
                  </td>
                  <td>
                    {roleDisabled ? (
                      ROLE_LABELS[member.role] || member.role
                    ) : (
                      <select
                        value={member.role}
                        onChange={(e) => updateMember(member, { role: e.target.value })}
                        disabled={isBusy}
                      >
                        {currentUser?.role === 'super_admin' && <option value="super_admin">技术管理员</option>}
                        <option value="business_admin">管理员</option>
                        <option value="consultant">顾问</option>
                      </select>
                    )}
                  </td>
                  <td>
                    <select
                      value={member.status}
                      onChange={(e) => updateMember(member, { status: e.target.value })}
                      disabled={statusDisabled}
                    >
                      <option value="active">启用</option>
                      <option value="disabled">停用</option>
                    </select>
                  </td>
                  <td>{member.created_at || '-'}</td>
                </tr>
              )
            })}
            {!members.length && (
              <tr>
                <td colSpan={4}>{loading ? '加载中...' : '暂无成员'}</td>
              </tr>
            )}
          </tbody>
        </table>
        {message && <p className="muted">{message}</p>}
        {error && <p className="error">{error}</p>}
      </div>
      <div className="card">
        <h3>规则</h3>
        <p className="muted">
          新注册用户默认是顾问。管理员可维护成员角色和账号状态；技术管理员作为兜底账号，只在必要时使用。
        </p>
      </div>
    </div>
  )
}
