# Temporal 接入验收记录

日期：2026-09-03。实现分支：codex/temporal-runtime-integration。

工作区：C:\Programs\LetsAIGC\.local\worktrees\temporal-runtime。基线为 master 的 87ca6da9eac61ace8e1c97178d07081e8fb124be；主工作区原有的 tracking/mlflow_store.py 修改未覆盖、未提交。

## 实现内容

- T00：创建独立 Git 分支和 worktree，记录原有测试基线。
- T01：optional Temporal SDK、固定版本/哈希、持久化本地开发服务和 runtime doctor。
- T02—T04：版本化公共 DTO、显式能力注册、内容寻址素材库、事务账本、批准回执、预算和 GPU 所有权。
- T05—T08：Client/Worker、Pydantic 转换、有限 Activity 重试、批准/拒绝/取消/对账、模拟修订、Continue-As-New 和状态投影。
- T06：保留 ComfyBackend.execute，抽出无生成提交的 prepare；独立 submit / inspect / collect / cancel 适配，恢复队列/历史中的原请求。
- T09—T10：兼容 RunManifest、父子操作关联、MLflow 幂等登记、pipeline/runtime CLI、操作文档。
- T11：领域、协议、本机 Temporal 服务及进程故障验证完成；单次真实 GPU 验收经用户精确批准后通过。
- T12：最终回归、真实 GPU 验收和交付审查完成，作为 UI 工作流的单机试点基线。生产高可用/Cloud 与游戏 UI 工作流不在本次交付内。

没有将整个旧 AgentOrchestrator 包成重试 Activity。Comfy v1 保持单次生成和零自动修订；重型多步骤领域流程可使用公共接口追加显式 Workflow 类型。

## 基线与门禁

| 门禁 | 实际结果 |
|---|---|
| 改动前全量 pytest | 109 passed / 3 failed；三个失败均为既有 MLflow artifacts 参数被 Path 覆盖 |
| 改动前 ruff | 通过 |
| 修复后第一轮离线回归 | 126 passed |
| 独立 Temporal/领域故障组 | 26 passed：9 组真实服务/进程实验 + 17 项领域用例；此后补充原生取消与投影故障取消回归 |
| MLflow 并发/崩溃窗口投影 | 1 passed |
| 领域和适配器合同组合 | 26 passed（后续又增加两个领域用例） |
| 全量验收，深层 worktree 临时目录 | 149 passed / 1 failed / 1 skipped；旧 drama 测试触发 MLflow Windows 长路径限制 |
| 第一次较短独立目录验收 | 150 passed / 1 skipped；随后补充取消与文件共享故障用例 |
| 中间回归 | 151 passed / 1 failed / 1 skipped；旧 Agent 状态文件遇到 Windows 临时共享冲突，已修复 |
| Windows 共享故障及旧 Agent 定向回归 | 25 passed；临时错误重试成功，持续错误有界失败 |
| 最终全量回归 | **154 passed / 1 skipped，124.48 秒**；10 项本机 Temporal 服务实验全部执行，唯一跳过项为真实 GPU 验收 |
| 最新 ruff check . / git diff --check | 通过 |
| 真实 GPU 生成 | **1 passed / 154 deselected，19.47 秒**；获批的 SD1.5 图像生成完成，实际 0.068934 GPU 分钟、$0，预留和未确认费用均为 0 |

为使用既有 MLflow 证据接口，在实现 worktree 内独立修复了 artifacts 参数遮蔽，并增加稳定逻辑标签去重。主工作区的原有修改保持原状。

深层目录失败由 MLflow 本地 artifact publish 的目标路径超过 Windows 限制导致。之后门禁使用较短的独立临时目录，失败报告仍保留。中间回归进一步定位到旧 Agent 原子替换 task.json 的 WinError 5；共享的 replace_file 对 Windows 访问/共享冲突最多重试五次、累计等待 0.75 秒，持续失败仍原样抛出，不修改文件权限。Agent 状态、Pipeline 素材与投影采用同一有限重试策略。

## 故障矩阵与可定位测试

