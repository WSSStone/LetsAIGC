# 013 UI 执行与批准契约

状态：设计合同；未实现。依据[规格](../spec.md)、[数据模型](../data-model.md)和[当前任务](../tasks.md)。能力按交付阶段启用，旧G依赖只作历史索引。

## 静态流程与分期

| 阶段 | workflow_type / Temporal名称 | 业务步骤 |
|---|---|---|
| 首版 | ui_analysis / letsaigc.ui.analysis.v1 | provide(manual/search，单图) → canonical → OCR → VLM → layout/crops → project |
| 编辑 | ui_analysis的decompose/reconstruct分支 | 布局后自动提案/预览 → 具体分割批准 → 按需最终mask补图批准 → review |
| 编辑 | ui_segmentation / letsaigc.ui.segmentation.v1 | 具体图/提示/选择校验 → SAM → mask/alpha/glyph → 收集 |
| 编辑 | ui_inpaint / letsaigc.ui.inpaint.v1 | 具体image/mask校验 → 编译/准备 → submit/observe/collect → CPU精确合成 |
| 批次 | ui_batch / letsaigc.ui.batch.v1 | 父级供给 → 顺序单图child → 汇总 |

首版只注册ui_analysis和manual/search/normalize/ocr/analyze/layout/crop/project等实际能力。分割、Comfy、批次未就绪时在plan阶段返回capability_not_ready，不用缺SAM阻塞parse，也不静默丢弃所请求输出。最终按手动原始条目数或search max_images选择单图/批次，具体规则见[CLI](cli.md)。

扩展validate_ui_registration/resolve_ui_step和submit_step等必要分派。旧validate_registration、submit(plan, revision)、旧Agent与专家行为不变，未知能力不能默认落到Comfy。单任务复用现有账本预算/批准/operations；跨账户额度在搜索阶段加入，父子根组在编辑阶段加入。Workflow只编排，Activity调用受限服务，不做任意工具执行。

## 分析与具体批准

1. import冻结输入，plan本地确定性校验并登记不可变根PipelinePlan；二者不发模型请求。search原文保存为受限素材。
2. execute --approve由可信CLI登记人工身份、请求ID和精确回执，才允许分析范围。伪造Temporal Update不产生权限。
3. parse完成即返回单图产物，不进入选择或GPU流程。首版预览需要双后端真实搜索，但单个manual任务不依赖搜索凭据/探测。
4. 编辑默认selection_mode=deferred，在已批准的VLM分析范围内根据真实布局和用户目标产生候选；提案用量计入原预算与每图次数。可信代码验证几何、来源和保留范围，生成预览及具体子计划。目标明确的推荐方案直接awaiting_approval。
5. 用户执行该child的具体指纹，同时确认提案范围和该GPU操作。分割后最终mask就绪才形成补图计划并再次具体批准。缺所需具体批准时GPU调用为0。
6. 只有目标含混/无有效方案才awaiting_selection；用户可ui select --candidate ID，或高级--selection FILE覆盖，二者互斥且零模型调用。变更选择写新绑定，原子使旧未执行child失效；已提交/未知GPU须先取消/对账。
7. 父子分析授权仅继承根允许子集；GPU具体批准不得继承。子请求冻结selection_ref/hash/version、实际图/提示或最终mask。最终非零mask不得超出获批区域或进入keep区域，根预算和编辑链计数不重置。

UI plan仍为零模型成本；旧Agent Responses规划预留保持 `$0.03 + 每张输入 $0.01`。正常用户无需手填来源/hash或机器选择文件；执行批准始终在模型之外。

## 操作与最小恢复边界

复用operation_id(plan,step_id,revision)和已有状态机。step_id不依赖Activity attempt/Worker/Temporal run_id；实际输入/参数在ui_step_bindings冻结，冲突拒绝。已登记结果读取已验证素材；本地纯计算可有界重算，外部提交先预留再跨submitting，不把未知当未受理。

付费/GPU提交不自动重发；有原请求身份就查询/收集，无法确认则outcome_unknown保留费用与资源。CPU OCR和搜索适配器只验证自身协议如何使用这个公共边界，不重复公共账本所有故障用例。视觉回执丢失按[operation只读查询](vision.md)恢复；搜索按[提供方合同](image-search.md)对账。

首版实现输入/已完成步骤复用、查询原操作及一次OCR完成后的代表性恢复。GPU交接、父子取消竞争和Continue-As-New在相关阶段实现，不能成为首版前置。

## 预算：预留与批准限额分开

单次指一个外部能力操作；全部操作还受单任务或根组总额。提交前保守预留必须满足单次限额，实际已结算 + 在途预留 + 未决预留 + 新预留不得超过总额；三个现有费用桶互斥。未知费用不记0，不能以乐观估计替代硬输出/参数/次数限制。

| 结算情况 | UI行为 |
|---|---|
| 实际不超过预留及批准 | 正常结算；下一步照常准入 |
| 实际超过预留，仍满足原逐次/总批准限额 | usage_verdict=reservation_adjusted，非失败；原子据实入账并重新核算下一步预留，允许则自动继续 |
| 实际尚未违反批准，但下一步预留容纳不下 | 停止新消费，保留成果；task failed、stop_reason=budget_insufficient；提高预算须新计划/批准 |
| 实际违反原逐次或总批准限额 | usage_verdict=budget_exceeded；任务失败、关闭当前任务/根新提交，保留真实账目和底层结果 |
| 实际仍未知 | awaiting_reconciliation，预留及适用资源归属保持，不自动替代调用 |

