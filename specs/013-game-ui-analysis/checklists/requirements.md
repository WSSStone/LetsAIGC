# Specification Quality Checklist: 游戏 UI 解析工作流

**Purpose**: 检查规格完整性、可验证性和主计划覆盖情况，为后续 Speckit 阶段提供入口。  
**Created**: 2026-09-04  
**Feature**: [013-game-ui-analysis](../spec.md)

## 当前修订入口（2026-09-04）

本轮按用户批准计划改为“手动单图→双后端搜索可用预览→拆解补图→批次及正式验收”。[当前Tasks](../tasks.md)为42项，均未勾选；原96项全部有新映射，原13项[LIVE]要求由10项新[LIVE]承接且全部未验收。自动区域提案可直接批准，选择文件为高级覆盖；实耗超过预留但仍在批准限额内记非失败reservation_adjusted并核算后自动继续。

现行依赖与需求覆盖以Tasks为准；本页下方的初次质量勾选、Gxx映射、阶段测试和重复Analyze授权均为历史记录，不是当前实施顺序或本次测试结果。后来的用户指令已明确覆盖旧循环约定：**修订后仅运行一次只读Analyze，汇报发现、严重程度、覆盖和限制后停止，即使有发现也不自动修订或复核。** 本次文档校验结果记入Tasks，不将历史结果替代当前结果。

## 历史：Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- 2026-09-04：逐项复核通过。勾选表示规格质量检查完成，不表示功能、Gxx 任务、真实供应商或 GPU 验收完成；spec 状态仍为 Draft。
- 规格按模板保留用户场景、需求、Policy and Evidence、实体、成功标准及假设，包含 6 个用户故事、55 条 FR、9 条 PER、12 条 SC。
- 规格中的供应商名称是允许的产品能力范围，JSON 文件名是用户产物；没有新增实现文件清单、库选型、接口签名或服务端点。实现细节由后续 Plan 承接。
- 数量默认值采用原主计划建议值；空蒙版不生成及普通输入失败时保留部分成果并继续，作为明确假设记录。没有需要用户现在补答才能形成规格的阻塞项。
- 质量度量采用 spec 的 PER-008 / SC-004：“开发集实测后、独立评估前冻结”“每项提供测量值、分母、适用样本和阈值版本”。这是可验证的冻结与验收门槛，不虚构现阶段没有的模型精度；后续未冻结数值时不得通过质量验收。
- 16 组原需求均已映射如下；U-V 编号保留作为追踪标识，不复制原任务勾选或运行状态。

## 主计划需求追踪

本地来源：[游戏 UI 解析工作流实施主计划](../../../.doc/plan/game-ui-analysis-workflow-plan.md)。该文件按工程约定保持本地忽略；规格的用户要求与验收标准可独立阅读，不要求其他 clone 拥有本地记录。

| 原需求 | 规格覆盖 | 成功标准 | 原实施步骤 | 原验收编号 |
|---|---|---|---|---|
| R01 统一手动/搜索素材合同 | FR-001—009；PER-006 | SC-002、003、006、011 | G01、G02、G06、G10 | U-V01、08、11、12 |
| R02 手动输入、快照和选图语义 | FR-002—009、049—052 | SC-002、003、008、009 | G02、G06、G11 | U-V01、09、11、13 |
| R03 标准图与坐标 | FR-010—012、018 | SC-001、003 | G02、G05 | U-V01、04 |
| R04 OCR 与局部重读 | FR-013—014、029 | SC-001、004、010 | G03、G05、G09 | U-V02、10 |
| R05 严格视觉分析、证据及权限 | FR-015—017；PER-003、006 | SC-001、004、006、011 | G04、G05 | U-V03、04、12 |
| R06 元素身份、布局和切片 | FR-017—019、029 | SC-001、010 | G05 | U-V04、05 |
| R07 视觉能力就绪及资源边界 | FR-021；PER-002、004、007、009 | SC-006、012 | G01、G03、G07 | U-V02、05、08 |
| R08 精细分割及真实性角色 | FR-019—021、028 | SC-004、005、012 | G07、G09 | U-V05、07 |
| R09 蒙版补图与编辑范围 | FR-023—028；PER-007 | SC-005、006、012 | G08、G09 | U-V06、07、08 |
| R10 批准、预算和共同资源 | FR-026、035、049、054；PER-002—004、007 | SC-006、007、008、009 | G01、G06、G09、G10、G11 | U-V08、09、13 |
| R11 搜索额度及路由 | FR-030—042、046—048 | SC-002、006、007、008 | G01、G10 | U-V11、12、13 |
| R12 下载、去重和来源安全 | FR-002、005—007、043—045；PER-005—006 | SC-003、006、011 | G02、G10 | U-V11、12 |
| R13 幂等、对账、修订和取消 | FR-029、039—041、051—055 | SC-006、008、009、010 | G06、G09、G10、G11 | U-V08、09、10、13 |
| R14 顺序批次、共享预算及部分失败 | FR-007—008、049—051 | SC-002、009、012 | G11 | U-V13、14 |
| R15 固定样本、分组及质量阈值 | PER-008—009 | SC-001、004、005、012 | G01、G12 | U-V01、02、04、05、07、14 |
| R16 来源、真实验收和兼容性 | FR-006、028、044、052—055；PER-001、004—009 | SC-008、011、012 | G06、G09、G11、G12 | U-V06、09、12、14 |

