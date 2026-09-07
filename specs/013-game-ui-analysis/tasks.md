# Tasks: 游戏 UI 解析工作流

**Feature**: 013-game-ui-analysis | **Date**: 2026-09-04 | **Branch**: dev-game-ui  
**Baseline**: `7b91c34deb23064aaa6b492ce105956a59b47b71`  
**状态**：50项中T001—T026与T043—T050共34项已完成；其余16项仍待实施。11项[LIVE]中T010、T017、T018、T024、T049已验收，其余6项未验收。所有环境命令继续使用conda run；历史mamba阻断、旧失败与unknown证据保留。

## 执行约定

依据[规格](spec.md)、[计划](plan.md)及配套合同。普通路径优先取得可用的“手动单图＋双后端搜索”，随后编辑、批次、正式验收。新任务依赖是开工依据；原G01全包和旧G依赖不再阻塞单图任务。

- 一个任务可以包含测试和实现，但必须先写其合同/政策/来源/安全/失败测试，再实现并运行；已有共享合同直接复用，不为每层复制相同矩阵。
- [P]只表示满足依赖后可独立开发；不是自动派发子代理。运行时能力、同一任务和批次child仍顺序调用。
- 本地任务记录代码/差异、命令和结果；外部执行才额外记录批准、实际受理次数、费用、资源和产物。缺条件保留未完成，不为纯函数或文档填不适用的费用字段。
- [LIVE]需明确开启、已有模型/服务、固定输入及预算和有效具体批准。按原规则保留失效/未知与失败证据，不自动部署、接受许可或沿用已消费GPU批准。
- 首版plan只本地规划，parse不需要GPU；旧Agent Responses预留规则不变。预留偏差且未超原批准限额时核算后自动继续；只有实耗超批准限额才budget_exceeded失败。
- 当前50项为增量清单；文末“旧T”仅为历史映射，不是当前依赖。FR/PER/SC、R、G、U-V与里程碑编号保留。
- 文档修订完成后只执行一次只读speckit-analyze并汇报；不自动修订、复核或循环运行。是否继续实施由用户后续指令决定，不自动commit。

## Phase 1：单图最小支撑

只建立三个开发样本、共享执行合同与必要步骤绑定；无全量样本、父子或GPU前置。

- [X] T001 核对dev-game-ui/Temporal基线及用户修改，逐文件审查旧worktree的10个草稿和4个设计提交，记录复用/重写范围；不重建工作区或重复合入。 文件：`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/tasks.md`。（依赖：无）
- [X] T002 固定英文横屏HUD、中文密集UI、竖屏三个开发样本和最小标注，提供隔离素材/账本及调用计数夹具、opt-in真实标记；三例以后只进入开发集，不要求先完成24例。 文件：`C:/Programs/LetsAIGC/tests/conftest.py`、`C:/Programs/LetsAIGC/tests/fixtures/ui_analysis/preview-cases.yaml`、`C:/Programs/LetsAIGC/tests/fixtures/ui_analysis/preview-annotations.json`、`C:/Programs/LetsAIGC/pyproject.toml`。（依赖：T001）
- [X] T003 先建立共享执行合同及v1兼容金样：单任务批准/输入hash拒绝、重复operation最多一次受理、unknown预留、取消门禁、预留调整后继续/不足停止/真实超限失败。只维护一套公共故障矩阵；UI输入/输出schema拒绝额外字段和不安全正文，旧CLI/指纹不变。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_schemas.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_execution.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_compatibility.py`、`C:/Programs/LetsAIGC/tests/fixtures/contracts/ui-schemas.json`、`C:/Programs/LetsAIGC/tests/fixtures/contracts/ui-v1-compatibility.json`。（依赖：T002）
- [X] T004 实现首版严格请求/产物DTO、UIProvider合同、ui_analysis静态注册、受限submit_step/resolve_ui_step和单任务账目判定，运行T003合同；v1→v2仅加入ui_step_bindings及事务迁移/回滚。先写该小迁移测试再实现；输入/步骤快照通过受限ref保存，旧submit和序列化语义不变。落单图资源/计数及必填预算配置，暂不创建父子或quota表。 文件：`C:/Programs/LetsAIGC/src/letsaigc/schemas/ui.py`、`C:/Programs/LetsAIGC/src/letsaigc/schemas/ui_provider.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_providers/base.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_providers/registry.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/registry.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/service.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/migrations.py`、`C:/Programs/LetsAIGC/configs/ui-analysis/default.yaml`、`C:/Programs/LetsAIGC/configs/ui-analysis/budget-example.yaml`、`C:/Programs/LetsAIGC/tests/integration/test_ui_migrations.py`。（依赖：T003）

## Phase 2：手动单图可运行（US1）

先获得真实手动单图产物；标注为开发/预览证据，正式质量尚未通过。

- [X] T005 [US1] 先写手动快照/作用域/HTTPS拒绝、全部EXIF与坐标/切片真值测试，再实现import素材层、ManualUIProvider、canonical及视图变换；复用AssetResolver的SSRF/MIME/大小检查。手动不初始化search/probe，正文与来源分别存受限素材，旧输入上限不变。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_manual_provider.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_coordinates.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_providers/intake.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_providers/manual.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/normalize.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/coordinates.py`。（依赖：T004）
- [X] T006 [P] [US1] 先写CPU OCR输入输出、静态loader/许可/hash、局部重读和轻量服务协议测试，再实现独立CPU环境锁、受控客户端/服务与Paddle适配。固定端点、认证、任务映射及响应丢失只读查询复用公共合同；只启用OCR，不部署SAM或依赖GPU所有权。原文/分数/多边形与视图身份保留，不自动下载模型。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_ocr.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_vision_service.py`、`C:/Programs/LetsAIGC/src/letsaigc/vision/base.py`、`C:/Programs/LetsAIGC/src/letsaigc/vision/client.py`、`C:/Programs/LetsAIGC/src/letsaigc/vision/service.py`、`C:/Programs/LetsAIGC/src/letsaigc/vision/ocr.py`、`C:/Programs/LetsAIGC/environment/vision-ocr.yml`、`C:/Programs/LetsAIGC/configs/runtime/vision.lock.yaml`、`C:/Programs/LetsAIGC/configs/runtime/vision.yaml`。（依赖：T005）
- [X] T007 [P] [US1] 先写严格VLM结构、资料注入、用量和观察/推测分离合同，再实现独立UI分析器；复用Responses连接与计价。全图/局部/筛选共享每图上限，原文不被建议覆盖，预留未知不记0；根plan本身不调用模型。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_analyzer.py`、`C:/Programs/LetsAIGC/src/letsaigc/agent/ui_analyzer.py`。（依赖：T005）
- [X] T008 [US1] 先写稳定元素/层级/文字关联/切片像素和manifest来源/脱敏合同，再实现布局融合、切片、overlay和完整产物索引。固定既有几何匹配规则，未受影响ID稳定；输出角色/原始与推测可区分，失败证据不覆盖。首版质量报告记录实测值与pending状态，不要求完整阈值引擎。 文件：`C:/Programs/LetsAIGC/tests/unit/test_ui_layout.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_manifest.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/layout.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/crops.py`、`C:/Programs/LetsAIGC/src/letsaigc/tracking/manifest.py`。（依赖：T005、T007）
- [X] T009 [US1] 先写单图CLI/Workflow合同和一次OCR后中断的恢复驱动，再接线manual→canonical→OCR→VLM→layout/crops→project。实现import/plan/execute/inspect/doctor及基本resume/cancel/reconcile；JSON无隐式确认，展示完整指纹/产物/费用。首版仅parse单图，缺编辑/批次能力明确not_ready，不被缺SAM/Comfy阻塞；只扩展必要消息和活动。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_cli.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_analysis_workflow.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_preview_recovery.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`、`C:/Programs/LetsAIGC/src/letsaigc/cli.py`、`C:/Programs/LetsAIGC/src/letsaigc/doctor.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_activities.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/worker.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/client.py`。（依赖：T006、T008）
- [X] T010 [US1] [LIVE] 使用已由操作者准备的真实OCR/VLM与固定分析预算跑三例手动链路，核验loader/模型/hash/许可、全部产物和实际费用；选择一例在OCR登记后中断并恢复，复用该证据供预览验收。缺能力如实未验收，不要求正式质量阈值；保存基线测量供后续开发集复用。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_manual_runtime.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_preview_recovery.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-manual.json`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-recovery.json`。（依赖：T009）

## Phase 3：双后端搜索与首版预览（US4）

两家搜索和自动切换是首版门槛；搜索协议可在手动真实环境准备期间开发，发布需两条链路证据。

- [X] T011 [US4] 先写SerpApi/Tavily请求、额度、计价、scope和路由合同，再建立共享搜索DTO与版本化配置。覆盖零/null、PAYGO排除、偏好/低水位/回切、单方不可用/双方耗尽及计价缺失；与公共执行套件分工，不重写通用操作恢复矩阵。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_image_search.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_quota_routing.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/search.py`、`C:/Programs/LetsAIGC/configs/providers/image-search.yaml`。（依赖：T004）
- [X] T012 [US4] 先写v2→v3迁移及最后额度竞争、探测合并/重启限频的搜索特例，再加入quota_scopes/snapshots/reservations/routes/probes五表。实现同事务单任务预算检查、选路、额度预留及持久计数，跨任务scope共享但不依赖父子预算组；实际费用仍只落operations。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_quota_ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/migrations.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/search_routing.py`。（依赖：T011）
- [X] T013 [P] [US4] 先写固定SerpApi协议/错误/账户字段夹具，再实现第一页Google Images及Account适配和已知search ID只读归档恢复；计价/条款按官方依据记录，原始密钥/URL/账户响应不持久化，受理不明交给公共unknown边界。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_serpapi.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/providers/serpapi.py`。（依赖：T011）
- [X] T014 [P] [US4] 先写固定Tavily协议/错误/usage夹具，再实现basic图片搜索和account/key额度归一化；禁自动参数升级/PAYGO/收费日志恢复。核实probe计价依据，缺失则能力未就绪；模型正文/账户秘密不进入历史，unknown交给公共边界。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_tavily.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/providers/tavily.py`。（依赖：T011）
- [X] T015 [US4] 先写SearchUIProvider候选/下载/去重/来源和同图两来源对照，再实现有界acquisition。根查询规划≤1次、查询/search尝试/切换≤3/3/2；每查询候选/下载/默认选图20/5/1，首张合格图后停止。瞬时URL留在受控边界，SHA与pHash去重/相关性分析用量归原任务；失败逐项可查。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_search_provider.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_providers/search.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_activities.py`。（依赖：T005、T007、T012、T013、T014）
- [X] T016 [US4] 先写单图search Workflow/CLI边界测试，再接入ui_analysis与Worker，plan --query --max-images 1冻结双后端范围并在execute后选路。inspect展示来源、切换原因、费用及结果；复用已取得图片，未知逻辑查询不换家补发，manual不探测。更大max_images在批次阶段前明确未就绪。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_search_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/worker.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`。（依赖：T009、T015）
- [X] T017 [US4] [LIVE] 两家各一次有界真实搜索并将各自取得的图片送入真实单图解析；使用已核实条款/计价、固定输入和分析预算，分别记录实际请求单位、费用、来源和筛选。可用供应商子集配置各验证一家，自动切换用固定额度响应验证，不故意耗尽账户；缺任一家记录不能宣告双后端预览完成。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_search_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-search.json`。（依赖：T016）
- [X] T018 [US4] [LIVE] 汇总三例手动、两家搜索与一次代表性恢复的有效证据，运行共享拒绝/预算套件和切换固定响应，发布“可用预览，完整质量验收待完成”。复用T010/T017真实记录，不为汇总重复推理；同步README与使用指南的首版入口、缺能力和停止原因。首版依赖闭包不得含SAM、Comfy、父子预算或全量评估。 文件：`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`、`C:/Programs/LetsAIGC/README.md`、`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/quickstart.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-acceptance.json`。（依赖：T010、T017）

## Phase 3A：人工校正单图预览（US7，T018之后）

独立目标：不调用模型或GPU，通过可视化页面校正既有解析结果并发布可追溯版本。编号追加保留T001—T042；执行顺序由依赖决定，不按数字排序。详细契约见[review.md](contracts/review.md)。

