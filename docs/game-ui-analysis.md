# 游戏 UI 单图解析：开发版本使用

当前已接通手动单图、SerpApi/Tavily 搜索、CPU OCR/VLM 分析、布局/文字/矩形切片/标注图和来源记录。离线合同及流程测试通过，真实预览尚未验收；T010、T017、T018 保持未完成。拆解、补图、批次与局部修订尚未开放，请勿把本页当作完整质量验收声明。进度见 [013 任务记录](../specs/013-game-ui-analysis/tasks.md)。

## 准备运行条件

在仓库根目录使用 letsaigc-core 环境。下列命令遵循项目的 mamba 约定；若遇到已知 Miniforge 激活脚本权限问题，可按本次已授权方式替换为 `conda run -n letsaigc-core --no-capture-output python -m letsaigc ...`，不修改 ACL 或使用全局 Python。

- 按 [Temporal 运行说明](temporal-runtime.md)准备已核验的本地服务及本分支 Worker。新建账本使用 v3；既存 v1/v2 不自动升级，必须停止全部写入者，再运行下方迁移。备份留在账本旁，旧任务记录不重写。
- OCR 使用 [独立环境定义](../environment/vision-ocr.yml)。操作者准备 [模型锁](../configs/runtime/vision.lock.yaml)列出的静态模型、版本、包及模型哈希、许可证据；占位 null/pending 不表示验证成功。运行时不安装或下载模型。
- OCR 服务和 Worker 使用相同的 `LETSAIGC_VISION_TOKEN` 与至少 32 字符的 `LETSAIGC_VISION_SIGNING_KEY`。可放在Git忽略的仓库 `.env` 或进程环境中，勿把值放进命令历史或记录；进程环境优先。服务仅监听回环地址。
- VLM 复用现有 `LLM_BASE_URL`、模型、凭据及计价配置。`LLM_BASE_URL` 必须是服务 URL，不能填密钥。模型/服务/计价改变后创建新计划。
- 搜索另需 `SERPAPI_API_KEY`、`TAVILY_API_KEY` 和 [搜索配置](../configs/providers/image-search.yaml)中已核实的账户计价与条款。当前实际单价和部分探测计价保持未知，因此不能直接进行双后端验收；缺搜索条件不阻塞手动解析。

仅在既存账本已停写时迁移：

```powershell
mamba run -n letsaigc-core python -m letsaigc.pipelines.migrations --writers-stopped --target-version 3
```

在已准备的 OCR 环境对应终端启动服务。使用本分支源目录，避免加载旧 worktree；此处假定上述认证环境变量已配置：

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PADDLE_PDX_CACHE_HOME = Join-Path (Get-Location) '.local/cache/paddlex'
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = 'True'
mamba run -n letsaigc-vision-ocr python -m letsaigc.vision.service --capability ocr --port 8766
```

服务与 Worker 启动后检查：

```powershell
mamba run -n letsaigc-core python -m letsaigc --json ui doctor
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
$imported = mamba run -n letsaigc-core python -m letsaigc --json ui import --image $sample | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '导入失败，请检查输出中的错误码' }
$imported.input_manifest_ref | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $refFile -Encoding utf8
$planned = mamba run -n letsaigc-core python -m letsaigc --json ui plan --input-manifest $refFile --budget $budgetFile | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '规划失败，请检查配置和错误码' }
$taskId = $planned.task_id
$fingerprint = $planned.plan_fingerprint
$planned | ConvertTo-Json -Depth 20
```

预算示例不是实际费用预测。检查计划中的输入、能力、完整指纹和逐次/总预算，决定批准后执行：

```powershell
mamba run -n letsaigc-core python -m letsaigc --json ui execute $taskId --approve $fingerprint
mamba run -n letsaigc-core python -m letsaigc --json ui inspect $taskId
```

execute 返回 accepted 表示已交给工作流；结果以 inspect 为准。输出引用指向 `.local/pipelines/artifacts` 内的不可变内容，可检查 canonical、坐标变换、合并文字、布局、矩形切片、overlay、质量状态和 manifest。manifest 的操作索引保存模型及费用证据，正文不进入 Temporal 历史。`inspect --local` 读取可能过期的本地投影。

## 搜索输入

```powershell
$planned = mamba run -n letsaigc-core python -m letsaigc --json ui plan --query 'game HUD screenshot' --max-images 1 --budget $budgetFile | ConvertFrom-Json
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
