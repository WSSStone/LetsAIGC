# LetsAIGC

面向 2D 游戏资产的本地 AIGC 工程：既容纳模型与工作流探索，也把可复现、
许可隔离、数据谱系和生产晋级作为默认约束。

## 工作方式

1. 用 Codex Plan Mode（`codex.plan`）做只读勘察和 decision-complete 计划。
2. 依次执行 Speckit Constitution → Specify → Clarify → Plan → Tasks → Analyze。
3. 获得确认后按 `tasks.md` 实施，运行自动测试与本机烟测。
4. 每次推理/评测/训练写入 RunManifest；生产导出再经过许可、哈希、契约和人工审批。
5. 新约束出现时回到 `codex.plan`，先修订规格再继续。

首批功能域为 `001-project-harness`、`002-comfy-runtime-and-models`、
`003-workflow-runner-and-evaluation`、`004-sdxl-lora-training`。`001` 保留贯穿式
初始化设计，后三项各有独立的 spec/plan/tasks，后续约束变化在对应特性中演进。

## 快速开始

```powershell
.\scripts\bootstrap.ps1 -Component core
mamba run -n letsaigc-core letsaigc --json doctor
mamba run -n letsaigc-core letsaigc models list
mamba run -n letsaigc-core letsaigc comfy serve
# 另一个终端：
mamba run -n letsaigc-core letsaigc workflow run sdxl-smoke
mamba run -n letsaigc-core letsaigc tracking serve
```

进一步部署和验证见
[`specs/001-project-harness/quickstart.md`](specs/001-project-harness/quickstart.md)。
本地思考与实测报告位于被 Git 忽略的 `.doc/`；运行时、模型、缓存和输出位于
同样被忽略的 `.local/`。

## 安全边界

- ComfyUI 和 MLflow 只监听 `127.0.0.1`。
- 默认拒绝 pickle 权重、未知许可、哈希不符和未确认的条件许可。
- 工具不会自动接受协议、关闭 GPU 程序、修改系统执行策略或创建 Git commit。
- 本仓库管理管线、谱系和 LoRA，不保存第三方基础权重或最终游戏资产。
