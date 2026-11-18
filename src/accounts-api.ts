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
  dispatch_not_found: '找不到该派发，请核对领取令牌。',
  dispatch_claimed: '该派发已经由另一台设备领取。',
  external_execution_required: '该需求的研发和独立验收从 tebb 领取。',
  delivery_plan_changed: '试点需要当前已承诺、仅包含本需求的计划修订。请核对计划。',
  delivery_evidence_required: '当前候选尚缺同一版本的独立验收、必过回归或质量放行。',
  delivery_ci_mismatch: 'CI 回执须是同仓库、同提交且成功的构建运行。',
  artifact_mismatch: '产物与回执登记的摘要不一致。',
  artifact_required: '候选产物尚未入档。',
  release_channel_unavailable: '本地试点发布渠道不可用，请核对服务配置。',
  auth_result_unknown: '认证回执未确认。请核对原请求，再决定是否重新申请。',
  invalid_or_expired_code: '验证码不正确或已过期。请检查邮箱中的最新验证码。',
  rate_limited: '请求过于频繁，请稍后再试。',
  login_required: '登录已过期，请重新登录。',
  auth_unavailable: '登录服务暂不可用。',
  network: '连接已断开。恢复后可以核对原操作。',
  database_unavailable: '数据库暂不可用，请稍后重试。',
  project_not_found: '当前账号无法访问该项目。',
  github_not_connected: '项目尚未连接 GitHub 仓库。',
  github_invalid_repository: '请输入 owner/repository 格式的公开仓库名称。',
  github_invalid_number: '请输入有效的 GitHub Issue 编号。',
  github_issue_not_found: '此编号对应的内容不是 Issue，或已不可读取。',
  github_source_unavailable: 'GitHub 来源不存在、已变更或当前不可读取。',
  github_unavailable: '暂时无法读取 GitHub，请稍后刷新。',
  github_rate_limited: '已达到 GitHub 读取限额，请稍后刷新。',
  workspace_not_found: '当前账号无法访问该工作区。',
  tool_unavailable: '工具暂不可用，请刷新工具目录。',
  task_not_found: '当前账号无法访问该事项。',
  invalid_calendar_range: '日程时间无效，请检查开始、结束和时间范围。',
  version_conflict: '版本已更新，请重新加载后核对。',
  specification_source_unavailable: '该提交中找不到指定的 OpenSpec 变更目录或提案。',
  spec_confirmation_required: '请从规格页确认新的验收条件与固定提交。',
  version_owner_required: '计划负责人须为当前项目所有者或管理员。',
  version_not_found: '当前项目找不到该计划或修订。',
  requirement_already_scheduled: '需求已在另一个版本，请先从原范围移出。',
  context_changed: '需求或已读取资料已更新，请核对后确认继续。',
  model_unavailable: '模型服务尚未配置。',
  model_failed: '模型执行失败，可以从检查点重试。',
  model_timeout: '模型本轮超时，已保留成功检查点，可核对后重试。',
  paused: '运行已暂停，可核对后继续。',
  run_cancelled: '运行已取消。',
  artifact_unavailable: '产物文件缺失或摘要不符，请核对存储。',
  model_configuration_changed: '运行绑定的模型配置已变化，请恢复该配置或创建新运行。',
  requirement_not_found: '当前项目中找不到该需求。',
  run_not_found: '当前账号无法访问该运行。',
  run_busy: '运行仍在执行，请先取消并等待回执。',
  run_completed: '已交接的验收记录保留在原规格版本，请创建新运行。',
  independent_review_required: '独立验收需要由其他成员负责。',
  group_not_found: '当前账号无法访问该需求群。',
  decision_not_found: '当前账号无法处理这条决定。',
  decision_pending: '原运行正在等待本人决策。',
  authorization_required: '原运行等待所需目录授权，决定已保存。',
  replacement_ineligible: '替补需要满足同岗位、目录授权和独立验收约束。',
  resource_store_unavailable: '服务尚未配置项目产物目录。',
  resource_not_found: '当前项目无法访问该产物目录。',
  execution_replaced: '运行已交接，旧执行入口失效。',
  stage_changed: '当前阶段或负责人已变化，请刷新群记录。',
  role_required: '请选择具备相同岗位和项目权限的成员。',
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
