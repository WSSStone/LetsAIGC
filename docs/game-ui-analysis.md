# 游戏 UI 单图解析：可用预览

当前已接通手动单图、SerpApi/Tavily 搜索、CPU OCR/VLM 分析、布局/文字/矩形切片/标注图和来源记录。三例真实手动链路、SerpApi/Tavily两家搜索来源解析及一次OCR中断恢复已验收，可用预览已通过，完整质量验收待完成。拆解、补图、批次与局部修订尚未开放，请勿把本页当作完整质量验收声明。进度见 [013 任务记录](../specs/013-game-ui-analysis/tasks.md)。

分析返回 `invalid_analysis` 时不会继续生成布局和切片，已知模型用量仍正常结算。内部analysis证据的 `validation_failure_stage` 与 `validation_failure_reason` 分别记录失败阶段和固定原因码；成功时为null。旧版本缺少这两项的记录不能回溯推断原因，也不能用回归测试通过替代真实样本成功。模型原始正文和异常文本不进入该诊断记录。

新计划同时冻结证据枚举策略：VLM返回的evidence_ids仅允许引用该图片视图和原始OCR文字的ID，避免把生成的元素ID或虚构ID当作来源。仍保留本地严格校验；提供方不遵守Schema时继续拒绝并记录费用，不把无效引用静默改成有效引用。修复后阿波罗祝福UI已成功产生25个元素、18条文字和25张矩形切片；完整质量仍pending。

## 准备运行条件

在仓库根目录使用 letsaigc-core 环境。下列命令统一使用 `conda run --no-capture-output -n ...`，避免 Conda 捕获输出时受 Windows 终端编码影响；不修改 ACL 或使用全局 Python。

- 按 [Temporal 运行说明](temporal-runtime.md)准备已核验的本地服务及本分支 Worker。新建账本使用 v3；既存 v1/v2 不自动升级，必须停止全部写入者，再运行下方迁移。备份留在账本旁，旧任务记录不重写。
- OCR 使用 [独立环境定义](../environment/vision-ocr.yml)。操作者准备 [模型锁](../configs/runtime/vision.lock.yaml)列出的静态模型、版本、包及模型哈希、许可证据；占位 null/pending 不表示验证成功。运行时不安装或下载模型。
- OCR 服务和 Worker 使用相同的 `LETSAIGC_VISION_TOKEN` 与至少 32 字符的 `LETSAIGC_VISION_SIGNING_KEY`。可放在Git忽略的仓库 `.env` 或进程环境中，勿把值放进命令历史或记录；进程环境优先。服务仅监听回环地址。
- VLM 复用现有 `LLM_BASE_URL`、模型、凭据及计价配置。`LLM_BASE_URL` 必须是服务 URL，不能填密钥。模型/服务/计价改变后创建新计划。
- 搜索另需 `SERPAPI_API_KEY`、`TAVILY_API_KEY` 和 [搜索配置](../configs/providers/image-search.yaml)中已核实的账户计价与条款。仓库默认配置保留未核实单价，实际验收使用本地冻结的账户计价，结束后恢复默认配置；新运行仍须核实自己的账户及探测计价，缺搜索条件不阻塞手动解析。

仅在既存账本已停写时迁移：

```powershell
conda run --no-capture-output -n letsaigc-core python -m letsaigc.pipelines.migrations --writers-stopped --target-version 3
```

