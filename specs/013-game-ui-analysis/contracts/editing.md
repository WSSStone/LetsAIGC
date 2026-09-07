# T019 编辑与父子边界合同

当前实施状态（2026-09-07）：T020—T027已验收，含Windows固定样本SAM及Mac原生ComfyUI节点只读验收。T028新增独立编辑Temporal工作流，具体分割和最终image/mask补图分别批准；实际商业UI编辑和正式质量仍由T030及后续LIVE任务验收。

本文件保留T019先行合同并追加后续实现边界。T020 实现 DTO、父子账本与登记，T021 实现选择入口，T025/T026 实现 mask 像素与编译，T028 接线工作流。原 T003 批准拒绝、重复受理、未知费用、单任务预算矩阵继续复用，不复制到每个 provider。

## 机器合同与验证层次

- `ui-selection.schema.json`：UISelection v1 受限文件内容。来源 1–10、目标 1–64；bbox 为 canonical 整数像素 xyxy、右下不含；element/keep/remove 引用必须提供 layout_ref。原自动布局 role=layout，确认布局 role=review_layout，不能用旧 reader 解析 review 布局。
- 同一 schema 的 `$defs.UISelectionCandidate` 固定编号、selection/preview/evidence 引用、origin、revision、state。状态为 proposed/selected/superseded/invalid，selected 不代表批准；候选结构没有批准权限或凭证字段。预览/解释分别使用 selection_preview/selection_evidence 角色。
- `masked-generation-plan-v2.schema.json`：MaskedGenerationPlan v2 受限工件内容，保留 v1 必要字段，加入必需 image_mask；旧 GenerationPlan reader 拒绝它。全部输入引用、选择 hash/revision、变换、mask 策略及 envelope 都参与规范化内容 hash，再由对应工件引用进入具体 PipelinePlan 指纹。
- `tests/fixtures/contracts/ui-edit-contracts.json`：确定性正例、独立 v1 序列化/指纹金样及 v2 hash。测试不在每次运行时重生成金样。

JSON Schema 只校验可表达的结构；不声称它能验证引用文件、作用域、几何关系或像素。T020/T021 必须额外拒绝重复 source_id、非正面积/越界框、未登记或跨原图元素、keep/remove 交集、移除框未完全包含在目标区域并集，以及过期/篡改引用。列表中的重复项显式拒绝，不通过去重更改指纹。decompose 的 keep/remove 为空且不能有 reconstruction_target；reconstruct 必须有 scene_background/map_surface；parse 不接受选择。

ImageMaskBinding 的 image_ref 是准备后的 image 角色，canonical_ref 保持 canonical；两种 mask 引用必须为 edit_mask。crop=[x1,y1,x2,y2]、resize=[width,height]、pad=[left,top,right,bottom]；width/height 是准备后尺寸，canonical_width/height 是回映目标尺寸。T020 校验同任务、selection_hash 等于 selection_ref.sha256、envelope 一致。T025/T026 读取真实工件核对图/mask 配对与通道、尺寸、变换、冻结的扩张羽化/target/keep 和原生 recipe 能力。0 保留、255 编辑；空最终 mask 返回 no_edit_pixels 且不生成。CPU 合成确保零 mask 像素原样保留。合约中的测试素材、模型快照仅是本地替身，不能作为真实模型/GPU 验收证据。

这些文件只供开发与测试使用；产品不得从 specs/ 加载合同、配置或流程。运行时 DTO 的代码实现属于 T020；受限正文通过 ArtifactStore 保存，Workflow 消息仍只携带现有安全、大小受限的引用。

## 从布局到具体批准

| 输入/动作 | 冻结与状态 | 批准及调用效果 |
|---|---|---|
| 默认自动分析 | 已授权分析内生成提案、预览、selection_ref 与稳定 candidate_id | 清晰有效方案直接 awaiting_approval；不强制额外 select，也不扩大已批准的 VLM 次数 |
| reviewed-task / review-revision | plan 时一次解析确认版本；冻结 ReviewedLayoutBinding 的 manifest/layout/texts/canonical 引用和版本 | 草稿拒绝；新确认版本不替换已冻结输入；该输入不重跑 OCR/VLM |
| 显式原自动布局 | 保留原 layout/hash，来源 automatic | 与 human_assisted 分开；不升级为 ground_truth |
| 多个含混候选或无有效候选 | awaiting_selection；展示编号与预览/所缺信息 | 没有 GPU 批准或外部调用 |
| select --candidate ID 或 --selection FILE | 必须且只能选择一种；同一可信验证入口；selection_recorded | 零 OCR/VLM/GPU；不记录/消费批准；不修改不可变根 request/fingerprint |
| 具体分割批准 | 完整 child task ID、64 位指纹、具体图/提示/选择、单轮/总预算和预览 | 只授权此分割；首次批准原子确认候选范围 |
| 最终 mask 补图批准 | 实际 image/mask、模型、recipe、变换和参数边界重新形成 child 指纹 | 分割批准不授权补图；mask/model/recipe/边界变化需新批准 |

