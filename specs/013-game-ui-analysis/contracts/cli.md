# 013 UI 命令行契约

状态：当前分支已接通 `letsaigc ui` 的单图 parse 命令及两家搜索适配，离线验证通过，真实预览尚未验收。Root `--json` 位于 `ui` 之前。下表仍包含后续编辑/批次合同：select、revise 尚未注册，编辑和批次 plan 返回 capability_not_ready；不静默改模式或截断输入。实际使用与就绪条件见[开发使用指南](../../../docs/game-ui-analysis.md)，验收状态以[任务记录](../tasks.md)为准。

## 命令

| 命令 | 参数 | 行为 |
|---|---|---|
| `ui import` | `--image SOURCE` 可重复；或 `--artifact REF_FILE` 可重复；`--metadata FILE` 可选 | 可信本地输入/HTTPS获取，最多10项；保存快照和来源，返回manifest ref，不执行模型 |
| `ui plan` | `--input-manifest REF_FILE` 与 `--query TEXT` 二选一；`--max-images N` 仅搜索可用；`--selection FILE` 高级选项；`--mode parse|decompose|reconstruct`；`--target scene_background|map_surface`；`--budget FILE` 必填；`--policy FILE` 可选；`--language auto|zh|en`；`--text-assets`、`--remove-text`、`--allow-local-revision` 可选 | 确定性验证/登记不可变计划，不搜索、不调用VLM/GPU；reconstruct必须target |
| `ui select TASK_ID` | `--candidate ID` 与 `--selection FILE` 必选其一、互斥 | 登记候选或高级覆盖，形成具体待批准子计划；不调用模型或GPU，不视为批准 |
| `ui execute TASK_ID` | `--approve FINGERPRINT` 必填 | 本地记录精确回执并启动或唤醒相应任务；对GPU子计划同时确认显示的范围并批准该操作 |
| `ui inspect TASK_ID` | `--local` 可选 | 活跃查询或显式本地投影；显示来源、阶段、产物、预算、候选及pending_approvals |
| `ui revise TASK_ID` | `--request FILE` 必填 | 验证枚举修订请求，返回新修订或子计划及批准要求，不默默执行新增消费 |
| `ui resume TASK_ID` | 无 | 复用既有计划、结果和仍有效批准，继续已授权步骤；不提高预算 |
| `ui cancel TASK_ID` | 无 | 先关闭提交门禁；后续支持子任务时再请求取消已启动子任务 |
| `ui reconcile TASK_ID` | 无 | 查询原请求/收集证据，对未知结果保守处理，不盲目重发 |
| `ui doctor` | 无 | 按能力显示协议、模型锁、服务和资源状态，不安装模型、不发搜索或计费探测；SAM/Comfy缺失不阻塞单图parse |

手动输入清单和查询不能混用。`--image`不是`ui plan`的别名；先import可让用户检查输入，不将登记等同批准。元数据文件不得指定任意输出路径或工具名称。

`--policy` 默认 `configs/ui-analysis/default.yaml`，`--budget`没有隐含默认；默认mode为parse、language为auto，其余布尔开关为false。预算文件复用TaskBudget五个字段，UI显式限制max_revisions≤2；资源与次数由policy确定并纳入最终计划。首版parse的GPU预算可为0，编辑阶段才要求对应GPU预算。

## 输入数量与 Workflow 选择

最终手动按去重前的输入条目数选路：1项为ui_analysis，2—10项为ui_batch，拒绝--max-images，不能截断用户选择。搜索的--max-images默认1、范围1—10，是根任务最多选图数而非成功数量保证；1走ui_analysis，2—10走ui_batch。该值在联网前冻结，不因实际结果或恢复改变Workflow。每查询选图/下载/候选上限和根搜索次数共同约束；取得所需图片即停止后续供给。

首版只接受上述单图parse计划，批次与编辑未就绪时在plan拒绝；import可以登记多项，但登记成功不表示已有批次执行能力。ui_analysis的acquisition支持manual/search。批次阶段由ui_batch父级获取一次，分析child使用登记后的manual引用，保留search来源与已消耗计数，不重搜。

## 默认编辑流程：自动提案，直接批准

1. decompose/reconstruct的根计划未附高级选择文件时冻结为selection_mode=deferred。分析获批后，Agent根据实际布局与用户目标提出推荐区域、保留/移除对象和预览；调用计入已批准的VLM次数与预算。
2. 可信层验证来源、hash、坐标和模式约束，内部冻结UISelection。推荐明确时直接显示awaiting_approval及具体分割子计划；用户审阅预览，通过该child的execute --approve同时确认范围并批准分割，无须准备选择文件。
3. 目标含混或无法形成有效推荐时显示awaiting_selection，列出简短候选编号及预览。用户执行ui select TASK_ID --candidate ID，再审阅具体待批准项；无需填写hash、来源引用或坐标合同。
4. SAM完成后，实际最终mask及图像/模型确定，另列补图child待批准项。此前分割批准不授权补图；Agent不能批准自己的提案。
5. 选择以版本化UIStepBinding保存，子请求绑定selection_ref及hash；根计划不修改。替换范围使旧未执行子计划与指纹失效，保留历史证据。存在已提交/未知GPU工作时先取消或对账，不能直接替换后重发。
6. select不调用OCR/VLM/GPU，不重置根预算、调用或修订次数。批次阶段对实际等待的单图child选择。

