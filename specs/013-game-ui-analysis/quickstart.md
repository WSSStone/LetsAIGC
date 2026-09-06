# 013 UI 工作流：开发定位与实施后使用

当前手动单图与SerpApi/Tavily双后端搜索已通过真实预览验收，含三例手动解析和一次OCR中断恢复；可用预览已交付，完整质量验收待完成。可用命令及本机准备条件见[开发使用指南](../../docs/game-ui-analysis.md)。本页保留完整实施后的使用目标：拆解补图、批次和正式质量验收部分尚未开放。正式参数见[CLI契约](contracts/cli.md)，实际完成状态见[任务记录](tasks.md)。


## 人工校正预览（T043—T050）

执行顺序为已完成预览→人工校正→拆解/补图→批次/正式评估。原T019—T042保留编号，新任务插入执行顺序，不按编号递增开工。

已有成功 parse 且本地账本为 v4 时可运行（旧库先按使用指南停写、备份迁移）：

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc ui review TASK_ID
```

页面显示原图、元素列表和属性。补框/改框，选择文字、图像或容器及可选标签，改字和父级，必要时撤销。保存草稿便于重开；确认后得到独立版本的布局、有效文字、切片及标注图，不发出模型请求、不改变原OCR或批准。原图不变，缺产物明确拒绝，不能通过打开页面重跑识别。

T021之后可从页面显示的已确认版本准备后续编辑计划：

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --reviewed-task TASK_ID --review-revision 1 --mode decompose --budget configs/ui-analysis/budget-example.yaml
```

`ui review` 已提供；上述 `ui plan --reviewed-task` 仍待 T021，不能提前执行。确认布局只选择校正版本；分割/补图仍形成新的具体批准。原自动流程保留且显示model来源，运行中不自动切换到最新review。真实浏览器校正验收使用已有三例开发输入并记录零推理；独立评估集不得拿校正结果当真值。完整合同见[人工校正](contracts/review.md)。

## 开发入口

在主目录的dev-game-ui工作，新PowerShell会话重新设置特性选择变量：

```powershell
Set-Location 'C:\Programs\LetsAIGC'
$env:SPECIFY_FEATURE = '013-game-ui-analysis'
& '.specify/scripts/powershell/check-prerequisites.ps1' -Json
```

[任务清单](tasks.md)是唯一现行执行状态入口；旧96项及Gxx保留映射和历史状态，依赖以新任务为准。本次修订后只运行一次只读speckit-analyze并汇报，发现项不自动修订或复核；文档生成不批准实际模型调用。

## 实施后：准备首版能力

按[Temporal运行说明](../../docs/temporal-runtime.md)准备回环服务和兼容Worker。单图步骤绑定迁移到ledger v2，双搜索额度表迁移到v3；每次停写、作一致备份并使用支持目标版本的Worker。GPU子流程的v4迁移属于后续阶段。

首版只准备独立CPU OCR、远端VLM连接、受控素材目录，以及搜索凭据/计价/条款；SAM、SDXL和Comfy不影响parse就绪。操作者准备OCR静态模型、包锁、哈希及许可，运行时不自动下载。缺搜索能力时仍可独立手动解析，双后端预览验收则必须两家均取得真实结果。

