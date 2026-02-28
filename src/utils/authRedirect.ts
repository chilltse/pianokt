/**
 * 将“无 userId 的 account 相关路径”解析为带当前用户 id 的路径，用于登录后跳转。
 */
export function resolveAuthRedirect(path: string, userId: string): string {
  const [pathname, search] = path.includes('?')
    ? [path.slice(0, path.indexOf('?')), path.slice(path.indexOf('?'))]
    : [path, '']
  const base = pathname.replace(/\/$/, '')
  if (base === '/account') return `/account/${userId}${search}`
  if (base === '/recordings') return `/recordings/${userId}${search}`
  if (base === '/challenge-songs') return `/challenge-songs/${userId}${search}`
  return path
}