在已准备的 OCR 环境对应终端启动服务。使用本分支源目录，避免加载旧 worktree；此处假定上述认证环境变量已配置：

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PADDLE_PDX_CACHE_HOME = Join-Path (Get-Location) '.local/cache/paddlex'
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = 'True'
conda run --no-capture-output -n letsaigc-vision-ocr python -m letsaigc.vision.service --capability ocr --port 8766
```

服务与 Worker 启动后检查：

```powershell
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui doctor
```

doctor 只检查本地配置与回环就绪状态，不发送付费探测。SAM、Comfy 和批次显示未就绪不影响单图 parse。

每项能力同时返回 `reasons`，例如 `model_lock_unverified`、`authentication_not_configured`、`endpoint_invalid`、`service_unreachable`、`search_pricing_unverified` 和 `probe_pricing_unverified`。这些是稳定的处理线索，不包含地址、密钥或账户响应正文；`credentials_configured: true` 也只表示存在非空配置，不能证明凭据有效。

## 手动导入、规划、批准与检查

准备自己的截图。import 保存输入副本及来源；plan 冻结模型、策略和预算，两者均不调用 OCR/VLM：

```powershell
$inputDir = Join-Path (Get-Location) '.local/ui-input'
New-Item -ItemType Directory -Path $inputDir -Force | Out-Null
$sample = Join-Path $inputDir 'screenshot.png'
$refFile = Join-Path $inputDir 'input-ref.json'
$budgetFile = 'configs/ui-analysis/budget-example.yaml'
$imported = conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui import --image $sample | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '导入失败，请检查输出中的错误码' }
$imported.input_manifest_ref | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $refFile -Encoding utf8
$planned = conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --input-manifest $refFile --budget $budgetFile | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '规划失败，请检查配置和错误码' }
$taskId = $planned.task_id
$fingerprint = $planned.plan_fingerprint
$planned | ConvertTo-Json -Depth 20
```

预算示例不是实际费用预测。检查计划中的输入、能力、完整指纹和逐次/总预算，决定批准后执行：

```powershell
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui execute $taskId --approve $fingerprint
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui inspect $taskId
```

execute 返回 accepted 表示已交给工作流；结果以 inspect 为准。输出引用指向 `.local/pipelines/artifacts` 内的不可变内容，可检查 canonical、坐标变换、合并文字、布局、矩形切片、overlay、质量状态和 manifest。manifest 的操作索引保存模型及费用证据，正文不进入 Temporal 历史。`inspect --local` 读取可能过期的本地投影。

## 搜索输入

```powershell
$planned = conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --query 'game HUD screenshot' --max-images 1 --budget $budgetFile | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '规划失败，请检查输出中的错误码' }
$taskId = $planned.task_id
$fingerprint = $planned.plan_fingerprint
```

审阅后使用同一 execute/inspect 流程。计划冻结供应商范围；批准后根据已知额度、低水位及冷却状态选路。SerpApi 固定 `no_cache=true`，成功搜索按已核实单价结算一个请求；Tavily 固定 basic，排除 PAYGO。未知的探测/归档费用不能当作免费，缺计价时该能力不可用。

当前使用原查询，模型查询规划计数为 0。候选先根据供应商声明的标题/描述、尺寸和对比度做本地筛选，记录筛选及下载结果；这不是已核实的游戏内容语义。首张合格图送入同一 OCR/VLM 链，完整质量仍 pending。每查询候选/下载至多 20/5，根任务搜索至多 3 次、切换至多 2 次，取得单图即停止。

## 恢复与当前限制

`ui resume TASK_ID`、`ui reconcile TASK_ID` 查询和继续原任务，不提高预算或重发未知搜索；`ui cancel TASK_ID` 先关闭提交门禁。已取得图片与 OCR 结果可复用。若搜索已返回但瞬时图片 URL 在中断中丢失，会报告 `input_resupply_required`；不会自动付费重搜。SerpApi 只读归档适配器已实现，但缺核实的归档计价时不可用。

实际费用超过预留而未超过批准限额时记录 `reservation_adjusted`，核算剩余预算后继续；实际超批准限额才 `budget_exceeded`。未知用量保留预留。资源检查采用输入/响应容量上界及执行前磁盘/RAM检查，工作流累计活动时间；真实推理峰值测量仍属待验收内容。

保留 `.local/pipelines`、`.local/vision/ocr/jobs` 及原有会话/运行证据，勿为恢复清空账本。输入、批准、费用与未知操作不是可随意重建的缓存。

## 2026-09-06 预览验收范围

三例手动截图来自Hades II、崩坏：星穹铁道和Clash Royale。SerpApi与Tavily分别真实搜索并下载到同一张Hades II Apollo祝福选择截图，均完成独立批准的解析；不能将这算作两个不同风格样本。五份成功解析共132张矩形切片，全部与对应原图矩形区域逐像素一致。来源页由供应商返回时记录；Tavily该结果未关联网页，保留unknown，不从另一家结果补造。

Tavily这次basic搜索使用1credit，搜索及probe按本账户免费额度和10次Usage实测记录为0USD，解析账本费用0.005586USD。用户确认该10次探测前后页面仍0/1000且费用不变，仅作为本次有界验收依据，不能视为官方永久免费保证；额度或计价复核过期后须重新核实。五份成功解析费用合计0.030470USD，历史已结算失败费用0.011492USD另列；旧失败任务仍有0.25USD未知费用预留，未重试或清账。

预览不包含正式CER/IoU质量结论。可见限制包括小图文字和装饰边缘精度、部分UI细节未独立分离、矩形切片保留背景，以及未知素材许可。当前不支持透明资产拆解、背景恢复、批次和局部修订。OCR/VLM失败会保留现场记录；`awaiting_reconciliation`表示受理或费用未知，应查提供方证据，不能重复执行来消除未知。

已有现场证据可通过以下命令复核，无新搜索或推理：

```sh
LETSAIGC_TEMPORAL_TEST_CLI="$PWD/.local/runtime/temporal/temporal" \
LETSAIGC_UI_LIVE_EVIDENCE="$PWD/.local/validation/ui-analysis/t010-commercial-2026-09-06" \
LETSAIGC_UI_SEARCH_EVIDENCE="$PWD/.local/validation/ui-analysis" \
conda run --no-capture-output -n letsaigc-core pytest --ui-live
```

汇总在本地 `.local/validation/ui-analysis/preview-acceptance.json`，不含密钥和模型权重；Git不携带现场证据。换机后必须恢复并核实证据与运行资源，不能只凭本页勾选现场验收。

## 人工校正预览

已有成功 parse 可直接打开。仅需现有 letsaigc-core 和本地原图/解析产物；无需 Torch、OCR/VLM 服务或新的推理批准。

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc ui review TASK_ID
```

