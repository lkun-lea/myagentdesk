# 版本计划与首版交付试点

项目页默认打开「版本时间线」。成员按发布节奏查看项目进展，点版本看范围与阻塞，点需求进入原阶段和证据。「事件记录」提供交接、返工、决定和构建的历史。[项目页面与图稿](project-pages/README.md#项目时间线)

实施由 [#16 版本计划与项目时间线](https://github.com/lkun-lea/myagentdesk/issues/16) 跟踪。

## 状态与职责

| 记录 | 维护者与更新规则 |
| --- | --- |
| 版本计划 | MyAgentDesk 保存版本标识、项目负责人、目标日期、里程碑、纳入的需求和发版必过项。计划有草拟、已承诺、取消或结束状态。已承诺计划的范围和日期每次修改形成新修订。 |
| 需求阶段 | 需求服务记录阶段、当前候选和下一位负责人。实现者提交候选，另一成员按已确认的验收条件复跑，质量负责人复核。 |
| 发布记录 | MyAgentDesk 从获准的 GitHub 发布或 CI 来源核对目标渠道、实际发布时间、集成提交、构建和产物。发布状态从回执得出。上线后回滚另记事件，并保留实际发布历史。 |
| 风险与跟进 | 从逾期、未满足的依赖或有效证据缺口定位真实阻塞。项目统筹登记跟进人、动作和期限，链接到原阶段。计划和需求仍分别在各自的入口更新。 |

有项目权限的成员可查看版本。仅获授权的项目负责人可确认或调整排期，提交时填写原因，记录原值、新值、操作人、时间和受影响的需求或里程碑。业务范围变更需要产品确认，质量放行和发布权限分别检查。并发调整基于计划修订号检查，旧页面提交冲突时重新加载。正式发布绑定当时的计划修订和实际交付范围。

## 时间线交互

1. 进入项目，查看计划版本、验收里程碑、目标日期和实际发布时间。没有日期的版本和未纳入版本的需求进入「待排期」。风险标签展示原因和受影响的里程碑。
2. 点版本看范围、有效验收、阻塞及版本决定。进度显示「已验收 / 待验收 / 阻塞」计数，同一需求只落入一类，阻塞优先。必过项单独标识。计数以当前范围、需求状态和有效证据计算，发布状态另列。移出需求或移动到其他版本时，在历史中保留原范围和决定。
3. 点需求打开当前阶段，查看文档、规格、产物和证据。点阻塞直接到处理处，显示等谁、下一步和期限。点实际发布进入对应构建和渠道回执。返回恢复所选版本和滚动位置。
4. 项目负责人从版本详情调整范围和日期。确认前展示差异和影响，确认后刷新范围和计数，并留下决定。统筹总览读取相同计划和阻塞记录，按项目权限显示，受限项目的标题、计数和通知内容均按权限过滤。

## GitHub、OpenSpec 与需求的关联

GitHub 保有 Issue、PR、CI 和发布原始记录，项目内 Issues 看板只读，编辑回 GitHub。项目成员可把获准的 Issue 关联到已有需求，也可确认创建一个带来源引用的需求。以仓库稳定 ID 和 Issue 编号去重。同一项目重复提交同一来源时返回已有需求关联，一条需求可以链接多个 Issue。界面分别显示来源状态、最近同步时间和需求阶段。Issue 关闭或 PR 合并只更新来源记录。

OpenSpec 提供仓库中的规格原件。需求保存变更标识、提交和已经确认的验收条件版本。规格调整影响验收时，由产品确认新版本，按受影响的条件重新验证。构建、验收和发布链接都可以回到当时使用的版本。

## 放行与中断恢复

每条需求的独立验收绑定候选、用例和证据。版本放行额外核对最终集成提交、CI 运行、产物摘要和同一构建的必过回归。候选重建后提交或产物变化，旧放行保留为历史，当前包等待重新核验。具备发布权限的成员提交发布确认，时间线在核对匹配的渠道回执后显示已发布。回执暂缺时显示待核对。

负责人超时或离开项目，授权统筹人员可将原决定和证据交给具有相应岗位与资源权限的替补，沿用原检查点。旧负责人的待决策入口无法再提交。缺少符合要求的独立测试成员时，显示「待独立验收」，继续展示自测证据。

tebb 本机派发按服务端唯一领取和本机启动记录恢复。启动状态不明时先核对原运行，仍无法确认时，由有权负责人核对本机影响并明确决定是否另跑。结果、候选可用性和独立验收分开记录。[派发、回执与断网验收](TEBB_INTEGRATION.md#断网重试和去重)

## 开发顺序与首版验收

1. 先建立项目身份和阶段契约，再实现版本计划和默认时间线。版本计划依赖 [#3 成员权限](https://github.com/lkun-lea/myagentdesk/issues/3) 和 [#6 需求及阶段](https://github.com/lkun-lea/myagentdesk/issues/6)，与 [#4 项目导航](https://github.com/lkun-lea/myagentdesk/issues/4) 联调。先交付计划、范围修订、成员视图、需求跳转和风险来源，使用替身验收。
2. 串起一条真实 GitHub Issue 的只读关联、确认规格、执行、独立验收和正式发布回执。[#9 Issues](https://github.com/lkun-lea/myagentdesk/issues/9) 先实现单条关联，[#10 OpenSpec](https://github.com/lkun-lea/myagentdesk/issues/10) 先支持固定规格引用。[#14 tebb 派发](https://github.com/lkun-lea/myagentdesk/issues/14) 是首版唯一接入的编码执行器。[#11 独立验收和发布](https://github.com/lkun-lea/myagentdesk/issues/11) 接入真实候选和构建回执。测试成员在隔离环境复跑，研发和测试是不同成员。
3. 使用一条已授权、可稳定复现且能进入实际发版渠道的需求。记录 GitHub Issue、版本修订、规格、派发编号、独立复跑、构建和实际发布的互相可追溯链接。用同一条需求覆盖一次延期或换版、一次启动结果不明、一次新候选使旧证据失效，并走通最终发布。保留人工接手次数、等待时间和异常处理记录。
4. 首版闭环验收后扩展 [#8 跨项目统筹](https://github.com/lkun-lea/myagentdesk/issues/8)、[#12 Skill 导入](https://github.com/lkun-lea/myagentdesk/issues/12)、[#13 Skill 设计](https://github.com/lkun-lea/myagentdesk/issues/13) 和 [#15 Codex](https://github.com/lkun-lea/myagentdesk/issues/15)。完整 Issues 看板与 OpenSpec 校验界面按各自 issue 验收。

## 调研依据

- GitHub Projects 用目标日期和里程碑组织路线图。Linear 将项目依赖和实际发布分开记录。MyAgentDesk 据此区分计划、需求阶段和发布事实。[GitHub Projects 路线图](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/changing-the-layout-of-a-view#about-the-roadmap-layout) · [Linear 项目依赖](https://linear.app/docs/project-dependencies) · [Linear Releases](https://linear.app/docs/releases)
- GitHub 的不可变发布关联标签、提交和发布产物。MyAgentDesk 将质量放行绑定具体集成构建及产物摘要。[GitHub 不可变发布](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
- Linear 提供 GitHub 工作项关联，OpenSpec 区分当前行为与变更提案。MyAgentDesk 用来源引用连接 GitHub、规格与内部交付阶段。[Linear GitHub 集成](https://linear.app/docs/github) · [OpenSpec 概念](https://github.com/Fission-AI/OpenSpec/blob/main/docs/concepts.md)
- Temporal 的活动重试考虑了副作用已经发生、完成记录仍缺失的情况。MyAgentDesk 将派发领取、执行和事件补传分别核对。[Temporal 活动幂等](https://docs.temporal.io/activity-definition#idempotency)
