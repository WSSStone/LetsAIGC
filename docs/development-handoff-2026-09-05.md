# 开发交接：013 游戏 UI 分析

> macOS arm64 最新状态（2026-09-06）：T001—T018解析预览及T043—T050人工校正预览已完成，当前账本v4并有v3备份。三例本地浏览器校正、84个原图像素一致切片、CAS冲突和确认进程中断恢复通过；新增搜索/推理/GPU调用0。旧成功/失败费用与0.25USD unsettled保留。下一实施入口是T019，GPU、局部模型和正式质量仍待完成。验证结果见文末及[任务记录](../specs/013-game-ui-analysis/tasks.md)。

本交接用于在另一台机器继续 `dev-game-ui` 分支。当前分支已经合并
`codex/game-ui-analysis-workflow` 和 `codex/temporal-runtime-integration`；前者遗留的未跟踪原型也已按哈希归档到
[worktree-prototype](../specs/013-game-ui-analysis/archive/worktree-prototype/README.md)。归档文件不是运行时代码，后续 T019+ 只能在与现行 Schema 对齐后参考。

## Windows 迁移时进度（历史快照）

- 013 的 spec、plan、tasks、合同、数据模型和 Quickstart 已完成以首个可用产品为中心的修订。
- T001—T009、T011—T016 的首版代码已经实现：手动输入、SerpApi/Tavily 适配器与额度路由、账本迁移、Temporal UI Workflow、CPU OCR、VLM 分析、布局、矩形切片、标注图、manifest 和 CLI。
- T010 尚未验收。三个真实开发样本都曾运行到 OCR；发现并修复了 NumPy 整数几何溢出以及嵌套 NumPy 数组无法 JSON 序列化的问题。修复后的真实 OCR/VLM 完整链路尚未再次执行。
- 竖屏样本完成过一次“OCR 请求登记后停止 worker、重启后复用原请求”的恢复演练；该次任务随后因上述序列化问题明确失败。三次尝试的实际费用均为 0，未调用 VLM。
- T017/T018 尚未开始真实搜索验收。`configs/providers/image-search.yaml` 中账户实际计价、探测计价和条款必须先核实；不得用真实账户故意耗尽额度。
- T019+ 的 SAM、GPU 补图、父子预算、批次和完整 24 例质量验收仍属后续阶段。
- 迁移前完整检查：`251 passed, 11 skipped`，`ruff check .` 通过。跳过项为需现场 Temporal CLI 或具体 GPU 计划与批准的测试。

任务真值以 [tasks.md](../specs/013-game-ui-analysis/tasks.md) 为准，使用方法见
[游戏 UI 开发指南](game-ui-analysis.md)和 [Quickstart](../specs/013-game-ui-analysis/quickstart.md)。

## Git 不会迁移的本机状态

`.env`、`.local/`、模型权重、Temporal 可执行文件、账本、批准回执、输入副本和运行证据都不会随 Git 推送。不要把密钥加入仓库。换机后应安全迁移必要密钥，或重新创建本地状态：

- 从 `.env.example` 配置 LLM、搜索及本地服务变量。为 `LETSAIGC_VISION_TOKEN` 和至少 32 字符的 `LETSAIGC_VISION_SIGNING_KEY` 生成新的随机值；OCR 服务和 worker 必须使用相同值。
- 用 `environment/vision-ocr.yml` 创建独立 OCR 环境，并按 `configs/runtime/vision.lock.yaml` 准备官方静态模型文件、许可证据及精确哈希。运行时不会自动下载模型。
- 按 `configs/runtime/temporal.lock.yaml` 取得并校验 Temporal CLI；服务、数据库和 worker 只监听回环地址。
- 旧机器 `.local` 中的 task ID 和批准指纹不能在新机器直接恢复。重新导入固定样本、重新规划，并再次展示新 task ID、指纹及预算以取得精确批准。

## 新机器启动顺序

