# UIProvider 输入与供给契约

状态：待实现；对应 FR-001—009、FR-043—045、U-V01/U-V08/U-V11/U-V12/U-V13。

首版同时交付 manual 与 search 的单图供给，并共用真实 OCR/VLM。多项 import 仅登记输入；ui_batch 未就绪时计划必须明确拒绝多图执行，不截断为一图。10项映射、顺序批次和部分失败在 T031—T034 补齐。

## 分层与接口

`UIProvider` 提供已验证截图素材；`manual` 与 `search` 是领域入口。SerpApi/Tavily 是 search 内部 ImageSearchProvider，不与 manual 一起参与额度排名。

```python
class UIProvider(Protocol):
    id: Literal["manual", "search"]
    version: str

    def provide(self, request: UIInputSpec, context: ProvisionContext) -> UIProvisionResult: ...
```

以上为拟实现签名。ProvisionContext 只提供任务/操作身份、已批准能力、受限 ArtifactStore 和公共账本接口；没有可由模型指定的输出目录、shell 或任意网络客户端。

## 可信输入边界

- `ui import` 在可信 CLI 层接收本地路径、HTTPS 图片直链或已授权素材 ref，逐项调用既有受控单图能力。首轮格式 PNG/JPEG/WebP，25 MiB，像素及资源上限见 [执行合同](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/contracts/execution.md)。
- 在检查原始输入数量 1—10 后，复制输入字节、登记哈希和 metadata，返回 UIInputManifest 引用；不触发 OCR/VLM/GPU 或搜索。此受控登记能力不能作为 Agent 任意文件读写工具。
- 不修改旧 AssetResolver.resolve_many 的 8 张合同。UI 使用自己的多项接入循环，逐项校验 MIME、magic bytes、decode、地址/重定向、哈希及作用域。
- 只作为来源说明的网页不抓取；直链返回 HTML 时拒绝，错误不得包含原始 URL 查询或本地绝对路径。
- manual provide 只接受已登记引用，不初始化搜索适配器、不读取搜索密钥、不调用 quota probe。模型/网络分析是否可运行另由对应计划约束。
- 新任务引用旧素材必须经访问验证，建立当前任务引用和来源关系；恢复读取冻结副本，不重新打开用户路径或重新下载 URL。

## 来源、结果和重复

成功来源含 `source_id + original_ref + provenance_ref`；图片内容可以复用，但每次手动选择/搜索发现独立保存来源边。用户标题与说明存 user_declared，尺寸/hash 为 observed，供应商说明为 provider_declared，许可证未知保持 unknown。

| status | 判定 | 后续 |
|---|---|---|
| ready | 至少一个有效 source，所有输入条目取得成功 | 按请求继续 |
| partial | 部分取得且失败条目有结构化错误引用 | 按已冻结批次策略继续独立输入 |
| empty | 已完成搜索没有可用图片 | 停止当前查询；已耗搜索结算；可用剩余查询额度按冻结策略继续 |
| unavailable | 没有可继续输入/能力，例如 invalid_input、missing_artifact、quota_unavailable | 明确恢复条件，不标成功 |

手动选择保持顺序；超过 10 张在去重前拒绝。精确重复可共用字节和一次解析，但每条 input_entry_id 均有结果映射；近似重复只提示。搜索可按显式阈值去除近似重复，输出排除理由。两种入口同一内容使用相同 canonical 和坐标规则。

## 搜索供给的内部边界

SearchUIProvider 仅在有效分析授权后处理 query_ref，按请求冻结的 max_images（默认1，最多10）限制根供给；委托内部搜索/额度适配器，取得候选、顺序受控下载并按清晰度及游戏/HUD场景筛选。搜索结果数与实际成功下载数分开记录；失效候选不进入 UISource。

原始签名 URL 只在 acquisition Activity 的瞬时内存中存在；持久化只保存 ArtifactRef、无 query/fragment/userinfo 的来源页及安全错误码。步骤重试首先复用已登记素材；没有已登记图且无法恢复瞬时 URL 时报告重新供给需求，不重发已可能计费的查询。

单图搜索由 ui_analysis 的 search acquisition 执行，取得首张合格图片后停止后续查询；空结果可在剩余查询/尝试预算内继续。批次由 ui_batch 父级供给，child 只复用已登记图片及来源，不重搜。max_images 只是上限，少于上限但没有失败并不自动判为partial；仍按逐项取得结果判定四种状态。

查询和候选筛选的模型正文通过受限内容文件传递。历史 DTO 只含结果引用，大小不超过 64 KiB；供应商原始响应、OCR 正文及用户说明不进入历史或日志。

## 验收实例

首版优先验证共同解析产物及手动独立可用；输入安全/来源合同先于对应实现。共享批准、结算和恢复矩阵集中在公共层，此处只验证边界接线；批次映射随批次实施，不进入预览依赖。

- 同图从 manual/search 输入，固定下游响应后 canonical、布局和坐标相同，来源边不同。
- 缺少全部搜索密钥、额度探测超时及耗尽时，manual 的搜索和 probe 调用数仍为 0。
- 改变原文件后恢复仍用冻结输入；引用篡改和越权在外部调用前拒绝。
- 10 个手动输入含精确/近似重复和一个无效项时，全部原始选择均有结果映射，正确显示 partial；11 个输入不开始分析。
- 标题/用户说明内嵌工具指令、路径或 URL 参数不会增加权限或污染历史和日志。