## 后续 Speckit 入口

当前实际 Git 分支保持用户在主计划中指定的 `dev-game-ui`，特性编号为 `013-game-ui-analysis`。`speckit-specify` 的默认新建分支步骤服从该既有工作位置要求：仅调用一次仓库创建脚本的 `-DryRun -Json -ShortName game-ui-analysis`，自动取得编号和路径，再写入规格；未传 `-Number`，未新建或切换分支。

后续调用 Speckit 脚本时，在同一次 PowerShell 执行中使用现有的特性选择机制：

```powershell
$env:SPECIFY_FEATURE = '013-game-ui-analysis'
& '.specify/scripts/powershell/check-prerequisites.ps1' -Json -PathsOnly
```

该变量只选择 Speckit 文档目录，不修改实际 Git 分支，也不必写入产品 `.env`。新的工具 shell 需要再次设置。`-PathsOnly` 仅验证定位，不表示尚未生成的 plan.md 或 tasks.md 已存在。

Specify 阶段的建议顺序为 `speckit-clarify → speckit-plan → speckit-tasks → speckit-analyze`，该阶段没有生成 plan.md/tasks.md。用户随后直接调用 Plan，再调用 Tasks；[实现计划](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/plan.md) 已记录该流程选择，[任务清单](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/tasks.md) 已生成。下一步为 Analyze。文档生成不批准实际模型操作。

Plan 必须承接原主计划中的技术约束：Temporal 公共多步骤注册及账本扩展、UIProvider 输入/结果合同、严格视觉分析结构、PaddleOCR/SAM 2 独立环境及模型锁、原生 ComfyUI image/mask 编译和 UI/API/工作流合同同步、SerpApi/Tavily 请求和额度协议、瞬时 URL 边界、恢复证据和既有功能回归。不能因规格用业务语言描述而丢弃这些已确定的设计输入。

Tasks 已映射原 G00—G12 依赖及 U-V01—U-V14 验收，保留 G00 历史证据，并安排实施前逐文件审查复用旧 worktree 草稿。原状态快照已迁入且本地主计划入口已更新；正式 tasks.md 的勾选和新证据是唯一生效执行状态。

## Specify 阶段验证记录（2026-09-04）

- 规格编号连续且唯一；6 个故事、55 条 FR、9 条 PER、12 条 SC、16 项质量检查及 R01—R16 映射均已核对；规格无残留模板占位符或待回答标记。
- 两份新文档及本地主计划中的 19 个本地链接可定位；PowerShell 示例语法检查通过，现有 Speckit 脚本通过 `SPECIFY_FEATURE` 正确定位本特性。
- `git diff --check` 通过；另外对两份未跟踪的新文档逐份执行 `git diff --no-index --check -- NUL <文件>`，无空白错误。原 Gxx 状态及里程碑验收状态未修改，`.doc` 仍被忽略，`.env.example` 原修改哈希保持不变。
- `mamba run -n letsaigc-core pytest`：本次为 **144 passed / 11 skipped / 1 warning，70.26 秒，退出码 0**。10 项 Temporal 运行测试因未设置已验证本地 CLI 而跳过，1 项真实 GPU 测试因缺少对应运行计划及批准条件而跳过；警告为 `.pytest_cache` 写入权限。mamba 启动时也报告激活脚本目录写入权限错误，但随后实际执行了上述测试，故以本次输出为准。
- `mamba run -n letsaigc-core ruff check .`：**未取得检查结果，退出码 1**；仅返回 mamba 无法写入 `D:\Programs\miniforge3\condabin` / `Scripts` 激活脚本的权限错误。未扩大本项去修复系统环境，也不引用历史检查结果代替本次结果。
- 本次仅编制规格和流程转接文档；没有修改运行时代码、执行生成或搜索验证，也没有创建 Git commit。上述仓库回归不计为 UI 三阶段验收。

