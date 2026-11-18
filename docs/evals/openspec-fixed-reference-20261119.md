# #10 OpenSpec 固定规格验收

用 [MyAgentDesk #10](https://github.com/lkun-lea/myagentdesk/issues/10) 创建隔离需求，经项目的只读 GitHub 接口核对真实提交和变更目录。运行使用实际 API、PostgreSQL 和官方 Pi SDK，产品、研发和测试使用独立测试身份。模型端点提供可重复的工具调用和证据样本，用来验证交接、留存和门禁。

## 正常路径

| 步骤 | 结果 |
| --- | --- |
| 固定需求 v2 | 绑定 `4425b7cb4cc8c9a775717d8bcd1ab4f2240dd275`。两条条件独立验收通过，保存研发候选、测试运行、结果及逐项证据。 |
| 更换提交和验收条件 | 绑定 `62eac4f0d5981f92ec4884fbacdfdcf401234601`，并将「历史版本可查看旧验收记录」改为「历史版本可按候选运行查看旧验收记录」。目录树保持 `904ec61076243d77a6a62485bb21d4dfbd706ee0`，可单独核对条件变化的影响。 |
| 查看需求 v3 | 新写的条件显示「待重新验收」。原条目「固定规格指向原始提交」显示「有历史通过记录，当前候选待验收」。需求群回到产品阶段，旧阶段凭据无法放行。 |
| 回查需求 v2 | 保留原 SHA、原条件、原候选、验收运行和逐项证据。结果标为历史证据。 |
| 完成需求 v3 | 产品确认后重新交给研发和测试。新候选、新验收运行分别留存，两条条件的当前证据齐全后才可完成。 |

[v2 原始 OpenSpec 变更](https://github.com/lkun-lea/myagentdesk/tree/4425b7cb4cc8c9a775717d8bcd1ab4f2240dd275/openspec/changes/pin-requirement-specification) · [v3 固定 OpenSpec 变更](https://github.com/lkun-lea/myagentdesk/tree/62eac4f0d5981f92ec4884fbacdfdcf401234601/openspec/changes/pin-requirement-specification)

## 权限、并发与证据校验

- 研发确认固定规格被拒。分支名、目录越界和不存在的目录被拒。撤销测试成员后，原版本链接返回 404。
- 在可重复上游用例中，两个并发请求里一次成功、一次版本冲突。更换目录树后，全部条件都重新进入待验收。
- 缺项、重复条件和与总判定矛盾的逐项记录被拒。旧验收运行不能重确认，也不能覆盖历史证据。

定向回归和真实仓库复验均为 5 项通过。默认测试使用可重复上游；设置 `MAD_OPENSPEC_LIVE_BASE` 和 `MAD_OPENSPEC_LIVE_HEAD` 为表中的两个完整 SHA，可通过 `uv run --locked pytest tests/test_specifications.py -q` 复验真实仓库。测试需要隔离 PostgreSQL 和 Node.js 22。真实执行器产物及回执由 #11 后续联调。