- [X] T043 [US7] 先写Review v2/白名单动作/旧v1读取合同及迁移失败测试：原OCR不改、人工ID不伪造证据、框/关系/标签校验、锁定、无权限写入拒绝、CAS冲突与幂等；记录v3→v4旧指纹/unknown行金样，新增纯函数测试不触发浏览器/模型。 文件：`tests/contract/test_ui_review_contract.py`、`tests/unit/test_ui_review_actions.py`、`tests/integration/test_ui_review_migrations.py`。（依赖：T018）
- [X] T044 [US7] 实现独立Review DTO、旧layout只读适配及ArtifactStore patch/快照；v3→v4增加ui_review_heads/ui_review_revisions/ui_review_requests，原子分配版本/CAS/幂等与失败状态；先停全部写入者并备份后才迁移现场库，不扩展UIStepBinding.revision≤2或旧根指纹；通过T043。 文件：`src/letsaigc/schemas/ui_review.py`、`src/letsaigc/ui_analysis/review.py`、`src/letsaigc/pipelines/ledger.py`、`src/letsaigc/pipelines/migrations.py`、`configs/ui-analysis/review.yaml`。（依赖：T043）
- [X] T045 [US7] 先写类型/文字/几何/父子变更的依赖闭包、未变ID/hash复用及确认中断/存储不足测试，再实现纯CPU有效布局/文字/矩形切片/overlay/manifest物化，全部hash验证后CAS发布确认head；原输出/自动分数不覆盖；重复确认最多发布一次。 文件：`tests/unit/test_ui_review_projection.py`、`tests/integration/test_ui_review_publish.py`、`src/letsaigc/ui_analysis/review_projection.py`、`src/letsaigc/ui_analysis/crops.py`、`src/letsaigc/tracking/manifest.py`。（依赖：T044）
- [X] T046 [US7] 先写回环Host/Origin/CSRF/令牌期限/跨task读取/路径穿越/恶意正文/1MiB写入拒绝和零付费调用合同，再实现ui review入口、受限HTTP路由及会话；只授予校正权，敏感令牌不进stdout/日志/持久浏览器存储，不新增Agent工具，不暴露approve/execute/模型路由。 文件：`tests/contract/test_ui_review_server.py`、`tests/contract/test_ui_review_cli.py`、`src/letsaigc/ui_analysis/review_server.py`、`src/letsaigc/ui_analysis/cli.py`、`src/letsaigc/doctor.py`。（依赖：T045）
- [X] T047 [US7] 实现本地原图画布、元素列表、属性面板和联动矩形/类型/标签/文字/父级/锁定编辑，缩放逆映射、快捷键、100步撤销重做、保存草稿/确认区分、重开与冲突保留；先建立坐标/状态操作用例，打包静态资源并验证无CDN依赖，缺模型重读能力不显示可执行按钮。 文件：`src/letsaigc/ui_analysis/review_web/index.html`、`src/letsaigc/ui_analysis/review_web/app.js`、`src/letsaigc/ui_analysis/review_web/styles.css`、`tests/integration/test_ui_review_web.py`、`pyproject.toml`。（依赖：T046）
- [X] T048 [US7] 先写ReviewedLayoutBinding只读导出/确认状态/hash/跨task重登记、旧reader拒绝新结构和模型建议不覆盖人工值的合同，再提供冻结版本读取与来源分类；只为后续T019/T021提供数据入口，不注册尚未实现的GPU/局部模型能力；明确automatic/human_assisted/ground_truth隔离。 文件：`tests/contract/test_ui_review_binding.py`、`src/letsaigc/schemas/ui_review.py`、`src/letsaigc/ui_analysis/review.py`、`src/letsaigc/ui_analysis/review_projection.py`。（依赖：T047）
- [X] T049 [US7] [LIVE] 复用T010三个商业截图及成功产物，在真实本地浏览器完成SC-013—016：补图标/改文字/分类/层级、增删改框/锁定、不同缩放、快捷键、草稿重开、双页面冲突和确认中断恢复；记录前后版本及截图，核验外部搜索/OCR/VLM/GPU全0、原任务/unknown费用不变。SC-017的绑定与来源通过合同复核，真实局部模型/child联动留T029/T030，不能提前标为现场通过。 文件：`tests/integration/test_ui_review_runtime.py`、`.local/validation/ui-analysis/review-acceptance.json`。（依赖：T048）
- [X] T050 [US7] 汇总T049有效证据及原预览兼容，运行完整conda pytest/ruff与diff检查，同步新页面启动、确认含义、来源/未开放能力及版本恢复指南；仅勾选人工校正预览，不宣告正式质量或GPU编辑交付。 文件：`README.md`、`docs/game-ui-analysis.md`、`docs/development-handoff-2026-09-05.md`、`specs/013-game-ui-analysis/quickstart.md`、`specs/013-game-ui-analysis/tasks.md`、`.local/validation/ui-analysis/review-regression.json`。（依赖：T049）

## Phase 4：自动提案、拆解与补图（US2/US3）

默认提案后直接具体批准；父子预算、SAM/Comfy和局部修订现在才进入依赖。

- [X] T019 [US3] 先写后续编辑/父子边界合同：确认布局版本/原自动布局→区域提案→预览→具体批准、候选编号/文件覆盖互斥、选择变化使旧child失效、根/子作用域与共享费用。只增加GPU与父子的差异用例，通用拒绝和重复受理引用T003；形成MaskedGenerationPlan v2和选择机器合同。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_selection_cli.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_child_approvals.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_masked_plan.py`。（依赖：T050）
- [X] T020 [US3] 实现v4→v5（承接T044的review v4）的budget_groups/child_bindings/operation_charges三表及先行迁移测试，扩展UI选择/候选/子请求DTO和根费用/修订归组。分割/补图定义此时注册；新旧单任务结算兼容，费用不重复入账，具体批准原子激活候选版本并禁用旧待执行child。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_child_ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/migrations.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/service.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/registry.py`、`C:/Programs/LetsAIGC/src/letsaigc/schemas/ui.py`、`C:/Programs/LetsAIGC/src/letsaigc/schemas/agent.py`。（依赖：T019）
- [X] T021 [US3] 实现分析授权内的区域提案和预览：接入--reviewed-task/--review-revision入口，冻结T048的确认版本，不重跑该输入OCR/VLM；保留显式原自动布局路径。复用VLM布局/目标，不增加免费规划次数；确定性校验bbox/元素、保留区域及来源。明确方案直接形成待批准准备信息；含混时列候选编号，ui select --candidate与--selection二选一且登记零模型调用。引用/hash由可信入口补齐，普通用户不手填机器合同。 文件：`C:/Programs/LetsAIGC/src/letsaigc/agent/ui_analyzer.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/selection.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_selection.py`。（依赖：T020）
- [X] T022 [US2] 先写SAM环境/安全loader/hash/预处理映射合同，再准备独立SAM锁结构和服务GPU执行凭证/卸载边界；仅使用固定官方safetensors和本地文件模式，不自动下载或.pt回退。为服务适配增加一次响应丢失恢复边界测试，不复制公共账本矩阵。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_segmentation.py`、`C:/Programs/LetsAIGC/environment/vision-segmentation.yml`、`C:/Programs/LetsAIGC/configs/runtime/vision.lock.yaml`、`C:/Programs/LetsAIGC/configs/runtime/vision.yaml`、`C:/Programs/LetsAIGC/src/letsaigc/vision/service.py`。（依赖：T020）
- [X] T023 [US2] 在先行领域测试后实现SAM提示/轮廓回映、估计alpha和字形颜色/连通域细化，保留原始切片、mask、alpha和glyph角色及低确定性。每图提示≤64，绑定具体selection/version/hash及GPU批准；可通过受限固定样本入口验证，不对Agent暴露执行工具。 文件：`C:/Programs/LetsAIGC/tests/unit/test_ui_text_assets.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_segmentation_assets.py`、`C:/Programs/LetsAIGC/src/letsaigc/vision/segmentation.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/text_assets.py`。（依赖：T021、T022）
- [X] T024 [US2] [LIVE] 用固定样本和具体批准验证真实SAM加载、mask/alpha/glyph角色、canonical映射、GPU释放及实际用量；记录分割开发测量。缺能力保持未验收，正式阈值在最终开发集阶段冻结，不阻塞后续编辑接线。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_segmentation_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/segmentation-runtime.json`。（依赖：T023）
- [X] T025 [US3] 先写原生mask编译/四工件、旧T2I/I2I金样和合成像素合同：配对输入、极性/通道/变换、扩张羽化裁到ROI并避开keep、空mask零生成、尺寸错误拒绝和区外逐像素相同。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_inpaint_compile.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_inpaint_pixels.py`。（依赖：T020）
- [X] T026 [US3] 实现MaskedGenerationPlan v2的编译分派及原生sdxl-inpaint UI/API图、工作流合同和recipe；旧v1字节/编译语义不变。实现冻结最终mask、回映及CPU精确合成，失败候选及模型/recipe/编译hash均入证据。 文件：`C:/Programs/LetsAIGC/src/letsaigc/workflows/compiler.py`、`C:/Programs/LetsAIGC/src/letsaigc/backends/comfy.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/inpaint.py`、`C:/Programs/LetsAIGC/configs/workflows/recipes/sdxl-inpaint.yaml`、`C:/Programs/LetsAIGC/workflows/ui/sdxl-inpaint.json`、`C:/Programs/LetsAIGC/workflows/api/sdxl-inpaint.json`、`C:/Programs/LetsAIGC/workflows/contracts/sdxl-inpaint.yaml`。（依赖：T025）
- [ ] T027 [US3] [LIVE] 只读核验锁定ComfyUI的object_info与原生节点/四工件，运行旧编译金样和本地像素合同；不提交生成。缺已部署服务保留未完成，作为真实补图前能力门槛。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_inpaint_object_info.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/inpaint-object-info.json`。（依赖：T026）
- [ ] T028 [US3] 运行T019选择/子批准合同及review版本变更/旧child失效测试并接线decompose/reconstruct：推荐选择冻结后直接展示具体分割计划，批准同时确认范围；分割后最终image/mask再形成补图批准。SAM/Comfy顺序交接并确认释放，选择候选替换使旧待执行批准失效；parse仍可独立完成。 文件：`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_activities.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/worker.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_reconstruction.py`。（依赖：T021、T024、T027）
- [ ] T029 [US3] 接入人工review的显式局部重读/复核，结果只作建议、采纳才改版本，锁定字段不被覆盖；先写四种枚举局部修订与依赖闭包测试，再实现reread_text/adjust_segmentation/review_region/regenerate。未变ID/产物复用，自动可调仅prompt/negative_prompt/seed；新mask/model/recipe须新批准，沿同一编辑链/根组计数，不重置预算，失败版本保留。 文件：`C:/Programs/LetsAIGC/tests/unit/test_ui_revision.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/revision.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`。（依赖：T028）
- [ ] T030 [US3] [LIVE] 补验SC-017真实局部复核不覆盖锁定字段及review版本绑定，验证真实decompose和scene_background/map_surface补图，覆盖默认提案直接批准、候选/高级覆盖、最终mask批准、区外像素一致、有限修订及GPU受理后一次恢复观察。保存实际质量值和谱系，复用公共故障证据；同步编辑操作指南，缺真值不报背景恢复准确率。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_reconstruction_runtime.py`、`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/editing-runtime.json`。（依赖：T029）

## Phase 5：两种来源的顺序批次（US5）

每条输入可追踪，父级共享预算和取消；不复制单图执行引擎。

- [ ] T031 [US5] 先写两种来源顺序批次的差异合同：10项/第11拒绝、精确重复映射/近似只提示、两种失败策略、单活动child、父取消竞争、共同次数和Continue-As-New边界。通用operation/费用故障复用T003/T019，不再次复制全矩阵。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_batch.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_batch_cli.py`。（依赖：T030）
- [ ] T032 [US5] 实现ui_batch父级一次供给后顺序单图child，登记父子scope、根费用与来源映射；GPU仍各自具体批准。取消先关闭根门禁，再REQUEST_CANCEL并保留未决归属；Continue-As-New只在无活动child安全边界。 文件：`C:/Programs/LetsAIGC/src/letsaigc/pipelines/registry.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_activities.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/worker.py`。（依赖：T031）
- [ ] T033 [US5] 接入多项import/plan和max_images≥2，按原始手动条目数/搜索上限选路；完善父子inspect、部分结果和候选/批准等待，复用manifest来源谱系与MLflow派生投影。先写对应输出/谱系合同再实现，正式批次能力不改变旧Agent输入限制。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_batch_evidence.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`、`C:/Programs/LetsAIGC/src/letsaigc/tracking/manifest.py`、`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`。（依赖：T032）
- [ ] T034 [US5] [LIVE] 有界验证手动与搜索两种来源的真实顺序批次及获批子生成，保留10项成功/失败/等待/未处理混合场景及真实/替身属性；取消无孤立提交，输入至结果映射齐全。复用已有单图与GPU证据，仅补批次差异。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_batch_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/batch-runtime.json`。（依赖：T033）

## Phase 6：正式质量与完整验收（含US6）

补齐完整数据与质量门槛，汇总有效证据，只补受影响或缺失的真实观察。T035可在预览后先准备。