## Plan 阶段验证记录（2026-09-04）

- 用户直接调用 speckit-plan 后生成 plan.md、research.md、data-model.md、quickstart.md 及五份 contracts 文档；没有生成 tasks.md，也没有改变 spec.md 内容。
- setup-plan 和 check-prerequisites 均通过现有 SPECIFY_FEATURE 机制定位 013，实际Git分支保持dev-game-ui。AGENTS已按技能运行更新脚本，并复核保留全部人工约束；新增技术明确标为计划候选。
- 复核全部11份特性Markdown、34个本地链接和13段PowerShell示例；编号/占位符检查通过，原13个Gxx依赖逐项一致且无环，U-V01—U-V14全部有设计与验收映射。
- 两份来源入口已转接到正式Plan，原Gxx状态和三个里程碑的未验收结论未改变；本地主计划保持Git忽略，.env.example原修改及spec.md的SHA-256保持不变。
- tracked `git diff --check` 通过，特性文档另用no-index逐份检查，无空白诊断；设计前后Constitution门槛均已核对，不把设计通过当运行验收。
- 本阶段实际尝试 `mamba run -n letsaigc-core pytest` 和 `mamba run -n letsaigc-core ruff check .`，两者均退出码1，只返回mamba无法写入 `D:\Programs\miniforge3\condabin` / `Scripts` 激活脚本的权限错误，没有产生测试/检查结果。上节144 passed的记录属于Specify阶段，不是本阶段结果。
- 官方资料研究未访问供应商账户、调用真实搜索/视觉服务或下载权重；运行代码、配置、数据库及教程没有变更。本阶段没有Git提交。

## Tasks 阶段验证记录（2026-09-04）

- 已生成 tasks.md：96 项，US1/US6/US2/US3/US4/US5 分别为 17/8/9/14/15/8 项，共享任务 25 项；31 项标记 [P]、13 项标记 [LIVE]，全部保持未勾选。历史 G00 的完成结论保留，不将其他草稿或文档生成推定为实现完成。
- 逐行检查 checkbox、连续唯一 ID、阶段故事标签、绝对目标路径和依赖；96 项均符合格式，依赖无环；9 个并行示例波次无相互依赖和目标文件冲突。
- 追踪覆盖 55 条 FR、9 条 PER、12 条 SC、R01—R16、G00—G12 和 U-V01—U-V14；原 G 前置与迁入状态逐项一致，历史状态行、实测证据行、哈希及原 [LIVE] 状态未改写。
- 检查全部 12 份特性 Markdown 的 48 个本地链接与 13 段 PowerShell 示例；语法及路径有效。tracked git diff --check 和 13 份特性/本地文档的 no-index 空白检查通过。Speckit check-prerequisites -Json -RequireTasks -IncludeTasks 正确返回 013 与 tasks.md。
- 本地主计划已转为历史快照，正式 Tasks 是唯一执行状态及新证据入口。spec.md 和用户 .env.example 的 SHA-256 保持不变；AGENTS 的前阶段修改保留，.doc 继续被 Git 忽略，教程未修改。
- 本阶段实际尝试 mamba run -n letsaigc-core pytest 和 mamba run -n letsaigc-core ruff check .，两者均退出码 1，仅返回无法写入 D:\Programs\miniforge3\condabin / Scripts 激活脚本的权限错误；未取得本阶段测试或 lint 结果，没有修系统环境或引用前阶段测试替代。
- 本阶段仅生成任务并更新文档转接，没有实施运行功能、调用真实模型/搜索、下载或提交 Git。当前格式/覆盖校验不替代下一步 speckit-analyze，也不计为 UI 验收。

## 历史：Analyze 修订交接（2026-09-04，循环约定已被覆盖）

用户授权修正全部发现并重复只读Analyze至无剩余问题。修订保持013特性、FR/PER/SC及T001—T096编号；历史Specify/Plan/Tasks验证记录和G状态快照不改写。修订覆盖先行来源合同、单图搜索Workflow、具体选择文件/等待阶段、实耗异常失败以及operation只读恢复。新的审核结果以本轮只读Analyze结论为准；文档通过不表示实现、模型部署或[LIVE]验收通过。
