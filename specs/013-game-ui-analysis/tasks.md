# Tasks: 游戏 UI 解析工作流

**Feature**: 013-game-ui-analysis | **Date**: 2026-09-04 | **Branch**: dev-game-ui  
**Baseline**: `7b91c34deb23064aaa6b492ce105956a59b47b71`  
**状态**：42项中T001—T009、T011—T016共15项首版代码任务已完成；T010及T017—T042未完成。用户已授权以同一letsaigc-core环境的conda run继续，原有测试已通过；首次mamba权限阻断保留为历史记录。10项新[LIVE]任务承接原13项真实验收要求，均未验收；具体记录见文末。

## 执行约定

依据[规格](spec.md)、[计划](plan.md)及配套合同。普通路径优先取得可用的“手动单图＋双后端搜索”，随后编辑、批次、正式验收。新任务依赖是开工依据；原G01全包和旧G依赖不再阻塞单图任务。

- 一个任务可以包含测试和实现，但必须先写其合同/政策/来源/安全/失败测试，再实现并运行；已有共享合同直接复用，不为每层复制相同矩阵。
- [P]只表示满足依赖后可独立开发；不是自动派发子代理。运行时能力、同一任务和批次child仍顺序调用。
- 本地任务记录代码/差异、命令和结果；外部执行才额外记录批准、实际受理次数、费用、资源和产物。缺条件保留未完成，不为纯函数或文档填不适用的费用字段。
- [LIVE]需明确开启、已有模型/服务、固定输入及预算和有效具体批准。按原规则保留失效/未知与失败证据，不自动部署、接受许可或沿用已消费GPU批准。
- 首版plan只本地规划，parse不需要GPU；旧Agent Responses预留规则不变。预留偏差且未超原批准限额时核算后自动继续；只有实耗超批准限额才budget_exceeded失败。
- 当前42项为新清单；文末“旧T”仅为历史映射，不是当前依赖。FR/PER/SC、R、G、U-V与里程碑编号保留。
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
- [ ] T010 [US1] [LIVE] 使用已由操作者准备的真实OCR/VLM与固定分析预算跑三例手动链路，核验loader/模型/hash/许可、全部产物和实际费用；选择一例在OCR登记后中断并恢复，复用该证据供预览验收。缺能力如实未验收，不要求正式质量阈值；保存基线测量供后续开发集复用。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_manual_runtime.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_preview_recovery.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-manual.json`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-recovery.json`。（依赖：T009）

## Phase 3：双后端搜索与首版预览（US4）

两家搜索和自动切换是首版门槛；搜索协议可在手动真实环境准备期间开发，发布需两条链路证据。

