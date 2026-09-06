# 013 游戏 UI 解析：研究与决策

日期：2026-09-04。范围为 [规格](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/spec.md) 的技术设计；前阶段研究检查了代码、文档和公开官方资料，没有调用供应商账户、部署模型或运行 GPU；本次仅同步已批准的文档决策，沿用这些研究引用。

本次产品优先修订保留 D01—D12 的技术依据和引用；当前交付顺序为手动单图→双后端搜索预览→拆解补图→批次与正式验收。下列决策已同步新顺序，原 Gxx 仅在历史快照中保留。

## D01：在现有持久运行基础上增加 UI 多步骤流程

**Decision**：保留 `PipelinePlan` v1、`ArtifactRef` 和现有 `PipelineService`，以严格 UI 请求素材引用扩展静态注册。先增加 `ui_analysis`（手动/搜索单图parse）及必需步骤绑定，随后增加 `ui_segmentation`、`ui_inpaint`，最后接入 `ui_batch`（父级获取后顺序分析子任务）。操作、额度、资源及后续父子预算使用同一公共账本；首版未实现的批次和编辑明确未就绪，不先注册空壳或走Comfy默认回退。

**Rationale**：CodeGraph 核验 [registry.py](/C:/Programs/LetsAIGC/src/letsaigc/pipelines/registry.py:7) 目前只有两个流程且一流程对应一个能力；[service.py](/C:/Programs/LetsAIGC/src/letsaigc/pipelines/service.py:169) 的提交路径固定 `generate`。因此不能只注册名称或把旧 Agent 整体包进重试 Activity。公共 [ledger.py](/C:/Programs/LetsAIGC/src/letsaigc/pipelines/ledger.py:127) 已具备精确批准、预留、资源归属和未知结果机制，应兼容扩展。

**Alternatives considered**：不另建 UI 调度器或数据库；不在本项引入任意 YAML DAG 执行器或完整 AgentProfile 平台。显式 Workflow 满足三个里程碑并限制权限面。

## D02：分析授权与动态确定的 GPU/补图批准分开

**Decision**：UI `plan` 仅进行确定性校验和输入登记，预规划模型成本为零；查询改写、候选筛选及 VLM 都在分析授权之后执行。分析产生 canonical、框/点或 mask 后，创建独立不可变分割/补图子计划，请用户批准其具体指纹。默认由 Agent 在真实布局上提出推荐区域、保留/移除对象与预览，可信层校验并冻结后直接形成待批准子计划；execute --approve 同时确认范围并批准该次 GPU 操作。含混时提供候选编号，选择文件保留为高级覆盖；区域内部指纹约束与分割/最终蒙版分别批准保持不变。同一图像编辑链最多两次生成修订（不含人工校正保存），还受根 TaskBudget 的共同修订上限约束；新 mask/子计划不清零。

**Rationale**：未知的分割提示、最终蒙版不能被最初的分析批准覆盖。父任务的分析授权可以通过已登记父子绑定约束分析子任务，但不制造新的人工批准回执。GPU 子任务必须有自己的精确回执。旧 `agent plan` 的 Responses 预留计费规则不变，为 $0.03 + 每张输入 $0.01。实耗超过预留但仍在原批准逐次及总限额内时，UI记录非失败 reservation_adjusted，据实结算并核算后续预算后自动继续；后续无法预留时停止新消费、保留成果，扩大预算须新计划和批准。实际违反批准限额才 budget_exceeded 失败。旧 ledger.result.budget_exceeded 当前表示“超过预留”，保持兼容，UI不能直接据此判失败；未知费用不按零结算。

**Alternatives considered**：拒绝“批准整个模板即授权以后所有生成”；不为每个已授权 CPU 步骤重复请求人工确认；不把审批工具交给 Agent。

## D03：历史指纹和公共数据库升级

