# 013 人工校正合同（2026-09-06 增量）

依据[US7与FR-056—067](../spec.md)、[计划](../plan.md)及[任务](../tasks.md)。T043—T050在T018后执行；T043—T050的本地校正预览已实施；后续模型/子计划接口仍按各自任务开放。

## 1. 入口与页面

`ui review TASK_ID`校验已完成parse的原图、layout、texts、manifest与作用域后启动本地页面。缺输入返回`review_input_unavailable`，旧版结构不可迁移返回`review_schema_unsupported`；不重启解析、不搜索、不创建批准。成功解析任务保持succeeded，校正另有状态。

页面为原图画布、元素列表和属性区。鼠标及键盘可选择、平移、缩放、画矩形、调整边界、删除、设置父级和文字关联、改类型/标签/文字、锁定、撤销/重做。支持Tab访问控件、Delete删除选中项、Escape取消未完成绘制、Ctrl/Cmd+Z及重做；输入框编辑时快捷键不得误删元素。分类同时用文字和视觉标记，不仅依赖颜色。保存草稿与确认版本为两个明确按钮；草稿有未保存指示，离开需保留/放弃选择。

第一版单图、单本地操作者；多页误开靠冲突检测处理，不实现协同编辑。本地服务最多8个并发请求、同一任务最多1个确认物化作业；普通请求读取超时10秒，长确认返回202及request_id后由单个有界本地作业继续，状态/恢复依据公共账本，不重复创建外部操作。

前端采用仓库内原生HTML/CSS、ES modules和SVG覆盖层，不引入CDN、遥测、在线字体或Node构建前置。Python 3.12核心环境提供专用受限回环HTTP服务及静态资源打包，领域层不依赖HTTP或浏览器。可通过已有Python测试驱动API，真实浏览器交互由T049记录；接口实现前先写安全合同。

## 2. 类型、坐标与有效文字

ReviewLayout为独立`schema_version=2`，不改变历史layout v1和VLM输出Schema。每个元素有`element_id`、`base_type=text|image|container|other`、`semantic_tags`、`bbox`、`parent_id?`、`text_region_ids`、`locked_fields`、`field_sources`。可选标签固定为`icon/character_illustration/illustration/background/button/panel/map/bar`；无标签表示未细分。Live2D/原画工程等仅放受限`user_declared`备注，不能升级为observed。

只读适配历史kind：text→text；image/icon/map→image；button/panel/bar→container；other→other；原kind保存在原始分析，适配标签保留原语义，允许用户校正。首次适配保留所有旧element_id与原关联，不修改旧文件。人工新增ID在首次保存时由服务端登记，客户端临时ID映射随幂等响应返回；移动、改类型不换ID。显式拆分/合并通过新增/删除动作及`replaces_ids`记录一对多/多对一关系；无关系的普通新增不可伪称继承旧身份。

全部写入为canonical整数xyxy，右下不包含。SVG屏幕坐标经逆变换映射，左上floor、右下ceil，只接受`0≤x1<x2≤width`和`0≤y1<y2≤height`；界面可约束拖动范围，服务端越界请求拒绝，不静默裁剪。允许重叠，父关系不是互斥分割。删除父项必须指定`detach_children`或`delete_subtree`，同步解除文字关联；循环、自父级、跨源引用及悬空关系拒绝。

原始`texts.json`始终保留。有效文字写入`review_texts.json`：旧OCR记录引用原text_id、原多边形/分数；`effective_text`、人工几何、`origin=human|ocr`及`correction_ref?`分别保存。人工新增text_region_id不冒充OCR ID，原分数/原OCR引用为空。改文字内容只重建文字投影和相应索引；不生成glyph、不画掉原图文字。文字来源和几何来源可分别追踪。

## 3. 草稿、保存、确认与身份

ReviewDocument绑定`task_id/source_id/canonical_ref/base_layout_ref/base_texts_ref`及哈希。初始有效基线为不可变模型结果；review head分别保存草稿head及最近confirmed head，互不混淆。

ReviewPatch字段为`request_id/base_draft_revision/base_confirmed_revision/actions`；动作白名单为`add_region/update_box/set_type/set_tags/set_parent/set_text_links/set_text/delete_region/set_lock/restore_revision`。内容最多1MiB、每次256动作；文档最多512元素/4096文字，备注及单条文字上限4096字符。超限拒绝且保留已有草稿。最多100步未保存动作的undo/redo在内存中管理；保存后仍可选择旧保存版本恢复为新草稿，绝不删除历史。锁定字段的直接修改先显式解锁；模型建议只能进入独立建议记录，不自动改有效内容。

保存：先写不可变patch和草稿快照到ArtifactStore，再于同一公共SQLite事务中按expected版本CAS更新head并登记幂等请求；并发旧版本返回409 `review_conflict`，重复request_id与同内容返回原版本，与不同内容返回`review_request_conflict`。页面保留待提交修改，并提供重新加载基线/人工重做，不隐式合并。服务端成功但响应丢失可按request_id读回；失败留下未发布素材可查，不回写模型产物。

确认：冻结保存草稿→校验/物化有效布局与文字→生成受影响切片、overlay和review manifest→验证所有hash→CAS发布confirmed head。每次确认记录开始、成功或失败；失败或进程退出不得推进head。幂等request_id保证最多一次发布，后台计算在相同冻结输入上可恢复；另一页面先改变草稿/确认head时本次发布冲突。磁盘不足为`review_storage_insufficient`，不删除旧证据来腾空间。

