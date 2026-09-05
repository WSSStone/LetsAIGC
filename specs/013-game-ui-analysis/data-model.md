# 013 游戏 UI 解析：数据模型

本文件定义拟实现的领域合同，尚未加入运行代码。按首版单图/搜索→编辑→批次分期实现，不要求预览提前构造全部实体。需求依据为 [spec.md](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/spec.md)，执行边界见 [execution.md](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/contracts/execution.md)。

## 1. 公共类型与版本策略

- 继续使用现有 `Identifier`、64 位小写十六进制 `Digest`、不可变 `ArtifactRef`；服务 DTO 总大小不超过 64 KiB。路径、原始 URL、OCR 正文和用户说明仅存在受控本地素材中，不放入历史 DTO。
- 新 UI 模型使用 Pydantic v2，`extra=forbid`、有限数值、显式 `schema_version=1`。索引、比例和预算不可为 NaN/Infinity，整数不接受布尔值。
- 本地内容模型允许受控文字和来源字段；传输模型额外应用现有 `validate_payload`。不能直接让保存 OCR 正文的内容模型继承会误拒绝普通文本的传输模型。
- 既有 `PipelinePlan`、`TaskBudget`、`GenerationPlan` v1 的字段和序列化保持不变。UI 入口为 `TaskBudget.max_revisions` 显式赋 2，不沿用类型默认 10 或旧预算配置的 3。
- 新 `MaskedGenerationPlan` 使用 `schema_version=2`，保留生成计划必要字段并增加强类型 `image_mask`；只由新版 UI/编译分派器读取。旧无 mask 计划始终按 v1 解析与计算原指纹。

## 2. 素材供给

| 实体 | 字段与关系 | 约束 |
|---|---|---|
| ManualUIInput | `kind=manual`、`inputs: ArtifactRef[1..10]`、`metadata_ref?` | 输入已经可信入口登记；当前任务范围；不接受任意路径 |
| SearchUIInput | `kind=search`、`query_ref`、`criteria_ref`、`routing_policy_ref`、`limits`、`max_images=1` | max_images为1—10的根选图上限；查询非空，内容只在素材内；拒绝顶层 manual/query 混用 |
| UIInputSpec | 上述判别联合 | 一个请求只有一个 kind |
| UIProvenance | `origin`、`acquisition_method`、`search_backend?`、`acquired_at`、安全来源页、观察字段、用户声明及提供方声明 | 原始签名 URL 和凭据不持久化；许可未知保持 unknown |
| UISource | `source_id`、`original_ref`、`provenance_ref`、`input_entry_ids` | 只有验证通过的图片可出现；原图与来源分开 |
| UIProvisionResult | `schema_version`、`provider_id`、`provider_version`、`status`、`sources`、`index_ref`、`errors_ref?` | ready/partial/empty/unavailable；失败项不伪造 source |
| UIInputManifest | 有序输入条目、来源及逐项状态、精确重复映射 | 保留用户选择顺序；原始数超过 10 时不先去重再放行 |

标题、游戏名、说明和用户宣称的使用条件为 `user_declared`；格式、尺寸、哈希为 `observed`；供应商标题与尺寸为 `provider_declared`。同一内容跨来源只共用字节及已授权计算，不能覆盖 provenance。

导入使用任务作用域 `intake_id`，只存素材而不注册执行批准。`ui plan` 分配正式 task_id 后将有权限的素材登记到该作用域，保留源素材边。跨任务物理字节可复用，但对外引用必须满足原 `ArtifactRef.task_id` 与计划 task_id 一致的约束。

## 3. 请求与计划载荷

`UIAnalysisRequest` 包含：

| 字段 | 合同 |
|---|---|
| `input` | UIInputSpec |
| `output_mode` | parse / decompose / reconstruct |
| `language` | auto / zh / en；影响分析而非改写原文 |
| `reconstruction_target` | reconstruct必填scene_background / map_surface；parse/decompose不得设置 |
| `selection_mode` | none / bound / deferred；parse为none，编辑显式文件覆盖为bound，默认deferred表示布局后自动提案，不表示必须手动提交选择 |
| `selection_ref?` | bound时是可信入口登记的UISelection；none/deferred根计划为空。deferred在已批准分析后自动产生候选，明确方案直接生成具体待批准子计划；分割/补图子请求引用已冻结选择 |
| `text_assets`、`remove_text` | 默认false；text_assets仅decompose/reconstruct可启用，remove_text仅reconstruct可启用；非法模式组合拒绝，不静默忽略 |
| `allow_local_revision` | 默认 false，启用后仍受动作和次数约束 |
| `budget` | 现有 TaskBudget，全部金额和 GPU 字段必填 |
| `resources` | 本计划冻结的像素、内存、磁盘、网络、时长和输出尺寸限制 |
| `limits` | 查询、尝试、候选、下载、VLM、OCR 重读及补图修订数量 |
| `batch_failure_policy` | continue_independent / stop_on_error；默认前者 |
| `model_bindings` | OCR、VLM、分割和生成候选的版本化策略引用 |