```powershell
git clone <origin-url>
Set-Location <repo-path>
git checkout dev-game-ui

conda env create -f environment/core.yml
conda env create -f environment/vision-ocr.yml
conda run --no-capture-output -n letsaigc-core python -m pip install -e ".[temporal,tracking,dev]"

conda run --no-capture-output -n letsaigc-core pytest -q -p no:cacheprovider --tb=short
conda run --no-capture-output -n letsaigc-core ruff check .
```

环境已存在时用 `conda env update --name <name> --file <file> --prune`，不要安装到全局 Python。随后按 Temporal 与游戏 UI 指南准备本地运行文件、启动服务并执行：

```powershell
$env:SPECIFY_FEATURE = '013-game-ui-analysis'
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui doctor
```

只有 `manual`、`ocr`、`vlm` 和 `temporal` 均 ready 才继续 T010。先 import/plan，确认规划阶段外部调用为 0，再把三个新计划的完整指纹和每轮/总预算交给用户批准。批准后顺序执行，避免单活跃 OCR 服务竞争；其中一例在账本登记 OCR provider request ID 后中断 worker，重启并验证同一操作没有重复受理。不要把旧失败任务、历史测试或本地模拟当作修复后的现场验收。

## macOS arm64 环境补充

当前T010只需远端VLM与Paddle CPU OCR，不需要Torch，也不复用`pytorch-m4`。SAM/Torch仍留待后续分割阶段。核心环境和OCR环境使用原有两个名称，按上文环境文件重新创建；本次核心环境另安装了字幕功能所需的FFmpeg：

```sh
conda install -n letsaigc-core -c conda-forge ffmpeg
conda run --no-capture-output -n letsaigc-core python -c 'import shutil; print(shutil.which("ffmpeg"))'
```

应定位到核心环境的`bin/ffmpeg`。本机Codex工具使用非登录shell执行环境命令，避免登录shell将无字幕滤镜的Homebrew FFmpeg排到前面；不需要改用户的shell初始化文件。

