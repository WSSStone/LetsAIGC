# Temporal 本机持久化运行

本版本在现有生成工具之上增加可选的 Temporal 执行层。旧 Agent 命令继续可用；当前开发分支另已接通[游戏 UI 单图解析](game-ui-analysis.md)，其真实预览验收尚未完成。

支持 Python 3.12、Temporal Python SDK 1.32.0、CLI 1.8.3（内含 Server 1.31.2、UI 2.50.1）。版本、官方下载地址与 SHA-256 记录在 configs/runtime/temporal.lock.yaml。这些是本机开发服务验证版本，不代表高可用生产部署已经验收。

## 1. 运行边界

- Workflow 只编排、等待消息和计时，不访问文件、数据库、网络或模型。
- Activity 使用显式注册的能力。单任务顺序调用；跨任务的 GPU 所有权在 SQLite 事务内协调。
- Temporal 历史决定流程推进；SQLite 保存批准、操作、费用与资源所有权；manifest 和 MLflow 是可重建的证据投影。
- 目前注册 temporal_smoke（模拟生产与有限修订）、comfy_generation（一次受限 ComfyUI 生成），以及开发中的 ui_analysis（手动/搜索单图 parse）。
- Comfy 计划从既有 GenerationPlan **复制为新的 pipeline-… 逻辑任务**，不迁移正在执行的旧 AgentStore 任务；旧执行器不会读取这个新任务的账本。
- v1 的 GPU 所有权协调覆盖使用同一账本的 Pipeline Worker。旧 Agent、直接 ComfyUI API 和外部程序不参与该账本；试点 GPU 需交给这组 Worker 独占使用。
- Agent 能力清单不包含批准、shell、任意写入、训练、模型下载或生产导出。

## 2. 安装与启动

在仓库根目录操作，使用 letsaigc-core 环境：

~~~powershell
conda run --no-capture-output -n letsaigc-core python -m pip install ".[temporal]"
conda run --no-capture-output -n letsaigc-core letsaigc runtime doctor
~~~

独立 worktree 的开发验证可设置 $env:PYTHONPATH = Join-Path (Get-Location) 'src'，避免使用指向另一个 checkout 的 editable 安装。不要安装到全局 Python。

从锁文件的官方地址取得对应 CLI，核对下载文件和可执行文件的 SHA-256。工具不会自动下载 SDK 测试服务、模型或第三方节点。

在一个终端显式启动持久化开发服务：

~~~powershell
conda run --no-capture-output -n letsaigc-core letsaigc runtime dev-server --binary .local/runtime/temporal/temporal.exe
~~~

默认数据库是 .local/pipelines/temporal-dev.sqlite，服务地址 127.0.0.1:7233，UI 绑定 127.0.0.1。可用 --database 指定另一个本地磁盘文件。此命令在前台运行，退出后再次使用同一个数据库启动即可恢复服务历史。不要使用网络共享盘存放这些 SQLite 文件。

在第二个终端启动 Worker：

~~~powershell
conda run --no-capture-output -n letsaigc-core letsaigc runtime worker
~~~

configs/runtime/temporal.yaml 控制地址、namespace、task queue、Activity 并发、超时上限、轮询间隔、Continue-As-New 阈值和 MLflow 投影。v1 只接受回环地址，不提供远程无认证批准 API。

## 3. 跑通免费模拟流程

~~~powershell
conda run --no-capture-output -n letsaigc-core letsaigc pipeline plan --task-id demo-temporal --revisions 2 --accept-after 2
conda run --no-capture-output -n letsaigc-core letsaigc pipeline start demo-temporal
conda run --no-capture-output -n letsaigc-core letsaigc pipeline inspect demo-temporal
conda run --no-capture-output -n letsaigc-core letsaigc pipeline approve demo-temporal --fingerprint '<plan 输出的完整 SHA-256>'
~~~

模拟任务同样经过批准状态机，但不访问模型、不产生 GPU 费用。示例生成三次模拟结果，在 revision 2 接受。业务修订与网络重试有不同的操作键。

计划创建后不可变。同一 task ID 不能绑定另一个指纹；Temporal 启动也拒绝复用已经执行过的 Workflow ID。需要另一次实际任务时明确创建新计划。

批准通过本机可信 CLI 先记录请求 ID、用户身份和指纹，然后发送 Update；Workflow 主循环通过 Activity 校验该本地回执。直接伪造 Temporal Update 不会获得生成权限。Update 的 registered_pending_validation 表示已登记，最终是否通过请查询状态。

## 4. 接入现有 ComfyUI