- [ ] T035 分别登记原自动输出、人工辅助输出与独立冻结真值，禁止将校正head直接用作评估标注；补齐24例完整数据/标注及底图分组，开发16/评估8、已知背景至少8且4/4分布；三个预览样本只在开发集。此任务可在预览后准备，不回头阻塞预览；私有图仅本地引用，拥有分发权的样本才可入Git/DVC。 文件：`C:/Programs/LetsAIGC/tests/fixtures/ui_analysis/cases.yaml`、`C:/Programs/LetsAIGC/tests/fixtures/ui_analysis/annotations.json`。（依赖：T050）
- [ ] T036 先写automatic/human_assisted/ground_truth分离与不覆盖模型分数合同，再写CER/IoU/文字关联/语义/背景误差/接缝度量分母与样本适用性测试，再实现正式质量计算与draft→frozen门槛；使用评估集前校验阈值及样本hash，未知背景不报恢复准确率。 文件：`C:/Programs/LetsAIGC/tests/unit/test_ui_quality.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/quality.py`、`C:/Programs/LetsAIGC/configs/eval/ui-analysis.yaml`。（依赖：T035）
- [ ] T037 [LIVE] 在16例开发集上建立OCR/VLM/分割/补图质量基线并冻结阈值和版本；复用适用的预览/编辑实测，只补缺失样本或已改变依赖。真实额外调用另有明确预算/批准，独立评估集不得用于调参。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_quality_runtime.py`、`C:/Programs/LetsAIGC/configs/eval/ui-analysis.yaml`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/development-quality.json`。（依赖：T030、T036）
- [ ] T038 [LIVE] 用8例独立评估集按冻结规则评估，结合开发集核对24例M-U1必需产物、至少8例已知背景真值与全部角色/像素硬检查；未达标保留失败，不降低阈值。记录正式质量证据，不能用预览报告代替。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_quality_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/evaluation-quality.json`。（依赖：T037）
- [ ] T039 [US6] [LIVE] 纳入T049已验证的草稿/确认恢复证据；汇总旧历史兼容、OCR后恢复、GPU受理后、搜索提交/响应窗口和父取消的已有证据；仅缺少真实记录或受版本变更影响时补对应观察。最终恢复索引区分真实/替身，确认unknown保留、无重复受理及资源归属，不无条件重跑全部故障。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_recovery_runtime.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_temporal_replay.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/recovery-acceptance.json`。（依赖：T034、T038）
- [ ] T040 依据实际交付完成使用/恢复/能力限制说明、Agent指南和Quickstart；合成秘密扫描覆盖日志/异常链/历史/manifest/移交结果，生产导出复用既有许可与人工门槛。先写新增公共输出的安全合同再改输出代码（若需要），不得因文档任务扩大运行特性。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_security.py`、`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`、`C:/Programs/LetsAIGC/docs/agent-quickstart.md`、`C:/Programs/LetsAIGC/README.md`、`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/quickstart.md`。（依赖：T039）
- [ ] T041 从仓库根目录运行一次完整conda run --no-capture-output -n letsaigc-core pytest、同环境ruff check .及git diff --check，检查新增公共schema/compiler/CLI的既有回归；只修实际失败涉及的范围，不重复已有效的网络/GPU验收。记录本次输出与无法执行原因。 文件：`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/tasks.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/regression.json`。（依赖：T040）
- [ ] T042 核对FR/PER/SC、R01—R16、U-V01—U-V14与M-U1—M-U3的证据和缺项，建立完整验收索引。只有正式质量、真实能力和必要回归均满足才宣布完整交付；保留用户修改/旧worktree，不自动commit。 文件：`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/tasks.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/acceptance-index.json`。（依赖：T041）

## 首版与人工校正增量依赖

T018的依赖闭包仍仅为T001—T017，其已完成记录不改写。新人工校正链T043→T044→T045→T046→T047→T048→T049→T050依赖T018；T019及T035现在依赖T050。T050依赖闭包不含T019之后的GPU或正式评估。它不含T019之后的父子预算、SAM、Comfy、批次或正式质量评估。先交付手动链路T010，再用T017/T018发布包含两家搜索的预览；T011—T016允许按真实接口依赖提前准备，不被缺现场凭据的验收任务阻塞。

2026-09-06 用户授权按剩余依赖使用 Luna xhigh 子代理并行实现。T020 可按 DTO 与账本/服务的独占文件拆分，接口先协调、最终统一集成；T035 仅依赖 T050，可独立准备，缺素材/独立真值时保留未完成。T020 验收后，T021、T022、T025 可按独占文件分支并行；后续各自仍遵守原前置任务。公共 registry/ledger/CLI/manifest 同一文件始终由单一代理负责，真实外部验证串行。历史 T006/T007、T013/T014 的并行安排不变。主代理负责合同审查、集成测试及任务勾选，完整交付仍以 T042 为准，不把预览或替身测试标为 M-U1—M-U3 全部通过。

## 需求与验收映射

| 原需求 | FR/PER | 当前任务 |
|---|---|---|
| R01 输入和供给合同 | FR-001—009；PER-006 | T003、T004、T005、T011、T015、T016、T031、T032 |
| R02 手动快照/选择 | FR-002—009、049—052 | T005、T009、T010、T031、T032、T033、T034 |
| R03 标准图/坐标 | FR-010—012、018 | T005、T008 |
| R04 OCR/重读 | FR-013—014、029 | T006、T007、T010、T029、T037 |
| R05 视觉结构/权限 | FR-015—017；PER-003、006 | T003、T007、T008、T021、T040 |
| R06 稳定布局/切片 | FR-017—019、029 | T008、T023、T029 |
| R07 视觉环境/就绪 | FR-021；PER-002、004、007、009 | T006、T010、T022、T023、T024、T027、T030、T037 |
| R08 精细资产/真实性 | FR-019—021、028 | T023、T024、T028、T030、T037、T038 |
| R09 受限蒙版补图 | FR-022—028；PER-007 | T019、T020、T021、T022、T023、T024、T025、T026、T027、T028、T029、T030 |
| R10 批准/共同资源 | FR-026、035、049、054；PER-002—004、007 | T003、T004、T012、T019、T020、T028、T031、T032、T033、T034 |
| R11 额度/路由 | FR-030—042、046—048 | T011、T012、T013、T014、T015、T016、T017、T018 |
| R12 下载/来源安全 | FR-002、005—007、043—045；PER-005—006 | T005、T011、T013、T014、T015、T016、T017、T040 |
| R13 幂等/修订/取消 | FR-029、039—041、051—055 | T003、T004、T006、T009、T016、T020、T028、T029、T031、T032、T033、T034、T039 |
| R14 顺序批次 | FR-007—008、049—051 | T031、T032、T033、T034 |
| R15 质量集/阈值 | PER-008—009 | T002、T035、T036、T037、T038 |
| R17 人工校正（2026-09-06新增） | FR-056—067；PER-003—009 | T043、T044、T045、T046、T047、T048、T049、T050、T019、T021、T029、T030、T035、T036、T039 |
| R16 来源/验收/兼容 | FR-006、028、044、052—055；PER-001、004—009 | T001、T002、T003、T008、T010、T017、T018、T024、T027、T030、T033、T034、T037、T038、T039、T040、T041、T042 |

| 成功标准 | 当前任务 |
|---|---|
| SC-001 | T008、T010、T038、T042 |
| SC-002 | T005、T009、T010、T016、T018 |
| SC-003 | T005、T015、T016 |
| SC-004 | T035、T036、T037、T038 |
| SC-005 | T023、T024、T025、T026、T027、T028、T029、T030、T038 |
| SC-006 | T003、T004、T019、T020、T021、T025、T028 |
| SC-007 | T011、T012、T013、T014、T017、T018 |
| SC-008 | T003、T006、T009、T018、T022、T039 |
| SC-009 | T031、T032、T033、T034 |
| SC-010 | T008、T019、T029、T030 |
| SC-011 | T003、T005、T007、T013、T014、T040 |
| SC-012 | T018、T024、T027、T030、T034、T037、T038、T039、T040、T041、T042 |
| SC-013 | T043—T049 |
| SC-014 | T045、T047、T049 |
| SC-015 | T043—T046、T049 |
| SC-016 | T043、T046、T048、T049 |
| SC-017 | T048、T019、T021、T029、T030、T035、T036 |

| 验收 | 检查职责 | 当前任务 |
|---|---|---|
| U-V01 | 坐标/EXIF/两来源一致 | T005、T008 |
| U-V02 | OCR原文及真实CPU | T006、T010、T037 |
| U-V03 | VLM结构/权限及提案 | T007、T010、T021 |
| U-V04 | 布局/稳定ID/引用 | T008、T029 |
| U-V05 | 矩形/mask/alpha/glyph角色 | T023、T024、T030、T038 |
| U-V06 | 原生编译及旧金样 | T025、T026、T027 |
| U-V07 | mask/区外像素与真实补图 | T025、T026、T030、T038 |
| U-V08 | 批准/输入/预算拒绝与偏差判定 | T003、T004、T019、T020、T028 |
| U-V09 | 公共恢复及能力边界 | T003、T006、T009、T016、T022、T039 |
| U-V10 | 局部修订依赖闭包 | T029、T030 |
| U-V11 | 手动零探测与双后端搜索 | T005、T011、T012、T013、T014、T015、T016、T017、T018 |
| U-V12 | 输入/日志/错误/历史脱敏 | T003、T005、T006、T007、T013、T014、T015、T040 |
| U-V13 | 额度竞争及父子共享/取消 | T012、T020、T031、T032、T033、T034 |
| U-V15（新增） | 人工校正、确认版本及零推理恢复 | T043、T044、T045、T046、T047、T048、T049、T050 |
| U-V14 | 真实能力/来源/正式验收 | T018、T030、T034、T038、T039、T040、T041、T042 |

## 历史G映射（不作为新依赖）

原T02—T12是Temporal历史编号。旧G状态与原依赖保持快照，实际开工由新三位任务依赖决定；G00的历史完成不把新T001自动勾选。原本地主计划仍为忽略的[历史记录](../../.doc/plan/game-ui-analysis-workflow-plan.md)。

| 原步骤 | 原依赖 | 历史状态 | 当前覆盖 |
|---|---|---|---|
| G00 | T12 | DONE | T001 |
| G01 | G00,T02 | IN_PROGRESS | T002、T003、T004、T006、T011、T012、T019、T020、T022、T035、T036 |
| G02 | G01,T03 | DRAFT | T005 |
| G03 | G01 | TODO | T006、T010、T037 |
| G04 | G01 | TODO | T007、T010、T017、T021、T037 |
| G05 | G02,G03,G04 | DRAFT | T008 |
| G06 | G05,T08,T09,T10 | TODO | T009、T010、T018、T039 |
| G07 | G01,G02,T04 | DRAFT | T022、T023、T024、T037 |
| G08 | G01,T06 | DRAFT | T025、T026、T027 |
| G09 | G06,G07,G08,T07 | DRAFT | T019、T020、T021、T028、T029、T030 |
| G10 | G01,G04,T03,T04 | TODO | T011、T012、T013、T014、T015、T016、T017、T018 |
| G11 | G09,G10,T10 | TODO | T031、T032、T033、T034 |
| G12 | G11,T11,T12 | TODO | T035、T036、T037、T038、T039、T040、T041、T042 |

历史证据：Temporal 非 GPU 回归为 154 passed / 1 skipped，原唯一获批 GPU 验收为 1 passed / 154 deselected，均属于前置工作，原批准不可重用。旧 worktree `C:/Programs/LetsAIGC/.local/worktrees/game-ui-analysis` 保留 10 个未迁入源码草稿和 `74f24eb`、`28085a4`、`fac4b8e`、`8a1badd` 四个独有设计提交。T001 逐文件审查复用；保留主目录用户的 `.env.example` 修改，不复制其他工作区秘密。旧原始记录继续保留在本地主计划第 9.4 节。

## 原96项任务到新任务

旧清单全部未勾选；以下映射只归并责任，不表示完成，也不强制执行顺序。重复的公共故障断言集中到T003，能力/批次特例和真实证据仍保留。

| 旧任务 | 新任务 |
|---|---|
| 旧T001 | T001 |
| 旧T002 | T002 |
| 旧T003 | T002、T035 |
| 旧T004 | T004、T011 |
| 旧T005 | T003、T004、T019 |
| 旧T006 | T003、T004、T012、T020、T039 |
| 旧T007 | T003、T019、T020 |
| 旧T008 | T006、T022 |
| 旧T009 | T004、T020 |
| 旧T010 | T004、T005、T015 |
| 旧T011 | T004、T016、T020、T032 |
| 旧T012 | T004、T012、T020 |
| 旧T013 | T004、T012、T020 |
| 旧T014 | T006、T022 |
| 旧T015 | T006、T022 |
| 旧T016 | T036 |
| 旧T017 | T036、T037 |
| 旧T018 | T004、T018 |
| 旧T019 | T005 |
| 旧T020 | T005 |
| 旧T021 | T006 |
| 旧T022 | T007 |
| 旧T023 | T008 |
| 旧T024 | T009、T016、T021 |
| 旧T025 | T005 |
| 旧T026 | T005 |
| 旧T027 | T006 |
| 旧T028 | T007 |
| 旧T029 | T010、T037 |
| 旧T030 | T010、T017、T037 |
| 旧T031 | T008、T036 |
| 旧T032 | T008 |
| 旧T033 | T009、T016 |
| 旧T034 | T009 |
| 旧T035 | T010、T018 |
| 旧T036 | T003、T009、T039 |
| 旧T037 | T003、T019、T031 |
| 旧T038 | T008、T033、T040 |
| 旧T039 | T004、T009、T016、T028、T039 |
| 旧T040 | T009、T016、T028、T033 |
| 旧T041 | T008、T033 |
| 旧T042 | T003、T039 |
| 旧T043 | T018、T039 |
| 旧T044 | T022、T023 |
| 旧T045 | T023 |
| 旧T046 | T023 |
| 旧T047 | T022、T023 |
| 旧T048 | T023 |
| 旧T049 | T023 |
| 旧T050 | T023、T024 |
| 旧T051 | T024 |
| 旧T052 | T024、T037 |
| 旧T053 | T025 |
| 旧T054 | T025 |
| 旧T055 | T025、T029 |
| 旧T056 | T019、T028 |
| 旧T057 | T020 |
| 旧T058 | T026 |
| 旧T059 | T026 |
| 旧T060 | T026 |
| 旧T061 | T027 |
| 旧T062 | T028 |
| 旧T063 | T029 |
| 旧T064 | T003、T019、T028、T029 |
| 旧T065 | T030、T037 |
| 旧T066 | T030、T039 |
| 旧T067 | T011、T013、T014 |
| 旧T068 | T011、T012 |
| 旧T069 | T011、T013、T014、T016 |
| 旧T070 | T015、T016 |
| 旧T071 | T011 |
| 旧T072 | T011、T012 |
| 旧T073 | T013 |
| 旧T074 | T014 |
| 旧T075 | T012 |
| 旧T076 | T012、T015 |
| 旧T077 | T015 |
| 旧T078 | T016 |
| 旧T079 | T016 |
| 旧T080 | T017 |
| 旧T081 | T017 |
| 旧T082 | T031 |
| 旧T083 | T019、T031 |
| 旧T084 | T031、T033 |
| 旧T085 | T032 |
| 旧T086 | T032 |
| 旧T087 | T033 |
| 旧T088 | T031、T034、T039 |
| 旧T089 | T034 |
| 旧T090 | T035、T036、T037 |
| 旧T091 | T038 |
| 旧T092 | T039 |
| 旧T093 | T040 |
| 旧T094 | T018、T030、T033、T040 |
| 旧T095 | T041 |
| 旧T096 | T042 |

## 原13项[LIVE]未完成要求

| 原[LIVE]任务 | 新[LIVE]承接 | 保留的真实要求 | 状态 |
|---|---|---|---|
| 旧T029 | T010、T037 | OCR静态加载/hash/许可、真实识别及开发阈值 | 未验收 |
| 旧T030 | T010、T017、T037 | 真实VLM/用量及语义开发阈值 | 未验收 |
| 旧T035 | T010、T018 | 真实手动完整CLI及无搜索依赖 | 未验收 |
| 旧T043 | T018、T039 | 单图中断恢复与适用旧历史重放 | 未验收 |
| 旧T051 | T024 | 真实SAM及资源释放 | 未验收 |
| 旧T061 | T027 | 真实object_info只读与原生工件 | 未验收 |
| 旧T065 | T030、T037 | 真实补图、像素及开发质量 | 未验收 |
| 旧T066 | T030、T039 | 编辑CLI、具体批准/修订及GPU恢复 | 未验收 |
| 旧T080 | T017 | 两家真实额度/搜索记录 | 未验收 |
| 旧T081 | T017 | 搜索相关性筛选与单图解析 | 未验收 |
| 旧T089 | T034 | 两种来源批次与完整输入映射 | 未验收 |
| 旧T091 | T038 | 独立评估及完整质量 | 未验收 |
| 旧T092 | T039 | 真实恢复证据缺口与对账 | 未验收 |

## 前阶段文档验证与修订快照

以下保留原Tasks生成及上次Analyze修订的实测记录；T001—T096、依赖和结论均指当时版本。本次执行顺序及结果以当前42项和下节为准，不将历史测试算作本轮结果。

Tasks 生成验证（2026-09-04）：编号/标签/绝对路径、96 项依赖及并行波次、需求/验收映射、文档链接/PowerShell 语法及空白检查通过；Gxx 历史状态和实测证据保持不变。pytest 与 ruff 本次均因 mamba 无法写入 Miniforge 激活脚本而退出 1，未取得运行检查结果。本轮未勾选任何实现或 [LIVE] 任务，下一步为 speckit-analyze。

## Analyze 发现项修订（用户授权，2026-09-04）

本节是文档修订记录，不勾选实现或真实验收任务。C1 由 T023 的先行 manifest/来源合同及 T032 的显式前置处理；I1 由 T070/T079/T081 的单图 search 接线与 max_images 选路处理；U1 由 UISelection、ui select、T024/T056/T062 处理；I2 由 SC-006、T007/T013/T039 的预算准入和异常失败处理；U2 由 T008/T015/T036 的只读 operation 查询及缺回执恢复处理。复核进一步明确 parse 跳过选择、各模式参数互斥、仅GPU子请求必须绑定选择及分割请求的选择字段，以及根门禁关闭后禁止新增付费对账。任务编号和总数仍为96，原G依赖/历史状态不变。
本次修订验证（2026-09-04）：12份Markdown的51个本地链接、14个PowerShell代码块、1个JSON示例及空白检查通过；96项任务编号/依赖和并行波次有效，55项FR、9项PER及12项SC均有任务映射，13项[LIVE]仍未勾选。受保护的.env.example、AGENTS.md、原本地主计划及Constitution哈希保持不变。本次实际运行 mamba run -n letsaigc-core pytest 与 mamba run -n letsaigc-core ruff check . 均因 D:\Programs\miniforge3\condabin 和 Scripts 的激活脚本写权限被拒而退出1，未执行pytest/ruff检查；没有将历史结果计作本次验证，也未修改系统环境。最终一致性结论由随后只读speckit-analyze输出。

## 本次文档修订验证

2026-09-04，本轮仅修订013的12份Markdown：

- 42项任务连续唯一、依赖无环，4项[P]的两组并行文件无冲突；T018依赖闭包仅T001—T017，不含SAM、Comfy、父子预算或完整24例质量验收。
- 55条FR、9条PER、12条SC及R01—R16/U-V01—U-V14都有任务映射；旧96项全部映射，原13项[LIVE]要求由10项新[LIVE]承接且全部未勾选。
- 45个本地链接、14段PowerShell和1段JSON结构示例检查通过；未来配置/CLI均明确标待实现，不执行示例。tracked git diff --check及12份未跟踪文档的独立no-index空白检查无诊断。
- .env.example、AGENTS.md、本地主计划和Constitution的SHA-256与本轮开始一致；历史G状态、实测数据及Specify/Plan/Tasks验证记录保留；.doc仍忽略，教程、运行代码及配置无本轮修改。
- 本次实际尝试一次 mamba run -n letsaigc-core pytest 和一次 mamba run -n letsaigc-core ruff check .，均退出1，仅报告无法写入 D:\Programs\miniforge3\condabin / Scripts 的激活脚本；未取得测试或lint结果。未修系统环境，也未引用历史结果替代。
- 文档修订不计为运行验收，不勾选实现任务，不创建Git commit。最终Analyze只在随后对话汇报；即使有发现，也不自动修订或复核。

## Implement：T001完成与环境阻断（2026-09-04）

用户随后调用speckit-implement，授权进入实施。前一轮只读Analyze已完成且无发现；本轮没有再次Analyze。前置脚本正确定位013，requirements.md为16/16完成、0项未完成；无before_implement扩展钩子。当前分支仍为dev-game-ui，HEAD为7b91c34deb23064aaa6b492ce105956a59b47b71；原Temporal基线不重做、不重新合入。现有.gitignore已覆盖Python缓存、环境、产物、秘密及.local/.doc，不需要新增忽略规则。

T001检查使用git status/worktree/log/show、CodeGraph公共账本上下文及逐文件读取。旧工作区仍在C:/Programs/LetsAIGC/.local/worktrees/game-ui-analysis，10份草稿全部保留原位，未复制、改写或删除。下表路径相对此旧工作区的src/letsaigc；“复用候选”只表示后续实现的参考，不表示代码或验收已通过。

| 草稿 | 审查结论与后续范围 |
|---|---|
| schemas/ui.py | 依据T003/T004重写。旧mode=analyze、顶层query、mobile/tiny模型默认值及24MP/32视图限制不符合当前parse/输入联合/server/large/资源合同；缺供给、选择版本和分期载荷。可参考严格基础模型和观察/推测分离结构。 |
| ui_analysis/__init__.py | 可复用模块说明；随领域模块落地，不单独视为实现交付。 |
| ui_analysis/coordinates.py | 有限数值、边界/正面积、相交多边形检查和EXIF矩阵可作T005候选；需以全部方向和边界真值先行验证，视图映射改为当前显式变换合同。 |
| ui_analysis/normalize.py | EXIF/ICC及原图保留可作T005候选；需分开1024 OCR分块与1536 VLM视图，补最长边和当前像素/块数上限，不复用旧混合视图默认值。 |
| ui_analysis/crops.py | 矩形裁切和标注绘制可作T008候选；重接当前UILayout/ArtifactRef、产物角色、来源及manifest合同，先验证像素与引用。 |
| ui_analysis/layout.py | 引用/层级检查可参考；融合需重写。旧重复IoU>0.95、身份匹配≥0.75、文字中心点关联与当前≥0.85/≥0.5/80%覆盖最小控件规则不同，不能直接搬入。 |
| ui_analysis/inpaint.py | 区外原像素复制的CPU合成可作T026候选。旧preserve依赖SAM mask、空mask抛通用错误；需改为冻结keep区域保护和no_edit_pixels零生成，并接入具体子计划批准。首版不引入此模块。 |
| ui_analysis/quality.py | CER计算可作T036候选；旧检查字典不具备正式样本分组、度量分母与阈值冻结合同，不能当质量验收。 |
| ui_analysis/revision.py | 图依赖传播可参考；旧refine_segment/reanalyze_region动作名和粗粒度失效图需按T029枚举、元素范围及批准失效规则重写，不进入首版。 |
| ui_analysis/text_assets.py | 仅局部对比度启发式，扩张后可能越出文字区域，缺真实分割及连通域约束。T023重新建立领域测试并实现，不把当前输出称为精细字形。 |

四个独有提交均为文档/示例调整，没有可合入的UI运行实现：

| 提交 | 保留与复用决定 |
|---|---|
| 74f24eb | 记录SerpApi/Tavily及密钥拼写的历史；固定供应商环境变量和“不自动切换”已过时，不合入。 |
| 28085a4 | 额度自动选路、低水位、限频和未知结果依据已由当前搜索合同承接；不覆盖用户.env.example。 |
| fac4b8e | manual/search供给层及来源分离已由当前数据模型、UIProvider合同承接；不复制旧G依赖。 |
| 8a1badd | 将入口指向本地主计划的历史导航已被specs/013现行入口取代；不重新合入README或旧导航文档。 |

环境验证是本次实际尝试，不沿用任何历史测试结果：

| 命令 | 本次结果 |
|---|---|
| mamba run -n letsaigc-core pytest | 退出1；仅报无法写入D:\Programs\miniforge3\condabin内mamba.bat、_mamba_activate.bat、activate.bat、mamba_hook.bat及Scripts/activate.bat。pytest未启动，无通过/失败计数。 |
| mamba run -n letsaigc-core ruff check . | 退出1；同一激活脚本写权限错误，ruff未启动，无lint结果。 |

按speckit-implement的失败停止规则，暂停后续顺序任务；按用户既定环境边界，不修系统权限/激活脚本、不改用全局Python。T001本身为已完成的只读审查，T002及后续任务继续未勾选。环境命令可正常执行后从T002继续，不把本次停止误记为UI能力失败或真实验收。

本次仅更新本任务文件的状态和审查记录；.env.example、AGENTS.md、本地主计划与Constitution的SHA-256均与本次开始相同。未修改运行代码、教程或旧工作区，未调用真实供应商/GPU、下载模型或创建Git commit。

### 2026-09-04 继续实施：T002

用户授权改用同一letsaigc-core环境的conda run，不修改系统权限或安装环境。原有测试本次运行144 passed、11 skipped（Temporal/GPU现场条件未开启），53.63秒；退出0。另有既存pytest缓存写权限警告，后续定向验证禁用缓存，不修改ACL。

新增三个原创合成UI开发样本（英文横屏HUD、中文密集界面、中文竖屏），固定PNG哈希、文字/元素像素标注与development分组。样本生成器显式运行，使用本机微软雅黑字体并仅记录字体哈希，不分发字体；已查看三图确认文字可见。它们不是商业游戏截图或独立评估集，不构成OCR/VLM真实验收。

- `conda run --no-capture-output -n letsaigc-core python -m pytest`：144 passed、11 skipped，退出0。
- `conda run --no-capture-output -n letsaigc-core python tests/fixtures/ui_analysis/build_preview.py --font C:\Windows\Fonts\msyh.ttc`：生成成功。先行夹具测试曾因样本文件尚不存在失败，生成后2 passed。
- `conda run --no-capture-output -n letsaigc-core python -m pytest tests/unit/test_ui_preview_fixtures.py -q -p no:cacheprovider`：2 passed，退出0。
- `conda run --no-capture-output -n letsaigc-core python -m ruff check tests/conftest.py tests/unit/test_ui_preview_fixtures.py tests/fixtures/ui_analysis/build_preview.py`：修正生成器局部函数绑定及格式后All checks passed。

隔离夹具提供ArtifactStore、Ledger、临时工作区和调用计数；新增ui_live标记和--ui-live显式开关，普通pytest默认跳过此类真实验收。T002完成，继续T003共享执行合同；本节为本次结果，前节mamba阻断不再作为当前状态。

### 2026-09-04 T003—T004：共享单任务支撑

先建立UI严格输入/输出拒绝、v1序列化/指纹/旧CLI及共享执行合同，运行确认缺少UI模块；再实现DTO、静态ui_analysis/步骤分派、输入绑定、顺序受理、原请求恢复、收集及独立UI预算判定。增加v2迁移前先运行迁移测试确认缺少模块；迁移只新增ui_step_bindings，要求显式停写、一致备份和事务回滚，不修改旧任务/操作payload，不自动升级真实账本。

`conda run --no-capture-output -n letsaigc-core python -m pytest tests/contract/test_ui_schemas.py tests/contract/test_ui_execution.py tests/contract/test_ui_compatibility.py tests/integration/test_ui_migrations.py tests/unit/test_pipeline_domain.py -q -p no:cacheprovider --tb=short`：46 passed，4.51秒。包含原账本回归与新增逐次/累计实际超限、预留偏差后继续、未知费用保留、调用上限及旧submit不误落Comfy。新冻结v1模型字段文件与HEAD一致；金样记录固定计划字节、指纹和operation ID，不以UI实现反向更新预期值。

`conda run --no-capture-output -n letsaigc-core python -m ruff check .`：All checks passed。仅本地替身验证，无OCR/VLM/搜索/GPU真实调用。首版能力适配器及Temporal/CLI接线仍待后续任务，注册定义不代表可运行预览。

### 2026-09-04 T005：手动快照与坐标

先运行新合同确认缺少接入/坐标模块，再实现受控本地/HTTPS/授权ref导入、有序条目及重复映射、来源分离、当前任务重登记和零搜索的ManualUIProvider。复用原AssetResolver的HTTPS、DNS固定、MIME/解码限制；不改变旧8张输入合同。标准图保留全分辨率/alpha并应用EXIF与ICC，1024/64 OCR分块和1536预览各有正逆变换。

`conda run --no-capture-output -n letsaigc-core python -m pytest tests/unit/test_ui_coordinates.py tests/contract/test_ui_manual_provider.py -q -p no:cacheprovider --tb=short`：18 passed，1.62秒。全部EXIF方向以独立像素阵列真值验证；覆盖奇数尺寸、分块、取整/非法几何、冻结副本、授权scope、输入数量及HTTPS拒绝。`conda run --no-capture-output -n letsaigc-core python -m ruff check .`：All checks passed。

接着只读检查发现本机已存在letsaigc-vision-ocr环境，包版本PaddleOCR3.4.0、PaddlePaddle3.2.2、PaddleX3.4.3；这只是安装信息，不是模型加载或OCR验收。仓库模型/runtime/cache目录暂未发现静态OCR inference.json，后续锁与服务按缺模型保持未就绪。

### 2026-09-04 T006：CPU OCR适配与服务

先行服务/OCR合同确认缺少模块，再加入仅CPU的显式静态加载器、模型/包锁模板、独立环境定义、回环HTTP服务、认证及绑定已准入operation的签名凭证。服务在推理前持久化独立request_id；恢复按operation只读查询，重启后的未决作业保留unknown。核心模块不导入Paddle；无自动下载、动态图checkpoint或GPU退路。

已核对本机PaddleX3.4.3静态加载分派和PaddleOCR3.4.0接口：rec_texts/rec_scores使用rec_polys配对，原dt_polys单独保留；固定显式det/rec目录和CPU，关闭方向分类/去畸变/文字行旋转。原始文字、置信分数、原视图及回映多边形保留，空结果明确empty，局部重读复用公共绑定及次数边界。

`conda run --no-capture-output -n letsaigc-core python -m pytest tests/contract/test_ui_ocr.py tests/contract/test_ui_vision_service.py tests/unit/test_ui_coordinates.py -q -p no:cacheprovider --tb=short`：20 passed，1.29秒，包含真实HTTP协议的替身路由、公共准入→签名→服务回执→原结果收集。未启动真实模型服务或推理；模型/包wheel hash及许可证据字段保持pending/null，无法作为ready使用。真实模型加载和三个样本验收仍属未完成T010。

### 2026-09-04 T007：VLM结构与费用

先行合同确认缺少独立分析器，再复用现有Responses连接/模型配置/计价，加入无工具、有限输出的严格UI结构。模型正文仅保存在素材，提示注入不进入开发指令；几何、层级、文字/证据及修订引用由本地校验。OCR原文与修正建议分离；已知费用在非法输出时仍保留，缺失/无效用量保持unknown。

接口核对参考[OpenAI结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)的text.format/json_schema及required字段规则；没有变更项目模型选择或价格。`conda run --no-capture-output -n letsaigc-core python -m pytest tests/contract/test_ui_analyzer.py -q -p no:cacheprovider --tb=short`：10 passed，0.74秒。全部为本地替身，未发真实Responses请求；VLM真实验收保留T010。

### 2026-09-04 T008—T009：产物与单图工作流接线

实现稳定布局融合、原文关联、精确矩形切片、overlay、质量pending报告及引用式manifest；操作详情单独保存为素材索引，避免大量OCR分块挤满Workflow载荷。CLI提供import/plan/execute/inspect/doctor及resume/cancel/reconcile，只有明确指纹批准才启动执行；编辑、批次和修订入口仍未开放。CPU服务与核心进程隔离，新增磁盘/RAM/输入响应容量检查和跨Continue-As-New累计活动时间。

布局/manifest定向验证13 passed；单图CLI与Workflow最后定向验证5 passed。Workflow用真实SDK沙箱校验和本地Activity分派运行，OCR/VLM为替身；跨两次Continue-As-New只受理各一次，并复用原结果。这是离线流程证据，不是Temporal服务重启或真实OCR后恢复验收。新恢复驱动集中于tests/integration/test_ui_analysis_workflow.py及已有公共合同，未为同一矩阵新增重复测试文件。doctor的UI实现位于ui_analysis/runtime.py，旧Temporal消息/调用入口的v1序列化保持兼容。

### 2026-09-04 T011—T016：双后端搜索接线

实现严格搜索DTO、冻结价格/路由、SerpApi Google Images与Account、Tavily basic与Usage、配额归一化和持久化准入。v3只增加五张额度表，与单任务费用预留同事务；迁移备份、回滚及旧任务保存已验证。价格未知、无可靠余额、取消或未知结果均不新增搜索消费；免费探测也计次数。PAYGO数值单独保存但不作为可用余额，刷新时间不自动清除本地未覆盖扣款。

两家协议夹具和错误/日志验证先于适配实现；SerpApi归档及v3故障回滚的补充验证纳入最终回归。已核对[SerpApi Account](https://serpapi.com/account-api)免费说明、[Google Images](https://serpapi.com/google-images-api)缓存参数、[Tavily basic/usage](https://docs.tavily.com/documentation/api-reference/endpoint/search)请求与响应结构。首版冻结serpapi_no_cache=true，成功搜索结算一个请求，配置/合同/计划摘要同时展示；真实账户单价、条款和Tavily探测费用仍待核实。未找到可据以认定Tavily Usage或SerpApi归档免费调用的明确依据，保持未知并禁用该能力；不把测试夹具单价写入实际配置。

搜索先做本地标题/描述相关性、尺寸、比例、对比度及哈希筛选；原查询不调用规划模型，计数为0。首张合格图片后停止下载，来源及候选索引区分供应商声明与观察值，image_semantics_verified=false，不声称通过游戏语义或正式质量验收。搜索供给→共同标准图的Activity测试与手动完整链路共用下游证据，不复制全故障矩阵。CLI单图search测试集中于test_ui_cli.py，供给/来源对照位于test_ui_search_provider.py。

SerpApi已知ID归档HTTP适配已实现并验证只GET原ID，但执行链对无可用图片且瞬时URL丢失的结果保守返回input_resupply_required；缺归档计价时不自动调用。原请求未知则保留预留，不能换家补发。精细语义质量与真实搜索结果是否满足产品预期仍需T017实测，不能用本地筛选替代该验收。

### 2026-09-04 本轮回归、就绪与停止点

| 本次命令/检查 | 结果 |
|---|---|
| conda run --no-capture-output -n letsaigc-core python -m pytest -q -p no:cacheprovider --tb=short | **247 passed、11 skipped，63.67秒，退出0**。10项真实Temporal测试缺已核验CLI，1项GPU测试缺冻结计划/具体批准及CLI；没有新增UI真实验收通过记录。 |
| conda run --no-capture-output -n letsaigc-core python -m ruff check . | **All checks passed，退出0**。本轮发现并修正了遗漏的json导入与新测试导入排序。 |
| git diff --check | 退出0；仅既有LF→CRLF提示，无空白错误。 |
| conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui doctor | 退出0；manual入口ready；OCR、VLM、Temporal及两家搜索未ready；未发付费探测。 |

安全补充：本轮配置诊断发现LLM_BASE_URL不是URL，旧异常会回显原值。已删除URL校验异常中的值，并用合成秘密验证；未修改用户.env。操作者需修正为服务地址；若曾将密钥误填于此，需更换该密钥。手动HTTPS导入的HTTP调试日志也曾在合成测试中泄露查询参数，现已与搜索共用上下文脱敏保护，回归通过。

T010阻塞条件：已核实OCR静态模型/包哈希/许可证据、服务认证、有效VLM地址/计价/凭据、运行中的本地Temporal服务，以及三个固定样本的具体分析计划和批准。T017另需两家已核实的实际单价、探测费用与条款，再分别做一次有界搜索并解析；不故意耗尽账户。T018须复用这些真实证据后才发布可用预览。按照现行依赖，T019之后的GPU、父子预算、批次和完整质量工作未提前实施。

当前模型锁保留null/pending，全部10项[LIVE]保持未勾选；资源峰值、真实模型输出和跨进程Temporal恢复不由本次替身测试代替。使用入口见[开发使用指南](../../docs/game-ui-analysis.md)。本轮遵照既定边界未部署/下载模型、修改系统权限、运行speckit-analyze或创建Git commit。

收尾检查：6份相关文档的相对链接均存在，26个PowerShell代码块通过语法解析；顺手为Temporal旧示例中的占位参数加引号，避免PowerShell把尖括号当运算符。8个现有UI子命令的--help均退出0。统计为42项/15项完成/0项[LIVE]勾选。.env.example、AGENTS.md、本地主计划和Constitution哈希与本轮开始一致；教程及旧worktree未修改。完整本地摘要保存在.local/validation/ui-analysis/implementation-regression.json。

### 2026-09-05 T010 真实验收前置复核

再次执行speckit-implement前置检查：checklist 16/16完成，无extensions hook。脚本首次按分支名寻找`specs/dev-game-ui`而失败；仅在该进程设置`SPECIFY_FEATURE=013-game-ui-analysis`后，正确返回现行特性目录及research/data-model/contracts/quickstart/tasks。这是开发工具的特性选择问题，不是产品运行失败，也未更改系统环境。

为避免仅返回`ready: false`，先增加doctor脱敏原因合同，再实现逐能力检查。OCR现在分别报告模型锁、回环服务、模型摘要和两项认证是否就绪；VLM分别报告端点、凭据和当前计价；Temporal报告SDK/服务；每个搜索后端分别报告凭据、search价格和probe价格。输出只含布尔值、原因码及模型摘要，不返回配置值、密钥或账户正文。先行测试因缺`reasons`失败，完成实现后5 passed。

本机只读结果：OCR隔离环境已有Paddle/PaddleOCR/PaddleX及psutil，但没有letsaigc包安装；按指南显式设置本分支`PYTHONPATH`即可运行，无需全局安装。仓库配置位置未发现OCR静态模型文件或已锁Temporal CLI。doctor显示VLM凭据和当前计价已配置，但endpoint_invalid；两家搜索凭据已配置，但search/probe计价未核实；OCR模型锁、回环服务与认证未准备；Temporal SDK可用、服务不可达。未读取、打印或修改任何凭据值。

`conda run --no-capture-output -n letsaigc-core python -m pytest -q -p no:cacheprovider --tb=short`：**248 passed、11 skipped，63.07秒，退出0**。跳过项仍为10个缺已核验CLI的Temporal现场测试及1个缺冻结计划/具体批准/CLI的GPU测试。`conda run --no-capture-output -n letsaigc-core python -m ruff check .`：All checks passed。没有执行真实OCR/VLM、搜索、下载或模型安装。

T010保持未勾选：它仍需要操作者提供已核实的静态模型/包/许可锁、Vision认证、有效VLM服务地址、运行中的Temporal，以及三份经审阅的单图计划及精确批准。T017还需账户级搜索计价和Tavily probe计价依据。由于T018依赖T010/T017，T019及后续任务不得提前开工。

### 2026-09-05—06 macOS arm64 接手与环境重建

只读核对远端及本地：`dev-game-ui=712c66e6195683d3301a560e897061f208fbf99b`、`master=7b91c34deb23064aaa6b492ce105956a59b47b71`；本机只有一个工作树，开始时干净。已阅读交接、规格、计划、任务、Quickstart、数据模型、研究与合同，未重建规格或合并旧原型。

Speckit 前置脚本已尝试，但本机没有 `pwsh`，不能声明脚本执行通过；按脚本逐项核对现有特性目录与必需文档，无 extensions hook。`requirements.md` checklist 共16项，完成16项、未完成0项。任务仍为42项、完成15项、10项[LIVE]均未勾选。

用户授权重新配置环境。新建 `letsaigc-core` 和 `letsaigc-vision-ocr`，均为 Python 3.12.14，未复制或修改 `pytorch-m4`，未向全局 Python 安装项目。核心安装 `.[temporal,tracking,dev]`；OCR保持 PaddleOCR3.4.0、PaddlePaddle3.2.2、PaddleX3.4.3，CPU自检成功，未安装Torch。该自检只验证框架，不是OCR模型推理或T010验收。

首次完整回归为 **246 passed、13 skipped、3 failed**。两项新增skip是Windows文件共享专属测试；三项失败分别为：URL脱敏测试的`private`标记误命中macOS临时目录、POSIX未拒绝Windows形式输出路径、系统FFmpeg缺字幕滤镜。先扩展路径拒绝测试，确认6项失败后修正跨平台路径校验；秘密检查改用独立query/fragment合成标记，仍检查完整序列化内容。相关测试21 passed。

在核心Conda环境安装含libass字幕滤镜的FFmpeg。Codex登录shell会重排PATH并优先找到Homebrew旧FFmpeg；本轮后续命令使用非登录shell和原有`conda run --no-capture-output -n ...`格式，未改用户shell配置。

| 本机验证 | 结果 |
|---|---|
| `conda run --no-capture-output -n letsaigc-core pytest`（修复后） | **258 passed、13 skipped，14.82秒，退出0**；10项Temporal缺CLI入口、1项GPU缺批准及计划、2项Windows专属。 |
| `conda run --no-capture-output -n letsaigc-core ruff check .` | 通过。 |
| 核心环境 `python -m pip check` | No broken requirements found。 |
| 已校验CLI后单独运行 `tests/integration/test_temporal_runtime.py` | **10 passed，47.50秒**；明确设置`LETSAIGC_TEMPORAL_TEST_CLI`，使用真实本地Temporal与模拟能力，不调用OCR/VLM/GPU，不算T010恢复验收。 |
| `ui doctor`（允许本机回环连接） | manual、VLM配置、Temporal ready；OCR认证已配置，但模型文件及服务未ready；两家搜索凭据存在，计价仍未核实，未发收费探测。 |

Temporal CLI1.8.3 darwin_arm64下载包已对照官方SHA-256核验；可执行文件及SDK1.32.0 macOS wheel另存哈希。已启动回环开发服务，数据库为新建本机`.local/pipelines/temporal-dev.sqlite`。核心沙箱内doctor曾不能连回环端口，后在允许本机端口的执行环境中确认ready，保留两次诊断。

`.env`由本机新建为0600并生成新Vision认证，随后用户完成VLM配置；未打印密钥，doctor只证明配置及计价就绪，未验证远端受理。Paddle/PaddleOCR/PaddleX/Temporal四个固定版本官方wheel均下载并与PyPI SHA-256一致。本机锁证据与OCR候选锁保存在本地；OCR候选锁尚未激活，旧Windows锁不能作为macOS wheel验收依据。

证据目录：`.local/validation/ui-analysis/migration-2026-09-05/`，包括首次/修复后回归、先行失败及通过的路径测试、doctor、环境信息、官方wheel校验、Temporal下载与运行记录。三例固定输入SHA-256均与`preview-cases.yaml`一致，尚未重新导入或创建T010计划。用户的模型下载授权仍待确认；随后须核验静态模型、许可证据及有效macOS锁，启动OCR/Worker并重新规划。真实OCR/VLM、搜索及GPU调用均为0，未产生可批准的T010指纹，未沿用旧批准。T010、T017/T018及T019+状态保持不变；历史失败未清除，未创建Git提交。

### 2026-09-06 T010 新机模型就绪与待批准计划

用户明确授权两份OCR静态模型下载和校验后，从原锁官方地址准备PP-OCRv5_server_det/rec。两个归档、六个静态文件和PaddleOCR v3.4.0 Apache-2.0 LICENSE全部与冻结SHA-256一致；只接受经过逐文件校验的静态内容，无自动模型下载或动态图checkpoint回退。软件/模型准备授权不作为T010付费VLM批准。

增加Darwin-arm64专用OCR锁及共享平台选择函数，原Windows锁保持不变。先写规划器/服务选同一平台锁、Windows兼容和缺平台锁不回退的测试，确认缺实现失败后接线。相关OCR合同10 passed；完整回归 **270 passed、3 skipped，61.88秒**，包含已核验CLI的10项Temporal运行测试；剩余skip为1项缺具体计划/批准的GPU测试和2项Windows文件共享测试。ruff通过。

真实OCR服务只加载已验证的本地模型，服务、Worker与根计划绑定的模型摘要一致。`ui doctor`确认manual、OCR、VLM配置和Temporal均ready；队列存在1个Worker poller。VLM配置就绪仍不等于已验证远端响应。两家搜索计价未核实，T017未启动。

按固定SHA重新导入三例开发样本并生成parse计划，来源元数据保留`original_synthetic_ui`及development标签。导入/规划阶段显式拦截socket/HTTP、直接模型调用及人工批准入口，拦截尝试0；规划后OCR提供方作业0、三任务operations/approvals均0。已逐份通过执行前模型/配置/资源检查，未调用execute。

| 样本 | 新task ID | 单轮USD上限 | 总USD上限 |
|---|---|---|---|
| 英文横屏 | `ui-90afad1372c64fc2a40a0694dbc528a9` | 0.25 | 1.00 |
| 中文密集 | `ui-2b96c9b0d75d422aa3a3d479d8cb3cb3` | 0.25 | 1.00 |
| 竖屏 | `ui-8f93581efed64347b878040cb8a03206` | 0.25 | 1.00 |

合计上限3.00美元、GPU逐次/总预算均0，VLM固定gpt-5.6-luna，计价ID为openai-standard-2026-09-02。每图VLM上限4次，当前首版parse不启用编辑/自动修订。完整64位指纹、输入hash、预算、执行顺序及零调用证据保存在`.local/validation/ui-analysis/t010-2026-09-06/approval-plans.json`，逐例原始CLI响应位于同目录`plans/`。

当前等待用户对新三份完整指纹及预算的精确批准。获批后顺序运行三例，竖屏在OCR provider request登记后中断Worker并重启，核验原request未重复受理；随后检查全部产物、来源、真实用量和预算结算。上述模型加载与准备不能替代T010真实推理/恢复验收；T010及所有未完成[LIVE]仍保持未勾选。先前迁移/Windows失败记录、候选锁和证据均保留，未实施T019+、未提交Git。

### 2026-09-06 T010 商业截图替换与重新规划

用户已确认三种商业 UI 截图符合预期，授权进入导入和规划。旧合成 fixture 及其未批准计划保留为历史，不再作为本轮执行对象；本次选图确认不作为付费 VLM 的指纹批准。

三张原图保存在 `.local/ui-input/t010-commercial/candidates/`，来源分别为 VideoGamer 攻略、HoYoLAB 用户帖子和 LDPlayer 攻略。导入元数据冻结来源页面、原图地址、SHA-256 与 development 标签，第三方图片许可保持 unknown/reference-only，不加入 Git。

| 样本 | task ID | 完整 plan fingerprint | 单轮/总 USD 上限 |
|---|---|---|---|
| hades2-en-landscape | `ui-49cd0e0e5df24ae0a81751faa309c5b7` | `076a43d58932a5be7ee351a45ad500aa51427f5fe14632516d3c7edfb537a115` | 0.25 / 1.00 |
| starrail-zh-dense | `ui-ab65f23641404466b51f0a1761366449` | `0f9806b062d24fcf21ff0fe3eeba08c060b2fd356b6e12ce6134bc9c4171a496` | 0.25 / 1.00 |
| clashroyale-en-portrait | `ui-7d00b9a3f5dc47d592023dc1c8cd95ff` | `dc23305e2667bad5946c62030dcbdf348e5a8af69db6525288dae2d7d8977671` | 0.25 / 1.00 |

合计上限 3.00 USD，GPU 上限 0；VLM 固定 gpt-5.6-luna，计价 ID openai-standard-2026-09-02。按表顺序执行，第三例在 OCR 请求登记后中断并恢复 Worker，验证同一 provider request ID 无重复受理。

导入/规划显式拦截 socket/HTTP、模型调用与批准入口，拦截尝试 0，OCR provider jobs 0，各任务 operations/approvals 0。三份计划执行前检查均通过；健康检查仅访问本机服务，不发送推理请求。完整证据位于 `.local/validation/ui-analysis/t010-commercial-2026-09-06/`。当前等待对以上新指纹与预算的确切批准，未执行 OCR/VLM/搜索，T010 和后续所有未完成 [LIVE] 状态不变。

### 2026-09-06 T010 商业截图真实链路与恢复验收完成

用户对上一节三个完整指纹及逐项0.25/1.00 USD、合计3.00 USD明确回复“批准”后，按Hades II、星穹铁道、皇室战争顺序执行。三任务Temporal最终结果均succeeded，来源原图hash、许可unknown及元数据均核验，全部布局、文字、切片、overlay、quality_report及manifest保存于 `.local/validation/ui-analysis/t010-commercial-2026-09-06/results/`。固定输入和本轮授权记录本地保存，旧失败、旧未批准计划及迁移记录保留。

| 样本 | OCR作业 | 元素 / 文字 | 精确矩形切片 | 账本实际USD |
|---|---:|---:|---:|---:|
| Hades II | 2 | 32 / 23 | 32 | 0.007264 |
| 星穹铁道 | 4 | 19 / 25 | 19 | 0.006893 |
| 皇室战争 | 1 | 32 / 12 | 32 | 0.005101 |

共7条OCR提供方回执、3次VLM分析，累计账本费用0.019258 USD，GPU为0，reserved/unsettled均为0。费用依返回token用量和冻结计价表计算，非账户账单对账；Hades原始计算0.0072636、皇室战争0.00510088，账本按6位小数结算。模型服务Hades返回output_tokens=4302，高于请求max_output_tokens=4096，记录为提供方行为观察，实际成本未超批准限额。

皇室战争OCR运行中，在提供方和账本均持久化回执后SIGKILL精确Worker PID 54446，重启PID 90991。原operation `op-245b97a3ee526b9c9c3bb3a82cacc858f0e6d02c8ddce938`恢复后仍使用 `vision-8047b8678aba477e963a806c9c3bdda3`，数据库只有一条匹配回执，成功收集并完成后续VLM/产物。OCR服务未重启。初次监视器因同时匹配Conda父进程而安全拒绝中断，收窄为精确Python Worker后完成演练，该准备失败留存。摘要分别为 `.local/validation/ui-analysis/preview-manual.json` 和 `preview-recovery.json`，完整Temporal历史、前后回执与Worker记录可复核。

新增 `test_ui_manual_runtime.py` 与 `test_ui_preview_recovery.py`：仅在 `--ui-live` 和 `LETSAIGC_UI_LIVE_EVIDENCE` 显式指定时检查已有真实证据，不重复付费推理。三例尚未完整落盘时检查3 failed，完成后3 passed。带已核验Temporal CLI与现场证据的完整 `conda run --no-capture-output -n letsaigc-core pytest --ui-live`：**273 passed、3 skipped，63.24秒**；ruff通过，git diff --check通过。3项skip为缺批准的GPU与2项Windows专属测试。

人工查看三张overlay后保留质量基线限制：星穹铁道只拆出第一排独立物品，后续物品/侧栏按整体区域表示；皇室战争部分宝箱合并、礼物图标未单独框出；矩形切片包含背景，不是透明资产。三例文字关联未分配数均0，但不能推出识别完整性。正式质量状态继续pending，后续开发集再冻结阈值。T010按“不要求正式质量阈值”的现行任务要求勾选；T017/T018及T019+仍未完成，未发布完整预览。

### 2026-09-06 T017 账户计价前置检查（未验收）

T010完成后核对两家官方计价和条款。SerpApi Account API官方明确免费且不消耗搜索额度，因此只做一次账户预检，HTTP200：Free Plan、月费0、250次/月、当前剩余240、extra_credits0，凭据有效；没有执行搜索。条款页版本2026-08-27。Tavily公开basic搜索1credit，免费1000credits/月，PAYG0.008USD/credit，但该公开信息不能证明当前账户套餐。Tavily Usage文档没有明确探测货币费用，因此未调用其账户端点或搜索；条款页版本2026-05-04。官方来源与白名单账户字段位于 `.local/validation/ui-analysis/t017-2026-09-06/`，没有保存密钥、邮件、账户ID或原始响应正文。

待操作者补充Tavily账户套餐及 `/usage` 计费依据后，才冻结完整两家计价并形成新的T017预算/指纹供批准。当前批准只覆盖T010三个任务，不扩展为搜索授权。T017/T018未勾选，T019+未实施；没有为推动验收而把未知探测费用填0。

### 2026-09-06 T017 SerpApi 子集待批准计划

用户再次授权使用speckit-implement推进下一步。复核checklist 16/16通过，无extension hooks；本机仍无pwsh，按已读取前置脚本完成等价路径/必需工件检查，记录prerequisites.json。T017明确允许供应商子集分别验证，因此先完成SerpApi部分准备，Tavily未知probe计价继续保持未就绪。

使用现有Free Plan账户依据，冻结SerpApi搜索/探测单价0、条款版本serpapi-2026-08-27、唯一供应商serpapi；查询为 `Hades 2 boons Hera UI screenshot site:videogamer.com`，最多1次search、1次probe、1次下载、1张图片；VLM单轮0.25USD、总1USD、GPU0。

- task ID：`ui-3520938acd4849b1b17ec38ead7921c4`
- 完整plan fingerprint：`4f853ff0011dd843a821acaade88c7b2a2ed862285b9148468e984bab03376f0`
- 证据：`.local/validation/ui-analysis/t017-2026-09-06/serpapi/`

规划期间显式拦截网络、OCR、VLM和批准入口，尝试0；新任务operations/approvals均0。冻结配置下本机preflight通过，未搜索、未额度探测、未下载、未推理。规划和预检采用临时安装已保存配置后finally恢复原文件的方式，原双后端配置字节保持不变。执行时需安装同一冻结配置进行preflight，随后恢复；不得直接用双后端当前配置执行该子集计划而绕过dependency_changed。

搜索协议/计价/固定额度路由及账本专项回归结果见search-contracts.log。等待该新指纹及预算的确切执行批准；此前T010的批准不转移到此任务。T017/T018及T019+状态不变。

### 2026-09-06 T017 SerpApi 首次受理但无可用候选（保留失败）

用户明确批准上节指纹后，通过现有CLI执行 `ui-3520938acd4849b1b17ec38ead7921c4`。一次Account probe及一次Google Images搜索已结算，供应商search ID `6a9cc2ee5abea5c01b11c748`、quota_units=1、candidate_count=0，候选索引为空；未下载、未调用OCR/VLM。Temporal最终failed/search_unavailable，费用0，reserved/unsettled均0。原始响应不持久化，因此仅确认适配后的候选为空，不推断Google原始响应的具体原因。完整安全操作记录、授权、空候选索引及47条Temporal事件保存于 `.local/validation/ui-analysis/t017-2026-09-06/serpapi/`；该记录不能作为T017成功验收。

未在已消费的单次搜索授权下自动重发。将查询从站点限定版放宽为 `Hades 2 boons`，本地生成替代计划：task ID `ui-83a66862f444466ca0129be67ba13988`，完整fingerprint `2b19e19ef6d425b303d5b0017fae379f314c8793b17cf6548af310cd36d90903`。范围仍为最多1次搜索/1次probe/1次下载/1图parse，单轮0.25USD、总1USD、GPU0。新任务外部调用及operations/approvals均0，preflight通过；等待新指纹的具体批准。两次配置切换均finally恢复原双后端配置，未改源码或重跑无关测试。

汇总 `.local/validation/ui-analysis/preview-search.json` 状态incomplete，T017/T018仍未勾选，T019+未实施。Tavily账户与probe计价仍需核实。

### 2026-09-06 T017 宽查询取得评级图，VLM结果未知（未验收）

用户批准新指纹后执行 `ui-83a66862f444466ca0129be67ba13988`。一次免费probe、一次搜索（SerpApi ID `6a9cc4289a1f2c050bf4d4bf`、quota_units1、候选20），一次下载、两条OCR均成功。下载的第一候选标题“Boons Tier List (Hades 2) : r/HadesTheGame”对应角色评级图，用户明确指出并经图像检查确认，不是真实游戏UI，不能作为T017样本成功证据。

analyze提交于2026-09-06 01:39:02 UTC（北京时间09:39:02）返回outcome_unknown，未登记响应ID且无持久analysis产物，工作流awaiting_reconciliation。账本actual0不代表实际未收费；0.25USD保留在unsettled，未重发、未清账、未强行取消。断网的本地提交验证可到达提供方调用边界，未发现输入预校验错误；无法由本地证据区分远端拒绝、网络异常或已受理响应丢失。需提供方该时段受理/用量记录后才可对账；未将评级图用于T017验收。

修复明确非UI元数据筛选：下载前排除英文tier list/character ranking及中文角色评级/强度榜等，在候选索引记录metadata_rejection_reason=non_ui_tier_list；不消耗下载名额。复用原有搜取/复用合同，添加英文和中文首候选为评级图、后候选为HUD、下载限额1的用例：修复前2 failed/2 passed，修复后4 passed。对已保存真实候选离线复核，第一张评级图被排除，外部调用0。该规则不证明其余候选的图像语义，后续真实样本仍需目视确认，不能把关键词匹配标为视觉验收。

所有调用及失败证据位于 `.local/validation/ui-analysis/t017-2026-09-06/serpapi-broader-query/`；旧空结果尝试保留，preview-search.json仍incomplete。T017/T018及T019+状态不变。源码已修正，后续新任务前需重启Worker载入新代码；当前未决任务不自动重试。

评级图筛选修复后的完整回归：`conda run --no-capture-output -n letsaigc-core pytest --ui-live`（已指定核验Temporal CLI与T010现场证据）**275 passed、3 skipped，63.60秒**；ruff通过，git diff --check通过。回归未增加外部搜索/VLM调用。

### 2026-09-06 T017 重新准备UI专用查询与下载后检查点

用户要求“重新进行”。保留旧 `ui-83a66862f444466ca0129be67ba13988` 的未知analyze和0.25USD unsettled，不重发旧operation。查询改为 `Hades 2 boon selection screen`，普通公开图片检索可找到实际祝福选择界面，但不将其当作SerpApi现场证据。

新计划 `ui-5d765ae5c9d84a81b4b20d942f3b5476`，完整fingerprint `cd9a7a635324f8f20fd843aefbdd1d723a7b47d8eeeb58d432cf5ad35942b3f4`；最多1次SerpApi搜索/1次probe/1次下载/1次VLM，单轮及总预算均0.25USD、GPU0、修订0。旧未决加新计划最大敞口0.50USD（不包括已结算T010）。本地规划零外部调用，preflight通过，原搜索配置恢复不变。

本地验收脚本 `.local/validation/ui-analysis/t017-2026-09-06/serpapi-ui-screen/review_worker.py` 在该新任务search结束后、normalize/OCR/VLM之前等待目视检查，按task_id和原图hash绑定；非游戏UI或300秒内没有检查即停止。检查记录不是人工预算批准，不赋予额外调用权限；执行仍须先取得新指纹批准。脚本另记录安全VLM异常类别/HTTP状态而不保存正文、地址或密钥，始终不自动重试。此为本地验收观察工具，没有新增产品运行入口或T019+功能。当前仅完成脚本语法检查，尚未启动该Worker或执行新任务。

后续获批时：先停普通Worker，启动该本地观察Worker，再安装冻结搜索配置调用原CLI execute并恢复配置；收到visual-review-needed.json后查看downloaded-image，确认游戏UI才写同hash的visual-inspection.json继续。验收结束后恢复普通Worker。T017/T018仍未勾选。

### 2026-09-06 T017 真实UI已确认，分析输出校验失败（保留失败）

用户以“yes”批准 `ui-5d765ae5c9d84a81b4b20d942f3b5476`，完整指纹 `cd9a7a635324f8f20fd843aefbdd1d723a7b47d8eeeb58d432cf5ad35942b3f4`，单轮/总预算均0.25USD、GPU0、修订0。一次免费probe、一次搜索（SerpApi ID `6a9ccab14c437fc2d6aa49fc`、quota_units1、候选20）、一次下载成功。图片为600×338的Hades II阿波罗祝福选择界面，来源 `https://hades2.wiki.fextralife.com/Boons`，SHA-256 `b2fe8c09e3da579c9b4d3fb7daca72e404a1ebe8b528e3b2be8a298c8ee8fc11`。下载后本地观察Worker先暂停，由Agent目视确认三项祝福选择面板及效果文字，再保存同哈希检查结果继续OCR/VLM；此检查不替代用户预算批准。