表中 T01—T18 对应研究文档中的故障编号；它们不是实施计划中的同名步骤。下面的“通过”指所列模拟/协议实验，不代表同一故障已在真实 GPU 上重演。

| 编号 | 场景 | 测试证据 | 结果 |
|---|---|---|---|
| T01 | Activity 完成记录后强制结束 Worker | test_temporal_completed_activity_survives_worker_kill；提供方 submit 调用数仍为 1 | 通过 |
| T02 | 受理后 Worker 退出，返回尚未落账 | test_temporal_worker_process_dies_after_provider_acceptance；真实子进程 os._exit，恢复原请求 | 通过 |
| T03 | 提供方不可查询的断线结果 | test_accepted_then_disconnected_never_resubmits[False]；保持未知、不重新提交 | 通过 |
| T04 | 重复批准 | test_temporal_approval_revisions_replay_and_projection | 通过 |
| T05 | 错指纹、无本地回执、旧/非法流程合同 | 上述批准测试 + test_approval_is_exact_local_and_cancel_blocks_submit / test_plan_identity_and_registry_are_immutable | 通过 |
| T06 | 预算竞争、超额与修订上限 | test_atomic_budget_and_process_competition / test_actual_overrun_is_recorded_and_blocks_further_budget；模拟三修订流程 | 通过 |
| T07 | 超时但 GPU 请求未确认停止 | test_comfy_timeout_retains_unknown_and_only_cancels_owned_request + 未知资源所有权单元测试 | 协议/领域通过 |
| T08 | 重复收集和结算 | test_idempotent_settlement_and_unknown_reservation / test_comfy_completion_collects_real_file_and_preserves_manifest | 通过 |
| T09 | 取消时已受理/仍未知 | 断线领域用例 + Comfy 超时取消合同；未提交拒绝、Update/原生 Cancel、投影失败期间取消 | 模拟/协议通过 |
| T10 | 素材丢失、变化、路径越界 | test_artifact_scope_integrity_and_atomic_repeat | 通过 |
| T11 | 不兼容代码重放 | test_temporal_replayer_rejects_incompatible_code | 检出不兼容 |
| T12 | 兼容 Worker build 替换后续跑 | 两个 Worker 子进程故障实验；v1 合同、不同 build 标识 | 通过；未启用自动版本路由 |
| T13 | 服务及 Worker 退出重启 | test_temporal_server_and_worker_restart_preserves_approval_wait | 通过 |
| T14 | Continue-As-New 保留身份和费用范围 | test_temporal_continue_as_new_keeps_operation_and_budget | 通过 |
| T15 | 投影写入失败 | test_temporal_projection_fault_does_not_repeat_generation；生成数仍为 1 | 通过 |
| T16 | 过大载荷 | test_payload_policy[oversized]；DTO 拒绝超限 | 通过 |
| T17 | 两个任务/进程竞争单 GPU | test_two_tasks_compete_for_single_gpu_across_processes | 通过 |
| T18 | 假凭据、URL query/fragment 进入错误路径 | test_temporal_provider_error_redaction_and_reconciliation / test_payload_policy | 历史和 manifest 检查通过 |

测试文件：
- tests/unit/test_pipeline_domain.py
- tests/contract/test_temporal_contracts.py
- tests/integration/test_temporal_runtime.py
- tests/integration/test_temporal_tracking.py
- tests/integration/test_temporal_gpu.py
- tests/fixtures/temporal/worker_process.py

另验证了 Standalone Worker 冷导入、CLI 在缺 SDK 时的旧入口/本地查询、SDK DTO 往返、Comfy Windows 模型枚举路径适配，以及真实 PNG 的 ffprobe/产物收集。冷启动暴露并修复了既有 agent 包 eager import 导致的循环依赖。原生 Cancel 的回归发现 SDK 会把 Activity 取消包在 ActivityError 内；现在显式识别取消链，进入后端收尾，避免误判为投影失败并无限等待。

## 本机真实后端准备