OCR服务在已准备的独立环境终端启动，读取受控素材映射、作业根和进程环境中的本地认证：

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PADDLE_PDX_CACHE_HOME = Join-Path (Get-Location) '.local/cache/paddlex'
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = 'True'
conda run --no-capture-output -n letsaigc-vision-ocr python -m letsaigc.vision.service --capability ocr --port 8766
```

```powershell
conda run --no-capture-output -n letsaigc-core letsaigc ui doctor
```

doctor逐能力报告就绪情况，不发搜索/计费探测、不下载模型。连接不通保持未就绪，不改成公开监听。

## 实施后：手动单图预览

先准备自己的截图与本地输入目录。import冻结输入，不触发OCR/VLM或搜索：

```powershell
$inputDir = 'C:\Programs\LetsAIGC\.local\ui-input'
New-Item -ItemType Directory -Path $inputDir -Force | Out-Null
$sample = Join-Path $inputDir 'screenshot.png'
$manifestFile = Join-Path $inputDir 'input-ref.json'
$imported = conda run --no-capture-output -n letsaigc-core letsaigc --json ui import --image $sample | ConvertFrom-Json
$imported.input_manifest_ref | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $manifestFile -Encoding utf8
```

审阅后准备自己的budget.yaml，以下仅示范TaskBudget结构和零GPU预算，金额不代表实际成本预测或批准：

```yaml
max_total_cost_usd: 1.00
max_iteration_cost_usd: 0.25
max_total_gpu_minutes: 0
max_iteration_gpu_minutes: 0
max_revisions: 2
```

```powershell
$budgetFile = Join-Path $inputDir 'budget.yaml'
$planned = conda run --no-capture-output -n letsaigc-core letsaigc --json ui plan --input-manifest $manifestFile --mode parse --budget $budgetFile | ConvertFrom-Json
$taskId = $planned.task_id
$fingerprint = $planned.plan_fingerprint
conda run --no-capture-output -n letsaigc-core letsaigc ui inspect $taskId --local
```

task_id和完整plan_fingerprint直接取自plan响应。UI plan只本地规划；查询改写、搜索与VLM均在执行阶段，旧Agent规划计费规则不变。确认实际输入、模型/条款、次数和预算后执行：

```powershell
conda run --no-capture-output -n letsaigc-core letsaigc ui execute $taskId --approve $fingerprint
conda run --no-capture-output -n letsaigc-core letsaigc ui inspect $taskId
```

检查来源、canonical、image-transforms、layout.json、texts.json、矩形切片和overlay。ArtifactRef对应公共素材库，文字正文与绝对路径不进入Workflow历史。首版需三个固定开发样本均真实产生上述产物；这不是原M-U1完整质量验收。

## 实施后：双后端单图搜索预览

通过进程环境或当前仓库本地.env提供SERPAPI_API_KEY与TAVILY_API_KEY。先核实两家计价、条款及额度字段；密钥值不出现在命令示例或证据中。搜索配置默认quota_aware、allowed_providers为两家、preferred_provider为SerpApi、allow_payg=false、serpapi_no_cache=true，并冻结入计划。实际单价和未核实探测价格仍为null时，不得进行真实验收。

```powershell
$planned = conda run --no-capture-output -n letsaigc-core letsaigc --json ui plan --query '中文策略游戏 战斗 HUD 截图' --max-images 1 --mode parse --budget $budgetFile | ConvertFrom-Json
$taskId = $planned.task_id
$fingerprint = $planned.plan_fingerprint
conda run --no-capture-output -n letsaigc-core letsaigc ui inspect $taskId --local
```

审阅该计划后，通过前述execute命令批准它。搜索取得的图片进入与manual相同的真实OCR/VLM链路。正常优先SerpApi，低水位或单方不可用时按冻结策略自动切换；两家共享最多3次外部search尝试、3个逻辑查询、2次供应商改变，每查询最多20候选/5下载，取得首张合格图即停止后续供给。扩大供应商集合、启用PAYGO或增加预算须新计划与批准。

额度未知、双方耗尽或缺少全部搜索凭据时明确停止，手动入口仍独立可用。预览验收分别冻结供应商各做一次有界真实搜索并解析图片；固定额度响应验证偏好、低水位、单方不可用及双方耗尽，不消耗真实账户来制造耗尽。

## 实施后：拆解与补图

此阶段才准备SAM独立环境、已核实的模型hash/许可、SDXL和原生Comfy，迁移父子绑定与共享预算到v4。SAM入口在已部署的WSL环境运行，不直接使用Windows路径：

```bash
conda run --no-capture-output -n letsaigc-vision-segmentation python -m letsaigc.vision.service --capability segmentation --port 8767
```

单独准备含正GPU额度的edit-budget.yaml。decompose请求拆解；reconstruct必须选scene_background或map_surface。默认由Agent根据实际布局与目标生成区域、保留/移除对象和预览，无须先填写选择文件：

```powershell
$editBudgetFile = Join-Path $inputDir 'edit-budget.yaml'
$planned = conda run --no-capture-output -n letsaigc-core letsaigc --json ui plan --input-manifest $manifestFile --mode reconstruct --target scene_background --budget $editBudgetFile --allow-local-revision | ConvertFrom-Json
$taskId = $planned.task_id
conda run --no-capture-output -n letsaigc-core letsaigc ui inspect $taskId --local
```

审阅后先用该根task_id和plan_fingerprint批准分析。推荐明确时inspect直接显示awaiting_approval、预览与具体分割child；从待批准项中选定一项，审阅实际范围后执行：

```powershell
$view = conda run --no-capture-output -n letsaigc-core letsaigc --json ui inspect $taskId | ConvertFrom-Json
$view.pending_approvals | Format-List
$childTaskId = '所选待批准项中的child_task_id'
$childFingerprint = '同一项中的完整plan_fingerprint'
conda run --no-capture-output -n letsaigc-core letsaigc ui execute $childTaskId --approve $childFingerprint
```

此操作同时确认所显示区域并批准对应GPU操作。分割产生实际最终mask后，inspect另列补图待批准项，需再次审阅并批准该child。上述占位文字必须替换为inspect实际输出；不能复用分割指纹批准补图。

目标含混才进入awaiting_selection。查看候选预览后，只选择编号：

```powershell
$candidateId = '当前候选编号'
conda run --no-capture-output -n letsaigc-core letsaigc ui select $taskId --candidate $candidateId
```

候选选择只登记范围并形成待批准项，不调用模型、不视为GPU批准。区域与来源hash由内部绑定处理；Agent没有人工批准权限。

### 高级区域覆盖

需要精确覆盖时使用ui select --selection FILE，与--candidate互斥。已知手动输入也可在plan附--selection；搜索输入必须先取得真实素材。以下仅是结构示例，source_id、hash和区域需来自当前授权素材：

```json
{
  "schema_version": 1,
  "sources": [{
    "source_id": "当前输入的source_id",
    "original_sha256": "当前输入的64位sha256",
    "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 256, 256]}],
    "keep_elements": [],
    "remove_elements": []
  }]
}
```

坐标是canonical左闭右开像素区域，必须在图内；引用element_id或keep/remove集合还需有效layout_ref。普通流程不用填写这些字段。批次时对实际等待的单图child覆盖：

```powershell
$selectionFile = Join-Path $inputDir 'selection.json'
conda run --no-capture-output -n letsaigc-core letsaigc ui select $taskId --selection $selectionFile
```

替换范围使旧未执行子计划的批准失效；存在已提交/未知GPU工作先取消或对账，预算与次数不清零。最终mask为零区域与canonical逐像素相同，结果分别标注原始、估计或generated。编译在具体获批执行的准备阶段进行，通过子manifest路径/hash检查，无独立只编译CLI。局部修订用枚举请求文件交给ui revise，改变mask/model/recipe要新具体批准。

## 实施后：顺序批次与正式验收

首版对多图/编辑未就绪请求明确拒绝，不静默转成单图。批次就绪后，手动import重复--image可选1—10项，去重前数量决定单图或批次，不接受搜索--max-images；精确重复保留每条映射，近似重复只提示。搜索显式--max-images 2—10才选ui_batch，由父级获取一次、顺序解析，child不重搜。普通失败按策略显示partial，批准/预算/未知结果仍遵守门禁。

随后补齐至少24例、开发16/评估8和8个已知背景（4/4）；首版三例始终在开发集。在开发集冻结阈值后独立评估，留出集不用于调整门槛。M-U1—M-U3和U-V01—U-V14全量通过才宣布完整交付。

## 实施后：结算、恢复和证据

实耗高于预留但未超原批准逐次/总限额时，usage_verdict=reservation_adjusted是非失败事件；据实结算并核算后续预留后自动继续。下一步无法预留则停止新消费、保留成果，task.status=failed且stop_reason=budget_insufficient；扩大预算需新计划和批准。实际违反批准限额才usage_verdict=budget_exceeded并关闭后续提交。旧ledger同名flag表示“超预留”，不能直接作为UI失败判据。

按需要独立选择恢复命令，以下不是必须依次执行的脚本：

```powershell
conda run --no-capture-output -n letsaigc-core letsaigc ui inspect $taskId --local
conda run --no-capture-output -n letsaigc-core letsaigc ui resume $taskId
conda run --no-capture-output -n letsaigc-core letsaigc ui reconcile $taskId
conda run --no-capture-output -n letsaigc-core letsaigc ui cancel $taskId
```

resume复用已完成产物和有效批准；reconcile查询原request，视觉回执丢失按operation查询，not_found不授权重发。unknown费用继续预留，不按零结算；cancel不证明外部已停止。保留Temporal历史、ledger、素材、任务/子运行manifest和代码配置版本，不能删证据来“恢复”。生产导出仍按既有人工审批。

## 分阶段验证

首版：三个真实开发样本、两家各一次搜索并解析、固定额度切换、无批准零调用、预留偏差继续、超批准限额停止及一次OCR登记后中断恢复。共享执行边界用固定响应验证，不以真实超额消费制造故障；搜索不可用时验证手动仍可用。

合同/来源/安全/失败测试先于对应实现；共享层集中故障矩阵，适配器只补参数和自身特例，业务链路检查产物。最终汇总有效证据，只补缺口和变更影响；纯本地任务记录代码、命令与结果，外部调用任务才要求批准、受理次数和费用证据。

以下命令属于未来实施验证；runtime/GPU测试须显式提供服务、输入、预算和批准，未就绪应skip并记录原因：

```powershell
conda run --no-capture-output -n letsaigc-core pytest -m 'not runtime and not gpu'
conda run --no-capture-output -n letsaigc-core pytest -m 'runtime and not gpu' -k ui
conda run --no-capture-output -n letsaigc-core pytest -m gpu -k ui
conda run --no-capture-output -n letsaigc-core pytest
conda run --no-capture-output -n letsaigc-core ruff check .
```

不能把预览、模拟合同或历史测试当成完整验收，也不为最终汇总重复消耗已验收的真实请求。


## 2026-09-06 编辑准备实施状态

T021—T023、T025—T026已完成，入口命令和候选选择见[编辑区域准备](../../docs/game-ui-analysis.md#编辑区域准备)。reviewed-task冻结确认版本，automatic-task显式沿用原自动布局，均不重跑OCR/VLM；核心环境无需Torch。当前编辑execute仍等待T028工作流接线，勿消费旧批准尝试执行。真实SAM/CUDA及锁定ComfyUI本机资源尚未就绪，T024/T027保持LIVE未验收。完整回归565 passed、4 skipped，不能替代真实GPU或最终质量验收。