默认启动本机浏览器和仅监听 127.0.0.1 的临时服务。保持终端运行，结束时 Ctrl+C；关闭页面不取消原分析任务。会话认证信息不会打印，`--no-open`只启动服务并输出无秘密地址，不会提供可复制的认证链接。会话失效时重新运行命令。

若提示 `migration_required`，先停止所有账本写入者（包括 Temporal worker 和校正服务），再执行下列命令；程序会保留 SQLite 备份，迁移失败自动回滚。迁移成功后重启 worker。本机已完成 v4 迁移，不需重复。

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc.pipelines.migrations --writers-stopped --target-version 4
```

页面左侧选择元素，中间画框、移动、缩放或拖动角点，右侧修改整数坐标、文字/图片/容器/其他类型、图标/立绘等标签、父级、文字关联与锁定。输入失焦时应用属性；原始 OCR 文字另行显示，人工文字不会获得虚构 OCR 分数。静态截图不能证明 Live2D 工程类型。

**保存草稿**保留可重开的修改；**确认版本**生成独立的布局、有效文字、原图矩形切片和标注图。未保存修改不能确认。确认不是生成批准，也不会改动原图、原始模型结果或旧任务费用。锁定项先解锁；删除父项时页面保留子项并解除父关系。Ctrl/Cmd+Z 撤销，Shift+Ctrl/Cmd+Z 重做，Delete 删除选中项，Escape 取消拖动；文本输入中 Delete 不删除框。

两个页面同时保存时，后提交的旧版本返回 `review_conflict`，本页修改保留。可先记录改动，再“重新载入草稿”后人工重做。历史版本可“恢复为新草稿”，不会删除旧版本。确认过程中进程退出，重新启动该任务的 review 服务会从账本恢复同一请求；未完整物化并校验前不推进已确认版本。

确认结果标记 `human_assisted`，原自动输出独立保留，不能把人工校正直接充当评估真值。确认布局可用于下面的编辑流程；局部 OCR/VLM 重读和编辑的真实 GPU 端到端验收仍待完成。

本地验收 `.local/validation/ui-analysis/review-acceptance.json` 记录 Hades II、星穹铁道、Clash Royale 三例、84 个像素一致切片、双页面冲突、缩放/快捷键/历史恢复及确认发布前实际进程中断。新增搜索/OCR/VLM/GPU 调用均为 0；旧 unknown 0.25 USD 未清账。这是工作流预览验收，完整 UI 质量仍待评估。

人工校正最终完整回归：340 passed、3 skipped（GPU1、Windows2），59.78秒；ruff及diff检查通过。临时验收页面和服务已关闭，按上述命令即可开启新会话。

## 编辑区域准备

复用人工确认版本或原自动解析布局，生成区域候选及 PNG 预览。该步骤只读已有图像与布局；不需要 Torch、OCR/VLM 服务或新推理。省略 `--review-revision` 时只在规划时解析一次最新确认版本，后续人工修改不会影响已冻结计划。

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --reviewed-task TASK_ID --mode decompose --budget configs/ui-analysis/budget-example.yaml
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --automatic-task TASK_ID --mode decompose --budget configs/ui-analysis/budget-example.yaml
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui select NEW_TASK_ID --candidate candidate-1
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui inspect NEW_TASK_ID --local
```

