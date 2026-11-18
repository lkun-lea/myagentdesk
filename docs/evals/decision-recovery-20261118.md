# #7 逐项验收记录

实现提交 `32f7e1e408b9f98aba353aa7f8e7562a86efacc1`，PR #22，基于 PR #21。运行时仍为官方 Pi SDK。身份和模型端点使用隔离替身，API、PostgreSQL、Node Worker、独立 Python Worker 和临时目录文件写入均走实际实现。

本机全量 **29 项通过（495.17s）**。后续资源绑定及模型输入的细化另跑 2 项定向测试通过，版本显示另跑 1 项通过。TypeScript 与 Vite 生产构建通过。[实现提交的 CI](https://github.com/lkun-lea/myagentdesk/actions/runs/44322129106) 重新运行全部测试及生产构建，全部通过。原始测试用例结果及浏览器运行记录见[脱敏 JSON](decision-recovery-20261118.json)。

## 按 issue 验收标准核对

| #7 条目 | 测试与实际断言 | 结果 |
| --- | --- | --- |
| 本人决定、原运行及证据 | `test_directory_revocation_keeps_decision_then_restores_once`；浏览器从决定深链接确认，原始资料固定版本可读，答复、回执和队列同事务保存；运行编号保持一致。 | 通过 |
| 他人、旧版本和重复答复 | 他人返回 404；相同答复返回原回执；冲突答复拒绝。`test_replaced_request_and_changed_version_reject_old_confirmation` 验证问题替换、旧入口及需求改版；界面标明过期并禁止继续。 | 通过 |
| 撤销目录授权后确认、恢复只执行一次 | 决定仍为 resolved，运行 waiting_authorization，模型请求不推进；恢复授权后原运行完成。产物操作键和摘要固定，重复确认不新增作用；已绑定目录不能借重试修改。 | 通过 |
| 执行中撤权 | `test_inflight_revocation_stops_before_next_protected_write` 在真实模型 HTTP 等待期间撤权，后续保护操作前停止；授权恢复后继续，只有预期产物。 | 通过 |
| 移除原本人、合格替补及无替补 | `test_reassignment_requires_current_grants_and_invalidates_original_entry`：无目录权限的同岗成员不可接手，候选列表为空，升级原因保留。补齐权限并移除原成员后改派；旧通知拒绝，替补读取相同证据和原检查点。 | 通过 |
| 已确认后改派 | `test_resolved_blocked_run_can_be_taken_over_without_redeciding`：原答复保留，替补核对后续跑，不要求重选答案。 | 通过 |
| 旧 Agent 迟到回调 | `test_transfer_back_still_rejects_original_execution_revision`：负责人 A→B→A 后，即使账号再次相同，原执行修订仍被拒；旧通知的修订也被拒。 | 通过 |
| 确认后重启 Worker | `test_restart_after_committed_confirmation_and_concurrent_workers`：确认事务已提交但快捷处理未开始，启动独立 `server.resume_worker --once` 恢复原运行；三个消费者再次领取不增加模型调用或产物。 | 通过 |
| 应用启动自动恢复 | `test_application_startup_recovers_queue_without_manual_click`：新建应用并启用实际生命周期 Worker，自动扫描已提交队列，原运行完成。 | 通过 |
| 外部文件已写、回执未提交时硬中断 | `test_worker_crash_after_file_publish_before_receipt_does_not_write_twice`：子进程在完整文件发布后以退出码 37 立即结束，数据库产物回执尚不存在，队列保持 running。重新启动后复用相同文件，mtime 不变、摘要匹配，回执补齐，文件数量不增加。 | 通过 |

所有测试位于 `tests/test_decision_recovery.py`。原运行成功检查点在等待和改派期间保持；恢复从既有工具回执继续。已完成阶段不会重新执行，同一业务键的产物不会重复发布；Agent 仍可为当前任务再次按需读取资料。

## 浏览器结果

三个隔离成员分别从产品入口操作，第四次以被移除的原账号复查旧链接：

1. 原成员从链接打开问题，确认后显示已确认、等待目录授权，原检查点为 4；固定版本原文和原答复可查。[桌面](../design/project-pages/decision-waiting-authorization.png) · [390px 深色](../design/project-pages/decision-waiting-mobile.png)。
2. 管理员查看无合格替补、登记升级原因，在成员页给予替补目录权限，移除原成员并改派。[无合格替补](../design/project-pages/decision-no-replacement.png)。
3. 替补打开相同决定，保留原答复，通过 Enter 激活续跑，同一 run_id 的检查点 **4→6**，队列 completed，只有 draft/approved 两个预期产物。[接手后完成](../design/project-pages/decision-takeover-completed.png)。
4. 原成员再次登录并打开旧链接，返回 decision_not_found，无法提交或读取该项目决定。Tab 焦点、Enter 激活、深浅色及窄窗口无横向溢出通过。活动事件按批合并刷新，避免为每条历史事件分别重取成员列表。

## 范围与限制

本次没有发真实邮箱邮件或调用真实模型服务；验证目标是当前授权、决定、恢复和副作用契约。Pi SDK/Worker 执行和文件作用是真实路径，模型端点为确定性 HTTP 替身。

目录为应用管理的项目产物目录，工具只能发布不可变文本。它不是任意代码目录授权、命令沙箱或外部编码器；这些继续由 #14 接入。文件幂等契约不能推导为任意第三方服务严格一次写入，后续适配器必须提供稳定业务键、结果核对及同等故障验收。

持久恢复覆盖已登记的续跑队列。需求群的首次自动启动及发布调度不在本次恢复保证内。尚未完成正式云部署、宿主机崩溃/磁盘故障或长时间压力验收。
