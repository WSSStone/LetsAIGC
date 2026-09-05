# 开发交接：013 游戏 UI 分析

本交接用于在另一台机器继续 `dev-game-ui` 分支。当前分支已经合并
`codex/game-ui-analysis-workflow` 和 `codex/temporal-runtime-integration`；前者遗留的未跟踪原型也已按哈希归档到
[worktree-prototype](../specs/013-game-ui-analysis/archive/worktree-prototype/README.md)。归档文件不是运行时代码，后续 T019+ 只能在与现行 Schema 对齐后参考。

## 当前进度

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

## 给新 Agent 的启动提示词

```text
继续 LetsAIGC 的 013-game-ui-analysis 实施。当前仓库应位于 dev-game-ui 分支；先阅读 AGENTS.md、docs/development-handoff-2026-09-05.md、specs/013-game-ui-analysis/spec.md、plan.md、tasks.md 和 quickstart.md，再检查 git status 与最新提交，不要重建或改写已经完成的规格。

所有环境命令使用 conda run --no-capture-output -n ...，不要使用 mamba或全局 Python。先运行完整 pytest 与 ruff，并通过 ui doctor 核对 manual、OCR、VLM、Temporal。`.env` 和 `.local` 不在 Git 中；不得输出密钥，也不得假设旧 task ID、批准指纹、模型文件、Temporal CLI 或运行证据仍存在。

从 T010 继续。重新导入 tests/fixtures/ui_analysis 的英文横屏、中文密集 UI 和竖屏三个样本，生成新的 parse 计划。规划不得调用 OCR、VLM 或搜索。向用户列出每个 task ID、完整 plan fingerprint、单轮/总预算及合计上限，获得确切批准后才执行付费 VLM。三个任务顺序运行；至少一例在 OCR 请求登记后中断 Temporal worker并恢复，核对同一 provider request ID 未重复受理。验证布局、文字、矩形切片、标注图、来源、实际费用和预算结算，再决定是否勾选 T010。

T010 完成后才处理 T017/T018。真实搜索前必须核实 SerpApi/Tavily 当前账户计价、探测计价、条款和凭据；用固定额度响应验证切换，不故意消耗真实额度。不要提前实现 T019+。保留历史失败证据和所有未完成 [LIVE] 状态。继续使用 speckit-implement，并在每个真实验收节点更新 tasks.md 的现场记录。
```