一条OCR作业成功、18条原始文字。VLM返回响应 `resp_042f38558eaa081f016a9ccada5b7887d1a6aa4d6811004753`，input8810/output3628 tokens，原始计算费用0.0061156USD、账本向上结算0.006116USD，reserved/unsettled均0。但analysis为failed/invalid_analysis、output=null，Temporal终态failed/provider_failed（171条历史事件）；未生成布局、切片和标注图，不能算作SerpApi解析成功。该执行版本未保存具体校验阶段和原始响应，因此不能推断是响应未完成、JSON、Schema还是引用约束失败。

补充内部analysis产物的 `validation_failure_stage` 固定阶段码（response_validation、output_type、response_size、json_decode、schema_and_references），成功时为null；保持现有error_code、严格校验、费用结算和不自动重试语义。只保存本地阶段码，不保存原始响应或异常文本。新增响应未完成、工具输出、超长、非法JSON、非法结构及未知引用的拒绝/计费合同：修复前6 failed/9 passed，修复后15 passed。该诊断不会回填本次失败原因，也不代表已修复远端输出。

证据位于 `.local/validation/ui-analysis/t017-2026-09-06/serpapi-ui-screen/`，包含授权、目视检查、候选/来源、OCR、失败analysis、费用与Temporal历史。原搜索配置已恢复，普通Worker已恢复并加载诊断。旧评级图任务0.25USD unsettled原样保留，未重放；preview-search.json保留三次尝试。后续付费调用需新计划及具体批准。Tavily账户/探测计价仍未核实，T017/T018仍未勾选，T019+未实施。