两种来源入口互斥。原自动入口仅接受已成功完成的单图 parse，保留 `automatic` 来源；人工入口保留 `human_assisted` 版本及来源证明。规划返回新的 `task_id`、预算、指纹和候选的 `preview_ref`。候选更换会产生新的选择版本，重复选择同一候选不增加版本。高级 `ui select --selection FILE` 与 `--candidate` 二选一，可信层补全原图 hash 和已冻结布局引用。

背景准备使用 `--mode reconstruct --target scene_background` 或 `map_surface`。没有明确目标时保留 `awaiting_selection`，不猜测整图背景。默认推荐保留文字；`--remove-text` 才把文字纳入移除提案。选择与预览均不是 GPU 批准。

编辑流程现在展示具体分割子任务及完整指纹。单个明确推荐可直接批准，不必先执行 `select`；根解析指纹不能授权分割。分割完成并确认 SAM 释放后，系统根据实际结果生成 image/mask、变换、模型和 recipe 的补图子计划，再单独等待批准。两步沿同一根任务顺序运行并共享总预算。替换选择会使旧待执行子批准失效；已有请求受理或状态未知时先恢复观察，不能用新选择绕过。

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui execute SEGMENT_CHILD_ID --approve FULL_SEGMENT_FINGERPRINT
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui inspect ROOT_TASK_ID --local
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui execute INPAINT_CHILD_ID --approve FULL_INPAINT_FINGERPRINT
```

以上为命令模板，不能使用历史 task ID 或指纹。规划/检查会列出子任务输入、单轮/总上限和根组总上限；示例预算文件的 GPU 上限为 0，只适合零 GPU 的准备工作。真实分割须先由操作者指定正数 GPU 预算，并核对每份新子计划。拆解输出保留分割原始证据及估计资产，补图保留生成候选和回映结果；空最终 mask 返回原图，零生成。

编辑执行要求显式离线迁移后的 v5 账本、运行中的 Temporal worker、核验后的 SAM CUDA 服务和原生 ComfyUI 模型资源。现有 Mac 账本仍为 v4，返回 `migration_required`；代码不会自动迁移。`ui doctor` 分别报告分割和补图的真实就绪条件。Mac CPU 的节点验收不满足 CUDA 生成及释放证明，缺资源时不会降级执行。核心环境无需新增 Torch。

T024已在Windows CUDA环境用具体子计划完成受限固定样本验收：真实SAM仅受理一次，实际0.314051 GPU分钟、0 USD，释放后CUDA已分配字节为0；本地证据位于`.local/validation/ui-analysis/t024-windows/segmentation-runtime.json`。固定样本是开发用合成HUD，不是商业UI或24例正式质量证据，Windows本地模型、账本和批准不随Git迁移。T028使用替身验证编辑接线，真实商业截图拆解/补图及质量仍由T030和后续任务验收。

## 显式局部修订

需要局部模型复核时，在新编辑根计划中指定 `--allow-local-revision`。此开关只允许后续规划；每个模型子任务仍展示具体输入、完整指纹和预算，等待单独批准。它不能补加到已冻结的旧计划中。

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui revise ROOT_TASK_ID --action reread_text --target-id TEXT_ID
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui revise ROOT_TASK_ID --action review_region --target-id ELEMENT_ID
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui execute REVISION_CHILD_ID --approve FULL_REVISION_FINGERPRINT
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui revision-accept ORIGINAL_REVIEW_TASK_ID --suggestion SUGGESTION_ARTIFACT_ID
```