原ledger.result.budget_exceeded仅表示actual>reserved，不改变其既有含义或旧路径行为。UI判定必须比较冻结的批准限额，不直接把旧标志当作批准违规。美元与GPU分钟均按此规则，不截断实际值、自动增预算或让超限结果通过验收。

停止后仍可执行原预留已覆盖的在途收集/取消和无新增费用的查询；新计费读取也须预算准入。实际超批准限额关闭后不接受新消费，不能以“只读对账”绕过门禁；结果不明继续保留。

| 资源/数量 | UI 首轮边界 |
|---|---|
| 图片 | 25 MiB、32,000,000 像素、最长边 8192；超过即拒绝 |
| 分析分块 | 1024 像素块、64 像素重叠、最多 64 块；坐标保存逆变换 |
| 视觉理解视图 | 全图最长边 1536，必要局部视图最多 3 个；不作裁切基准 |
| SAM | 1024 模型视图；每图最多 64 个元素提示；输出回映至 canonical |
| 补图工作视图 | 每边最多 1024、补齐至 8 的倍数；最终交付仍为 canonical 分辨率 |
| 内存/显存 | 每活动任务 RAM 上限 24 GiB，GPU 预算上限 10 GiB；就绪检查计入服务常驻资源 |
| 磁盘 | 启动时至少 16 GiB 可用；单图临时占用上限 8 GiB、批次 16 GiB，不自动删除历史证据腾空间 |
| 网络 | 根任务总传输上限 1 GiB，单下载 25 MiB；模型部署不占运行下载能力 |
| 活动时长 | 单图累计活动执行上限 3600 秒，10图批次 36000 秒；人工等待不计 CPU/GPU活动时长 |
| 调用限制 | 每根任务查询/搜索尝试/切换 3/3/2；每逻辑查询候选/下载/默认选图 20/5/1；搜索 max_images 默认1、硬上限10；手动输入最多10 |
| 模型修订 | 每图 VLM 最多4次（含该图筛选及复核）、局部OCR重读2次、补图初次后修订2次 |
| 查询规划 | 每根搜索任务最多1次纯文本查询规划，产生至多3个候选查询，单独计费计数 |

以上是可审阅的默认运行配置，不是本机性能实测。有效请求可进一步收紧；放宽已批准上限须新计划。GPU 资源失联时保留所有权；同一账本可协调的范围和现有试点相同，旧 Agent/外部 Comfy 客户端不在协调内。

分割与 Comfy 顺序占用同一物理 GPU。切换能力前请求对应受控服务卸载模型并确认资源释放；没有响应或外部占用时停止并报告，不杀进程、不降级模型。服务卸载不删除权重或运行证据。

每图 VLM/OCR/SAM 数量按根组内稳定 source 身份计数，批次 child 和依赖闭包修订沿用；精确重复复用同一分析身份。根 TaskBudget.max_revisions 是所有补图 child 的共同修订上限（默认2，可收紧），每图也不得超过2；两张图不能各用2次绕过根上限。一次新的最终 mask/子计划不会清零已消费修订；首次生成和修订的归类沿同一根的图像编辑链固定，不能把换子计划伪装为新的免费首次。


## 超时与重试

这些是按能力使用的有界运行配置，不要求首版先实现GPU/批次重试体系。

| 步骤类型 | 单次 Start-To-Close / 最大尝试 | 恢复 |
|---|---|---|
| 本地校验、批准、配额只读 | 30秒 / 3；配额外部探测仍受独立次数限制 | 已知业务拒绝不重试；共享缓存可复用 |
| canonical/layout/crops/compose | 180秒 / 3 | 原子发布、相同输入复用 |
| OCR、VLM、搜索 acquisition、分割/生成提交 | 600秒 / 1 | 耗时推理采用 submit 后按请求ID查询，不靠重复提交恢复 |
| observe | 30秒 / 3，每轮后持久timer | 超出观测阈值则 Continue-As-New 或等待对账 |
| collect | 180秒 / 3 | 相同输出hash幂等登记及结算，不再推理 |
| cancel/project | 60秒 / 3 | 未确认取消保持对账；投影可重建 |

Schedule-To-Close 必须包含总尝试和有界排队余量，不允许无限等待；heartbeat 对长 Activity 周期发送且不承载敏感正文。公共 runtime 配置可收紧上述上限。

纯外部读取失败可重试，但每次真实请求均先按相应调用上限记账。超时、5xx、提交后崩溃不是未受理证明：保留 outcome_unknown、费用和资源，禁止创建替代生成/同逻辑查询。


## 批次生命周期与投影（后续阶段）

父级获取后，child只接收已登记的manual引用及原search provenance，不再次搜索。每次只允许一个活动单图child；明确提案等待批准，含混提案等待候选选择，后续图不绕过等待。

父取消先关根新提交，再REQUEST_CANCEL子任务；未确认外部终止时保留对账及资源归属。Continue-As-New只在无活动child且消息处理完成的安全边界，保持根计划、计数及游标；旧历史采用兼容Worker回放，不重写历史。

UI状态与公共PipelineState分开：awaiting_selection仅适用于含混/缺有效提案；推荐方案直接awaiting_approval；部分批次为UI partial、公共failed/partial_results；预算调整可成功，真实超批准限额优先为failed。首版preview标记与单个任务succeeded相互独立，任务成功不意味着正式质量达标。

## 验证职责

共享执行合同维护一次无批准/hash/取消/重复受理/unknown/结算矩阵。搜索增加额度竞争与切换特例，GPU增加实际输入和释放边界，批次增加父子关系；业务链路核对用户产物，不复制相同公共用例。最终复用仍有效的证据，仅补缺口和变更影响，映射见新Tasks。