诊断改动完整回归：指定 `LETSAIGC_TEMPORAL_TEST_CLI` 与T010证据运行 `conda run --no-capture-output -n letsaigc-core pytest --ui-live`，**280 passed、3 skipped，61.69秒**；ruff与git diff --check通过。首次验证误用Temporal变量名导致13项跳过的日志保留，已用正确变量重验。测试未增加外部推理。

### 2026-09-06 T017 只读诊断恢复与缓存图片新计划

用户要求继续诊断、SerpApi解析和Tavily验收，并报告Tavily官网账户显示0/1000。复核checklist仍16/16通过，无extension hooks；继续使用既有PowerShell前置脚本的等价检查，不改写已完成规格。

针对已有VLM响应做一次只读GET恢复，返回HTTP404，未调用create，未新增推理；原请求store=false，本地也未保存原始正文，旧invalid_analysis仍不能确诊。证据位于 `.local/validation/ui-analysis/t017-2026-09-06/serpapi-response-recovery/read-only-retrieval.json`。内部analysis增加 `validation_failure_reason`，区分已知本地固定错误（响应未完成/拒绝、非法JSON/Schema、重复ID、坐标、层级与各类引用），未知异常仅记validation_failed；不持久化异常消息、原始正文或模型提供的自由文本。严格拒绝与费用结算不变。新增具体原因、几何与引用、异常内容不泄漏合同：修复前12 failed/8 passed，修复后20 passed。全量回归 **285 passed、3 skipped，62.92秒**；ruff与git diff --check通过。Worker已重启加载新代码，无新增付费调用。