`reread_text` 针对一个文字区域，`review_region` 针对指定元素区域；可重复 `--target-id` 指定多个元素。模型结果保留为不可变建议和原始证据，不修改现有审阅版本。`revision-accept` 是显式人工采纳，只产生新草稿；锁定字段及旧版本冲突仍会拒绝。新草稿需要另行确认后才能用于新编辑计划，已冻结的旧计划不受影响。

`adjust_segmentation` 以成功的分割子任务为基准，通过 `--parameters FILE` 提供目标元素的 `prompts`；重新分割产生的最终 mask 仍须形成新的补图批准。`regenerate` 以成功的补图子任务为基准，参数文件只允许 `prompt`、`negative_prompt`、`seed`。模型、recipe、mask 或其他采样参数不能借局部修订替换。相同请求重复规划复用具体子任务；需要下一次独立修订时显式递增 `--base-revision`，根费用、模型调用次数和 GPU 修订上限不会重置。

这些命令仍需 v5 账本和相应服务就绪；本机 v4 账本不会自动迁移。真实模型建议与 GPU 编辑效果留待 T030 验收。

## macOS ComfyUI 节点验收环境

本机为 T027 单独部署了 `letsaigc-comfy`，Python 3.12、PyTorch 2.9.1、torchvision 0.24.1、torchaudio 2.9.1。源码位于 `.local/runtime/ComfyUI`，固定 ComfyUI v0.34.2 / `169fcf35a2fc163fec31338b816503ddac0d3fcf`。Apple Silicon 选择 `configs/runtime/comfyui.macos-arm64.lock.yaml`；其他平台继续使用原锁，缺少 Mac 锁时不回退到 CUDA 包配置。

该 Mac 配置使用 CPU，并禁用第三方 custom nodes 和远端 API nodes，用于只读 `/object_info` 检查。核心环境没有新增 Torch；未下载 SDXL 权重，未提交生成。它不表示 MPS 推理、模型效果或 GPU 资源交接已经验收，也不能替代 Windows T024。

安装资源保留在本机，需要使用时在仓库根目录启动：

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc comfy serve
```

只监听 `127.0.0.1:8188`，终端 Ctrl+C 停止。启动前检查端口，保留用户已有服务。另开终端做专用只读验收：

```sh
LETSAIGC_UI_INPAINT_OBJECT_INFO_LIVE=1 \
conda run --no-capture-output -n letsaigc-core pytest --ui-live tests/integration/test_ui_inpaint_object_info.py -q
```

验收同时核对源码提交、干净 checkout、监听进程及其实际环境、节点模块/端口和 mask 参数。macOS 上进程检查需相应系统权限；无法核实时明确跳过，不冒充通过。官方 `ImageToMask` 来自 `comfy_extras.nodes_mask`，属于内置节点；`grow_mask_by` 默认6，但工作流显式指定0，验收检查接口是否允许0。

环境版本清单位于 `.local/locks/letsaigc-comfy-macos-arm64.txt`，其 SHA 只证明清单内容，不代表全部 wheel 字节已逐一校验。运行证据与日志位于 `.local/validation/ui-analysis/t027-macos-2026-09-06/`，早期未就绪记录保留。换机须重建环境与本地模型路径配置，不能把 Git 中的验收状态当作新机已经部署。