`PipelinePlan.parameters` 对 UI workflow 只允许三个键：`request_ref`、`policy_ref`、`evaluation_ref`，值均为当前任务 ArtifactRef。UI 注册校验器读取并验证内容、schema 版本和依赖哈希；允许集合来自静态定义与请求的交集。`PipelinePlan.inputs` 只列少量入口引用，完整多图及派生索引以 manifest 引用表达，避免超过公共 32 引用/64 KiB 上限。

搜索目标、用户说明、模型正文、图像字节均不进入上述 parameters。计划指纹覆盖这些引用的哈希，所以改变说明、模型策略、输入、限制或预算会形成新计划。

### 3.1 区域提案、UISelection 与选择版本

UISelection 是受限内容模型：schema_version=1、sources[1..10]；每项含 source_id、original_sha256、layout_ref?、target_regions[1..64]、keep_elements[]、remove_elements[]。target_regions 使用严格判别结构：{kind: bbox, xyxy: [x1,y1,x2,y2]} 或 {kind: element, element_id}。bbox 均为 canonical 像素、右下不包含；元素目标展开为该已登记元素的 bbox。各列表中的重复 ID/区域先显式拒绝，不依赖无序去重改变选择指纹。

source/hash 必须属于实际已登记输入。layout_ref 在使用任何元素 ID 时必填，并与同一 original/canonical 身份及 hash 对齐；CLI 验证访问权后建立当前 task 的 ref。元素必须存在、无跨源引用；保留/移除集合不相交；remove 元素必须处于 target_regions 允许范围（超出时拒绝，不能隐式扩展）。keep 元素使用冻结的 canonical bbox 作为最小保留区域，不依赖尚未产生的 SAM mask 来决定保护范围。decompose只使用target_regions，keep/remove为空；reconstruct将最终编辑范围限制到target_regions并扣除keep区域。

默认编辑请求在已授权分析中由Agent依据实际布局和目标提出候选；新增 UISelectionCandidate：candidate_id、task_id、selection_ref、preview_ref、evidence_ref、origin（agent_proposed/user_override）、revision及状态。编号稳定，正文/解释与预览保存在受限素材。Agent只提案；几何、scope/hash和目标限制由可信代码校验，非法候选不能驱动GPU。

目标明确时只推荐一个有效方案并直接形成具体待批准子计划，UI显示awaiting_approval；用户批准该子指纹同时确认范围。只有目标含混、无有效区域或用户希望换方案时使用awaiting_selection，提供候选编号。ui select --candidate ID与--selection FILE互斥，可信入口补齐hash/ref并登记，不执行模型/GPU。选择文件保持上述严格结构，但属于高级覆盖入口。

ui plan --selection可为已知手动来源绑定覆盖选择；默认deferred在布局后自动提案，搜索来源未知也不要求用户手填引用。bound选择在当前布局就绪后复核。parse始终none，不产生编辑提案或GPU子计划。提案若需额外VLM仍计入原每图≤4及分析预算，不制造免费规划调用。

选择以UIStepBinding的selection:<source_id>/revision保存。候选对应的原图/布局/hash与最终selection均冻结；根request_ref/fingerprint不改写。分割或补图批准绑定具体子请求，首次批准同时确认系统建议的范围。替换选择原子更新当前版本并使旧未执行child失效；已完成证据保留，已提交/未知GPU先取消/对账，预算及编辑链计数不重置。

批次父级可把显式选择按source授权重登记到child；默认逐图自动提案，清晰方案等待具体批准，含混方案才等候选选择。每批同时最多一个活动单图child，等待不启动后续图。

## 4. 标准图、布局与派生资产

| 实体 | 关键字段 | 验证 |
|---|---|---|
| CanonicalImage | 原始 ref、canonical ref、宽高、颜色/alpha 规则、EXIF 原方向 | 保留分辨率；结果及原始字节分别哈希 |
| ImageView | `view_id`、输入 ref、crop rect、scale、orientation、forward/inverse matrix | 变换可逆；所有点回映到 canonical |
| TextRegion | `text_id`、polygon、原始 text/score、model_id/version、view_id、状态 | polygon 有效；原文不可被模型建议覆盖 |
| TextCorrection | text_id、新建议、证据 refs、revision_id | 与原始记录分开；局部重读计数不重置 |
| UIElement | element_id、bbox/polygon、kind、parent_id?、text_ids、occlusion_refs、evidence、hypotheses | 无环层级、无悬空引用、几何正面积 |
| UILayout | source_id、canonical_ref、elements、texts_ref、revision_id、mapping_ref | 稳定身份与拆分/合并/删除映射 |
| UIAsset | artifact_ref、element_id?、role、epistemic_status、quality_ref、source_refs | 角色不代替来源或真实性标签 |
| RevisionRequest | base_task/fingerprint、base_revision、action、target_ids、受限参数 | action 仅 reread_text / adjust_segmentation / review_region / regenerate |

