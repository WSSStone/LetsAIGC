# 013 视觉分析、分割和补图契约

状态：待实现；本地部署和真实模型推理仍需后续验收。模型选择及官方来源见 [research.md](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/research.md)。

首版仅要求真实 CPU OCR 与 VLM：按 T006—T010 实现并检查三个开发样本。SAM、GPU 凭证/资源交接及 Comfy 蒙版属于 T019—T030；完整质量阈值属于 T035—T038，不阻塞首版能力就绪。

## 1. 服务和加载边界

OCR 服务使用独立 `letsaigc-vision-ocr` 环境、CPU，监听 `127.0.0.1:8766`；SAM 服务使用独立 `letsaigc-vision-segmentation` 环境，首选 WSL2 Ubuntu，监听该环境的 `127.0.0.1:8767`，Windows 通过已验证的 localhost 转发访问。连接失败时保持未就绪，不改成公网/0.0.0.0。

服务实现使用标准库 HTTP server 加受限任务队列及 Pydantic 请求校验；不引入额外 Web 应用。单个服务最多执行一个推理作业，返回稳定 request_id。核心只持有轻量 httpx 客户端，不导入 Paddle/Torch/Transformers。

| 方法/路径 | 输入 | 输出与限制 |
|---|---|---|
| GET /v1/health | 本地认证 | 服务版本、已加载模型/哈希、设备、能力、协议版本及就绪原因；不自动加载/下载 |
| POST /v1/jobs | operation_id、capability、input refs、锁定模型、允许参数；执行凭证置于认证头，不入历史 DTO | 接受后返回request_id；同operation同payload复用，冲突拒绝 |
| GET /v1/operations/{operation_id} | 本地认证、已授权 task/operation 作用域 | 只读查询既有受理记录，返回 request_id、payload_hash、作业状态；缺记录为 not_found，不创建作业 |
| GET /v1/jobs/{id} | 已授权作业身份 | pending/running/succeeded/failed/unknown、资源使用及结果描述 |
| GET /v1/jobs/{id}/outputs/{output_id} | 受限输出ID | 有上限的PNG/JSON数据流和hash；无路径参数 |
| POST /v1/jobs/{id}/cancel | 同作业身份 | cancel_requested或已确认terminal，响应成功不等于推理已停止 |
| POST /v1/models/release | 固定模型ID、无活动作业证明 | 卸载本服务模型并报告资源；不终止其他进程 |

认证材料来自本地受限配置/环境，不能进入DTO、日志或模型正文。执行凭证由可信Windows协调端在公共账本校验批准和资源归属后签发，绑定operation、输入hash、模型、能力、参数hash、资源上限及有效期；服务不能根据Agent自报budget运行。凭证过期只拒绝新提交，不证明已运行作业停止。

WSL端只读受控映射的素材根目录，并校验引用/task/hash；Windows绝对路径不进入请求。服务只写自身受限作业目录，Windows通过有界结果端点收集并发布到公共ArtifactStore。服务的作业回执是提供方侧幂等记录，不另建预算或批准账本，也不从WSL写Windows公共SQLite。

operation_id 是协调端提交前已持久化的操作身份；request_id 是服务受理后生成的独立作业身份，二者不隐式等同。服务在任何推理/返回成功前原子持久化 operation_id→request_id、task_id、payload_hash 及 accepted 记录。相同 operation/payload 的 POST 可返回既有回执，但客户端不得以重复 POST 代替恢复查询。

若响应丢失，客户端只调用 GET /v1/operations/{operation_id}，找到记录后沿原 request_id 查询/收集。查询鉴权不依赖已过期的提交凭证；使用当前本地认证并验证原 task/scope，不能读取别的任务。not_found/404、存储不可用或进程重启后的记录缺失都不是“未执行”证明：协调端一旦越过 submitting，保持 outcome_unknown、费用与 GPU 归属，不重新 POST 或启动替代作业。作业记录存在但状态不可确认时返回 unknown。只有协调端能证明尚未跨提交边界的操作才允许按既有规则释放预留。