准备阶段启动了隔离的 127.0.0.1:8189 ComfyUI 0.34.2，使用独立 input/output/user/temp 目录，禁用 custom/API nodes。复用已存在的 SD1.5 权重，通过硬链接避免再下载或复制 4.3 GB 文件；权重 SHA-256 已通过现有模型校验。硬件实际报告为 RTX 3080 10 GB，PyTorch 2.9.1+cu130。

已完成原生图编译、/object_info 合同检查及获批的单次 GPU 工作：

- 新 task ID：pipeline-9c96182b478d9dc09827dc1f8453f865。
- 完整批准指纹：df9428d7449b84eea31e8519019109a4efddeb2b8eec23781733bc4d6f3eafba。
- 512×512、2 steps、seed 20260903、一个输出、零修订。
- 单次/总上限均为 2 GPU 分钟、$0。
- 冻结文件：.local/temporal-gpu-validation/approved-plan-required.json。
- 编译图 SHA-256：396d05523ab134c02edcc747b0ddbba7062fa1a9cebaf270d51343383ae1b606。

用户已明确批准上述精确计划，随后执行 GPU 标记测试并通过。单张真实生成通过不能将取消、超时、提供方历史丢失等所有真实故障标记为已验证；这些场景的已有模拟/协议证据仍分别记录。

ComfyUI 首次隔离启动触发其既有数据库迁移逻辑：原 user/comfyui.db 被改名为 .bak 并复制到隔离 user 目录。已从该备份恢复原路径；保留备份，未删除原数据。

第一轮收尾时关闭了空闲隔离 ComfyUI；获批后重启该 8189 服务，完成唯一一次生成。测试 Temporal 服务已退出；本地数据库、素材和批准计划保留。以下为同一隔离实例的启动方式：

~~~powershell
mamba run -n letsaigc-comfy python .local/runtime/ComfyUI/main.py --listen 127.0.0.1 --port 8189 --models-directory .local/models --output-directory .local/output --input-directory .local/input --user-directory .local/comfy-user --temp-directory .local/comfy-temp --disable-all-custom-nodes --disable-api-nodes --disable-auto-launch
~~~

在实现 worktree 中执行上述命令；核心 Worker/验收进程另设 COMFY_URL=http://127.0.0.1:8189。原验收任务已完成，不得以原批准再次生成；新实验须建立新的具体计划。

真实输出 SHA-256：d16e6621c150308811ae9be68e5aefd70f87822ae1f4303e272aac9dff77cc87。Temporal Run ID：01a067b1-91a2-751b-a24c-b62cd9b72c1e。operation 数为 1，费用已结算，输出为一张 512×512 PNG。

## 证据位置与后续界限

- 实现 worktree 的 .local/temporal-validation-results.xml：深层目录的首轮完整结果。
- 实现 worktree 的 .local/temporal-validation-shortpath.xml：第一次短目录完整通过的结果。
- 实现 worktree 的 .local/temporal-validation-release.xml：定位 Windows 共享冲突的中间回归。
- 实现 worktree 的 .local/temporal-validation-verified.xml：最终回归结果。
- C:\Programs\LetsAIGC\.local\tv-0903-verified：最终独立测试的数据库、提供方计数、素材和代表性历史。
- 实现 worktree 的 .local/temporal-gpu-validation：真实后端批准计划、gpu-history.json、gpu-result.json、gpu-usage.json 及内容寻址产物。
- 实现 worktree 的 .local/temporal-gpu-acceptance.xml：实际 GPU 测试报告。
- configs/runtime/temporal.lock.yaml：SDK/CLI/Server 版本与官方下载哈希。

当前是单机本地磁盘、可信 CLI、受控 GPU 的试点架构。跨机器账本、服务认证、高可用、自动 Worker Versioning 路由、硬件级硬预算、无人处理的历史丢失对账及真实故障演练仍需要后续部署/验收。不要将此开发服务当作生产 HA 服务。

单机试点代码与现有验收组均已通过，随后按 UI 实施计划合入 master，作为新 UI 分支的基线。生产部署和完整真实故障演练仍遵循上述边界。