角色集合：`original`、`canonical`、`layout`、`texts`、`rect_crop`、`contour_mask`、`estimated_alpha`、`glyph_mask`、`glyph_image`、`edit_mask`、`generated`、`overlay`、`quality_report`。原始像素为 observed；透明度及隐含结构为 estimated/hypothesis；补图为 generated。

初次 element_id 由登记分配并持久化。后续修订以相同来源和几何/文字匹配保留身份；匹配歧义时创建新身份并记录替代关系，不把数组位置当身份。

## 5. 分割和蒙版生成计划

`UISegmentationRequest`：canonical_ref、selection_ref及选择revision/hash、按稳定元素排列的 box/point 提示、模型快照、资源预算、提示版本及结果角色。实际提示形成后才构建其精确 PipelinePlan；任务内最多 64 个元素提示，超过时要求用户缩小目标。

`ImageMaskBinding`：

- `image_ref`、`mask_ref`、`canonical_ref`、`canonical_edit_mask_ref`、`selection_ref`及选择revision/hash。
- `width/height` 与图像/蒙版一致；`mask_role=edit_mask`，灰度 0 保留、255 全编辑。
- `view_transform_ref`、crop、resize、pad、回映射尺寸；mask 的扩张及羽化必须在冻结前完成，最终非零区域裁到选择的target_regions并排除冻结keep区域。
- `mask_policy_version`、`recipe_supports_mask=true`；所有引用哈希进入指纹。

原图和 mask 缺一、错角色、维度不符或不支持编辑的 recipe 均拒绝。空 canonical_edit_mask 返回 `no_edit_pixels`，外部生成次数为零。最终 CPU 合成使用已批准全分辨率 mask；其为零处直接复制 canonical，避免浮点计算引入像素变化。

修订不修改已批准子计划：保存有效参数 overlay 及其哈希，只有在 envelope.mutable_parameters 内才执行。改变 mask、图像、模型、recipe 或参数边界必须建立新子计划；旧已消费预算仍留在原根预算组。

## 6. 执行、费用和额度实体

| 实体 | 字段/键 | 目的 |
|---|---|---|
| UIWorkflowDefinition | workflow_type、版本、步骤定义、每步输入/输出合同、可用 capability IDs | 显式注册，拒绝任意任务图 |
| UIStepBinding | task/step/revision、capability、input refs/hash、参数 hash、依赖版本、输出 refs | 将步骤实际输入与不可变根计划同时固定 |
| UIChildBinding | parent_task、child_task/fingerprint、root_budget_id、source_ids、purpose、authorization_kind、selection_step_id/revision? | 保持父子作用域，分析继承与精确 GPU 批准分开 |
| BudgetGroup | root_task、TaskBudget、resource limits、根/每源计数上限、submission_gate、stop_reason | 编辑子流程阶段才加入；首版根身份直接用task_id和已有operations预算，不重复汇总入账 |
| OperationCharge | operation_id、root_budget_id、capability、计数预留、金额/GPU预留及结算、status | 操作是唯一消费单位；unknown 不清零 |
| QuotaSnapshot | provider、scope_id、known/stale/unknown、单位、套餐/Key限制、剩余、observed_at、reset_at?、版本 | 仅白名单字段；作用域ID不使用密钥/邮箱 |
| QuotaReservation | operation_id、snapshot_id、单位、状态、covered_watermark | 未确认用量在快照未覆盖前保守扣减 |
| RouteDecision | logical_query_id、attempt_no、provider、snapshot_ref、策略hash、原因、cooldown_until | 提交前固定；重试读取原决定 |
| ProbeRecord | provider/scope、operation_id、开始时间、结果状态、成本状态 | 共享刷新及 10 分钟限频跨重启持久化 |

美元和 GPU 分钟复用百万分之一为单位的内部整数；上限向下取整、预留向上取整。额度单位单独保存为 search/credit，不与美元混加。账目展示 `actual / reserved / unsettled` 三个互斥桶。UI视图使用usage_verdict=within_budget/reservation_adjusted/budget_exceeded/awaiting_reconciliation。旧operations.result.budget_exceeded实际表示“超过预留”，其历史序列化/行为不变；新UI读取原预留、实际值及冻结批准限额独立判定。实耗高于预留但仍满足逐次/总限额时为非失败reservation_adjusted，原子据实结算并重算后续预留，足够则自动继续；不足则以budget_insufficient停止新消费并保留成果。实际超过批准限额才budget_exceeded失败并关闭根/单任务门禁；unknown保留预留。根max_revisions在编辑阶段跨child共享，每源计数沿稳定source累计。