跨 task 的 review/布局必须经过可信重登记，保留来源；不能把外来 ArtifactRef 塞进子任务。原 parse 在任何阶段都拒绝 select，完成后 review 不改 parse 终态。普通用户不填写机器引用或哈希。

选择版本使用 `selection.<source_id>` 作为 UIStepBinding.step_id（与现有 Identifier 一致，不能使用冒号）。review_revision 与受限执行 revision 是独立计数，人工保存次数不消耗 GPU 修订预算；替换 child 不重置根计数。

## T020 先行内部服务边界

`PipelineService.ui_child_plan(task_id, *, parent_task_id, purpose, source_ids, request_ref, selection_ref, selection_revision, budget)` 返回冻结 PipelinePlan，purpose 为 segmentation 或 inpaint。它只进行可信登记，不提交 provider、不消费批准。父级 selection_ref 需已验证/在子作用域重登记；request_ref 已在子作用域保存。只有显式注册 `ui.segment` 后端、消费精确子批准并取得 `local-gpu` 所有权后，公共 submit/observe/collect 才可调用回环SAM服务；未注册时继续返回 `capability_not_ready`。T024受限入口不接线T028编辑Workflow，也不启用Comfy补图。

继续复用可信 `approve` + `Ledger.consume_approval`；消费精确 child 批准时在单个事务中激活该候选版本并禁用旧待执行 child。`Ledger.usage(task_id, include_children=True)` 读取根组，默认省略该参数时保留旧单任务行为。内部 API 名称如在实现时调整，须同步先行用例及本合同，不可删除边界断言来获得通过。

| 边界 | 必须结果 |
|---|---|
| 根分析批准 / 分割批准 | 分别不能替代分割 / 补图的具体批准，拒绝时零操作登记 |
| 子来源或预算扩大、未知父级、自引用/环 | 原子拒绝，不留下 child binding |
| 新选择获批 | 旧待执行 child 返回 selection_superseded；原计划/历史/已消费费用保留 |
| 旧 GPU submitted/outcome_unknown | 新版本不能激活，awaiting_reconciliation；provider request ID 和预留不变 |
| 父子费用 | operations 是唯一金额/GPU 事实；ui_operation_charges 仅归组/计数，operation_id 唯一；actual/reserved/unsettled 互斥，根汇总不重复入账 |
| 新 child、新 mask、同源修订 | 消费沿同一根/源/编辑链累计；max_revisions、VLM 4 次、OCR 重读 2 次及补图修订 2 次不重置，根预算可更严 |
| 根取消 / 预留与提交竞争 | 根提交门禁先关闭，已准备 child 也不能 begin_submit；外部 I/O 在事务外 |

`test_ui_child_approvals.py` 只使用本地账本和合成工件，不连接 SAM/Comfy。`test_ui_segmentation_runtime.py` 默认只读已明确选择的现场证据，pytest不会启动模型或签发批准。T028默认提案、候选覆盖及工作流接线使用替身验收；T024固定开发样本通过不能替代编辑的真实GPU端到端或正式质量验收。

## 测试门禁

三个 T019 测试文件分别覆盖选择与 CLI、mask 内容与 v1 隔离、GPU/父子差异。T048 的确认版本/跨 task 重登记合同继续执行；T003 同一矩阵增加 v4 参数，保护 review 迁移后的分析/搜索接入。

缺失的指定 DTO/CLI/child 入口通过显式 `pytest.xfail` 报告归属任务；入口一旦存在，后续断言正常执行，运行错误不被捕获为 xfail。`pytest --runxfail` 可展示尚未实现入口的真实失败。T019 完成表示先行合同已交付；这些预期失败必须在 T020/T021/T028 对应交付时逐项消除，所有未完成 [LIVE] 状态保持不变。

## T020 已实现语义

`ui_child_plan`登记、批准激活、步骤输入与选择绑定、根/子金额限额和根取消已实施。ImageMaskBinding的准备尺寸为resize加pad，子批准budget必须与MaskedGenerationPlan的envelope预算一致。旧UIStepBinding新增选择字段为None时不进入序列化，历史input_hash不变。