parse始终selection_mode=none，完成后直接收集结果，对其select必须拒绝。decompose提取对象可请求--text-assets，keep/remove必须为空，不接受--target或--remove-text。reconstruct必须指定--target，保留/移除列表进一步限定目标区域，可请求--text-assets/--remove-text。parse不接受上述编辑选项。非法组合在plan拒绝。

## 高级选择文件

`ui select --selection FILE`保留为高级覆盖入口。已知手动输入也可在plan传入--selection，冻结为bound；搜索输入身份尚未确定时只能deferred。文件不替代--target，也不授权模型执行。

文件是[数据模型](../data-model.md)中的严格UISelection：schema_version=1，sources每项含source_id、original_sha256、可选layout_ref、非空target_regions，以及默认空数组keep_elements/remove_elements。target_regions只能是canonical像素bbox或已登记element_id；bbox为左闭右开xyxy。元素引用必须有同图、已授权且hash有效的layout_ref，外部布局经CLI重登记到当前作用域。不存在的ID、交叉来源、过期布局、越界/零面积、重复来源和保留/移除集合相交均拒绝。metadata/policy不承载选择。

默认提案、候选选择与高级文件共用相同的内部验证。最终编辑mask的非零范围必须裁到目标区域，并排除冻结的保留区域；改变范围必须形成新子计划和具体批准。

## JSON 输出

成功响应统一含schema_version=1、command、status；按命令补充task_id、plan_fingerprint、result_ref、source=live|local_projection、stale、phase、root_budget_id、usage、pending_approvals、children、selection_candidates、selection_requirements、usage_verdict和安全错误引用。单图阶段root_budget_id就是task_id，不要求父子预算表。

- import：status=ready|partial|unavailable、input_manifest_ref、输入/素材数及逐项索引。REF_FILE是把响应中的ref保存为本地文件供可信CLI读取；路径不进入Workflow历史。
- plan：status=planned、task_id、完整64位plan_fingerprint、输入/输出及授权摘要、estimated_usage、budget、workflow_type。从这里取得execute所需ID和指纹。
- select：selection_ref、selection_revision、superseded_children和pending_approvals；status=selection_recorded仅表示登记成功。
- execute：status=accepted只表示回执登记及启动/唤醒成功；最终检查inspect，不伪装同步完成。
- inspect：独立显示task.status与phase；pending_approvals每项含child_task_id、kind、plan_fingerprint、preview_ref、selection_ref和受限输入/模型/预算摘要。人类模式显示预览位置和范围说明；无需手填这些引用。awaiting_selection时列候选编号、预览及实际等待task_id，不能伪称正在生成。
- revise：revision_id、受影响步骤、复用产物、新task/fingerprint或待批准项；改mask/model/recipe标记invalidates_previous_approval，不覆盖原计划。
- resume/cancel/reconcile：返回请求登记状态和当前视图；结果未知明确awaiting_reconciliation，保留预留。reservation_adjusted表示实耗高于预留但仍在原批准逐次/总限额内，是非失败结算；重新预留可行时自动继续。
- 下一步无法预留时停止新消费、保留成果，task.status=failed、stop_reason=budget_insufficient；扩大预算须新计划和批准。实际超过批准限额才标usage_verdict=budget_exceeded并失败关闭后续提交。旧ledger.result.budget_exceeded仅表示超过预留，UI不得直接据此判失败。

人类与JSON模式语义一致。JSON不交互提示、不自动批准；本项不新增ui run/ui chat。inspect查询成功返回0，即使所查任务等待或失败。

## 错误与退出码

稳定错误对象含code、safe_message、task_id/operation_id?、details_ref?，不含原路径、签名URL、凭据、标题正文或异常原文。

| 退出码 | 场景 |
|---|---|
| 0 | 请求被接受或查询成功，包括partial/等待/失败任务的inspect；调用方查看status |
| 2 | 语法、非法几何、输入种类/数量、结构或策略验证失败 |
| 3 | capability_not_ready、模型/条款/计价未就绪或资源不足 |
| 4 | 批准、输入hash、作用域、预算或数量门禁拒绝 |
| 5 | 命令确认执行失败/未知，或无法完成所请求的恢复操作 |

部分import返回0与partial及逐项错误；全部无效时返回2或3与unavailable。批准或预算拒绝的能力不得发生外部调用。

## 兼容和验证

旧agent/pipeline/runtime命令及JSON语义保持不变。UI CLI复用可信批准入口，不能注册成运行时Agent工具。

合同测试先于对应CLI实现：首版验证参数、JSON、取得指纹、单图搜索及未就绪能力；编辑阶段验证默认提案、候选与高级覆盖、旧指纹失效、select零模型调用；批次阶段补输入映射与部分结果。调用公共批准/预算机制的边界断言即可，不在CLI复制完整故障矩阵。真实链路按[Quickstart](../quickstart.md)分阶段验收。