## 7. 公共账本分期迁移

不另建UI数据库，费用和GPU结算始终在已有operations。按实际能力引入数据：

| 阶段 | 数据库版本迁移 | 新表 |
|---|---|---|
| 手动单图 | v1→v2 | ui_step_bindings |
| 双后端搜索 | v2→v3 | quota_scopes、quota_snapshots、quota_reservations、quota_routes、quota_probes |
| 编辑子流程 | v3→v4 | ui_budget_groups、ui_child_bindings、ui_operation_charges |

单图阶段root_budget_id是现有task_id；quota route唯一键用(root_task_id,logical_query_id,attempt_no)，不外键依赖尚不存在的预算组。跨任务scope额度共享不等于父子预算共享。步骤唯一键为(task_id,step_id,revision)，实际输入/参数hash随不可变绑定保存，不改变旧operations.input_hash的v1含义。

编辑阶段ui_operation_charges.operation_id唯一外键关联原operations，仅保存归组/计数/版本及额外资源信息，实际金额不复制。父子绑定检查同根无环和能力/预算子集；选择当前版本在提交前验证。

BEGIN IMMEDIATE内完成适用的取消/授权、单任务或根组预算/计数检查及额度决策预留，I/O在事务外。结算和准入各自原子化，异常费用不能通过并发提交绕过限制。首版只需单任务分支，父子分支在v4增加。

每次升级先停写、备份、事务迁移/失败回滚，并运行这一增量与适用旧金样；禁止旧二进制打开不支持的版本。新兼容Worker可回放旧历史，旧payload/fingerprint不重写；数据库版本与MaskedGenerationPlan的schema_version=2是独立编号。

## 8. 状态与恢复

`UIRunView.status`：planned、running、awaiting_selection、awaiting_approval、awaiting_reconciliation、succeeded、partial、failed、cancel_requested、cancelled、rejected。`phase` 为 intake / acquisition / analysis / selection / segmentation / reconstruction / review；不是状态的替代。

沿用公共 PipelineState。由于其没有 partial，已结束但仅部分完成的 UI 父任务投影为公共 `failed + stop_reason=partial_results`，UI 展示 `partial` 及每个子项；不能对用户输出整体 succeeded。不扩大旧 PipelineState 枚举。编辑目标含混时保持公共非终态并显示awaiting_selection，不占GPU；有效推荐方案直接awaiting_approval。reservation_adjusted不是失败，可正常完成；实际超批准限额才failed。下一次预留不足时公共任务以failed/stop_reason=budget_insufficient结束并保留成果，增加预算需要新计划及批准。parse完成可标该任务succeeded；预览不冒充正式M-U1质量或完整功能验收。

外部操作仍使用 prepared → submitting → submitted/running → succeeded/failed；结果不明转 outcome_unknown，并使 UI 等待对账。完成步骤只读取已保存结果；失败输入按批次策略处理；unknown、预算和批准门禁不被 continue_independent 绕过。

取消先关闭根及子提交门禁。能证实尚未提交的 prepared 操作可释放预留；已提交或未知操作保留资源与成本，直到确认停止/完成。失联、超时或租约到期都不证明 GPU 已空闲。

## 9. 评估与证据

`UIEvaluationCase`：case_id、来源/许可、底图group_id、输入sha256、语言/场景标签、布局与文字标注、mask/背景真值refs、split。

首版仅保存三个开发样本的测量及预览标记，不要求完整阈值引擎；以下类型与完整数据在正式质量阶段落地。

`UIQualityThresholdSet`：metric_id、unit、direction、threshold、适用标签、minimum_sample_count、development_case_ids/hash、冻结时间、版本和审阅记录。状态只有 draft/frozen；draft 或无对应样本禁止宣告质量通过。

指标：OCR CER、检测/分割 IoU、文字关联 precision/recall、带事实标签的场景语义准确率、已知背景编辑区误差及接缝人工评分；无真值图片不计入背景恢复准确率。候选/失败修订的质量报告均保留。

`UIAcceptanceEvidence` 区分 mock/offline/runtime/network/gpu，引用 U-V01—U-V14 和 M-U1—M-U3。纯本地任务记录代码版本、命令、结果及跳过原因；发生外部调用才增加实际输入/计划指纹、批准、提供方受理次数、费用、运行ID与适用硬件峰值。证据可复用须注明适用版本和缺口，不把本地验证伪装为真实模型验收。
