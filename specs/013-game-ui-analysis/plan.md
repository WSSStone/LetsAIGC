# Implementation Plan: 游戏 UI 解析工作流

**Feature ID**: `013-game-ui-analysis` | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)  
**Working Branch**: `dev-game-ui` | **Baseline**: `7b91c34deb23064aaa6b492ce105956a59b47b71`  
**Status**: 按用户批准的产品优先计划修订；代码与真实验收均未完成。修订后执行一次只读 Analyze，只汇报结果，不自动修订或重跑。

## Summary

首个可用产品是**手动单图解析 + SerpApi/Tavily 双后端搜索及自动切换**。工程顺序先完成手动单图，再把双后端接到同一个 ui_analysis，交付明确标注的可用预览。之后交付分割/补图，再交付批次与正式质量验收。原 M-U1—M-U3、FR/PER/SC、R01—R16、U-V01—U-V14 保留，首版预览不是全量验收完成。

普通编辑流程由 Agent 提出区域、对象与预览，用户直接批准相应具体 GPU 子计划。实耗高于预留而仍在原批准限额内时，记录 reservation_adjusted 并核算后自动继续；实际违反批准预算才失败。

## Technical Context

- Python 3.12；复用 Typer、Pydantic v2、httpx、Pillow/NumPy、现有模型连接和 Temporal SDK 1.32.0。
- CPU OCR：独立环境，PaddleOCR 3.4.0 / PaddlePaddle 3.2.2，显式静态模型及已验证加载器。分割阶段才准备独立 SAM 环境：Transformers 4.57.6、PyTorch 2.9.1 / torchvision 0.24.1、官方 safetensors。
- 复用公共 PipelineService、ArtifactStore 和 .local/pipelines/ledger.sqlite；不新建调度器、预算库或产品控制平面。
- Windows 11、RTX 3080 10 GiB、RAM 64 GiB；首版 parse 不使用 GPU，GPU 服务就绪不能阻塞它。
- 现有单任务 ledger 已有按 step 预留、批准、取消与 unknown 费用；新增能力采用明确的多步骤分派，不默认落到 Comfy。
- 测试以领域正确性、共享执行合同、适配器边界和真实用户链路为主。完整质量集和 GPU/批次故障随阶段推进。
- 保留既定资源限制：单图25 MiB/32MP/最长边8192，RAM24GiB、VRAM10GiB、根网络1GiB、单图活动3600秒；完整值见[执行合同](contracts/execution.md)。请求可收紧；金额与GPU预算必填，parse可为0 GPU。
- 软件、模型及供应商条款分别记录许可；未知许可拒绝相应能力，模型准备由操作者完成。回环服务、原生 Comfy 节点、输入检查及凭据脱敏不变。

## Constitution Check

用户已通过计划讨论明确新的优先级、选择体验和预算语义，本次属于规格/设计调整，不修改 Constitution 2.0。

| Gate | 设计约束 |
|---|---|
| Development Harness | specs只约束开发，不成为产品运行依赖；Analyze最终只读汇报 |
| 可重现与证据 | 输入/参数/版本/hash/费用和输出可追溯，预览不等于正式验收 |
| 许可与输入安全 | 许可人工确认，安全权重、回环、SSRF及秘密脱敏继续硬校验 |
| 预算和批准 | 每次准入验证原批准限额；自动区域提案不构成人工GPU批准 |
| 受限工作流 | 按阶段注册确定流程，动态Comfy图仍由已审查recipe编译 |
| 测试先于实现 | 同一任务内先写相关合同/失败测试再实现；共享不变量只维护一套 |
| 验收诚实性 | 未完成的真实验收保留，复用证据须匹配当前依赖版本和输入 |

不存在 Product Invariant 例外。前阶段用户直接调用 Plan、未单独运行 Clarify 的记录仅作历史；本轮明确需求已通过计划问答锁定。精简的是重复验证与不必要前置，不是许可、批准、预算或安全要求。

## Project Structure

### Documentation (this feature)

