import { auth, AuthUser } from '../api'

export function hasRole(user: AuthUser | null, roles: string[]) {
  if (!user) return false
  return user.role === 'super_admin' || roles.includes(user.role)
}

export function canManageBusiness(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin'])
}

export function canManageCustomers(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}

export function canManagePrompts(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}

export function canManagePositions(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}

export function canStartMatch(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}

export function canViewReports(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}

export function canUseAI(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}

export function canGenerateClientReports(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}

export function canManageProcess(user: AuthUser | null = auth.user()) {
  return hasRole(user, ['business_admin', 'consultant'])
}
