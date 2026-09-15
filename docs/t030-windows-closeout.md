# Windows T030：单图编辑 MVP 交接

## 范围和结果

按用户确认的 [T030 MVP 范围](t030-mvp-scope.md) 收尾，不是原完整质量矩阵验收。
复用 BG3 高清截图的成功解析及确认 review v0，已通过真实产品 CLI 和 Temporal 完成
选择 → Luna high 提示词 → 独立批准 GPT Image 2 → 保存原始 1024×1024 PNG。
用户检查后明确反馈“检查完毕，效果很好”，随后授权收尾、提交和推送。

- 分支：`codex/t030-windows`；基线 `bc25feddf5906dd1f06811d3dae13db6fc0c3239`。
- 确认 review：`ui-d95b56613e1944a2a3d50d6a5ba5601b`，v0。
- 编辑根：`ui-98cee0bce5504bae96a6067e1113d598`。
- 最终生图 child：`ui-cloud-retry-b07958fccf7ef6d2b65e65dc8b7008bc`。
- 最终 operation：`op-8961674a4e31f0324a4b0a6744ac34313ee544f2633552a8`。
- Temporal run：`01a0a055-1bd2-7608-adde-86f3e50c6e8c`，COMPLETED，121 个事件。

交付的是 generated 局部上下文图，不是独立背景真值，也未自动拼回整张截图。
本次响应同时带 base64 和 URL，优先保存 base64。URL-only 下载已做离线安全测试，
不能把本次成功写成 URL-only 真实下载验收。

## 费用与失败保留

成功提示词调用 1 次、最终成功生图调用 1 次；原根另有 2 次历史 unknown 生图提交。
最终生图项目计价 $0.055193，原根已结算合计 $0.055576；原根 unknown 预留 $0.20 保留，
另两条历史提示词 unknown 合计 $0.02 也未清除。以上不是 provider 对账结论。
最终单次上限 $0.15，原根有效上限 $0.36，含上述其他预留的授权范围上限 $0.38。
更早解析/本地 GPU/实验调用的记录继续各自在原任务中保存，不混入本根小计，也不声称是整个 T030 总费用。

最终图片响应没有 provider request/response ID，记录为 null；`local-result-*` 只表示本地持久化结果引用。
没有手工改账、reconcile、清空 unknown、自动重发或替换原根。收尾期间无新 VLM、生图或 GPU 调用。

## 代码与状态显示

本分支保留本次 Windows 工作的 VLM 请求兼容/脱敏诊断、review 画布交互、SAM/Comfy 现场修复、
补图精确 child 批准绑定，以及可选云端后端、受控 URL 收集和有界显式重试。
新配置在新计划批准范围中冻结；旧计划和旧操作保留。重试最多两次显式追加，每次分别批准，不能自动重试。

收尾修正了 `ui inspect` 顶层仍显示旧失败的问题：解析已就绪的编辑任务使用当前编辑账本状态，
新增 `status_source=editing_ledger`。`task/source/stale` 保留旧投影及其失败原因，不伪造最新运行。
根因是投影按序号拒绝较小更新，重新启动的 run 序号可能小于旧 run；本次不重构跨 run 投影排序，
也不改动 Temporal 命令序列。实时工作流状态仍应查具体 run；通用跨 run 投影重建是独立维护事项。

## 复核方法与本地证据

从仓库根运行，均不需要模型或 GPU：

```powershell
conda run --no-capture-output -n letsaigc-core pytest -q -o cache_dir=.local/pytest-cache-t030-closeout
conda run --no-capture-output -n letsaigc-core ruff check .
git diff --check
conda run --no-capture-output -n letsaigc-core python -m letsaigc --json ui inspect ui-98cee0bce5504bae96a6067e1113d598 --local
```

最后一条只适用于保有 Windows 原账本/产物的机器，不能在 Mac 上凭 task ID 重建任务。
离线 cloud/CLI/批准/下载定向测试 79 passed；完整收尾回归 **792 passed、24 skipped、0 failed**，
耗时 200.87 秒。ruff 与 `git diff --check` 通过；两条已有历史及最终成功历史共三份离线回放通过。
既有 review HTTP WinError 10053 本轮完整测试未复现；早先失败报告保留，不能据此声称已修复该偶发问题。
两条 Pillow `getdata` 弃用警告及回放的 Pydantic converter 提示保留，不影响上述验证结果。
普通 pytest 的 opt-in LIVE/Temporal 跳过不算真实通过，也不要求重复运行 T024。

现场目录：`.local/validation/ui-analysis/t030-windows/`，以下文件不提交 Git：

- `cloud-image-followup-runtime.json`、`cloud-image-followup-operation.json`：真实调用与账本观测。
- `cloud-image-followup-temporal-history.json`：真实完成历史。
- `cloud-image-followup-prepared.json`：具体计划及批准范围。
- `cloud-image-followup-generated-1024.png`：用户检查的最终图片。
- `cloud-image-followup-user-review.json`：用户视觉确认，与原运行记录分开保存。
- `closeout-tests-20260915.xml`：本次完整测试记录。
- `mvp-closeout.json`：收尾结论，引用原记录，不把历史失败包改写成成功。
- `verify-mvp-closeout.py`：本地只读复核 PNG/用户确认/测试报告及最终历史回放，不执行活动或模型。

## 未包含的扩展验收

SC-017 真实局部重读/建议采纳及锁定拒绝、完整三种编辑模式/高级选择矩阵、
有限修订资产复用的完整现场证据、GPU 受理后 worker 中断恢复，仍是待扩展验收，未标记通过。
24 例正式质量、批次和 T031 以后任务未开展；没有无真值的背景准确率宣称。
旧 SAM/Comfy 成果保留；云端成功不证明所有 CUDA 释放场景。Comfy 的小量稳定显存残留
按既有记录保留实际字节，不应视为独立测得的模型驻留计数或所有平台通用证明。

## Mac 接手与清理

本次只推送工作分支，不合并、不自动创建 PR。Mac agent 可先审查分支差异和以上离线测试，
再由用户决定集成；不要拉取或覆盖 Windows 的 `.local` 到 Mac 主账本，亦不要用 Mac 数据覆盖 Windows。
密钥、截图、模型、账本、批准、运行记录、Temporal CLI 和数据库均不随 Git 分发。
需要现场复核时单独迁移选定的只读证据包，不把机器路径/ID 当作 Mac 可执行输入。
最终生图后已关闭本次创建的 worker/Temporal 和包装进程，持久化证据保留；收尾无需重启它们。