操作映射和未决回执属于恢复证据，不当作可删除缓存。纯 CPU 已确认可安全重算的结果遵循受控恢复规则，GPU 作业不自动再推理。

## 2. 模型锁和安全格式

`vision.lock.yaml` 记录服务协议、Python/包及wheel哈希、模型官方来源/固定revision/文件hash/许可通道、设备、预处理、输出模式与实际加载器版本。下载和许可确认只由操作者通过受控准备完成。

- OCR：PaddleOCR3.4.0 + PaddlePaddle3.2.2 CPU，PP-OCRv5_server_det/rec静态推理包，显式目录；关闭文档方向分类、去畸变和文字行方向自动变换，若后续启用必须记录相应矩阵与模型。
- 分割：Transformers4.57.6 + PyTorch2.9.1/torchvision0.24.1，SAM2.1 hiera-large官方safetensors固定revision；1024模型视图，记录resize/pad及逆变换。
- 不接受pickle checkpoint、torch.load的.pt回退、Paddle动态图checkpoint或动态远端代码。运行时强制本地模式；缺文件或hash不符返回model_not_ready。
- OCR 的锁结构、协议夹具与部署随 T006 落地，T010 真实验证；SAM 随 T022—T024 落地。未部署文件或未实测组合保持 pending_verification，模型哈希为空永远不能执行。单个能力的 ready 只表示其加载/推理及许可条件就绪，不表示24例正式质量验收通过，也不等待其他能力。

## 3. OCR 合同

请求：task/operation、canonical/view引用、语言、固定det/rec模型、视图transform_ref、可选已有text_id。输出内容包含原始rec_texts/rec_scores/dt_polys（具体3.4.0字段通过合同夹具确认并规范化）、识别模型版本、view_id及空/低确定性状态。

适配器统一转为TextRegion，所有polygon逆变换到canonical。先用确定性规则去掉重叠块上的相同文字重复结果，保留合并来源；不能丢掉同位置不同文本的冲突。局部重读最多两次，保留原识别和建议版本。

VLM不能覆盖原始OCR。识别分数只表示该模型输出，未校准前不称准确率；没有文字时输出空集合及状态，不制造文本。

## 4. VLM 与布局融合

复用当前Agent的模型连接、配置优先级、模型路由和usage适配；新增独立UI分析结构，不把布局塞进生成意图模型。输入只含受控视图、OCR内容和用户说明各自的资料角色。

严格输出字段：observations、hypotheses、elements、text_links、occlusions、correction_suggestions、revision_proposals。额外字段、非法引用、无依据的确定性断言及未知动作拒绝。UI不能依据自然语言指令动态添加工具。

全图及必要局部视图合计按每图最多4次VLM调用控制，候选筛选/复核及编辑阶段区域提案也占用该图计数；每次调用前预留cost和次数，失败用量也记录。纯查询规划每搜索根任务最多一次，输出至多三个有限查询，并单独计数。

布局融合先校验几何，再用OCR真实文本关联VLM观察；默认以至少80%文字区域覆盖关联至最小包含控件，歧义保留unassigned或hypothesis。同kind且IoU≥0.85的重复元素候选可合并，保留映射。首轮稳定ID匹配采用同源/同kind、IoU≥0.5的确定性排序匹配，歧义创建新ID及替代关系；这些规则作为版本化策略，不是模型准确率阈值。

正文、建议和假设保存为受限素材；活动历史只返回ref。原始OCR、用户声明、供应商说明和模型推测分开，图中提示注入不增加权限。

## 5. SAM、alpha 与字形

编辑阶段默认由 Agent 依据真实布局与用户目标生成区域、保留/移除对象和预览；可信层校验并冻结 UISelection，推荐明确时直接形成具体待批准子计划。含混时提供候选编号，高级文件只是覆盖入口。自动提案不构成人工批准。