```text
C:\Programs\LetsAIGC\specs\013-game-ui-analysis\
  spec.md
  plan.md
  research.md
  data-model.md
  quickstart.md
  contracts/cli.md
  contracts/execution.md
  contracts/ui-provider.md
  contracts/vision.md
  contracts/image-search.md
  checklists/requirements.md
  tasks.md  （已生成；唯一执行状态入口）
```

### Source Code (repository root)

以下是后续新增/修改目标，不表示已经实现：

```text
C:\Programs\LetsAIGC\
  src/letsaigc/
    schemas/ui.py, ui_provider.py, agent.py
    ui_providers/base.py, registry.py, intake.py, manual.py, search.py
    ui_analysis/normalize.py, coordinates.py, layout.py, crops.py,
                text_assets.py, inpaint.py, quality.py, revision.py, selection.py, cli.py
    vision/base.py, ocr.py, segmentation.py, client.py, service.py
    agent/ui_analyzer.py
    assets/search.py, search_routing.py, providers/serpapi.py, providers/tavily.py
    pipelines/registry.py, service.py, ledger.py, migrations.py
    execution/temporal/ui_workflow.py, ui_activities.py, worker.py, client.py
    workflows/compiler.py
    backends/comfy.py
    tracking/manifest.py
    cli.py, doctor.py
  configs/ui-analysis/default.yaml, budget-example.yaml
  configs/eval/ui-analysis.yaml
  configs/runtime/vision.lock.yaml, vision.yaml
  configs/providers/image-search.yaml
  configs/workflows/recipes/sdxl-inpaint.yaml
  environment/vision-ocr.yml, vision-segmentation.yml
  workflows/ui/sdxl-inpaint.json
  workflows/api/sdxl-inpaint.json
  workflows/contracts/sdxl-inpaint.yaml
  tests/fixtures/ui_analysis/, contracts/
  tests/unit/test_ui_*.py, test_search_*.py
  tests/contract/test_ui_*.py
  tests/integration/test_ui_*.py
  docs/game-ui-analysis.md
  pyproject.toml
```

**Structure Decision**：领域模块不依赖Temporal SDK；Workflow只编排，Activity调用受限公共服务。UIProvider供给素材，vision执行固定分析，生成进入现有编译/Comfy证据链。机器schema由Pydantic导出到测试合同夹具，运行不依赖specs目录。


## 设计工件

[research.md](research.md) 保留12项决策及官方来源；[data-model.md](data-model.md) 定义分期数据与区域候选；[CLI](contracts/cli.md)、[执行](contracts/execution.md)、[供给](contracts/ui-provider.md)、[视觉](contracts/vision.md)、[搜索](contracts/image-search.md) 合同定义拟实现接口。[Quickstart](quickstart.md) 按首版预览→编辑→批次组织，不要求预览准备SAM/SDXL。

## 按可用结果推进

### A. 手动链路与双后端预览：新 T001—T018

1. 核对现有草稿，固定三个开发样本与最低公共执行合同；不先要求24例标注或所有模型环境。
2. 复用单任务费用、操作和素材存储，增加 ui_analysis 与必要 step 绑定；实现 manual/canonical、CPU OCR、VLM、布局/切片、CLI。
3. 先产生真实手动单图结果，再接入双后端的协议、共享额度缓存/限频/原子预留、自动切换、有界下载及筛选。
4. 两家各一次真实搜索并使用同一单图解析；首版展示来源、费用、完整产物与停止原因。批次、GPU或完整评估缺失不阻塞首版。
5. 三个样本分别为英文横屏HUD、中文密集UI和竖屏图。一次代表性恢复选在OCR已登记后中断，复用输入/OCR，不再重复执行已完成推理；公共替身覆盖提交窗口。

首版仍使用候选/下载/查询/切换硬上限及当前双后端额度规则。自动切换用固定额度响应验证，不故意耗尽真实账户。旧 M-U1 的24例正式验收尚未通过时，状态明确为预览。

### B. 拆解与补图：新 T019—T030

按需扩展父子授权/预算，准备SAM，再接入MaskedGenerationPlan v2、原生recipe和CPU精确合成。默认由VLM在已批准分析范围及计数内提出区域；确定性校验后生成预览与子计划，执行具体指纹同时确认范围。目标含混只要求候选编号；文件选择为高级覆盖。分割与最终mask补图各自批准，单次批准不扩大根预算。

