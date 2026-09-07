# Windows T030 执行提示词

在 Windows Codex 中选择 **gpt-5.6-sol / xhigh**，打开 LetsAIGC 仓库，粘贴下方提示词。提示词不会自行切换模型。

```text
继续 LetsAIGC 的 013-game-ui-analysis，按仓库 speckit-implement 流程从 T030 真实编辑验收开始。本次主代理直接执行，无需额外子代理。

1. 同步与范围

仓库通常位于 C:/Programs/LetsAIGC。先 git status，保留本地改动，再 git fetch origin。确认 origin/dev-game-ui 包含实现提交 2a4907495efa63319f900531583e9b8e9dad98bf（T028/T029）和 Windows T024 提交 72bde6ab00224a806d5afbf24a12bf750d232b83。工作树干净且可快进时同步 dev-game-ui；有分歧先分析，不 reset、不覆盖。可从已同步状态建立 codex/t030-windows 分支。

阅读 AGENTS.md、docs/development-handoff-2026-09-05.md、specs/013-game-ui-analysis/{spec,plan,tasks,quickstart}.md、specs/013-game-ui-analysis/contracts/{editing,execution,vision,review}.md 和 docs/game-ui-analysis.md（花括号表示分别读取各文件）。保留已有规格、编号和历史证据。当前完成37/50，LIVE完成6/11。T024已完成，不重跑其验收脚本。T027是Mac CPU节点检查，不等于Windows Comfy CUDA就绪。T028/T029已经接线，Mac最终回归684 passed、5 skipped、ruff通过；这些替身测试不能替代T030。T030完成前不实施T031及以后，不勾选24例正式质量验收。

2. 核实本机资源

所有Python及环境命令使用 conda run --no-capture-output -n ENV ...，核心ENV为letsaigc-core，核心环境不装Torch。保留独立OCR、SAM、Comfy环境，不使用mamba或全局Python。先完整pytest、ruff及只读ui doctor，再核对GPU、驱动、固定模型/包版本和文件hash。复用Windows T024已验证的官方SAM safetensors/cu130资源，不能仅凭历史通过判断当前就绪。Comfy使用仓库固定v0.34.2、原生sdxl-inpaint recipe和catalog中核验的sdxl-base-1.0；核对Windows节点、进程、模型、许可及CUDA释放能力。缺资源先列准备方案，不自动下载、接受受限许可或降级。服务只监听127.0.0.1。

.env、.local、商业截图、确认review、模型、Temporal CLI、账本、批准和运行证据不随Git同步。Git中的hud-*是旧合成样本，不能冒充商业UI。先核实本机是否有已授权的Hades II、星穹铁道、Clash Royale截图及来源、原解析和确认review。缺少时列准确的安全迁移/新输入清单，不捏造任务、版本或证据，不把Mac .local覆盖到Windows账本。需要新解析时另行生成计划并取得确切模型预算批准。

检查主账本版本、未决费用及活动worker。Mac主账本仍v4；Windows版本未知，T024专用acceptance-state不是通用编辑账本。若需v4→v5，先准备停写、备份、校验和回滚方案，取得明确离线迁移授权后执行；已有v5不重复迁移。不得为重置费用/次数创建替代账本，保留本机原批准、unknown和失败记录。

3. 零推理规划与确切批准

优先复用确认review或成功自动解析，通过真实ui plan/execute与Temporal工作流验收，不以直接模型调用或手写批准记录绕过产品路径。准备覆盖decompose、scene_background、map_surface的最小案例组合，包含默认推荐直接批准、候选/高级覆盖，以及一次已确认review的局部OCR或VLM复核。需要局部修订的根计划在创建时带--allow-local-revision，不能改旧根请求。

规划、review保存/确认、选择和预览阶段不得调用OCR/VLM/搜索/GPU。先展示实际输入/预览及预算草案；逐项列根/child task ID、完整64位plan fingerprint、选择/review版本、每轮金额/GPU分钟、根总上限及所有根合计上限。获得针对具体计划的明确批准后才执行。本提示词和“继续实施”均不是模型/GPU批准。

SAM和补图顺序执行、分别批准。SAM完成并证明释放后，展示实际最终image/mask、模型、recipe和参数所绑定的补图child完整指纹，再取得新批准。根指纹不能授权分割，分割指纹不能授权补图；预算不足保留成果，不自动扩大。示例预算GPU为0，真实执行要单独准备正数预算并批准。

主要命令模板，所有ID/文件都须来自当前真实输出：
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui doctor
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --reviewed-task REVIEW_TASK_ID --mode decompose --budget BUDGET_FILE --allow-local-revision
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui plan --reviewed-task REVIEW_TASK_ID --mode reconstruct --target scene_background --budget BUDGET_FILE --allow-local-revision
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui inspect ROOT_TASK_ID --local
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui execute CHILD_ID --approve FULL_FINGERPRINT
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui revise BASE_ID --action review_region --target-id ELEMENT_ID
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui revision-accept ORIGINAL_REVIEW_TASK_ID --suggestion SUGGESTION_ID

map_surface替换目标参数；reread_text使用稳定文字ID。adjust_segmentation/regenerate参数文件格式见操作指南。选择/批准/启动/恢复均沿当前真实根任务，不复用历史task ID或指纹。

4. 真实验收与恢复

检查真实分割、矩形切片、轮廓mask、估计alpha、可选glyph及来源，确认未变化元素资产复用。两种补图分别检查最终mask、canonical逆变换、无损PNG和保留区域；区外像素差必须为0。区分原始、估计、generated，记录实际视觉质量问题；无独立背景真值不能报告背景恢复准确率。

SC-017：在含锁定字段的已确认review上显式局部重读/复核。真实模型输出先为建议，未采纳时review头/确认版本/锁定字段不变。需要采纳时由用户明确选择，验证只创建原review任务的新草稿，锁定/过期建议拒绝，确认仍独立。原OCR/VLM输出不可覆盖，human_assisted不是独立真值。

在一例GPU已经持久化受理后，记录本次worker PID及受理证据，中断该worker并重启，保留provider运行。验证同一operation/provider request ID只恢复观察与收集、不重复受理。保存Temporal历史、provider回执、账本操作和释放证明；unknown保留费用及资源归属，不重提交。不得杀用户其他服务，也不重复制造收费失败。

覆盖至少一条有限修订，先展示新具体计划并批准，验证费用、OCR/VLM次数和GPU修订沿原根/编辑链累计，旧成功/失败版本保留。SAM/Comfy先确认释放再交接；HTTP200不是CUDA释放证明。

5. 证据与交付

新增/补齐 tests/integration/test_ui_reconstruction_runtime.py，对现场证据只读复核；普通pytest不得启动模型、签发批准或运行GPU。依据实际产物形成 .local/validation/ui-analysis/t030-windows/editing-runtime.json 和相关manifest，记录输入/代码/模型hash、来源、具体批准、实际费用、受理次数、恢复、释放和质量测量。T030证据格式/校验器尚需实际实现，不假设已有脚本能跑完全部验收。原始失败证据保留；模型、密钥、账本和本地证据不加入Git，代码/文档记录摘要与复核方法。

只修复T030现场阻塞问题，按影响运行定向检查后再完整pytest和ruff。满足全部T030要求后才勾选；缺输入、迁移授权、资源、确切批准或真实恢复/质量证据时保持未完成并列缺项。不要用跳过、替身或历史T024代替T030。

最后清理本次创建且不再使用的worker/服务/浏览器，保留用户已有进程及持久化证据。汇报分支/提交、变更、测试、真实调用及费用、各验收项和缺项，供Mac Agent审查集成；本提示词不授权自行合并或推送其他分支。
```