准备已安装并通过哈希校验的模型、已提交的 recipe 和 native workflow，以及回环地址上的 ComfyUI。允许通过 COMFY_URL 指定另一个回环端口。禁用第三方 custom nodes；不要在运行此组 Worker 时让旧 Agent 或其他客户端竞争同一试点 GPU。

示例文件 configs/pipelines/temporal-sd15-smoke.json 使用 SD1.5、512×512、2 steps、固定 seed、一个输出、零修订，单次及总 GPU 预算均为 2 分钟，美元预算为 0：

~~~powershell
conda run --no-capture-output -n letsaigc-core letsaigc pipeline plan --generation-plan configs/pipelines/temporal-sd15-smoke.json
~~~

输出新的 Pipeline task ID 和指纹。审核生成参数、模型、依赖、预算和许可条件后，使用上节的 start / approve 命令。plan 本身不提交生成。

创建计划时会冻结 recipe、基础 API workflow、模型 catalog、ComfyUI lock 的哈希，并复制输入到受控素材库。图像输入只通过 ArtifactRef 进入 Temporal 历史；完整 GenerationPlan 保存在本地内容寻址文件中。模型来源、权重哈希、有效参数、seed、编译图与合同哈希仍由既有 Comfy manifest 记录。

Windows 上 ComfyUI 可能返回反斜杠形式的模型枚举值。编译器只对同一相对模型路径采用服务器提供的分隔符形式；不会将未知模型替换为别的模型。最终编译图重新计算哈希。

v1 的 Comfy 工作流接受 max_revisions=0。自动质量评价/提示词修订不属于本次接入；改变参数需要创建并批准新的生成计划。

## 5. 操作边界与故障恢复

| 阶段 | 行为 | 自动重试 |
|---|---|---|
| validate / approval | 指纹、素材、依赖和本地回执校验 | 已知业务错误不重试 |
| prepare | 校验模型、上传受控输入并编译；不提交 GPU 工作 | 未提交时可重新准备 |
| submit | 先预留预算并取得资源，再持久化 submitting，最后调用提供方 | Activity 最多尝试一次 |
| observe | 按已知请求 ID 查询；或按操作标识恢复提供方回执 | 读取失败有界重试，最多三次 |
| collect | 校验产物、原子发布素材、幂等结算 | 最多三次；不再次生成 |
| project | 写本地状态、manifest、MLflow | 有界 Activity 重试，必要时持久化等待后重试 |

Activity 的 Start-To-Close 上限分别为：批准/查询 30 秒，取消/投影 60 秒，收集 180 秒，验证/提交 600 秒；配置的统一上限可以进一步缩短。Schedule-To-Close 包含最多三次尝试与有限排队余量。Heartbeat 在入口与完成边界发送，其超时不长于单次 Activity 超时。耗时生成采用独立查询和持久化 timer，不长时间阻塞一个 wait Activity。

业务操作键由 task、step、revision 和完整计划指纹决定，与 Activity attempt、Worker 进程或 Temporal Run ID 无关。

ComfyUI /prompt 不提供本适配器可依赖的幂等键。适配器在 extra_data 写入操作标识与受限回执，恢复时搜索队列/历史，并与本地 manifest 的 task、operation 和 graph hash 交叉校验：

- 找到原请求：继续查询或收集。
- 找不到、记录已被清理、找到多个冲突回执：保持 outcome_unknown，不再次提交。
- 提交前可确定的准备失败：记录失败，释放未使用预留。
- 已提交后的产物/依赖故障：保留对账入口与资源/费用记录，不能假定原工作未发生。

查询与人工操作：

~~~powershell
conda run --no-capture-output -n letsaigc-core letsaigc pipeline inspect '<task-id>'
conda run --no-capture-output -n letsaigc-core letsaigc pipeline inspect '<task-id>' --local
conda run --no-capture-output -n letsaigc-core letsaigc pipeline reject '<task-id>' --fingerprint '<fingerprint>'
conda run --no-capture-output -n letsaigc-core letsaigc pipeline cancel '<task-id>'
conda run --no-capture-output -n letsaigc-core letsaigc pipeline reconcile '<task-id>'
conda run --no-capture-output -n letsaigc-core letsaigc pipeline rebuild-projection '<task-id>'
~~~

--local 返回可能滞后的投影，明确标记来源。reconcile 重新查询/收集，不提供强制清零、替换计划或盲目重提的入口。无法证实的请求需要保留记录并调查提供方证据，不能删除数据库或手工释放 GPU 所有权来“恢复运行”。

取消先在本地账本关闭新提交门禁，然后通知 Workflow。只有提供方确认停止/完成并正确结算后才成为 cancelled；否则保持 awaiting_reconciliation。取消请求的 HTTP 成功、Worker 退出、Activity 超时、TTL 到期都不等于远端工作停止。直接 Terminate 无法执行清理，须从账本检查遗留请求。

