# 游戏 UI 单图解析：可用预览

## T030 当前入口：可选云端补图（2026-09-14）

T030 按[单图编辑 MVP 范围](t030-mvp-scope.md)收尾；下文日期较早的能力状态保留作历史说明。
新增 `--inpaint-backend openai`，默认仍为 `comfy`。云端路线复用成功的自动解析或已确认 review，
不重跑 OCR/解析，也不启动 SAM/Comfy：VLM 生成提示词后，独立批准 GPT Image 2 生图。
代码及离线测试不等于真实产品验收；此前获用户认可的是临时程序的生成结果。

使用已有确认任务及本地预算文件；下面 ID/文件名是占位，不是本次批准：

```powershell
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --reviewed-task REVIEW_TASK_ID --mode reconstruct --target map_surface --inpaint-backend openai --edit-instruction "Remove the blue player markers; preserve the adjacent white dot, terrain, grid and border." --budget BUDGET_FILE
```

预算至少为 `max_total_cost_usd: 0.11`、`max_iteration_cost_usd: 0.10`；本地 GPU 两项均可为 0。
当前分配为提示词子任务 0.01 USD、生图子任务 0.10 USD，两者共享根预算。这是批准限额，不是 provider 硬限价承诺；
按项目已配置费率结算实际 usage，不采用用户控制台显示金额覆盖项目计价，不自动扩大预算。

规划和选择不调用模型。若需要选择，沿新根使用现有 `ui select --candidate` 或 `--selection`。
`plan`/`inspect` 的 `editing.pending_approvals` 提供实际 child ID、完整指纹、预算、`details.preview_path`、
提示词和请求 profile。用户检查图和意图即可，内部文件 hash 不需要手工核验。

```powershell
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui inspect ROOT_TASK_ID
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui execute GUIDE_CHILD_ID --approve GUIDE_FINGERPRINT
```

提示词成功后，工作流生成新的生图 child；重新 inspect，检查实际提示词、输入图和生图预算，再批准：

```powershell
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui execute IMAGE_CHILD_ID --approve IMAGE_FINGERPRINT
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui inspect ROOT_TASK_ID
```

VLM 使用配置中的 `LLM_VLM_MODEL`、1024 最大输出、流式传输；`--edit-reasoning low|high` 默认 low，
可在创建新计划时明确选择 high，值随计划冻结，不修改既有计划或自动降级。生图为 `gpt-image-2`、medium、
1024×1024、PNG、opaque、n=1。两者 connect/write/pool/read 为 10/30/10/300 秒，SDK 自动重试为 0。
模型读取本工程 `.env` 的 `LLM_BASE_URL`/`LLM_API_KEY`；`LLM_IMAGE_MODEL` 如配置必须为 `gpt-image-2`。
选区周围至少保留 64px 上下文、可用时至少取 256px 窗口，实际局部缩放为 512×512 后展示并送模。
该图不是全张原截图；尺寸转换以 `details.context` 为准。生成结果完整保存，不用硬边 mask 重新合成。

`editing.image_files` 返回可打开的输出路径，`editing.children[].details.operations` 展示调用状态、
脱敏 trace 和已取得的输出路径（包括尚未结算的图片）；账本 receipt 保存 usage、费用和脱敏错误阶段。
HTTP 成功不等于完整产物成功；usage 缺失时保留已取得的图片但状态仍为未结算，不自动重发。

### 显式重试云端生图（2026-09-15）

`ui retry-image IMAGE_CHILD_ID` 是零推理规划入口：支持同一根下至多两次串联的额外云端生图尝试，
复用原图片、成功 VLM 提示词、参数和 selection。它不创建新根、不重跑 VLM、不消费批准。
只接受已结束本地调用、无可恢复成功产物、无资源占用的 `outcome_unknown` 生图；
运行中、链外未决 operation、已有有效图片/receipt、第三次追加重试均拒绝。禁止分叉或循环。

输出包含新 child / fingerprint、原 operation、`retry.budget_before/budget_after` 和完整请求预览。
新额度为原根上限加一次生图预算，修订计数上限加 1；其他预算字段不变。
例如 0.11 → 0.21 USD、修订 0 → 1，原 0.10 USD unknown 仍计入合计。
首次重试已经独立批准并以 unknown 结束后，可用
`ui retry-image FIRST_RETRY_CHILD --image-budget 0.15` 零调用规划最后一次后续重试。
这会冻结 0.21 → 0.36 USD、逐次 0.10 → 0.15 USD、修订 1 → 2 的预算提案；
两次旧 unknown 均保留。新请求仅调整预算字段，实际图片、提示词、模型和生成参数不变。
预算参数必须显式给出，不允许用第二次调用抹掉第一次批准的范围或修改旧计划。
只有随后通过 `ui execute NEW_CHILD --approve NEW_FINGERPRINT` 消费具体批准，额度才生效。
原根计划和 v5 表结构不变；有效预算从不可变 retry 计划及已消费批准推导，重复批准不会重复加额度。
`inspect.editing.shared_total_limit` 显示有效额度，旧根 `budget` 仍显示创建时额度。