`ui_operation_charges.revision_units`显式记录修订预留：某源/能力首次revision=0为0，显式非零revision或同源后续新child为1；跨分割/补图共用根max_revisions。已受理失败和unknown保留次数；仅有未提交证明的取消准备释放。`revision_count`及按源计数是这些记录的派生汇总，金额仍只在operations。当前单图根预算已被UIAnalysisRequest的generation_revisions（上限2）收紧，根上限同时约束每源；原OCR/VLM冻结调用上限继续复用T003。批次与局部复核按T029/T032继续接线。

v4→v5迁移测试覆盖备份、失败回滚、现有根登记和已确认review读取。真实本地账本本轮未迁移，执行provider仍拒绝未配置能力；473 passed、3 skipped、3 xfailed不代表真实GPU或正式质量已验收。

## T028 接线边界

新增`letsaigc.ui.editing.v1`保留旧`letsaigc.ui.analysis.v1`的解析历史。根任务承载顺序子步骤、取消和恢复；每个GPU子任务仍使用各自的不可变计划、批准回执和operation。Continue-As-New保存当前child及批准状态，worker中断后只恢复原request ID；补图成功后返回终态，不重新打开已完成子任务的批准门禁。

`EditingExecution.prepare`只读冻结布局/模型锁并执行有界CPU准备，不加载模型、不调用搜索/OCR/VLM/SAM。跨作用域重登记只允许task/ref身份变化，几何、人工锁定及像素必须保留。`ui select`在账本事务内拒绝进行中/unknown请求，原子失效未提交子任务，再发布新的选择版本。

`collect_step`先持久化产物，再取得provider结构化释放证明，最后结算并解除`local-gpu`占用。SAM要求CUDA释放证明；Comfy使用原生`/free`及空队列和固定版本CUDA计数验证，单次HTTP 200不算释放成功。释放未知时保留费用预留和资源归属，不开始下一模型。补图编译/上传位于具体批准之后；规划最终image/mask的hash、canonical回映变换、模型/recipe/参数及预算均进入子指纹。

`ui inspect ROOT --local`展示本地投影、子计划、待批准项及根组费用；投影标注为可能滞后。已完成child重复`ui execute`返回已有结果而不新增批准。现有Mac真实账本仍为v4；T028回归的v5账本全部为临时测试数据。

## T029 局部修订边界

`RevisionRequest`固定`base_task_id/base_fingerprint/base_revision/action/target_ids/parameters`。四个动作是`reread_text`、`review_region`、`adjust_segmentation`、`regenerate`。`ui revise`只产生不可变`revision_request`及`revision_plan`，后者保存根任务、具体child ID/完整指纹、原请求引用和依赖影响；不签发批准、不调用模型。`ui inspect ROOT --local`的`revisions`列出修订状态、manifest及可采纳建议引用。

子任务purpose新增`reread_text`和`review_region`，分别绑定`ui.ocr`和`ui.analyze`。`LocalRevisionInputs`固定同child作用域的分析请求、修订请求、选择版本、局部view、文字上下文及参数引用。局部图像必须等于canonical的未缩放裁剪；文字上下文使用同一局部坐标，稳定文字ID不变，原始多边形/人工文本保留在来源工件。VLM policy中的定价引用也重登记到child。根计划须已允许局部修订；parse根不能进入编辑修订路径。

局部模型child仍要求完整具体指纹。OCR重读次数、VLM总次数/局部view数和金额沿原根累计；失败/unknown不释放已受理次数，局部OCR/VLM不消耗GPU修订计数。新child ID、Temporal新run或Continue-As-New不会重置这些限制。`UIEditingWorkflowInput.revision_ref`固定本次修订；新修订可在已完成根workflow下启动新run，活动中的另一修订会拒绝接收其批准。

模型输出仅生成`review_suggestion`；人工来源的建议保存于原review任务作用域。`ui revision-accept ORIGINAL_REVIEW_TASK --suggestion ID`通过原确认binding、头版本CAS及字段锁检查创建新草稿，重复请求幂等；不自动确认、不改冻结计划、不提供批准权。

分割修订只对目标元素调用SAM。`revision-v1`提示的完整参数hash由原签名permit覆盖；不同于原布局的提示框也必须位于已选择ROI内，不能包含keep元素或虚构ID，不能修改人工布局。未变化元素复用原资产的内容hash/ID，必要的child作用域副本保持像素相同；原SAM输出不改写。合并结果用于拆解或生成新的最终mask补图计划，后者另行批准。基准child选择已过期时拒绝修订。`regenerate`只允许prompt/negative_prompt/seed，保留mask/model/recipe等冻结输入，全部GPU修订共用根上限。GPU释放未确认不得发布完成或进入下一模型。