复用第三次SerpApi已下载并目视确认的阿波罗祝福UI，经原有 `ui import --artifact` 与 `ui plan --input-manifest` 创建独立解析任务：

- task ID：`ui-55aa572cbc394c6b900fd66a2aba4108`
- 完整fingerprint：`45a3b8b1fa05d265cb3e25cf68b46157f24e6346a89b2cd72dfa9539f072398a`
- 单轮/总预算均0.25USD、GPU0、修订0；最多1次VLM，不新增搜索、probe或下载。
- SHA及artifact来源链验证通过，保留原搜索task、SerpApi请求ID、来源页面与provenance ref。新任务为manual acquisition的独立预算解析，不能伪称旧search工作流恢复成功；解析成功后再以相同图片哈希和来源链汇总T017证据。
- plan阶段阻断网络/OCR/VLM/approval验证调用数0；preflight与manual/OCR/VLM/Temporal就绪检查通过，operations/approvals均0。等待该新指纹的具体批准，旧0.25USD unsettled保持原状。

Tavily：0/1000作为用户报告的当前额度记录，官方价格页确认免费月额度1000及basic搜索每次1credit，已配置凭据；但官网Usage文档、定价页和changelog未明确GET /usage的credit或独立货币收费。不得据此将未知probe费用填0。可用浏览器未登录，未读取账户秘密，临时页已关闭。局部草案保持probe价格null，未创建可执行Tavily计划，API调用0；官方支持问题草稿位于 `.local/validation/ui-analysis/t017-2026-09-06/tavily-account-review/support-question.txt`，未发送。T017/T018仍未勾选，T019+未实施。

