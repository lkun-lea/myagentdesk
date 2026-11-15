export class ApiError extends Error {
  constructor(readonly code: string, readonly status: number) {
    super(code)
  }
}

export async function api<T>(path: string, method = 'GET', body?: object): Promise<T> {
  let response: Response
  try {
    response = await fetch('/api' + path, {
      method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'X-MAD-Request': '1', ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('network', 0)
  }
  const data = await response.json().catch(() => null)
  if (!response.ok || data === null) throw new ApiError(data?.error ?? 'unavailable', response.status)
  return data
}

const messages: Record<string, string> = {
  auth_result_unknown: '认证回执未确认。请核对原请求，再决定是否重新申请。',
  invalid_or_expired_code: '验证码不正确或已过期。请检查邮箱中的最新验证码。',
  rate_limited: '请求过于频繁，请稍后再试。',
  login_required: '登录已过期，请重新登录。',
  auth_unavailable: '登录服务暂不可用。',
  network: '连接已断开。恢复后可以核对原操作。',
  database_unavailable: '数据库暂不可用，请稍后重试。',
  project_not_found: '当前账号无法访问该项目。',
  workspace_not_found: '当前账号无法访问该工作区。',
  invite_not_found: '当前账号无法接受此邀请。',
  invite_expired_or_revoked: '邀请已过期或被撤销，请联系项目管理员。',
  permission_denied: '当前账号没有此操作的权限。',
  already_member: '该邮箱已是项目成员。',
  invalid_request: '请检查输入。',
  challenge_missing: '请先申请验证码。',
  owner_protected: '项目所有者的角色不能在这里修改。',
  sign_out_first: '请退出当前账号后再登录。',
  operation_conflict: '操作编号冲突，请核对后重试。',
}

export function messageFor(code: string) {
  return messages[code] ?? '服务暂不可用，请稍后重试。'
}

export const errorCode = (error: unknown) => error instanceof ApiError ? error.code : 'unavailable'
export const isUnauthorized = (error: unknown) => error instanceof ApiError && error.status === 401