- [X] T011 [US4] 先写SerpApi/Tavily请求、额度、计价、scope和路由合同，再建立共享搜索DTO与版本化配置。覆盖零/null、PAYGO排除、偏好/低水位/回切、单方不可用/双方耗尽及计价缺失；与公共执行套件分工，不重写通用操作恢复矩阵。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_image_search.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_quota_routing.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/search.py`、`C:/Programs/LetsAIGC/configs/providers/image-search.yaml`。（依赖：T004）
- [X] T012 [US4] 先写v2→v3迁移及最后额度竞争、探测合并/重启限频的搜索特例，再加入quota_scopes/snapshots/reservations/routes/probes五表。实现同事务单任务预算检查、选路、额度预留及持久计数，跨任务scope共享但不依赖父子预算组；实际费用仍只落operations。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_quota_ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/migrations.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/search_routing.py`。（依赖：T011）
- [X] T013 [P] [US4] 先写固定SerpApi协议/错误/账户字段夹具，再实现第一页Google Images及Account适配和已知search ID只读归档恢复；计价/条款按官方依据记录，原始密钥/URL/账户响应不持久化，受理不明交给公共unknown边界。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_serpapi.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/providers/serpapi.py`。（依赖：T011）
- [X] T014 [P] [US4] 先写固定Tavily协议/错误/usage夹具，再实现basic图片搜索和account/key额度归一化；禁自动参数升级/PAYGO/收费日志恢复。核实probe计价依据，缺失则能力未就绪；模型正文/账户秘密不进入历史，unknown交给公共边界。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_tavily.py`、`C:/Programs/LetsAIGC/src/letsaigc/assets/providers/tavily.py`。（依赖：T011）
- [X] T015 [US4] 先写SearchUIProvider候选/下载/去重/来源和同图两来源对照，再实现有界acquisition。根查询规划≤1次、查询/search尝试/切换≤3/3/2；每查询候选/下载/默认选图20/5/1，首张合格图后停止。瞬时URL留在受控边界，SHA与pHash去重/相关性分析用量归原任务；失败逐项可查。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_search_provider.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_providers/search.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_activities.py`。（依赖：T005、T007、T012、T013、T014）
- [X] T016 [US4] 先写单图search Workflow/CLI边界测试，再接入ui_analysis与Worker，plan --query --max-images 1冻结双后端范围并在execute后选路。inspect展示来源、切换原因、费用及结果；复用已取得图片，未知逻辑查询不换家补发，manual不探测。更大max_images在批次阶段前明确未就绪。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_search_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/worker.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`。（依赖：T009、T015）
- [ ] T017 [US4] [LIVE] 两家各一次有界真实搜索并将各自取得的图片送入真实单图解析；使用已核实条款/计价、固定输入和分析预算，分别记录实际请求单位、费用、来源和筛选。可用供应商子集配置各验证一家，自动切换用固定额度响应验证，不故意耗尽账户；缺任一家记录不能宣告双后端预览完成。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_search_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-search.json`。（依赖：T016）
- [ ] T018 [US4] [LIVE] 汇总三例手动、两家搜索与一次代表性恢复的有效证据，运行共享拒绝/预算套件和切换固定响应，发布“可用预览，完整质量验收待完成”。复用T010/T017真实记录，不为汇总重复推理；同步README与使用指南的首版入口、缺能力和停止原因。首版依赖闭包不得含SAM、Comfy、父子预算或全量评估。 文件：`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`、`C:/Programs/LetsAIGC/README.md`、`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/quickstart.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/preview-acceptance.json`。（依赖：T010、T017）

## Phase 4：自动提案、拆解与补图（US2/US3）

默认提案后直接具体批准；父子预算、SAM/Comfy和局部修订现在才进入依赖。

- [ ] T019 [US3] 先写后续编辑/父子边界合同：自动区域提案→预览→具体批准、候选编号/文件覆盖互斥、选择变化使旧child失效、根/子作用域与共享费用。只增加GPU与父子的差异用例，通用拒绝和重复受理引用T003；形成MaskedGenerationPlan v2和选择机器合同。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_selection_cli.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_child_approvals.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_masked_plan.py`。（依赖：T018）
- [ ] T020 [US3] 实现v3→v4的budget_groups/child_bindings/operation_charges三表及先行迁移测试，扩展UI选择/候选/子请求DTO和根费用/修订归组。分割/补图定义此时注册；新旧单任务结算兼容，费用不重复入账，具体批准原子激活候选版本并禁用旧待执行child。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_child_ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/migrations.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/ledger.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/service.py`、`C:/Programs/LetsAIGC/src/letsaigc/pipelines/registry.py`、`C:/Programs/LetsAIGC/src/letsaigc/schemas/ui.py`、`C:/Programs/LetsAIGC/src/letsaigc/schemas/agent.py`。（依赖：T019）
- [ ] T021 [US3] 实现分析授权内的区域提案和预览：复用VLM布局/目标，不增加免费规划次数；确定性校验bbox/元素、保留区域及来源。明确方案直接形成待批准准备信息；含混时列候选编号，ui select --candidate与--selection二选一且登记零模型调用。引用/hash由可信入口补齐，普通用户不手填机器合同。 文件：`C:/Programs/LetsAIGC/src/letsaigc/agent/ui_analyzer.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/selection.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_selection.py`。（依赖：T020）
- [ ] T022 [US2] 先写SAM环境/安全loader/hash/预处理映射合同，再准备独立SAM锁结构和服务GPU执行凭证/卸载边界；仅使用固定官方safetensors和本地文件模式，不自动下载或.pt回退。为服务适配增加一次响应丢失恢复边界测试，不复制公共账本矩阵。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_segmentation.py`、`C:/Programs/LetsAIGC/environment/vision-segmentation.yml`、`C:/Programs/LetsAIGC/configs/runtime/vision.lock.yaml`、`C:/Programs/LetsAIGC/configs/runtime/vision.yaml`、`C:/Programs/LetsAIGC/src/letsaigc/vision/service.py`。（依赖：T020）
- [ ] T023 [US2] 在先行领域测试后实现SAM提示/轮廓回映、估计alpha和字形颜色/连通域细化，保留原始切片、mask、alpha和glyph角色及低确定性。每图提示≤64，绑定具体selection/version/hash及GPU批准；可通过受限固定样本入口验证，不对Agent暴露执行工具。 文件：`C:/Programs/LetsAIGC/tests/unit/test_ui_text_assets.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_segmentation_assets.py`、`C:/Programs/LetsAIGC/src/letsaigc/vision/segmentation.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/text_assets.py`。（依赖：T021、T022）
- [ ] T024 [US2] [LIVE] 用固定样本和具体批准验证真实SAM加载、mask/alpha/glyph角色、canonical映射、GPU释放及实际用量；记录分割开发测量。缺能力保持未验收，正式阈值在最终开发集阶段冻结，不阻塞后续编辑接线。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_segmentation_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/segmentation-runtime.json`。（依赖：T023）
- [ ] T025 [US3] 先写原生mask编译/四工件、旧T2I/I2I金样和合成像素合同：配对输入、极性/通道/变换、扩张羽化裁到ROI并避开keep、空mask零生成、尺寸错误拒绝和区外逐像素相同。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_inpaint_compile.py`、`C:/Programs/LetsAIGC/tests/unit/test_ui_inpaint_pixels.py`。（依赖：T020）
- [ ] T026 [US3] 实现MaskedGenerationPlan v2的编译分派及原生sdxl-inpaint UI/API图、工作流合同和recipe；旧v1字节/编译语义不变。实现冻结最终mask、回映及CPU精确合成，失败候选及模型/recipe/编译hash均入证据。 文件：`C:/Programs/LetsAIGC/src/letsaigc/workflows/compiler.py`、`C:/Programs/LetsAIGC/src/letsaigc/backends/comfy.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/inpaint.py`、`C:/Programs/LetsAIGC/configs/workflows/recipes/sdxl-inpaint.yaml`、`C:/Programs/LetsAIGC/workflows/ui/sdxl-inpaint.json`、`C:/Programs/LetsAIGC/workflows/api/sdxl-inpaint.json`、`C:/Programs/LetsAIGC/workflows/contracts/sdxl-inpaint.yaml`。（依赖：T025）
- [ ] T027 [US3] [LIVE] 只读核验锁定ComfyUI的object_info与原生节点/四工件，运行旧编译金样和本地像素合同；不提交生成。缺已部署服务保留未完成，作为真实补图前能力门槛。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_inpaint_object_info.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/inpaint-object-info.json`。（依赖：T026）
- [ ] T028 [US3] 运行T019选择/子批准合同并接线decompose/reconstruct：推荐选择冻结后直接展示具体分割计划，批准同时确认范围；分割后最终image/mask再形成补图批准。SAM/Comfy顺序交接并确认释放，选择候选替换使旧待执行批准失效；parse仍可独立完成。 文件：`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_activities.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/worker.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_reconstruction.py`。（依赖：T021、T024、T027）
- [ ] T029 [US3] 先写四种枚举局部修订与依赖闭包测试，再实现reread_text/adjust_segmentation/review_region/regenerate。未变ID/产物复用，自动可调仅prompt/negative_prompt/seed；新mask/model/recipe须新批准，沿同一编辑链/根组计数，不重置预算，失败版本保留。 文件：`C:/Programs/LetsAIGC/tests/unit/test_ui_revision.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/revision.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`。（依赖：T028）
- [ ] T030 [US3] [LIVE] 验证真实decompose和scene_background/map_surface补图，覆盖默认提案直接批准、候选/高级覆盖、最终mask批准、区外像素一致、有限修订及GPU受理后一次恢复观察。保存实际质量值和谱系，复用公共故障证据；同步编辑操作指南，缺真值不报背景恢复准确率。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_reconstruction_runtime.py`、`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/editing-runtime.json`。（依赖：T029）

