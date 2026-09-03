# LetsAIGC

由多模态 LLM Agent 理解用户意图、规划并调用本地 ComfyUI 或远端图片模型的游戏资产
生成工作台。它覆盖生图、图片编辑、生视频、RGBA 序列帧/Sprite Sheet 和短剧镜头编排，
并把预算批准、许可隔离、输入安全、运行谱系和生产晋级作为默认约束。

`.agents/`、`.specify/`、`AGENTS.md` 与 `specs/` 只组成 **Development Harness**：
它们约束 Codex 如何开发本项目，不是 LetsAIGC 的产品功能。产品控制面是
`src/letsaigc`、CLI、配置/schema、生成后端、ComfyUI 与媒体管线。

## Agent 工作方式

```text
CLI: agent plan / execute / run / chat / inspect
             ↓
Agent: 意图 → 能力路由 → 预算计划 → 指纹批准 → 评审与有限修订
             ↓
确定性工具: 资产解析 / recipe 编译 / 生成 / Sprite / 短剧
       ↓                              ↓
本地 ComfyUI                     GPT Image 2
             ↓
RunManifest 1.2 / MLflow / 本地会话
```

所有生成任务先产生不可变计划与 SHA-256 批准指纹。JSON 模式永不交互；它只返回
`awaiting_approval`，之后必须显式调用 `agent execute --approve`。Agent 没有 shell、
任意文件写入、模型下载、LoRA 训练、人工批准或 production export 权限。

Development Harness 的工作方式是：

1. 用 Codex Plan Mode（`codex.plan`）做只读勘察和 decision-complete 计划。
2. 依次执行 Speckit Constitution → Specify → Clarify → Plan → Tasks → Analyze。
3. 获得确认后按 `tasks.md` 实施，运行自动测试与本机烟测。
4. 每次推理/评测/训练写入 RunManifest；生产导出再经过许可、哈希、契约和人工审批。
5. 新约束出现时回到 `codex.plan`，先修订规格再继续。

首批功能域为 `001-project-harness` 至 `004-sdxl-lora-training`；视频扩展为
`005-video-runtime-models`、`006-sprite-sequence-pipeline` 和
`007-short-drama-orchestration`。每个特性保留独立的 spec/plan/tasks。
Agent-first 重构为 `008-agent-first-architecture` 至
`012-agent-critique-media`。历史特性 001 的名称仅为兼容记录，不再作为产品术语。

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

Agent 入口（计划不会生成媒体或下载模型，但 Responses 意图解析会使用预算文件中的
planning reserve，并可能产生少量 API 费用）：

```powershell
# 进程环境优先；也可写入被 Git 忽略的 .env。
$env:OPENAI_API_KEY = "..."

mamba run -n letsaigc-core letsaigc --json agent plan `
  "生成一个透明背景的魔法药水游戏图标" `
  --backend auto --budget configs\agent\budget-remote-low.yaml

mamba run -n letsaigc-core letsaigc --json agent execute TASK_ID `
  --approve PLAN_FINGERPRINT

# 图 + prompt：本地文件和 HTTPS URL 均可重复传入。
mamba run -n letsaigc-core letsaigc --json agent plan `
  "保留轮廓，把它改成水彩道具图标" --image C:\assets\potion.png `
  --backend auto --budget configs\agent\budget-local.yaml
```

视频与序列帧入口：

```powershell
# FFmpeg/ffprobe 由用户维护在系统 PATH；ComfyUI 需已在 127.0.0.1:8188 运行。
mamba run -n letsaigc-core letsaigc models sync video-local-smoke
mamba run -n letsaigc-core letsaigc --json video run wan21-t2v-smoke

# 从有谱系的视频运行派生 RGBA 帧、Sprite Sheet 和 JSON。
mamba run -n letsaigc-core letsaigc --json sprites build `
  --source-run VIDEO_RUN_ID --config configs\sprites\general-rgba-512.yaml
mamba run -n letsaigc-core letsaigc --json sprites validate SPRITE_RUN_ID

# 短剧项目支持已记录镜头、内联工作流、外部 WAV/SRT 和内容寻址续跑。
mamba run -n letsaigc-core letsaigc --json drama render `
  --project configs\drama\example.yaml --resume

# 高规格任务只打包声明、工作流和契约，不携带权重、秘密或绝对路径。
mamba run -n letsaigc-core letsaigc --json runpack build `
  --job configs\video\wan22-cloud-job.yaml
```

进一步部署和验证见
[`specs/001-project-harness/quickstart.md`](specs/001-project-harness/quickstart.md)。
视频部署边界见
[`specs/005-video-runtime-models/quickstart.md`](specs/005-video-runtime-models/quickstart.md)。
本地思考与实测报告位于被 Git 忽略的 `.doc/`；运行时、模型、缓存和输出位于
同样被忽略的 `.local/`。

## 安全边界

- ComfyUI 和 MLflow 只监听 `127.0.0.1`。
- 默认拒绝 pickle 权重、未知许可、哈希不符和未确认的条件许可。
- runpack 拒绝秘密、绝对路径和权重文件；实验模型未完成本机资格验证前阻断生产导出。
- HTTPS 输入会逐跳检查 DNS/重定向并拒绝私网地址；仅接受小于等于 25 MiB 且
  MIME、magic 与解码格式一致的 PNG/JPEG/WebP。
- 下载连接固定到已验证 IP，同时保留原始 TLS SNI；每个任务最多 8 张图片。
  原图保留本地，发送给模型的是去除元数据且最长边不超过 1536px 的派生图。
- OpenAI 图片固定为 `gpt-image-2-2026-04-21`；远端视频不在 v1 范围。
- 工具不会自动接受协议、关闭 GPU 程序、修改系统执行策略或创建 Git commit。
- 本仓库管理管线、谱系和 LoRA，不保存第三方基础权重或最终游戏资产。

详细使用、恢复边界与验收状态见 [Agent 操作指南](docs/agent-quickstart.md)。
