import { useEffect, useState } from 'react'
import { ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api, auth, AuthUser } from './api'
import MatchRunPage from './pages/MatchRunPage'
import ReportPage from './pages/ReportPage'
import PositionsPage from './pages/PositionsPage'
import AccountPage from './pages/AccountPage'
import ProcessPage from './pages/ProcessPage'
import MatchHistoryPage from './pages/MatchHistoryPage'
import ClientRecommendationPage from './pages/ClientRecommendationPage'
import PromptsPage from './pages/PromptsPage'
import CustomersPage from './pages/CustomersPage'
import ProjectsPage from './pages/ProjectsPage'
import MatchChatPage from './pages/MatchChatPage'
import MembersPage from './pages/MembersPage'
import { canManageBusiness, canUseAI, canViewReports, hasRole } from './utils/authz'

const NAV = [
  { to: '/match', label: '发起匹配', roles: ['super_admin', 'business_admin', 'consultant'] },
  { to: '/prompts', label: '提示词管理', roles: ['super_admin', 'business_admin', 'consultant'] },
  { to: '/customers', label: '客户管理', roles: ['super_admin', 'business_admin', 'consultant'] },
  { to: '/projects', label: '项目管理', roles: ['super_admin', 'business_admin', 'consultant'] },
  { to: '/positions', label: '岗位管理', roles: ['super_admin', 'business_admin', 'consultant'] },
  { to: '/process', label: '进程管理', roles: ['super_admin', 'business_admin', 'consultant'] },
  { to: '/members', label: '账号管理', roles: ['super_admin', 'business_admin'] },
]

function canAccess(user: AuthUser | null, roles: string[]) {
  return hasRole(user, roles)
}

function requireLogin(user: AuthUser | null, element: ReactNode) {
  if (!user) return <Navigate to="/account" replace />
  return hasRole(user, ['super_admin', 'business_admin', 'consultant']) ? element : <Navigate to="/account" replace />
}

function requireAccess(user: AuthUser | null, allowed: boolean, element: ReactNode, fallback = '/account') {
  if (!user) return <Navigate to="/account" replace />
  return allowed ? element : <Navigate to={fallback} replace />
}

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(auth.user())
  const visibleNav = NAV.filter((item) => canAccess(user, item.roles))
  const defaultPath = user ? (visibleNav[0]?.to || '/account') : '/account'

  useEffect(() => {
    const onChange = () => setUser(auth.user())
    window.addEventListener('auth-changed', onChange)
    if (auth.token()) {
      api
        .get<AuthUser>('/api/auth/me')
        .then((freshUser) => auth.save(auth.token(), freshUser))
        .catch(() => {
          auth.clear()
          setUser(null)
        })
    }
    return () => window.removeEventListener('auth-changed', onChange)
  }, [])

  return (
    <div className="layout">
      <aside className="sidebar">
        <h1 className="logo">猎头匹配 Agent</h1>
        <nav>
          {visibleNav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-account">
          <NavLink
            to="/account"
            className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
          >
            {user ? `👤 ${user.display_name}` : '登录 / 注册'}
          </NavLink>
        </div>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to={defaultPath} replace />} />
          <Route path="/match" element={requireLogin(user, <MatchRunPage />)} />
          <Route path="/match/runs" element={requireAccess(user, canUseAI(user), <MatchChatPage />, defaultPath)} />
          <Route path="/match/history" element={requireAccess(user, canViewReports(user), <MatchHistoryPage />, defaultPath)} />
          <Route path="/prompts" element={requireLogin(user, <PromptsPage />)} />
          <Route path="/customers" element={requireLogin(user, <CustomersPage />)} />
          <Route path="/projects" element={requireLogin(user, <ProjectsPage />)} />
          <Route path="/process" element={requireLogin(user, <ProcessPage />)} />
          <Route path="/results/:resultId" element={requireAccess(user, canViewReports(user), <ReportPage />, defaultPath)} />
          <Route path="/client-reports/:resultId" element={requireAccess(user, canViewReports(user), <ClientRecommendationPage />, defaultPath)} />
          <Route path="/candidates" element={<Navigate to="/match" replace />} />
          <Route path="/positions" element={requireLogin(user, <PositionsPage />)} />
          <Route path="/positions/:positionId" element={requireLogin(user, <PositionsPage />)} />
          <Route path="/members" element={requireAccess(user, canManageBusiness(user), <MembersPage />, defaultPath)} />
          <Route path="/account" element={<AccountPage />} />
        </Routes>
      </main>
    </div>
  )
}