本机已准备并核验[官方Temporal1.8.3](https://github.com/temporalio/cli/releases/tag/v1.8.3)的darwin_arm64包，保存在`.local/runtime/temporal/`，不要执行Windows的`.exe`。下载包SHA-256为`77c5bef1753ddfcdcaced2a2d44207aeced1c776e7bcbf94520c7911bd0c4080`，解出的可执行文件SHA-256为`b5855c2f9fa9f44c9ab6a84cf41f0278c58f90bd781e1467736ce3d197697f37`。只在服务尚未运行时启动：

```sh
conda run --no-capture-output -n letsaigc-core python -m letsaigc runtime dev-server --binary .local/runtime/temporal/temporal
```

当前`.env`已在本机配置，勿覆盖或输出内容。Paddle3.2.2的[官方macOS CPython3.12 arm64 wheel](https://pypi.org/project/paddlepaddle/3.2.2/)已核验，SHA-256为`c62dab0d4df6de2978960832f0015a145c23cd379418d932ddd8f546629d1fe4`；其余软件包和完整本机锁记录位于`.local/validation/ui-analysis/migration-2026-09-05/`。

2026-09-06用户已授权模型准备，两份官方归档、六个静态文件与许可证据均通过原锁SHA-256核验。已加入`configs/runtime/vision.macos-arm64.lock.yaml`，由`vision.yaml`的`platform_locks`为Darwin-arm64选择；规划器、Worker、OCR服务共用`vision.settings.load_ocr_settings`。Windows仍选原锁，缺失已选平台锁时拒绝回退。模型加载和doctor已就绪，不表示样本推理验收。

OCR服务按本页设置PYTHONPATH后在`letsaigc-vision-ocr`运行；本机Temporal与Worker已启动，重复启动前先检查端口和队列。已完成的商业截图计划、批准、执行历史及产物位于`.local/validation/ui-analysis/t010-commercial-2026-09-06/`；旧合成样本计划保留且未执行。T010/T017/T018已勾选，当前下一阶段是已规划的T043—T050人工校正；不要重新执行已完成任务或复用消费过的批准。

已有现场证据可用`LETSAIGC_UI_LIVE_EVIDENCE`指定上述目录，并运行`conda run --no-capture-output -n letsaigc-core pytest --ui-live`复核，不会新增推理。换机后先安全恢复本地证据并验证；无法恢复时才重新规划和批准，Git中的成功记录不代表新机运行资源仍在。

## 给新 Agent 的启动提示词

```text
继续 LetsAIGC 的 013-game-ui-analysis 实施。当前仓库应位于 dev-game-ui 分支；先阅读 AGENTS.md、docs/development-handoff-2026-09-05.md、specs/013-game-ui-analysis/spec.md、plan.md、tasks.md 和 quickstart.md，再检查 git status 与最新提交，不要重建或改写已经完成的规格。

所有环境命令使用 conda run --no-capture-output -n ...，不要使用 mamba或全局 Python。先运行完整 pytest 与 ruff，并通过 ui doctor 核对 manual、OCR、VLM、Temporal。`.env` 和 `.local` 不在 Git 中；不得输出密钥，也不得假设旧 task ID、批准指纹、模型文件、Temporal CLI 或运行证据仍存在。

本机T010已完成，SerpApi真实搜索来源图片的解析也已在 ui-547a475a116747b9bcd73882327910e0 成功。先读取 `.local/validation/ui-analysis/t017-2026-09-06/next-action.json` 和 `serpapi-evidence-enum-parse/acceptance.json`；不要重复成功任务。旧 ui-83a66862f444466ca0129be67ba13988 的0.25USD unsettled仍需提供方证据，禁止重发或无证据清账。Tavily已完成用户授权的10次GET /usage实验，用户确认额度0/1000且费用不变；仅作为本账户下一次有界验收的零费用依据。Tavily任务ui-23925829f177462b82a1d38d39c796de已获批并成功，实际1credit、0.005586USD，24切片/18文字。本机T017/T018已完成，读取preview-acceptance.json及tavily-ui-screen/acceptance.json，不得重跑实验或成功任务。人工校正T043—T050已规划且应先于T019，读取contracts/review.md及最新tasks依赖；新功能仍待实施授权。如换机且本地证据无法安全恢复，再重新规划和批准相应现场验证。

T010 完成后才处理 T017/T018。真实搜索前必须核实 SerpApi/Tavily 当前账户计价、探测计价、条款和凭据；用固定额度响应验证切换，不故意消耗真实额度。不要提前实现 T019+。保留历史失败证据和所有未完成 [LIVE] 状态。下一次获得实施指令后从T043继续speckit-implement，先读人工校正合同；不要按旧编号直接做T019。T044才允许按停写/备份流程升级v3→v4，T020父子预算随后v4→v5。T049仅复用本地已验证素材，不重复付费推理。
```


## 人工校正实施交接（2026-09-06）

本段覆盖上文“从T043继续/仅规划”的旧提示；保留其迁移历史，不重跑既有任务。运行 `conda run --no-capture-output -n letsaigc-core python -m letsaigc ui review TASK_ID` 即可编辑成功解析。现行公共账本v4，迁移备份路径见 `.local/validation/ui-analysis/review-2026-09-06/migration.json`。迁移前后旧表哈希完全一致，Temporal worker已恢复。

T049复用三个原任务；确认版本与产物哈希、原OCR不变/切片逐像素核验、浏览器截图和中断请求见 `.local/validation/ui-analysis/review-acceptance.json`。确认发布前实际终止进程，旧head保持，重启服务自动恢复 `live-interrupted-confirm`，未重复发布。保存/确认均不创建付费操作。测试中的人工修改属于工作流验收，未登记为ground_truth。

先读当前tasks、contracts/review.md和使用指南再继续T019；不得误用ReviewVersion作为旧UIStepBinding/GPU修订号。T020承接v4→v5，T021才接入reviewed-task规划。真实GPU/付费局部模型仍需新计划及确切指纹/预算批准，旧unknown记录禁止重发或无证据清账。所有新证据、截图和账本备份仍仅在.local；Git不携带它们。

人工校正最终完整回归：340 passed、3 skipped（GPU1、Windows2），59.78秒；ruff及diff检查通过。临时验收页面和服务已关闭，按上述命令即可开启新会话。

## T019 先行编辑合同交付（2026-09-06）

T019 已完成：选择文件/候选、MaskedGenerationPlan v2 的开发期 JSON Schema 与先行测试见 `specs/013-game-ui-analysis/contracts/editing.md`。旧 GenerationPlan v1 的规范化字节与指纹保持；确认 review 版本与原自动布局保留独立来源。

完整验证：407 passed、3 skipped、20 xfailed，ruff 与 diff 检查通过。20 个预期失败属于尚未实现的 T020/T021 接口，启用 `--runxfail` 后确认为 5 failed、15 errors；不算 GPU 验收。日志在 `.local/validation/ui-analysis/t019-2026-09-06/`。T020 起应逐项实现并消除这些门禁，T028 再完成 Workflow 接线。

兼容修复：旧分析/搜索代码仅允许账本 v2/v3，review 升级到 v4 后新任务会被拒绝，额度也会漏结算。已扩展已知支持版本并复用 T003/搜索矩阵验证 v4；未增加 v5 支持或迁移。真实账本仍为 v4；历史 unknown 的 0.25 美元预留保留。此轮没有新增 provider、GPU 或付费调用，没有打开新的内置浏览器。所有未完成 LIVE 项及旧失败证据保留。

## Luna xhigh 并行实施与 T020 验收（2026-09-06）

用户授权采用 `gpt-5.6-luna` + `xhigh` 子代理实现，主代理负责拆分、独占文件分配、关键合同审查及最终验收。本轮三路分别完成DTO、父子账本/服务、T035现有数据准备审计；修复复核发现的旧步骤hash、padding、冻结输入、跨purpose旧选择、根/子实费、失败修订与根共享次数边界。

T020 已勾选；完整回归 **473 passed、3 skipped、3 xfailed**（仅T021尚未注册的CLI选择入口），ruff/diff通过。日志：`.local/validation/ui-analysis/t020-2026-09-06/pytest-final.log`。v5迁移代码可用但真实账本保持v4；63条历史绑定hash一致，unknown 0.25美元保留，无新provider/GPU调用。下一波可并行T021/T022/T025，公共文件和真实外部验证仍串行，具体GPU计划仍需新指纹批准。

T035 保持未完成：`tests/fixtures/ui_analysis/cases.yaml` 与 `annotations.json` 只登记6例现有素材及 automatic/human_assisted/independent_ground_truth 来源；缺18例、独立评估8例及已知背景8例，尚无完整独立冻结标注。本地审计 `.local/validation/ui-analysis/t035-readiness-2026-09-06.json`。这些计数仅反映准备程度，不代表现有6例已获正式质量资格；已知背景加合成HUD仍是PER-008允许的正式样本类型，须独立标注并按底图分组。


## T021—T023、T025—T026 并行实施验收（2026-09-06）

本次沿用用户选择的 `gpt-5.6-luna` / `xhigh`，三路分别负责选择与字形、SAM 服务与分割、蒙版编译与合成；主代理完成来源、像素、费用、CLI 和跨模块独立审查及修复。五项已勾选，累计33/50；T024、T027和其余未完成LIVE保持未验收。

- T021：reviewed-task 冻结确认版本，automatic-task 显式复用原布局；候选预览、选择、检查均不重跑模型。可信入口核验原图与EXIF/ICC规范化图的实际像素关系，不以二者SHA相同作为条件。当前编辑执行仍返回 capability_not_ready，T028才接入具体子计划批准。
- T022/T023：固定本地safetensors及伴随文件校验、服务执行凭证和单GPU占用边界；SAM提示、canonical轮廓/估计alpha、原始切片与字形资产。保留原透明像素；文字细化不把控件轮廓直接冒充字形；部分失败保留成功产物和unknown质量。质量不确定不抹除实际运行时间。替身与CPU合同通过不代表真实SAM验收。
- T025/T026：原生sdxl-inpaint四工件、MaskedGenerationPlan v2编译、配对变换/极性、最终mask裁剪与keep保护、空mask零生成、回映及区外逐像素不变；原始生成候选先保存证据再校验，旧v1编译金样保持。

完整集成回归：**565 passed、4 skipped、2 warnings，63.54秒**；ruff及diff检查通过。跳过为既有真实GPU测试1项、专用object_info探测1项、Windows共享冲突2项；警告为Pillow测试API弃用。此前T019/T020的预期失败均已消除。本地汇总与日志在 `.local/validation/ui-analysis/delegated-editing-2026-09-06/acceptance.json`、`pytest-final.log` 和 `ruff-final.log`。回归复核已保存的现场证据，新增provider/GPU调用均0。

T024只读准备检查显示本机Darwin arm64缺SAM模型目录，锁定CUDA环境与伴随文件哈希尚未验证；记录 `t024-readiness.json`。T027专用探测因缺 `.local/runtime/ComfyUI` 锁定运行目录，在发送GET前退出，证据 `.local/validation/ui-analysis/inpaint-object-info.json`；不得据此断言已有其他服务端口不可用。真实SAM需完整锁定资源及新计划指纹/预算批准；ComfyUI须先有匹配锁的本地部署，再只读核验object_info，不提交生成。T028尚未接线；T024明确允许缺能力时保留LIVE未验收并继续后续接线，T027仍是实际补图能力门槛，不能用本轮离线通过替代它们。

账本审计：早期旧CLI测试未隔离本地存储，留下4个未批准、未执行的测试计划（tasks 12→16）。已修复为临时账本测试，保留这些记录及旧失败日志；其余所有表的行数与SHA均与本轮前一致。最终完整回归前后所有表SHA一致。真实账本仍v4，approvals 9、operations 63、ui_step_bindings 63，历史unknown 0.25USD保留。本轮没有迁移、模型下载或Git提交，也未打开新的内置浏览器。

继续时以当前 tasks.md 为准，不重复上文已完成阶段；先处理真实运行资源准备与T028接线的依赖，再推进剩余任务。核心选择/人工校正不需要Torch；SAM仍是独立环境，不能把现有Mac环境复制后直接视为CUDA验收通过。


## T027 macOS 只读现场验收（2026-09-06）

用户明确指定Luna xhigh子代理部署、主代理验收。锁定ComfyUI v0.34.2 / 169fcf35a2fc163fec31338b816503ddac0d3fcf，干净源码checkout位于.local/runtime/ComfyUI，独立letsaigc-comfy环境Python3.12.14、torch2.9.1、torchvision0.24.1、torchaudio2.9.1。Darwin-arm64使用新增平台锁，CPU启动并禁用第三方/API节点；原平台CUDA锁保留。没有模型下载或生成请求。

真实GET /object_info返回639个节点，8种所需节点均为官方内置；ImageToMask位于comfy_extras.nodes_mask，红通道单选及grow_mask_by显式0受支持。验收核对实际源码提交/干净状态、监听PID与同一进程的环境和启动参数；主代理独立核验10节点13连接、旧编译金样和像素保护合同。修复旧探测的模块误判、COMBO格式假设与跨PID拼接证明风险。

主代理完整回归：**574 passed、3 skipped、2 warnings，68.62秒**，含真实T027只读探测；ruff/diff通过。跳过仅既有真实GPU1项、Windows共享冲突2项。现场证据inpaint-object-info-2.json及后续编号保留在.local/validation/ui-analysis/；详细日志、主代理独立检查和账本对比在t027-macos-2026-09-06/。包版本清单SHA不等于全体wheel逐一验证；本轮结论仅为固定运行实例的节点能力，不是MPS/CUDA生成、SDXL模型或最终图像质量通过。

T027已勾选，累计34/50。T024交Windows Sol xhigh执行，提示词见docs/windows-t024-agent-prompt-2026-09-06.md；T028及其余未完成LIVE不变。真实账本保持v4且本轮前后所有表SHA一致，历史unknown0.25USD和4个旧未执行测试计划保留。验收临时CPU服务收尾停止，环境与源码保留，可按docs/game-ui-analysis.md重新启动；本轮没有打开内置浏览器。