### 2026-09-06 T017 缓存解析定位unknown_evidence并约束生成Schema

用户批准 `ui-55aa572cbc394c6b900fd66a2aba4108` / `45a3b8b1fa05d265cb3e25cf68b46157f24e6346a89b2cd72dfa9539f072398a` 后执行。没有新搜索、probe或下载；manual、normalize及1条OCR均成功。一次VLM响应 `resp_0856baa638da6cf3016a9cd0d1643c87d1a20f9fa10d84ef8a`，input8810/cached4864/output3741 tokens，原始费用0.00537568USD、账本0.005376USD，reserved/unsettled均0。最终failed/provider_failed，具体diagnostic为schema_and_references/unknown_evidence，说明模型引用了本次view/OCR集合之外的证据ID。无法由该固定原因码推断具体错误ID或回溯解释更早的invalid_analysis；未保存原始模型正文。

修复生成约束：所有evidence_ids共享InputEvidenceId定义，enum只来自当前view ID和原始OCR text_id，保留严格本地校验。新计划在UIVLMPolicy冻结 `evidence_policy=source-id-enum-v1`，旧计划缺字段时保留旧请求Schema；新字段改变模型策略工件和计划指纹。按官方Structured Outputs限制检查总枚举<=1000及>250字符串枚举的总字符<=15000，超限请求前拒绝，不截断、不回退到无限制引用。来源：[官方结构化输出文档](https://developers.openai.com/api/docs/guides/structured-outputs)。实际样本18条OCR加1个view共19个允许ID，离线Schema检查通过；无新增模型调用。

合同覆盖五类evidence_ids、允许当前来源、无OCR、数量/字符上限、历史策略兼容；原有unknown_evidence拒绝及费用合同保留。新增核心合同修复前2 failed/20 passed；最终24 passed。中间两个上限用例错误地匹配人类可读message，已改为断言PipelineError.code，失败日志保留。执行合同和开发指南已同步。

修复后独立计划已准备：task `ui-547a475a116747b9bcd73882327910e0`，完整fingerprint `5d15054d349ffcecb9777d9626256e0a137bfdb7f1f8e7e68af72e984d295baa`；单轮/总预算0.25USD，GPU0、修订0、最多1次VLM，搜索/probe/下载新增数均0。复用同一原图和来源，冻结新枚举策略；规划禁网检查、preflight通过，未批准、未执行。新预算加旧0.25USD unsettled的未决/新支出上限合计0.50USD（不含已经结算的历史费用）。Worker已重启加载修复，不复用已消费批准。

本次失败、OCR、账本、171条Temporal历史及测试证据在 `.local/validation/ui-analysis/t017-2026-09-06/serpapi-cached-parse/`；新计划在同级 `serpapi-evidence-enum-parse/`。原搜索与全部失败记录保留，旧评级图任务unsettled未动。修复后现场结果仍待新批准，SerpApi尚未验收；Tavily仍缺probe计价依据，T017/T018未勾选，T019+未实施。

证据枚举修复最终完整回归：**289 passed、3 skipped，61.57秒**；ruff和git diff --check通过。全部测试无新增外部推理。

### 2026-09-06 T017 SerpApi来源图片解析通过（Tavily仍待验收）

用户批准 `ui-547a475a116747b9bcd73882327910e0` / `5d15054d349ffcecb9777d9626256e0a137bfdb7f1f8e7e68af72e984d295baa` 后执行成功。按冻结的source-id-enum-v1约束，VLM全部evidence_ids均属于输入view/OCR集合。1条OCR成功；1次VLM响应 `resp_0a0d3ab9e38c2a0f016a9cd3f903e887d1b6a6366b5107e712`，input9228/output3150 tokens，实际原始费用0.0056256USD、账本0.005626USD，reserved/unsettled均0，GPU0。未新增搜索、probe或下载。Temporal succeeded，261条历史事件已归档。

产物为25个元素、18条原始文字、25张矩形切片和标注图；未关联文字0，所有切片与canonical相应区域逐像素一致，manifest与输出SHA通过。目视确认三项祝福面板、小图标、文字及Boon Info控件位置；正式质量仍pending，左上大徽章未单独切出，面板装饰末端未全覆盖，整图美术为单个图像元素，矩形切片保留背景，未宣称透明抠图或完整质量验收。

原SerpApi搜索task `ui-5d765ae5c9d84a81b4b20d942f3b5476` 的1次probe、1次搜索和1次下载已有真实成功记录。已核验原下载→intake→本次parse的不可变artifact ID关联与相同图片SHA，保留搜索请求 `6a9ccab14c437fc2d6aa49fc`、来源页面及原provenance；原search工作流自身因早期分析失败而保持failed。组合证据满足“真实搜索取得的图片送入真实单图解析”，没有伪称整个原search工作流恢复成功，也没有重复搜索。

SerpApi部分验收记录见 `.local/validation/ui-analysis/t017-2026-09-06/serpapi-evidence-enum-parse/acceptance.json` 与review.md；preview-search.json登记accepted.serpapi，保留全部历史尝试。SerpApi已知结算合计0.017118USD，旧评级图任务0.25USD unsettled仍原样保留、不计为已知费用。

新增 `tests/integration/test_ui_search_runtime.py`，由 `LETSAIGC_UI_SEARCH_EVIDENCE` 指定preview-search.json所在目录并用--ui-live启用，只复核已保存的批准、预算、搜索/下载请求、来源链、输出哈希、证据引用和切片像素，不产生推理。SerpApi两项通过；Tavily无成功证据的两项明确skip。Tavily仍需GET /usage的明确计价依据，当前API调用0；T017/T018仍未勾选，T019+未实施。

SerpApi证据纳入后的完整回归：**291 passed、5 skipped，61.34秒**；跳过为Tavily未验收两项、GPU现场批准一项、Windows特定两项。ruff及git diff --check通过，无重复推理。

### 2026-09-06 Tavily Usage实测与下一次验收计划

按用户明确要求，间隔至少1秒连续执行10次GET /usage，全部HTTP200，无重试或额外探测。Researcher账户用量始终0/1000、paygo_usage为0，用户刷新计费页后确认“仍为 0/1000，费用无变化”。该账户实测作为下一次有界验收的probe零费用依据，有效复核至2026-09-07；不是官方永久或全账户免费承诺。证据、调用前登记及用户确认保存在 `.local/validation/ui-analysis/t017-2026-09-06/tavily-usage-experiment-10/`，未保存密钥或完整账户响应。

核实官方Usage页的OpenAPI描述“Returns null if unlimited”，修复显式key.limit=null被误判为unknown的问题：有效key.usage加显式null只受account套餐剩余额度限制；缺失/异常/零仍保持unknown，PAYGO池不参与。官方文档副本SHA-256为 `1d512af1f3568d3098a8f03bc1677a0a78692405b580ad7a4bf825f341521de8`。新增合同测试先复现失败再通过。实验白名单未保存null字段，不能据此断言本账户实际key.limit，获批后的运行时probe将按真实字段判断。

新Tavily计划：task `ui-23925829f177462b82a1d38d39c796de`，完整指纹 `1e58e734357b53a56d0720a73fd5d10660bac619e85a72eca6582943b61c5cbe`，查询“Hades 2 boon selection screen”；单轮/总预算各0.25USD，GPU0，修订0，最多1probe、1basic搜索（1credit）、1下载、1VLM。下载后先目视核对实际UI。计划和预检均无搜索/OCR/VLM调用，账本操作及批准数为0；原搜索配置已恢复。等待本次精确批准，不能复用历史批准。T017/T018仍未完成，T019+未开始；旧0.25USD unsettled原样保留。

Tavily空值额度修复后的完整回归：**300 passed、5 skipped，60.04秒**；skip仍为Tavily待验收两项、GPU现场批准一项和Windows两项。ruff及git diff --check通过。仅使用既有现场证据，无新增搜索或模型推理。

### 2026-09-06 T017 Tavily与T018预览验收通过

用户“granted”批准 `ui-23925829f177462b82a1d38d39c796de`，指纹 `1e58e734357b53a56d0720a73fd5d10660bac619e85a72eca6582943b61c5cbe`，单轮/总上限各0.25USD。实际1次probe、1次basic搜索、1下载、1OCR、1VLM，搜索消耗1credit；搜索request ID为 `6a1dd436-b247-4200-9118-a9fce697f6a9`，VLM response ID为 `resp_0025a240b1cdfaf3016a9ce4c4822487d19139e9a151b39d1c`。搜索与probe按本账户已核实的有界计价结算0USD，VLM输入9228、输出3117tokens，账本实耗**0.005586USD**，reserved/unsettled均0，GPU0。

目视检查确认Hades II Apollo祝福选择UI后才放行OCR/VLM，最终Temporal succeeded。产物含24元素、18条文字、24矩形切片、overlay和完整manifest，unassigned文字0；全部切片逐像素匹配canonical矩形，证据ID均来自真实视图/OCR。来源页未由Tavily关联，保留null/unknown；下载原图SHA-256 `b2fe8c09e3da579c9b4d3fb7daca72e404a1ebe8b528e3b2be8a298c8ee8fc11`，与SerpApi取得图片相同，不算独立风格样本。原始候选描述只是供应商声明，未当事实替换人工检查。小图、装饰边界与背景保留等质量限制仍pending。

Tavily批准、目视检查、来源、实际用量、全部产物、Temporal历史在 `.local/validation/ui-analysis/t017-2026-09-06/tavily-ui-screen/`。普通Worker及原搜索配置已恢复，未重放历史失败，旧任务0.25USD unsettled仍保留。现场复核测试同时支持Tavily同一工作流搜索/解析和SerpApi单独批准的后续解析，均核对原始来源链，不伪造中间导入。

T018复用T010三例手动、T017两家搜索、一次OCR登记后中断恢复证据，无新增推理。完整回归**302 passed、3 skipped，60.95秒**，包含共享拒绝/预算和固定额度响应切换；skip为未授权GPU现场一项、Windows专项两项。ruff与git diff --check通过。五份成功解析共132张矩形切片、费用0.030470USD；历史已结算失败费用0.011492USD另列，历史0.25USD未知费用不算已知实际消费。汇总在 `.local/validation/ui-analysis/preview-acceptance.json`，状态“可用预览，完整质量验收待完成”。README、使用指南和Quickstart已同步；T017/T018勾选，T019+保持未实施。

### 2026-09-06 人工校正增量规划（未实施）

用户授权“修改计划，改造spec”。保留T001—T018全部完成行、旧任务/失败/批准和原规格编号；新增US7、FR-056—067、SC-013—017及T043—T050，执行插入T018之后，T019/T035改依赖T050。当前50任务、18完成、32待做；11项LIVE中3完成8待做，T049新增真实本地浏览器验收，零新增模型调用。当前机器T017/T018已有验收不被这次规划取消。

明确基础类型与可选语义标签、人工/OCR文字来源、草稿/确认版本、稳定ID及锁定，CPU局部物化不调用模型、不计补图修订。校正v2与历史layout/VLM Schema分开；公共账本先T044 v3→v4加入review三表，原T020父子迁移顺延v4→v5。本次不运行迁移，当前运行库和Worker仍为原v3。页面原生HTML/CSS/ES modules+SVG，受限Python回环服务，不安装Node或模型；人工确认与付费批准分离。

计划工件采用增量Specify→范围澄清→Plan→Tasks，模板/脚本只读检查；pwsh缺失，以已核实路径完成等价检查，未运行会覆盖现有plan的setup-plan或新建feature脚本。客户端当前Default模式，Agent无法自行切换；本次授权仅落实为设计文档，无运行时代码修改或外部推理。修订后仅一次只读Analyze，报告后停止，不自动实现或循环修复。

本次规划校验：50任务编号唯一、依赖无环，T043/T019/T035入口符合新顺序；18条已完成任务行逐字一致，93项FR/PER/SC均有任务映射，新合同本地链接可解析。src/tests/configs及开发脚本/技能文件哈希与修订前一致；仅同步AGENTS的计划技术说明。完整既有回归**302 passed、3 skipped，58.58秒**，ruff与git diff --check通过，外部搜索/模型调用0。新增人工校正功能尚未实现，以上测试不是其功能验收。


### 2026-09-06 T043—T050 人工校正实施

T043先建立Review v2、只读旧v1适配、纯动作/来源/锁定、CAS/幂等、迁移与失败合同；T044加入公共账本三表及不可变patch/草稿，现场停worker、备份后v3→v4，旧11表哈希保持，随后恢复worker。T045纯CPU确认物化保留原输出，切片按canonical+bbox复用；磁盘不足、事务冲突、中断不推进确认head，新request重复确认同一版本也不替换旧manifest。

T046的回环Host/Origin/CSRF/一次bootstrap与超时、跨task artifact闭包、1MiB上限/额外字段、未认证与付费路由拒绝均有合同。会话Cookie按端口隔离，多图同时打开互不覆盖。T047交付原生SVG编辑页面、类型/标签/文字/父级/锁定、画框/移动/角点、缩放/平移、100步撤销重做、保存/确认、重开/冲突保留与旧版本恢复。静态文件随Python包发布，无Node构建或Torch要求。

T048冻结具体confirmed版本，验证任务/角色/hash并支持只读检查导出，拒绝草稿/跨task重登记与ground_truth伪装；不增加GPU、Agent工具或模型重读路由。T049真实浏览器复用三张商业截图，当前有效确认切片84个全部逐像素来自canonical；原OCR记录完整保留。实际确认产物生成后、发布前终止专用进程，旧head未推进；服务重启恢复同一request成功。浏览器验证画框、文字、分类/父级、锁定、删除撤销、125%缩放与角点、键盘撤销/重做、文本Delete安全、保存重开、双页CAS冲突和恢复历史草稿。

证据在.local/validation/ui-analysis/review-acceptance.json及review-2026-09-06/；旧业务表哈希与迁移前一致，搜索/OCR/VLM/GPU新增调用均0，unknown 0.25USD仍保留。人工修改仅标记human_assisted，正式质量pending；SC-017局部模型/child现场联动仍留T029/T030，后续所有未完成LIVE不变。

T050最终完整回归：**340 passed、3 skipped，59.78秒**；跳过项为真实GPU1项、Windows共享冲突2项。ruff与git diff --check通过。人工校正现场的84个切片中82个复用原ArtifactRef与SHA；新增调用0，旧11表哈希不变。按用户要求关闭4个验收浏览器标签页，并停止本次临时校正服务；既有OCR/Temporal及普通worker保留。未提交Git，下一阶段从T019开始。

### T019 合同交付记录（2026-09-06）

选择/候选与 MaskedGenerationPlan v2 机器合同及三个先行测试文件已交付，见 [editing.md](contracts/editing.md)。完整回归 407 passed、3 skipped、20 xfailed；20 项为 T020/T021 尚未实现的 DTO/选择/父子边界，不能计为 GPU 功能通过，T028 仍须接线验收。同时补齐 v4 账本的分析、搜索及额度结算兼容回归。未升级真实账本 v5，所有未完成 [LIVE] 保持原状态。

### T020 并行实施记录（2026-09-06）

用户授权 Luna xhigh subagent 实施。两路分别实现 DTO 与账本/服务，第三路独立审计 T035；主代理审查、补预算/修订边界并完成集成。T020 完整回归 473 passed、3 skipped、3 xfailed（仅 T021 CLI），ruff/diff 检查通过。真实账本保留 v4，v5 仅在临时账本验证；63 条历史绑定 hash 全部一致，历史 unknown 0.25 美元不变，无新增 provider/GPU 调用。日志 `.local/validation/ui-analysis/t020-2026-09-06/`。

T035 仅交付来源分离清单和只读准备审计：6 例现有素材、缺18例、缺独立评估集和背景真值，未完成独立标注；保持未勾选。T021/T022/T025 的前置已满足，可按上面的独占文件规则安排下一波；真实 GPU 与正式质量验收仍按原依赖与具体批准门槛执行。


### T021—T023、T025—T026 并行实施验收（2026-09-06）

本次沿用用户选择的 `gpt-5.6-luna` / `xhigh`，三路分别负责选择与字形、SAM 服务与分割、蒙版编译与合成；主代理完成来源、像素、费用、CLI 和跨模块独立审查及修复。五项已勾选，累计33/50；T024、T027和其余未完成LIVE保持未验收。

- T021：reviewed-task 冻结确认版本，automatic-task 显式复用原布局；候选预览、选择、检查均不重跑模型。可信入口核验原图与EXIF/ICC规范化图的实际像素关系，不以二者SHA相同作为条件。当前编辑执行仍返回 capability_not_ready，T028才接入具体子计划批准。
- T022/T023：固定本地safetensors及伴随文件校验、服务执行凭证和单GPU占用边界；SAM提示、canonical轮廓/估计alpha、原始切片与字形资产。保留原透明像素；文字细化不把控件轮廓直接冒充字形；部分失败保留成功产物和unknown质量。质量不确定不抹除实际运行时间。替身与CPU合同通过不代表真实SAM验收。
- T025/T026：原生sdxl-inpaint四工件、MaskedGenerationPlan v2编译、配对变换/极性、最终mask裁剪与keep保护、空mask零生成、回映及区外逐像素不变；原始生成候选先保存证据再校验，旧v1编译金样保持。

完整集成回归：**565 passed、4 skipped、2 warnings，63.54秒**；ruff及diff检查通过。跳过为既有真实GPU测试1项、专用object_info探测1项、Windows共享冲突2项；警告为Pillow测试API弃用。此前T019/T020的预期失败均已消除。本地汇总与日志在 `.local/validation/ui-analysis/delegated-editing-2026-09-06/acceptance.json`、`pytest-final.log` 和 `ruff-final.log`。回归复核已保存的现场证据，新增provider/GPU调用均0。

T024只读准备检查显示本机Darwin arm64缺SAM模型目录，锁定CUDA环境与伴随文件哈希尚未验证；记录 `t024-readiness.json`。T027专用探测因缺 `.local/runtime/ComfyUI` 锁定运行目录，在发送GET前退出，证据 `.local/validation/ui-analysis/inpaint-object-info.json`；不得据此断言已有其他服务端口不可用。真实SAM需完整锁定资源及新计划指纹/预算批准；ComfyUI须先有匹配锁的本地部署，再只读核验object_info，不提交生成。T028尚未接线；T024明确允许缺能力时保留LIVE未验收并继续后续接线，T027仍是实际补图能力门槛，不能用本轮离线通过替代它们。

账本审计：早期旧CLI测试未隔离本地存储，留下4个未批准、未执行的测试计划（tasks 12→16）。已修复为临时账本测试，保留这些记录及旧失败日志；其余所有表的行数与SHA均与本轮前一致。最终完整回归前后所有表SHA一致。真实账本仍v4，approvals 9、operations 63、ui_step_bindings 63，历史unknown 0.25USD保留。本轮没有迁移、模型下载或Git提交，也未打开新的内置浏览器。