GPU 时间使用提供方执行起止时间；缺失时使用保守墙钟估计。提交预留整个获批单次上限，而不是乐观估计。超时尝试取消，仅确认结束后释放资源。Worker/服务长时间离线可能使取消迟到；真实超额必须如实记账并停止后续修订，不能宣称软件计时器是硬件级强制配额。

## 6. 本地数据、证据和备份

~~~text
.local/pipelines/
  ledger.sqlite
  temporal-dev.sqlite
  simulation.sqlite
  artifacts/<task>/<operation>/<role>/<sha256>
  tasks/<task>/run.json
  tasks/<task>/manifest.json
~~~

真实 Comfy 子运行沿用 .local/runs/<run>/manifest.json；父运行 ID 指向 Pipeline manifest 中的逻辑 ID，操作账本保存子 run ID、提供方请求 ID、图哈希及 ArtifactRef。父 manifest 位于本节的 tasks 目录，使用现有 RunManifest 格式，不提高现有 schema 版本。

批准、预算和资源表使用 SQLite 事务与唯一键。费用以百万分之一美元/分钟为内部整数精度，上限向下取整、预留向上取整。未知费用只列在 unsettled，一笔费用不同时进入 reserved 和 unsettled。

开启 mlflow_enabled 后，终态按稳定逻辑运行标签查找/复用 MLflow run；本地事务串行化同一账本的登记，并覆盖“MLflow 已创建、本地记录尚未保存”窗口。MLflow 不调度业务。

素材通过不可变内容引用和 SHA-256 校验；临时文件 fsync 后原子替换。Windows 文件访问支持深层 worktree 的长路径。64 KiB 是工程 DTO 载荷上限，不是 Temporal 官方容量声明。凭据字段、URL query/fragment/userinfo 和资产绝对路径不能放入 DTO；实际文件路径只在本地受控解析中使用。

备份/恢复至少同时保留：Temporal 持久化数据库、业务账本、素材库、Pipeline/Comfy manifests、所用代码/配置版本以及可查询的提供方回执。停止写入后备份，避免分别复制仍被修改的 SQLite 文件产生不一致快照。

## 7. 升级、重放和停用

本版采用显式 v1 Workflow/Activity 名称与队列兼容策略，build_id 用作 Worker 识别；没有启用服务器自动 Worker Versioning 路由。

兼容变更发布前，使用本地保存的真实历史运行 Replayer。破坏兼容的改动使用 v2 名称、独立队列及对应客户端注册，同时保留 v1 Worker、环境和素材；不能将不兼容实现投到仍有旧任务的 v1 队列。集成测试验证相同 v1 合同跨 Worker build 重启，并验证不兼容代码重放失败。

Continue-As-New 在有界查询次数后携带 PipelineRun、批准状态、取消状态和同一计划继续执行。新 Run 不重置预算、修订号、逻辑 task 或操作去重范围。对需要升级的任务保留每段 Run 历史。

停用顺序：停止创建新任务 → 逐个查询并完成/取消/对账已有任务 → 核对 resource_owners 和 unsettled 费用 → 保存历史、账本、素材及旧 Worker 环境 → 停止 Worker 和服务。不能把未完成任务同时交给旧 Agent 执行。

## 8. 验证

默认测试不会调用 GPU；运行服务测试须显式提供已验证的本地 CLI，不会自动联网下载测试服务器。

~~~powershell
conda run --no-capture-output -n letsaigc-core pytest -m "not runtime and not gpu"
$env:LETSAIGC_TEMPORAL_TEST_CLI = (Resolve-Path .local/runtime/temporal/temporal.exe).Path
conda run --no-capture-output -n letsaigc-core pytest -m "runtime and not gpu" -k temporal
conda run --no-capture-output -n letsaigc-core pytest
conda run --no-capture-output -n letsaigc-core ruff check .
~~~

真实 GPU 测试额外要求 LETSAIGC_TEMPORAL_GPU_PLAN 指向已冻结并保存在对应 PipelineService 根目录的 PipelinePlan JSON，LETSAIGC_TEMPORAL_GPU_APPROVAL 是用户批准的完整指纹；然后运行 pytest -m gpu -k temporal。测试拒绝复用已有 operation 的 GPU 验收任务。先对账失败/未知尝试，再为下一次实验建立新的具体批准计划。

故障覆盖与本次执行结果见 [验收记录](temporal-validation.md)。真实适配器协议测试、模拟故障测试和实际 GPU 验收分别记录，不能互相替代。