这是用户显式接受另一次可能收费请求的窄范围例外，不代表原请求未受理，也不解除其他 unknown 的拦截。
Temporal 的 `ui-cloud-image-retry-v1` 版本分支允许等待对账的原工作流接收新目标，批准 activity 验证后才执行。
SDK 和提交 activity 不自动重试。普通旧任务继续只观察/收集，不能借旧指纹发起第二次请求。

生图异常记录图片数量、是否仅返回 URL、编码长度、解码后格式/尺寸、校验/保存阶段和已取得的 usage；
不记录 URL、完整错误正文或 base64；现支持安全下载 URL 结果（见下文）。有界候选字节保留为本地诊断 artifact，
未通过校验不得当作有效 PNG。原 2026-09-14 响应未保存，不能用新增分类反推它的具体错误。
无 provider request ID 时显示为空，`local_result` 只标识本地持久化结果，不冒充提供方 ID。
云端图无“框外逐像素不变”保证；是否保留正确、修补自然由用户检查。

当前已接通手动单图、SerpApi/Tavily 搜索、CPU OCR/VLM 分析、布局/文字/矩形切片/标注图和来源记录。三例真实手动链路、SerpApi/Tavily两家搜索来源解析及一次OCR中断恢复已验收，可用预览已通过，完整质量验收待完成。拆解、补图、批次与局部修订尚未开放，请勿把本页当作完整质量验收声明。进度见 [013 任务记录](../specs/013-game-ui-analysis/tasks.md)。

分析返回 `invalid_analysis` 时不会继续生成布局和切片，已知模型用量仍正常结算。内部analysis证据的 `validation_failure_stage` 与 `validation_failure_reason` 分别记录失败阶段和固定原因码；成功时为null。旧版本缺少这两项的记录不能回溯推断原因，也不能用回归测试通过替代真实样本成功。模型原始正文和异常文本不进入该诊断记录。

新计划同时冻结证据枚举策略：VLM返回的evidence_ids仅允许引用该图片视图和原始OCR文字的ID，避免把生成的元素ID或虚构ID当作来源。仍保留本地严格校验；提供方不遵守Schema时继续拒绝并记录费用，不把无效引用静默改成有效引用。修复后阿波罗祝福UI已成功产生25个元素、18条文字和25张矩形切片；完整质量仍pending。

## 准备运行条件

在仓库根目录使用 letsaigc-core 环境。下列命令统一使用 `conda run --no-capture-output -n ...`，避免 Conda 捕获输出时受 Windows 终端编码影响；不修改 ACL 或使用全局 Python。

- 按 [Temporal 运行说明](temporal-runtime.md)准备已核验的本地服务及本分支 Worker。新建账本使用 v3；既存 v1/v2 不自动升级，必须停止全部写入者，再运行下方迁移。备份留在账本旁，旧任务记录不重写。
- OCR 使用 [独立环境定义](../environment/vision-ocr.yml)。操作者准备 [模型锁](../configs/runtime/vision.lock.yaml)列出的静态模型、版本、包及模型哈希、许可证据；占位 null/pending 不表示验证成功。运行时不安装或下载模型。
- OCR 服务和 Worker 使用相同的 `LETSAIGC_VISION_TOKEN` 与至少 32 字符的 `LETSAIGC_VISION_SIGNING_KEY`。可放在Git忽略的仓库 `.env` 或进程环境中，勿把值放进命令历史或记录；进程环境优先。服务仅监听回环地址。
- VLM 复用现有 `LLM_BASE_URL`、模型、凭据及计价配置。`LLM_BASE_URL` 必须是服务 URL，不能填密钥。模型/服务/计价改变后创建新计划。
- 新计划的 VLM 请求 profile 默认使用 `reasoning_effort=high`、`image_detail=high`、`provider_image_encoding=jpeg-rgb-q90-v1`、`correlation_strategy=operation-id-header-v1`、`response_transport=streaming-response-v1`，以及 connect/read/write/pool 分别为 10/300/30/10 秒的超时。可在规划前用 `LLM_VLM_REASONING_EFFORT`（`low|medium|high|xhigh|max`）、`LLM_VLM_IMAGE_DETAIL`（`low|high`）、`LLM_VLM_RESPONSE_TRANSPORT`（`streaming-response-v1|raw-response-v1`）、`LLM_VLM_CONNECT_TIMEOUT_SECONDS`、`LLM_VLM_REQUEST_TIMEOUT_SECONDS`（read）、`LLM_VLM_WRITE_TIMEOUT_SECONDS` 和 `LLM_VLM_POOL_TIMEOUT_SECONDS` 配置；所有值会冻结进 plan fingerprint。流式传输仅改变 Responses 的 HTTP/SSE 传输，不改变严格 JSON schema 或本地校验。历史缺少关联、传输和分离超时字段的计划继续使用原请求路径及统一超时。`plan`/`inspect` 会显示不含密钥的 `request_profile`；真实执行仍只接受该新 fingerprint 的明确批准。VLM 提交证据只保留 operation/client/provider request ID、response ID、请求 hash/字节数、阶段、固定错误分类、usage 和费用，不保存 endpoint、密钥、请求正文、图片 base64、provider 错误正文或完整响应。
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

