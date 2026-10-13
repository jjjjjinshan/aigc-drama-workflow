---
name: google-flow-manga
description: 为 Google Flow 准备和审核 AI 漫剧镜头生产包，管理角色、场景、道具、首尾状态、Ingredients/Frames 引用与生成后成片回审。用户提出“整理 Flow 提示词/生产包”“检查角色或场景连续性”“准备 Ingredients/首尾帧”“审核 Flow 生成片段”时使用；不创作上游剧本或分镜，不自动调用 Google Flow，也不消耗生成额度。
---

# Google Flow 漫剧生产包

把已确认的剧本、视觉设定、分镜、关键帧和视频提示词，转换为可检查、可逐镜执行、可回审的 Google Flow 交接包。连续性来自显式状态，不依赖模型记忆。

## 边界

- 上游内容以项目现有的 `剧本.md`、`视觉设定.md`、`分镜.md`、`图片提示词.md`、`视频提示词.md` 或用户明确指定的同类文件为准。
- 不替代 `$short-drama-write`、`$short-drama-assets`、`$short-drama-storyboard`、`$short-drama-video-prompts` 修改来源文件；发现问题时写入审核报告并路由回正确层。
- 默认只创建本地生产包和审核报告。不得登录、操控或自动提交 Google Flow，不得把“准备好了”“继续”或既有接受状态当作本次付费生成确认。
- 不硬编码模型、地区功能或额度价格。执行前以 Flow 界面的当前模型兼容提示和额度显示为准。
- 保留用户现有目录和命名；生成物放在项目内独立的 `flow/` 目录，除非用户指定其他位置。

## 接单先查真值

1. 确认项目根目录、目标集、画幅和来源文档。
2. 检查角色/造型、地点/视图、道具/状态、关键帧和已有 Flow 片段的真实文件。
3. 报告当前镜头数、已批准引用数、缺失引用、连续性阻塞项和已有回审状态。
4. 不猜测媒体路径、Flow 资产名、审核状态或镜头边界。

## 选择工作模式

- `prepare`：创建或更新 Flow 交接包、状态台账和逐镜卡。
- `preflight-review`：只检查现有生产包是否可进入 Flow，不改上游来源。
- `output-review`：检查已生成片段，记录接受/重做结论和正确修复层。

需要判断 Flow 输入模式或当前功能时，读取 [Flow 交接规则](references/flow-handoff-contract.md)。
需要建立状态台账时，读取 [连续性规则](references/continuity-contract.md)。
需要生成前或成片回审时，读取 [审核规则](references/review-contract.md)。
需要创建或校验 JSON 时，读取 [生产包结构](references/package-schema.md)。

## Prepare 工作流

1. 从已接受的来源文档提取稳定 ID：集、场、镜头、角色、造型、地点、道具和引用资产。
2. 为每个镜头写 `start_state`、`end_state` 和 `continuity_delta`。任何可见变化都必须有 `why_changed`。
3. 按镜头目标选择一个 Flow 输入模式：
   - `ingredients`：身份、物件或场景引用是主要约束。
   - `frames`：构图、动作起点或首尾状态是主要约束。
   - `text`：没有可复用媒体引用，且用户接受较低连续性控制。
   - `video-edit`：基于已有片段做局部修改或延续。
4. 每镜生成一张人类可读的 Shot Card；提示词只描述本镜可见动作、表演、环境运动、摄影和声音，不复述整段剧情。
5. 写入：
   - `flow/<episode-id>/flow-package.json`
   - `flow/<episode-id>/continuity-ledger.json`
   - `flow/<episode-id>/shot-cards/<shot-id>.md`
   - `flow/<episode-id>/review/flow-readiness-report.json`
6. 运行校验器：

```powershell
python <skill-dir>/scripts/validate_flow_package.py <flow-package.json> --output <flow-readiness-report.json>
```

7. 只有零错误且创作者明确确认本批次时，才把 `package_status` 标记为 `ready_for_flow`。警告必须保留并展示。

可从 [生产包模板](assets/flow-package.template.json) 和 [镜头卡模板](assets/flow-shot-card.template.md) 起步，不要把模板占位符当作真实信息。

## 连续性门禁

逐镜检查以下状态轴：

- 人物身份、发型、服装、伤势、持物、知识和意图。
- 地点、内外、昼夜、天气、实用光源和空间关系。
- 道具位置、状态、数量、文字、损坏和所有权。
- 左右位置、视线、动作方向、景深层次和轴线。
- 当前剧情阶段，避免提前泄露结果或退回旧状态。

单帧好看不能覆盖序列错误。无法用脚本、前镜、世界规则或明确设计锁解释的变化，视为连续性风险。

## 审核与修复路由

- 身份、服装、物件、空间或状态漂移：修连续性台账或引用资产。
- 关键帧无法承载剧情信息：退回分镜/冻结关键帧，不靠加长视频提示词补救。
- 动作、表演、镜头或环境运动不清楚：修视频提示词或 Flow 输入模式。
- 引用污染、输入冲突、错误资产绑定：修 Flow 生产包。
- 片段节奏、反应时间或剪辑顺序不成立：修剪辑决策，避免整批重抽。
- 对白、声音、字幕抢画面或错位：修对应音频/字幕层。

审核只记录问题、证据、严重度、影响镜头和修复层。除非用户同时要求修订，否则不改来源文件。

## 状态与停止条件

允许的生产包状态：`draft`、`blocked`、`ready_for_flow`、`generated`、`reviewed`、`accepted`。

- 存在错误：`blocked`。
- 只有警告：可保持 `draft`，由创作者决定是否确认。
- `ready_for_flow` 必须有本批次创作者确认。
- `accepted` 必须有输出片段和成片回审结论。
- 一次失败生成或审核拒绝不能自动重试；先记录原因，再等待新的明确生产确认。

## 设计来源

本 Skill 将 MIT 项目 `dynamic-manga-video-skill` 的连续性台账、冻结包和导演审核思想改写为 Google Flow 专用流程。需要追溯来源时读取 [来源与许可](references/provenance.md)。