**Decision**：公共 `PipelinePlan` v1 保持序列化不变；UI 的严格参数载荷仅放请求、策略和评估配置的 `ArtifactRef`；用户后续选择保存为步骤素材，具体子请求绑定 selection_ref，不改根计划或增加顶层参数键。带蒙版的生成采用显式 `MaskedGenerationPlan` v2，旧 `GenerationPlan` v1 不增加默认输出字段。数据随功能分期迁移：v1→v2仅 ui_step_bindings，v2→v3增加 quota_scopes/snapshots/reservations/routes/probes 五表，v4→v5增加 ui_budget_groups/child_bindings/operation_charges 三表（2026-09-06先由T044的v3→v4加入review三表）。后两组全名分别见数据模型；费用仍只在既有operations入账。每次迁移随对应变更验证，保留旧行和操作键，首版不等待父子预算。

**Rationale**：[schemas/pipeline.py](/C:/Programs/LetsAIGC/src/letsaigc/schemas/pipeline.py:141) 对完整对象计算指纹；旧模型默认多序列化一个 null 都可能改变批准。当前账本只接受版本 0/1，升级必须明确停写、备份和兼容 Worker，不能让原版 Worker 继续打开已迁移数据库。

**Alternatives considered**：不原地重算历史指纹，不另建 UI 账本，也不修改已运行计划。旧 `comfy_generation` 仍只接受零修订；UI 修订由新的 Workflow 实现。

## D04：Temporal 重试与取消

**Decision**：不可逆提交 Activity 不自动重试；查询、收集及纯本地计算按有界策略重试。子任务使用 `REQUEST_CANCEL` Parent Close Policy，父级先关闭账本提交门禁，再请求取消并等待可确认结果。Continue-As-New 只在没有活动子任务、消息处理完成时进行。