## Phase 5：两种来源的顺序批次（US5）

每条输入可追踪，父级共享预算和取消；不复制单图执行引擎。

- [ ] T031 [US5] 先写两种来源顺序批次的差异合同：10项/第11拒绝、精确重复映射/近似只提示、两种失败策略、单活动child、父取消竞争、共同次数和Continue-As-New边界。通用operation/费用故障复用T003/T019，不再次复制全矩阵。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_batch.py`、`C:/Programs/LetsAIGC/tests/contract/test_ui_batch_cli.py`。（依赖：T030）
- [ ] T032 [US5] 实现ui_batch父级一次供给后顺序单图child，登记父子scope、根费用与来源映射；GPU仍各自具体批准。取消先关闭根门禁，再REQUEST_CANCEL并保留未决归属；Continue-As-New只在无活动child安全边界。 文件：`C:/Programs/LetsAIGC/src/letsaigc/pipelines/registry.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_workflow.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/ui_activities.py`、`C:/Programs/LetsAIGC/src/letsaigc/execution/temporal/worker.py`。（依赖：T031）
- [ ] T033 [US5] 接入多项import/plan和max_images≥2，按原始手动条目数/搜索上限选路；完善父子inspect、部分结果和候选/批准等待，复用manifest来源谱系与MLflow派生投影。先写对应输出/谱系合同再实现，正式批次能力不改变旧Agent输入限制。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_batch_evidence.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/cli.py`、`C:/Programs/LetsAIGC/src/letsaigc/tracking/manifest.py`、`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`。（依赖：T032）
- [ ] T034 [US5] [LIVE] 有界验证手动与搜索两种来源的真实顺序批次及获批子生成，保留10项成功/失败/等待/未处理混合场景及真实/替身属性；取消无孤立提交，输入至结果映射齐全。复用已有单图与GPU证据，仅补批次差异。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_batch_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/batch-runtime.json`。（依赖：T033）

## Phase 6：正式质量与完整验收（含US6）

补齐完整数据与质量门槛，汇总有效证据，只补受影响或缺失的真实观察。T035可在预览后先准备。

- [ ] T035 补齐24例完整数据/标注及底图分组，开发16/评估8、已知背景至少8且4/4分布；三个预览样本只在开发集。此任务可在预览后准备，不回头阻塞预览；私有图仅本地引用，拥有分发权的样本才可入Git/DVC。 文件：`C:/Programs/LetsAIGC/tests/fixtures/ui_analysis/cases.yaml`、`C:/Programs/LetsAIGC/tests/fixtures/ui_analysis/annotations.json`。（依赖：T018）
- [ ] T036 先写CER/IoU/文字关联/语义/背景误差/接缝度量分母与样本适用性测试，再实现正式质量计算与draft→frozen门槛；使用评估集前校验阈值及样本hash，未知背景不报恢复准确率。 文件：`C:/Programs/LetsAIGC/tests/unit/test_ui_quality.py`、`C:/Programs/LetsAIGC/src/letsaigc/ui_analysis/quality.py`、`C:/Programs/LetsAIGC/configs/eval/ui-analysis.yaml`。（依赖：T035）
- [ ] T037 [LIVE] 在16例开发集上建立OCR/VLM/分割/补图质量基线并冻结阈值和版本；复用适用的预览/编辑实测，只补缺失样本或已改变依赖。真实额外调用另有明确预算/批准，独立评估集不得用于调参。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_quality_runtime.py`、`C:/Programs/LetsAIGC/configs/eval/ui-analysis.yaml`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/development-quality.json`。（依赖：T030、T036）
- [ ] T038 [LIVE] 用8例独立评估集按冻结规则评估，结合开发集核对24例M-U1必需产物、至少8例已知背景真值与全部角色/像素硬检查；未达标保留失败，不降低阈值。记录正式质量证据，不能用预览报告代替。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_quality_runtime.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/evaluation-quality.json`。（依赖：T037）
- [ ] T039 [US6] [LIVE] 汇总旧历史兼容、OCR后恢复、GPU受理后、搜索提交/响应窗口和父取消的已有证据；仅缺少真实记录或受版本变更影响时补对应观察。最终恢复索引区分真实/替身，确认unknown保留、无重复受理及资源归属，不无条件重跑全部故障。 文件：`C:/Programs/LetsAIGC/tests/integration/test_ui_recovery_runtime.py`、`C:/Programs/LetsAIGC/tests/integration/test_ui_temporal_replay.py`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/recovery-acceptance.json`。（依赖：T034、T038）
- [ ] T040 依据实际交付完成使用/恢复/能力限制说明、Agent指南和Quickstart；合成秘密扫描覆盖日志/异常链/历史/manifest/移交结果，生产导出复用既有许可与人工门槛。先写新增公共输出的安全合同再改输出代码（若需要），不得因文档任务扩大运行特性。 文件：`C:/Programs/LetsAIGC/tests/contract/test_ui_security.py`、`C:/Programs/LetsAIGC/docs/game-ui-analysis.md`、`C:/Programs/LetsAIGC/docs/agent-quickstart.md`、`C:/Programs/LetsAIGC/README.md`、`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/quickstart.md`。（依赖：T039）
- [ ] T041 从仓库根目录运行一次完整mamba pytest、ruff check .及git diff --check，检查新增公共schema/compiler/CLI的既有回归；只修实际失败涉及的范围，不重复已有效的网络/GPU验收。记录本次输出与无法执行原因。 文件：`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/tasks.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/regression.json`。（依赖：T040）
- [ ] T042 核对FR/PER/SC、R01—R16、U-V01—U-V14与M-U1—M-U3的证据和缺项，建立完整验收索引。只有正式质量、真实能力和必要回归均满足才宣布完整交付；保留用户修改/旧worktree，不自动commit。 文件：`C:/Programs/LetsAIGC/specs/013-game-ui-analysis/tasks.md`、`C:/Programs/LetsAIGC/.local/validation/ui-analysis/acceptance-index.json`。（依赖：T041）

## 首版依赖与并行

T018的依赖闭包仅为T001—T017。它不含T019之后的父子预算、SAM、Comfy、批次或正式质量评估。先交付手动链路T010，再用T017/T018发布包含两家搜索的预览；T011—T016允许按真实接口依赖提前准备，不被缺现场凭据的验收任务阻塞。

同波可并行的仅T006/T007（OCR与VLM）及T013/T014（两家适配器），目标文件无交叉；公共registry/ledger/CLI/manifest修改和真实外部验证串行。完整交付仍以T042为准，不把首版预览标为M-U1—M-U3全部通过。

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
