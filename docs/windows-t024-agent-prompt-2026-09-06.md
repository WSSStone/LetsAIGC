# Windows T024 交接提示词

在 Windows 的 Codex 中选择 **gpt-5.6-sol / xhigh**，打开 LetsAIGC 仓库，再粘贴下方提示词。提示词本身不会切换当前 Agent 的模型。

```text
接手 LetsAIGC 013-game-ui-analysis 的 T024 真实 SAM 验收。使用当前选择的 gpt-5.6-sol / xhigh 主代理；本次无需额外子代理。

仓库通常位于 C:\Programs\LetsAIGC。先检查 git status，保留本机未提交改动；fetch origin，确认 origin/dev-game-ui 包含基线提交 6186fe04327918163f6a294b633a3abadeb2445f（feat(ui): add human review and guarded editing foundations）。工作树干净时在 dev-game-ui 上使用 git pull --ff-only；如本机存在分歧，不 reset、不覆盖，先分析。可从已同步基线建立 codex/t024-windows 分支开展独立工作。Mac Agent 正在独立实施 T027，不要同时修改或部署 T027，也不要覆盖后续 Mac 提交。

阅读 AGENTS.md、docs/development-handoff-2026-09-05.md、specs/013-game-ui-analysis/{spec,plan,tasks,quickstart}.md、contracts/editing.md、configs/runtime/vision{,.lock}.yaml、vision-sam2-source-evidence.yaml 和 environment/vision-segmentation.yml，遵循仓库 speckit-implement 流程，不重建已经完成的规格。

基线已完成 T001—T023、T025—T026、T043—T050，33/50。Mac 最终回归565 passed、4 skipped，ruff通过；这些不是Windows运行证明。T024真实SAM未验收，T028编辑Temporal工作流尚未接线，现有编辑execute返回capability_not_ready。T024允许受限固定样本入口，不要为了现场测试提前实现整个T028或绕过具体批准。

所有Python及环境内命令使用 conda run --no-capture-output -n ENV ...，核心ENV为letsaigc-core。不要使用mamba或全局Python，不修改其他项目环境。先检查Windows GPU型号、NVIDIA驱动和CUDA可用性，并运行完整pytest、ruff及ui doctor。缺环境时按锁准备独立letsaigc-vision-segmentation；Python3.12、transformers4.57.6、torch2.9.1、torchvision0.24.1。当前SAM锁中的Torch/torchvision wheel是Linux版本，必须从官方来源核验适合Windows和驱动的构建、SHA及运行版本，并保留其他平台锁。CUDA wheel自带运行库，不要无依据安装系统CUDA Toolkit。

模型仅使用facebook/sam2.1-hiera-large官方safetensors，固定revision 665f8e2ad61cf5f53d65644ff27c8ee525124610，model.safetensors SHA256 dc407dce21301fd94abb395c5099b4f2c455fdc8a8f261ac3d0ea6d4cd197230。需要在同一revision核实config.json、preprocessor_config.json、processor_config.json的实际存在性、内容、哈希、许可证和1024x1024预处理映射；现有required_processor_files只是待验证锁，若官方固定版本与假设不一致，应查证并修复锁/loader及合同，不伪造文件。只由显式运维准备模型；运行时不得自动下载，不使用.pt/pickle回退，不自动接受受限许可证。将下载来源、哈希、包版本及加载检查记录在.local，runtime只加载本地已核验文件。

.env、.local、商业截图、人工确认版本、账本、批准、模型、Temporal CLI和运行证据均不在Git中。检查本机实际资源，不假设Mac task ID或批准可用，不输出密钥，不把完整.local直接覆盖到已有账本。需要现有商业截图时由用户安全迁移所需输入与来源证明；不能把Git中的旧合成图冒充商业UI/正式质量样本。可以先用明确标注为开发验证的固定样本准备受限入口，并如实说明验收范围。

先完成零推理规划：生成新的task/child、canonical图、具体selection版本及哈希、提示、model_snapshot、参数和资源边界。复用真实可信的PipelineService/ArtifactStore/账本批准与SAMPermit流程；不得在测试中手工签发通行证冒充人类批准，不得把替身模型、跳过用例或默认批准当作LIVE。如需要v5，优先使用明确独立的T024验收账本；公共账本迁移只能在确认其当前版本、停写、备份和验证后按项目流程进行，不静默升级或覆盖。

真实GPU运行前向用户展示每个task ID、完整64位plan fingerprint、输入/预览、每轮和总GPU/金额预算及合计上限；等待该具体计划的确切批准。代码开发、环境准备与这份交接不替代GPU批准。规划/校正/选择阶段禁止OCR、VLM、搜索或模型推理；现有parse/review输出能复用就不重跑。

获批后顺序执行受限固定样本验证，检查：真实SAM权重/版本、canonical坐标回映、原始矩形切片、轮廓mask、估计alpha、字形图及各自来源/低置信度；透明像素不被变成不透明，SAM控件轮廓不冒充字形；失败或partial保留真实证据与实际用量。验证具体selection/model/参数与批准完全一致、GPU单占用、卸载/释放确认、请求恢复不重复受理、时间和预算实际结算。保留原OCR/模型输出，人工修改不得标为independent_ground_truth。

新增 tests/integration/test_ui_segmentation_runtime.py 和 .local/validation/ui-analysis/segmentation-runtime.json 等真实证据；普通测试默认零模型调用，LIVE需显式开关与新批准。开发测量与最终正式质量阈值分开。未满足全部T024要求保持未勾选，准确列出缺项；不能因mock测试通过勾选。

运行必要定向检查，再完整 conda run --no-capture-output -n letsaigc-core pytest 和 ruff check .；由主代理审查真实证据后才更新T024任务状态与交接。历史失败、unknown 0.25USD与未完成LIVE保留；Mac上曾因旧测试留下4个未批准未执行计划，隔离已修复，不得清理历史记录掩盖问题。不要处理T028+，不要自动推送或合并Mac的并行工作；汇报实际差异、测试、现场证据与待处理项。
```
