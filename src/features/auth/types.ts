export type AuthUser = {
  id: string
  email: string | null
  displayName: string | null
  avatarUrl: string | null
}

export type AuthState = {
  user: AuthUser | null
  loading: boolean
  error: string | null
}