保留两种背景目标、可选字形、最多两次补图修订及依赖闭包。原始/估计/生成资产分开。真实分割与补图先证明可用并记录质量值，正式阈值冻结在最终质量阶段。

### C. 批次与正式验收：新 T031—T042

复用同一单图流程，父级获取素材、顺序创建child，保持每条输入映射；普通失败按策略继续，批准/unknown/预算门槛不得绕过。补齐24例、开发16/评估8、已知背景至少8且4/4分布；先在开发集冻结阈值，再独立评估。三例预览属于开发集，不能移入留出评估集。

最终汇总仍有效的来源、质量、两家搜索、GPU和恢复证据，只补缺口及版本变化影响。旧 Gxx 依赖是历史索引，当前执行仅依赖新 Tasks 的接口/数据条件，不保留“G01全部完成才开工”的门槛。

## 公共接口与数据演进

- 保留 PipelinePlan、GenerationPlan v1 序列化和既有 Agent/专家语义。新增 resolve_ui_step/submit_step 路径，UI计划仅允许请求/策略/评估素材引用；旧 submit(plan, revision) 不改名或改变合同。
- 新 ui_analysis 首版只启用manual/search单图parse；首版计划阶段拒绝未就绪的多图和编辑请求，保留最终1—10图规格，不静默截断。
- 数据库分三次小迁移：v1→v2仅 ui_step_bindings；v2→v3增加五张quota表；v3→v4增加 ui_budget_groups、ui_child_bindings、ui_operation_charges。费用始终只在原operations入账。每次只测自身迁移和旧兼容样本；旧二进制不得打开不支持的版本。
- 单图阶段根身份就是task_id，额度scope协调多个独立单图任务，不需要父子预算表。GPU子流程阶段才建立根组及选择版本绑定。
- ui select 新增 --candidate ID，与 --selection FILE 二选一。默认方案直接形成待批准计划；候选选择只登记范围，不调用模型或GPU。
- UI usage_verdict使用 within_budget / reservation_adjusted / budget_exceeded / awaiting_reconciliation。旧ledger.result.budget_exceeded比较实际与预留，保持历史含义；UI依据实际和批准限额独立判定。
- 结算偏差先原子更新真实账目，再重新检查下一次预留。仍在单次/总限额内自动继续；不足则停止新增消费并保留结果；实际超批准限额才关闭根/当前任务门禁并失败，unknown仍保留预留。

## 验证分工与证据

公共层只维护一套批准、operation、unknown、取消和结算矩阵。适配器只证明参数/响应正确及公共机制被调用；搜索新增额度/路由特例，GPU新增精确输入与资源交接。业务测试关注实际产物、用户流程和父子关系，不复制完整公共故障矩阵。

U-V01—07主要验证领域与产物正确性，U-V08—10由共享合同加边界例覆盖，U-V11—13验证搜索和批次特例，U-V14汇总真实链路。具体映射在Tasks。已有证据仅在输入、模型、代码/依赖及合同版本仍适用时复用；变更后只重验受影响断言。

本地函数/文档任务记录代码、命令和结果；只有外部执行记录才要求批准、受理次数、费用和资源证据。真实验证显式开启，默认不下载、不计费。用户要求修订后运行一次只读speckit-analyze，报告发现及限制后停止，不自动修订复核。

## 前阶段历史交付记录

以下为原Plan阶段快照，包含当时的任务数量和下一步，不约束本次新顺序：

## Plan 阶段交付记录

本轮生成设计工件，并通过项目脚本更新AGENTS计划技术上下文，保留人工约束。未创建tasks.md、未改变运行代码/配置、未迁移数据库或部署模型、未创建Git提交。

用户随后调用speckit-tasks，现已生成96项可执行任务并迁入Gxx历史状态与依赖。下一步以speckit-analyze核对规格—设计—任务一致性，通过门槛后实施；Tasks生成不改变上述Plan阶段记录或真实验收结论。