**Rationale**：Temporal 子流程默认关闭策略是终止，不能据此假定外部生成停止；Workflow 重放要求确定性，实时额度和数据库访问应放 Activity。首版采用单任务多步骤与一次代表性中断恢复；父子取消随GPU/批次扩展。公共执行层只维护一套批准、操作唯一性、unknown、取消和结算矩阵，适配器验证边界接线，最终复用适用证据、只补变更影响与缺口。具体超时和对账见 [执行契约](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/contracts/execution.md)。[Child Workflows](https://docs.temporal.io/develop/python/workflows/child-workflows)、[Versioning](https://docs.temporal.io/develop/python/workflows/versioning)、[Error handling](https://docs.temporal.io/develop/python/best-practices/error-handling)。

**Alternatives considered**：不依赖 Temporal 自动重试实现提供方“恰好一次”；不使用默认终止策略代替外部取消；不重做当前试点没有启用的服务器 Worker Versioning 路由。

## D05：全分辨率标准图与素材存储

**Decision**：Pillow/NumPy 完成 EXIF、标准图和切片；分析视图可缩小，裁切基准始终是完整 canonical。矩形坐标为左闭右开像素 xyxy，所有变换显式保存。源、布局、mask 与结果统一通过公共 ArtifactStore 保存到 `.local/pipelines/artifacts`，任务索引及 manifest 在 `.local/pipelines/tasks`。

**Rationale**：[PipelineService](/C:/Programs/LetsAIGC/src/letsaigc/pipelines/service.py:29) 已拥有上述公共素材库。原本地主计划的 `.local/agent/tasks/.../ui` 是建议路径，此处选用实际公共根目录，避免 UI 再复制一套存储。旧 Agent 的 8 张输入合同和缩略图行为保持不变，UI 单独处理 10 张上限。

**Alternatives considered**：不在最长边 1536 的缩略图上做最终裁切；不把原文件路径或带签名 URL 放入 Workflow 历史。

## D06：OCR 独立 CPU 部署

**Decision**：首轮候选锁为 Python 3.12、PaddleOCR 3.4.0、PaddlePaddle 3.2.2 CPU；模型明确选 PP-OCRv5 的 `PP-OCRv5_server_det` 和 `PP-OCRv5_server_rec` 静态推理包。T006 定义锁、协议样本并实现独立CPU服务，T010在三个开发样本上真实识别；正式阈值与24例质量基线后置到T035—T038。模型加载、hash、许可和真实推理仍是首版门槛，SAM就绪不是。

**Rationale**：Paddle 3.2.2 发布了 Windows x64/CPython 3.12 CPU wheel，但这不是完整依赖组合和模型实测证明。新 OCR 文档已经含更新模型，因此不能依赖随版本变化的默认模型。[PaddleOCR 3.4.0](https://pypi.org/project/paddleocr/3.4.0/)、[PaddlePaddle 3.2.2](https://pypi.org/project/paddlepaddle/3.2.2/)、[OCR 接口](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html)。

**Safety**：Paddle 动态 checkpoint 保存使用 pickle；只允许审查过的静态 `inference.json` / `inference.pdiparams` 与对应配置，记录实际加载路径，拒绝动态图 checkpoint 和隐式自动下载。扩展名本身不是安全证明。[保存格式](https://www.paddlepaddle.org.cn/documentation/guides/beginner/model_save_load_cn.html)、[代码许可](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/v3.4.0/LICENSE)。

**Alternatives considered**：不修改核心或 Comfy 环境来容纳 Paddle；不因机器有 GPU 而自动切换 OCR 设备；ONNX 转换不纳入首轮。

## D07：SAM 2 安全格式和平台

**Decision**：使用独立 Python 3.12 环境、`transformers==4.57.6` 的 `Sam2Model` 静图适配器，选择 `facebook/sam2.1-hiera-large` 官方 revision `665f8e2ad61cf5f53d65644ff27c8ee525124610` 的 `model.safetensors`。显式启用 safetensors、本地文件模式、禁用远端代码；不从 `.pt` 回退。首选 WSL2 Ubuntu 运行独立分割服务，Windows 负责资源及批准。

**Rationale**：Meta 原生加载器仍以 `torch.load` 读取 `.pt`；即使 `weights_only=True`，也不符合本项目禁止 pickle checkpoint 的要求。官方 safetensors 提供满足约束的部署路径。[原生加载器](https://github.com/facebookresearch/sam2/blob/main/sam2/build_sam.py)、[固定模型快照](https://huggingface.co/facebook/sam2.1-hiera-large/commit/665f8e2ad61cf5f53d65644ff27c8ee525124610)、[Transformers 4.57.6](https://pypi.org/project/transformers/4.57.6/)、[该版本 Sam2Model](https://raw.githubusercontent.com/huggingface/transformers/v4.57.6/src/transformers/models/sam2/modeling_sam2.py)。

**Compatibility**：分割环境候选为 PyTorch 2.9.1、torchvision 0.24.1、CUDA 13.0 wheel，使用与现有 Comfy 锁相同版本但不同环境。官方提供 Linux/Windows wheel；驱动、WSL、模型键映射和显存仍需 T022—T024 验证。模型配置有视频架构字段，必须核对静图加载时预期的未使用键，禁止用任意忽略不匹配掩盖失败。[PyTorch 版本](https://pytorch.org/get-started/previous-versions/)、[SAM2 文档](https://huggingface.co/docs/transformers/v4.57.1/en/model_doc/sam2)、[Meta 安装说明](https://github.com/facebookresearch/sam2/blob/main/INSTALL.md)。

**Alternatives considered**：tiny 是需显式新配置和验收的替代，资源不足时不自动切换。SAM 仅估计可见区域轮廓，不承担原始 alpha、遮挡真值或文字语义恢复。

## D08：原生蒙版补图

**Decision**：保持 ComfyUI v0.34.2 / commit `169fcf35a2fc163fec31338b816503ddac0d3fcf`，新增 `sdxl-inpaint` recipe，使用现有 `sdxl-base-1.0` safetensors 模型。独立灰度 mask 经明确通道转换进入 `VAEEncodeForInpaint`，其隐式扩张设为 0；扩张/羽化在批准前完成。裁剪编辑视图、补齐到编码倍数并记录逆变换，最后在 CPU 上与原 canonical 精确合成。

**Rationale**：锁定版本有 `VAEEncodeForInpaint`、`ImageToMask` 等原生节点；VAE 可能改变整图及裁切尺寸，外部精确合成才能保证批准区域外像素相同。节点可用不等于 SDXL 补图质量已验收。[固定节点代码](https://github.com/Comfy-Org/ComfyUI/blob/v0.34.2/nodes.py)、[固定蒙版节点](https://raw.githubusercontent.com/Comfy-Org/ComfyUI/v0.34.2/comfy_extras/nodes_mask.py)、[原生补图说明](https://docs.comfy.org/tutorials/basic/inpaint)。

**Alternatives considered**：不把现有普通 I2I recipe 冒充蒙版补图；不安装第三方 custom nodes；不为本项无依据升级整个 Comfy 运行时。原始 UI JSON、API JSON、工作流合同和 recipe 同步维护并测试。

## D09：搜索适配与计价

**Decision**：双后端和自动切换随首版 T011—T018 交付，先各一次真实搜索取得图片并接入同一解析链路，不等待GPU或批次。SerpApi 使用 Google Images 第一页；Tavily 使用 basic，关闭自动参数、答案和原始页面内容，开启图片、图片说明及 usage。每次外部搜索都预留一次尝试和相应单位；切换不重置计数。

**Rationale**：SerpApi Account API 明确不计搜索额度，成功且非缓存的搜索即使为空仍计额度。Tavily basic 为一个 credit，自动参数可能升级到 advanced；不把“有密钥”视为免费证据。具体字段见 [图片搜索契约](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/contracts/image-search.md)。[SerpApi 图片 API](https://serpapi.com/google-images-api)、[SerpApi FAQ](https://serpapi.com/faq)、[Account API](https://serpapi.com/account-api)、[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[Tavily credits](https://docs.tavily.com/documentation/api-credits)。

**Alternatives considered**：不引入第三家供应商、advanced、自动翻页、自动充值或账户套餐管理。

## D10：额度未知与跨任务预留

**Decision**：SerpApi 区分套餐和额外余额；Tavily 同时受账户及明确 key 上限约束，默认排除 PAYGO。无法确认的零/null 特殊语义保持 unknown，不当无限使用；只读额度查询也须有合同化成本状态，不擅自将 Tavily Usage 记为零费用。

**Rationale**：Tavily Usage 有账户和 key 两层，可选项目头会改变范围；本项不发送项目头。官方 Usage 限频为每 10 分钟 10 次。75 秒最短刷新、300 秒有效期及每任务每家两次探测还不足以应对所有并发，必须在首版五张额度表中持久化共享作用域限频和预留；单任务task_id即根预算身份，不要求后续父子预算组。[Usage API](https://docs.tavily.com/documentation/api-reference/endpoint/usage)、[Rate limits](https://docs.tavily.com/documentation/rate-limits)。

**Alternatives considered**：不根据旧余额继续消费，不把原始不同单位直接排名。无法证实快照覆盖某笔本地扣减时，保留扣减；保守少用额度可恢复，重复扣费不可接受。

## D11：搜索结果未知与恢复边界

**Decision**：搜索响应解析与候选下载放在一个受控 acquisition Activity，瞬时 URL 仅存内存；每次搜索、下载分别登记操作。确认未受理才切换；已发出但结果未知时冻结该逻辑查询。SerpApi 有已知 search ID 时可在其保留期内查询 archive；Tavily 不依赖收费日志 API 来恢复搜索结果。

**Rationale**：SerpApi 429 同时可能表示限流或额度耗尽，不能统一解释。Tavily 日志不是免费、完整的结果重建接口；超时及 5xx 不能证明未受理。[SerpApi 错误](https://serpapi.com/api-status-and-error-codes)、[搜索归档](https://serpapi.com/search-archive-api)、[Tavily Logs](https://docs.tavily.com/documentation/api-reference/endpoint/logs)。

**Alternatives considered**：不以另一家的调用作为免费重试；没有可恢复签名 URL 且原图尚未登记时，报告输入重新供给需求，而不是重跑付费查询。

## D12：评估、许可证和就绪状态

**Decision**：首版固定三个开发样本：英文横屏HUD、中文密集UI、竖屏截图，检查真实布局、文字、矩形切片、标注图及来源；另两家各一次真实搜索并解析，固定额度响应验证切换，明确为可用预览。随后T035补齐至少24例、8个已知背景，按底图分开发16/评估8，其中已知背景4/4；三例首版样本始终属于开发集。T036—T037在开发集冻结阈值，T038独立评估，不用评估集调整门槛。

**Rationale**：完整质量验收和最早可检查产品是不同交付节点。提前捏造精度阈值或要求首版具备全部真实适配器都会造成错误验收。每项度量须有样本范围、分母、阈值方向及版本；模型自评分不代替真实背景准确率。

**License decision**：PaddleOCR 与 SAM2 代码/官方模型逐项记录 Apache-2.0 依据；推理包本身的许可仍需归档。SDXL 沿用现有 catalog 的 CreativeML-OpenRAIL++-M、production 通道及权重哈希。ComfyUI 软件 GPL-3.0 与模型许可分别记录。远程分析和搜索条款使用版本化条款 ID，未核实不得将默认用户条件提升为 production；所有派生物的生产导出仍须人工审批。[SAM2 官方仓库](https://github.com/facebookresearch/sam2)、[ComfyUI 许可](https://raw.githubusercontent.com/Comfy-Org/ComfyUI/v0.34.2/LICENSE)。

**Alternatives considered**：不把包安装、文档核对、模拟合同或历史 Temporal 测试当 UI 真实验收。没有设计层面的待澄清项；模型哈希、真实资源峰值、提供方账户样本及质量数值属于各自阶段的验证门槛，不是可跳过的假设。纯本地任务只记录代码、命令与结果；发生外部调用才要求批准、受理次数、费用和运行证据，不给每项本地工作附加同一外部故障矩阵。

## D13：人工校正是独立于模型修订的版本层（2026-09-06）

**Decision**：原模型结果只读，ReviewLayout v2以基础类型＋可选标签表达人工布局；字段来源分别记录human/ocr/model，显式null/unknown不被猜测补齐。草稿保存与确认分开，确认后的manifest/hash成为下游绑定；锁定字段不接受模型自动覆盖。原自动评估、人工辅助结果和冻结真值分开。

**Rationale**：本仓库两次同图成功解析分别得到25/24元素，像素一致只证明切片正确，不能证明语义完整。UISelection原来解决GPU目标范围，RevisionRequest仅有四种模型/分割动作；它们不能承担人工自由编辑、草稿和长期版本历史。依据用户本轮要求及已保存验收证据制定本地设计，不新增模型能力或推断Live2D工程。

**Alternatives considered**：不以每次改框重新调用VLM；不把人工补字写成OCR观测；不将确认当GPU批准；不等整个013完成才接入基础版本层。

## D14：校正存储、前端与旧版本兼容

**Decision**：复用ArtifactStore和公共SQLite，T044先v3→v4加入ui_review_heads/ui_review_revisions/ui_review_requests，T020父子迁移顺延v4→v5。保存CAS＋request_id幂等，物化成功后才发布head；原PipelinePlan/旧VLMSchema/修订次数不变。原生HTML/CSS、ES modules及SVG覆盖层由Python 3.12受限回环服务提供；无CDN、云端、Node构建或新模型依赖。

**Rationale**：仓库当前只有Python运行时，没有既有前端工程；首版单图矩形编辑采用浏览器原生能力，避免为这次校正引入完整应用平台。缩放逆变换、草稿/确认、安全会话和版本冲突形成可测试边界；服务仅操作已授权task产物。完整会话/API约束见[review合同](contracts/review.md)。

**Alternatives considered**：不另建SQLite库/调度器，不无限延长Temporal Activity等待操作，不把review历史塞入只允许两次生成修订的字段。Vue等框架可在后续界面复杂度确有需要时评估，本阶段选择已明确，不要求实现者重新选型。

## D15：增量任务与现场验证

**Decision**：保留T001—T042编号，新T043—T050在T018后执行，T019及T035依赖T050；先合同/失败测试，后实现。T049复用已取得三张商业截图和解析产物完成真实浏览器验收，新增搜索/OCR/VLM/GPU为0。局部模型建议与真正GPU子绑定留T029/T030验证，不能提前勾选。

**Compatibility**：当前运行库仍v3，规划不迁移、不重启、不重放成功或unknown任务。本次只修改设计文档，环境命令使用conda run --no-capture-output -n letsaigc-core。用户授权增量改造覆盖重新建feature/重置plan的模板默认流程；当前无pwsh，以只读检查过的脚本做等价路径验证，最终只读Analyze一次。没有需要外部调用才能确定的设计选项。