补图准备策略修复后，可用 `ui reprepare-inpaint SEGMENT_CHILD_ID` 从已成功且确认释放的 SAM 产物零推理重建提案。
该命令仅接受尚无批准记录、尚无 operation 的补图；不重跑 SAM、不改变选择或根预算、不清理历史。
新提案的 image/mask、坐标变换及指纹需要重新展示并批准。旧提案保持不可变，新子任务实际获批时才由账本使旧待执行子任务失效。
重复准备同一策略与输入返回同一计划。已有批准、失败、成功或 unknown 的补图不得通过此入口重建。

补图批准按回执中的 task ID 和完整 fingerprint 绑定目标，不按候选创建顺序选择。
工作流活动校验目标及已释放的父 SAM 后，原子消费批准并替代旧待执行候选；失败不改变旧候选。
`ui execute` 的 `accepted` 仅表示工作流收到请求：`approval_recorded`、`workflow_received`
与 `provider_acceptance` 分开报告，不能据此宣称 GPU 已生成。重复同一批准使用同一回执及 operation，
历史 unknown 仍只允许恢复观察，不重新提交。旧 Temporal 历史保留并通过版本分支回放。

Windows T030 2026-09-10 现场摘要：原指纹补图已沿原根任务完成一次，真实 Temporal 根状态
`succeeded`，1920×1080 无损重建的 mask 外变化像素为 0，账本费用 $0、GPU 0.420917 分钟。
模型释放记录使用稳定运行时残留判据（10,888,454 bytes），不是 CUDA 零占用证明；服务随后关闭。
蓝色玩家标识已移除，但局部有明显色块，不能据此判定质量通过。`ui inspect --local` 仍可能返回
旧失败 run 的投影，当前结果应与 Temporal 当前 run 及 operation 核对；投影恢复问题尚待修复。
本地复核材料位于 `.local/validation/ui-analysis/t030-windows/`：
`bound-inpaint-operation.json`、`bound-inpaint-measurements.json`、`bound-inpaint-temporal-history.json`
及 `bg3-blue-before-after.png`；均不进入 Git。此记录不代表 T030 全部完成。

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

### 云端图片 URL 结果收集

GPT Image 2 兼容提供方可以返回 base64 或图片 URL。适配器优先读取 base64；仅在没有 base64 时，
用独立、无模型凭据的客户端下载 URL，再沿相同流程保存候选、校验 1024×1024 PNG、保存产物与回执。
这一步只是获取已生成结果，不再次调用生图。模型输入、参数、批准指纹及计费计算方式不变。

下载只接受公网 HTTPS，沿用现有 DNS 地址固定和 TLS 主机名校验；最多 3 次逐跳校验的重定向、
25 MiB 文件及 60 秒传输预算，不使用环境代理、不转发 API key/认证头或 Cookie、不接受压缩传输。
日志和回执只记录下载状态码、字节数、固定错误分类，不记录完整 URL、签名参数或错误正文。
生成 HTTP 状态与下载状态分开记录。缺 usage 或超过原批准预算时仍保留图片，不擅自结算为成功。

下载/校验失败保留 unknown 与原 usage，不自动重发生成；已保存回执可离线读回。
签名 URL 仅在本次调用内存中使用，不持久保存，因此中断在下载完成前或链接过期后，
本地不能自动恢复该 URL。本次修复不恢复之前未保存的链接，也不授权新的真实调用。

离线复核：`tests/unit/test_cloud_image_download.py`、`tests/integration/test_ui_cloud_editing.py`。

### 智能吸附

画布工具栏的“吸附”控制移动、画框和角点缩放；“边缘”“中心”“画布边界”“等间距”分别控制参照类型，默认全部开启。拖动时按住 Ctrl 临时反转吸附开关，松开恢复。输入框内按键不改变吸附设置。设置仅保留在当前页面，保存草稿不会重置，刷新恢复默认。

粉色参考线显示实际命中的对齐关系，参照框同时高亮；等间距显示两段间距及原图像素数。等距支持插入相邻框之间或向序列两端延续；文字框也可作为参照。参考线不参与鼠标命中、不保存到草稿或导出图。吸附进入/释放距离为 6/10 屏幕像素，缩放后保持相同手感；无法用整数坐标精确满足的中心/间距不吸附。

移动保持宽高，画框和缩放仅调整活动端点。单次拖动只产生一次撤销记录；Escape、失焦、指针取消或切换工具恢复拖动前状态。手输坐标不吸附。所有操作均为本地计算，不产生 OCR/VLM/GPU 调用。
# T030 当前验收状态（2026-09-15）

T030 已按用户确认的单图编辑 MVP 完成：真实产品云端补图成功，用户视觉验收通过。
完整回归792 passed、24 skipped；扩展模式/恢复矩阵与24例正式质量仍待验收，不开展T031。
范围、费用、保留的unknown及Windows/Mac交接见 [T030收尾](t030-windows-closeout.md)。
此前段落中的未完成状态为阶段性记录，不应替代上述当前结论。
