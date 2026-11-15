# 运行基座与 tebb 复用清单

MyAgentDesk 使用 React + TypeScript、FastAPI 和独立 PostgreSQL。FastAPI 可同源提供前端构建产物。配置只读取 `MAD_` 环境变量，迁移显式执行，健康接口分开报告进程可用和数据库就绪。

工作区与项目分别使用 UUID，项目持有工作区归属。`server/contracts.py` 定义已核验身份、项目范围和最小运行事件（版本、运行编号、序号、类型、绝对时间）。身份核验器和事件源可替换。Pi SDK 执行 Worker 由 #5 接入。

| 能力 | 固定来源 | 依赖、归属与接入 | 许可与验证 |
| --- | --- | --- | --- |
| 邮箱登录 | [account_auth.py](https://github.com/lkun-lea/tebb/blob/3769741dc807c55c52c9ea9a768633ba21d4fff2/desk_companion/accounts/account_auth.py) | 参考 Supabase OTP、身份核验和固定错误文案。#3 的 Web 会话、账号与成员由 MyAgentDesk 管理，tebb 的 Windows 会话存储由 tebb 管理。 | MIT，按邮箱错码、过期、刷新、退出与隔离验收。 |
| 目录授权与沙箱 | [项目模块](https://github.com/lkun-lea/tebb/tree/3769741dc807c55c52c9ea9a768633ba21d4fff2/desk_companion/projects) | 本机目录、终端、容器及设备授权由 tebb 管理。#14 通过派发和回执连接，验证目录越界与撤权。 | MIT，按固定样本复跑设备权限与隔离。 |
| 运行事件与证据 | [project_receipts.py](https://github.com/lkun-lea/tebb/blob/3769741dc807c55c52c9ea9a768633ba21d4fff2/desk_companion/projects/project_receipts.py) | tebb 保留原始本机记录。MyAgentDesk 管理项目内引用、同版证据和验收。 | MIT，验证回执重放、候选更换与旧证据。 |
| 日程时间解析 | [calendar_time.py](https://github.com/lkun-lea/tebb/blob/3769741dc807c55c52c9ea9a768633ba21d4fff2/desk_companion/planning/calendar_time.py) | 原样引入纯 Python 模块供运行事件校验。无运行时依赖，MyAgentDesk 保留副本。 | [MIT 与署名](../../licenses/tebb/LICENSE)。上游 SchedulingTests 复跑时间样例，新仓复跑时间和事件契约。 |
| 官方 Skill 导出 | [skills-source.json](https://github.com/lkun-lea/tebb/blob/3769741dc807c55c52c9ea9a768633ba21d4fff2/skills-source.json) · [固定 Skill 版本](https://github.com/lkun-lea/tebb-skills/tree/8f7d34a14f621379be0817891cbe78fb542e144f) | #12 采用固定版本与内容摘要导出，tebb-skills 维护上游。 | 仓库 MIT，逐项保留上游附带许可，验证锁定和引用完整性。 |

按根目录 README 启动。在专用测试数据库上运行 `uv run --locked pytest tests/test_foundation.py -q`。测试创建隔离 schema，并检查迁移、配置错误和替身契约。
