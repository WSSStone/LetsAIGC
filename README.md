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

Agent 的每个媒体任务先产生不可变计划与 SHA-256 批准指纹。成功的 `agent plan` 和
JSON 模式的 `agent run` 返回 `awaiting_approval`；之后显式调用
`agent execute --approve` 执行，JSON 模式也会执行已批准任务。`agent chat` 不支持
JSON 模式。Agent 没有 shell、任意文件写入、模型下载、LoRA 训练、人工批准或
production export 权限。

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

### 核心环境准备

以下命令从仓库根目录在 PowerShell 执行，前提是 Miniforge/Mamba 已安装且在 PATH。

```powershell
.\scripts\bootstrap.ps1 -Component core
mamba run -n letsaigc-core letsaigc --json doctor
mamba run -n letsaigc-core letsaigc agent --help
```

在进程环境或被 Git 忽略的 `.env`（参见 `.env.example`）中配置 `LLM_API_KEY`，可选
`LLM_BASE_URL` 指向兼容 Responses API 的中转站，并按需设置 `LLM_DECISION_MODEL`、
`LLM_VLM_MODEL`、`LLM_IMAGE_MODEL`。中转站必须支持结构化 JSON Schema 输出、顺序
function tool、vision data URL 与图片接口。本地媒体生成的 Agent 同样使用 Responses
规划和视觉评审；`doctor` 只检查配置，不验证账户或发起付费请求。

### Agent 使用

规划会解析和暂存输入、保存本地会话，并调用 Responses，可能产生 API 费用。
预留为 `$0.03 + 每张输入 $0.01`，同时受所传预算文件的单轮及总 USD 预算限制。
媒体生成仍需单独批准。随附预算配置允许一次初始生成加最多 3 次修订；
`TaskBudget` 类型省略修订字段时默认 10 次，允许的最大值也是 10 次。

本地 SDXL：先由操作者部署 ComfyUI、同步模型并启动服务。

```powershell
.\scripts\bootstrap.ps1 -Component comfy
mamba run -n letsaigc-core letsaigc models sync production-sdxl
mamba run -n letsaigc-core letsaigc comfy serve
```

保持服务终端运行，在另一终端从仓库根目录规划任务：

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "生成一个居中的蓝色药水游戏图标" `
  --backend comfy --budget configs\agent\budget-local.yaml
```

检查返回的计划、费用/GPU 预算和输出上限。用顶层 `id` 替换下面的 `TASK_ID`，
用顶层 `plan_fingerprint` 替换 `PLAN_FINGERPRINT`，确认后执行：

```powershell
mamba run -n letsaigc-core letsaigc --json agent execute TASK_ID `
  --approve PLAN_FINGERPRINT
mamba run -n letsaigc-core letsaigc --json agent inspect TASK_ID
```

远端图片：使用已配置的凭据和远端预算即可，无需本地 ComfyUI 或模型权重。
下面的计划也须检查返回值后，按上述方式单独批准执行。

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "生成一个透明背景的魔法药水游戏图标，低质量 1024x1024" `
  --backend openai --budget configs\agent\budget-remote-low.yaml

# 本地图片编辑：先完成本地 SDXL 准备，并替换示例图片路径。
mamba run -n letsaigc-core letsaigc --json agent plan `
  "保留轮廓，把它改成水彩道具图标" --image C:\assets\potion.png `
  --backend comfy --budget configs\agent\budget-local.yaml
```

图片来源支持本地文件与 HTTPS URL；本地 recipe 最多接受单张输入，透明背景与
多参考图能力边界见 [Agent 操作指南](docs/agent-quickstart.md)。该指南还说明会话恢复、
视频与派生任务、编译证据和当前验收状态。

### 专家命令

以下保留接口直接运行底层管线，不经过 Agent 规划与指纹批准。执行前由操作者准备
相应模型、服务和资源；生成完成后仍须通过生产门禁。

```powershell
mamba run -n letsaigc-core letsaigc workflow run sdxl-smoke
# MLflow 前台服务，可在独立终端运行。
mamba run -n letsaigc-core letsaigc tracking serve
```

视频与序列帧示例：

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

历史专家部署与验收记录见
[图片/训练 quickstart](specs/001-project-harness/quickstart.md) 和
[视频 quickstart](specs/005-video-runtime-models/quickstart.md)，其既有烟测结果不代表
新增 Agent 链路已完成实机验收。
本地思考与实测报告位于被 Git 忽略的 `.doc/`；运行时、模型、缓存、会话和输出位于
同样被忽略的 `.local/`。会话、批准记录、输入副本及运行证据需要保留，不能与缓存一起
视为可随时删除的数据。

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