角色为`review_patch/review_draft/review_layout/review_texts/review_overlay/review_manifest`，矩形仍为`rect_crop`。导出一份版本目录时可映射为layout.json/texts.json/overlay.png，但文件声明校正schema与来源，不能冒充原模型产物或生产导出。确认manifest绑定代码/投影版本、taxonomy版本、操作者来源、时间、base refs、patch refs、输出hash和review版本；纯本地动作记录`external_calls=0`，不伪造provider request或批准。

## 4. 局部更新及下游绑定

依赖规则：框/增删影响该元素切片、关联及overlay；文字影响有效文字、关联及标签；类型/父级/标签影响布局和overlay。未变canonical+bbox的切片复用原ArtifactRef及SHA；所有新切片仍逐像素来自原canonical。父项删除/文字重关联的受影响闭包不得遗漏，不能仅按直接修改元素计算。

原PipelinePlan和UIStepBinding.revision≤2不扩展为人工历史；ReviewVersion使用独立单调版本，与TaskBudget.max_revisions及VLM/OCR计数无关；版本受原磁盘预算限制，不借“无模型修订上限”允许无限存储。新确认版本不改变已完成任务、根指纹或任何旧计划。T048提供`ReviewedLayoutBinding(task_id,review_revision,review_manifest_ref,layout_ref,texts_ref,canonical_ref)`的只读校验/导出，不提前实现GPU分支。

后续`ui plan --reviewed-task TASK_ID --review-revision REV ...`使用明确确认版本，与`--input-manifest/--query/--selection`入口互斥；可在规划时默认解析一次最新confirmed版本并显示，冻结后禁止运行时重新找最新。未确认草稿不得进入该入口。分割/补图的UISelection与child绑定review refs/hash和目标，不重复OCR/VLM。无review绑定的原始自动流程仍可用并标记`layout_origin=model`，不得悄悄替换已有确认布局。

新review发布后，既有计划保持原版本。用户选择采用新版本时创建新selection/子计划并使被替换的旧待执行child失效；已提交/unknown操作按原绑定取消或对账，不能换输入重放，根预算与已消耗次数继续累计。浏览器校正服务没有execute/approve/search/模型调用路由；review确认不是授权。

T029接入显式局部OCR/VLM时，人工文本ID与OCR证据ID分开，原OCR模型结果继续保留；human patch是user_declared上下文，模型不得伪造人工来源。重读结果先进入建议，用户采纳再生成新review。未提供该能力时页面不显示可执行的重识别按钮。

## 5. 本地接口与隔离

监听只允许`127.0.0.1`，端口默认由系统分配，不提供任意host参数。每次CLI启动生成短时单次bootstrap token，仅交给本机浏览器启动URL的fragment；页面立即清除fragment并POST换取有任务作用域的HttpOnly/SameSite=Strict会话cookie，token至少32随机字节，10分钟内未兑换失效，会话闲置30分钟失效。bootstrap和cookie均不进入日志、公共CLI JSON、manifest、LocalStorage或查询参数；JSON模式只返回无秘密入口及session状态，`--open`通过可信本机浏览器启动，不打印带token链接。

服务校验Host为本次精确回环host:port，写入必须匹配Origin及CSRF token，bootstrap只接受本页同源交换；默认拒绝CORS与跨来源访问，设置CSP禁止外部连接/脚本、frame-ancestors none、no-store及nosniff。模型正文、文字和标签按纯文本渲染，不注入HTML。静态文件和artifact读取都经固定路由及作用域allowlist，不做任意目录服务、不接受路径/URL下载。Cookie或bootstrap不能充当生成批准；同机其他不受信OS账户不在本次单操作者会话信任域内。

| 接口 | 请求/结果 | 约束 |
|---|---|---|
| POST /api/session | bootstrap交换 | 单次、过期拒绝、敏感体不记录 |
| GET /api/review | 基线、heads、只读版本清单、能力/限制 | 必须已认证；正文仅本地任务会话 |
| GET /api/artifacts/{artifact_id} | 已登记原图/对应版本产物 | 必须属于当前review引用闭包，非任意id读取 |
| POST /api/review/drafts | ReviewPatch→新版本/临时ID映射 | CAS与幂等；允许动作白名单 |
| POST /api/review/confirm | request_id、draft_revision、expected_confirmed_revision | 先产物完成后原子发布；确认中返回状态 |
| GET /api/review/requests/{request_id} | saved/confirming/confirmed/failed/conflict | 仅当前会话任务，支持响应丢失恢复 |

GET不改变草稿或发布状态。校正会话与保存记录复用同一公共账本，页面关闭无需保持Temporal Activity等待；本地会话关闭不取消原Workflow或付费任务。后续CPU物化可有界恢复，不引入第二调度器。

## 6. 验收与质量隔离

T043—T048先写对应合同/失败用例再实现；旧v1批准金样、v3历史行及unknown费用必须保持。T049复用T010三张商业截图及已有输出，真实本地浏览器覆盖画框/改字/分类/层级/锁定、快捷键、缩放映射、草稿重开、同图双页冲突、确认中断恢复；操作证据和前后版本截图留本地，模型/搜索/GPU调用全部0。可选网络拦截测试证明无外部请求，不能只用函数测试代替页面验收。

T035—T038分别标记`evaluation_lane=automatic|human_assisted|ground_truth`。人工辅助指标单列，原自动结果保持可评估；ground_truth必须来自独立冻结标注，不能直接导入当前review head。复用历史证据时只补受新增结构/依赖影响的检查，不为此重复已成功推理。
