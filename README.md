# MyAgentDesk

MyAgentDesk 是个人 Agent 工作台。每个账号绑定一个 Agent。成员决定需求范围、实施取舍与交付是否放行，各自的 Agent 按岗位执行、验证和交接。

## 首版计划

- 工作台提供项目入口、常用工具和可扩展的工具目录。
- 项目默认按版本查看排期、范围和阻塞，发布记录可追溯到实际渠道回执。
- 需求群把讨论、阶段、规格、候选与验收证据关联到同一条需求。研发和独立验收由不同成员负责。
- 「我的 Agent」支持选择模型和 Skill。项目管理员分派岗位，并授予仓库、目录、环境和动作权限。Agent 共用工具目录。
- 人在「待我决策」确认后，Agent 按原检查点继续。项目统筹通过里程碑和依赖处理阻塞。
- 项目内提供 GitHub Issues 只读看板与 OpenSpec 规格页。个人可以导入 Skill，也可以请自己的 Agent 起草并隔离试跑。

项目已提供可操作的首版 Demo。真实试点计划使用一条需求和 tebb 编码执行器，从版本排期走到独立验收和实际发布。开发依赖、顺序和验收见 [首版路线图](https://github.com/lkun-lea/myagentdesk/issues/1) 与 [版本计划和试点](docs/design/RELEASE_PLAN.md)。

## 首版 Demo

需要 Node.js 20+。在仓库根目录运行：

```sh
npm ci
npm run dev
```

打开终端显示的地址。项目默认使用全宽时间线，按首版路线图展示交付顺序。点里程碑、Issue 或「等待前置」打开右栏，查看开发前置、产品联调、验收标准和 GitHub 原文。右栏关闭时恢复全宽。「Issues」显示 MyAgentDesk 的 GitHub 只读快照，时间轴日期用于排期演示。

右上角「试点演示」打开同一流程的模拟操作。沿着版本排期、规格确认、tebb 派发、独立验收、质量放行和发布回执推进。模拟断网或请求产品决策后可恢复原运行，独立验收后可模拟集成包变更。试点来源是 [tebb #313](https://github.com/lkun-lea/tebb/issues/313)，进度保存在当前浏览器。生产构建使用 `npm run build`。

[项目五页设计与可点预览](docs/design/project-pages/README.md) · [项目开发规则](AGENTS.md)

## 与 tebb 的关系

设计来源是 [tebb #314](https://github.com/lkun-lea/tebb/issues/314)。可从 tebb 复用的候选包括目录授权与沙箱、运行回执与证据、账号登录交互和日程交互。官方 Skill 使用 [tebb-skills](https://github.com/lkun-lea/tebb-skills) 固定版本。是否直接复用代码由 [基座与复用边界](https://github.com/lkun-lea/myagentdesk/issues/2) 逐项验证并记录来源和许可。

MyAgentDesk 独立管理应用、账号空间、项目状态和数据。计划使用官方 Pi SDK 执行模型及工具循环。

两端通过 GitHub Issue、PR 和构建链接定位原记录。首版试点把研发任务派给 tebb Agent，把完成通知、产物和证据带回原需求。试点验收后通过同一协议接入本机 Codex。详见 [联动架构与验收](docs/design/TEBB_INTEGRATION.md)。

[工作台与项目页设计](https://github.com/lkun-lea/tebb/blob/3769741dc807c55c52c9ea9a768633ba21d4fff2/docs/design/workbench/README.md) · [账号与个人 Agent 设计](https://github.com/lkun-lea/tebb/blob/3769741dc807c55c52c9ea9a768633ba21d4fff2/docs/design/workbench/ACCOUNT_DESIGN.md) · [MIT 许可证](LICENSE)