分割请求先验证具体 UISelection，固定实际canonical、box/point、模型快照、目标元素集合及 selection_ref/hash 后等待精确GPU批准。每图最多64个元素提示，逐项推理并输出二值/软轮廓mask和来源。恢复复用已登记mask，不能把剩余元素默认为已完成。

SAM输出不等于原始透明图层。估计alpha和可见像素裁图分别保存；遮挡和半透明部分明确estimated/unknown。可选字形提取在OCR区域和分割结果上做确定性颜色/连通域细化，包含描边/阴影不确定性；精度不足时报告unavailable/low_confidence，不拿OCR矩形冒充字形。

## 6. 原生 ComfyUI 蒙版契约

新增四份同步工件：`configs/workflows/recipes/sdxl-inpaint.yaml`、`workflows/ui/sdxl-inpaint.json`、`workflows/api/sdxl-inpaint.json`、`workflows/contracts/sdxl-inpaint.yaml`。本文件中的路径均相对仓库根 `C:\Programs\LetsAIGC`，是后续实现目标。

模型固定现有catalog的sdxl-base-1.0及其许可/hash。recipe保持image_to_image意图，但用显式mask支持字段和`MaskedGenerationPlan` v2分派到inpaint编译路径；禁止先走普通I2I改图逻辑。

原生节点最小集合：CheckpointLoaderSimple、LoadImage、ImageToMask、VAEEncodeForInpaint、CLIPTextEncode、KSampler、VAEDecode、SaveImage。白名单仅增加所需原生节点，按v0.34.2本机object_info验证类型、参数和来源模块。

mask作为独立灰度PNG读取，经ImageToMask明确选red通道，不能误用LoadImage的反向透明度MASK输出。预先完成扩张/羽化，再将非零范围裁到已选目标区域并排除已冻结保留区域，`grow_mask_by=0`；图像和mask在同一编辑视图、同一pad/resize规则下输入。recipe声明这些固定参数，Agent不能改变。

生成参数：单输出，工作视图每边不超过1024并补齐到8倍数，steps1—60、cfg1—20、denoise0.05—1.0；实际请求可收紧。可调字段只限prompt、negative_prompt、seed、steps、cfg、denoise，必须同时满足recipe、子执行envelope和原批准的上限。

由于公共ApprovalEnvelope v1只允许prompt/negative_prompt/seed，本轮UI自动修订实际仅开放这三个字段；steps/cfg/denoise固定于子计划，改变它们需要新计划及批准。不得通过recipe有可调字段就绕过公共envelope。

编译发生在具体批准后的执行准备阶段，冻结实际graph/contract/hash并写入子运行manifest。只编译不是新增公共CLI。生成输出先检查尺寸、hash与对应请求，再逆变换到canonical并使用已批准全分辨率mask进行CPU合成；mask==0处直接复制原像素，输出无损PNG。

## 7. 验收

公共执行层集中验证批准拒绝、同operation不重复受理、unknown、取消及预算结算。视觉适配器验证参数/输出及调用公共机制；GPU阶段补原request恢复和资源释放，业务链路以产物正确为主，不复制公共故障矩阵。最终只补证据缺口和受版本变化影响的检查。

U-V01：全部EXIF方向、奇数尺寸、边界/分块回映射。U-V02：真实OCR和低确定性原文保留。U-V03：严格VLM和注入拒绝。U-V04：融合/稳定ID/无悬空引用。U-V05：真实分割、估计alpha与字形角色。U-V06：四份工作流工件与旧T2I/I2I兼容。U-V07：真实补图、mask极性/变换及编辑区外差为零。U-V08：任何实际输入/选择/模型/mask改变都不能越过原批准。U-V09：模拟服务已受理而响应丢失，按operation只读找回原作业；not_found及重启缺记录保持unknown，受理次数不增加。
