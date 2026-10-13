# AIGC 漫剧创作工作流

> 面向 AIGC 漫剧 / 短剧创作的 Agent 工作流。11 个职责隔离的 Skill 协作运转，用显式状态契约解决生成一致性，用机械门禁守住质量底线。

[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-standard-5B9BE8?style=flat-square)](https://agentskills.io/specification)
[![License](https://img.shields.io/badge/license-MIT-4FAE85?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-D89A4A?style=flat-square)](docs/installation.md)

---

## 这是什么

一套把**长篇小说**变成**可交付成片**的完整创作流水线。

它不是「把提示词串起来」，而是一套有所有权边界、机械门禁和显式状态契约的工业化流程：每个阶段都产生可被下游引用的确定产物，关键分叉点停下来交还给人。

已在一个 **58 集规模**的竖屏漫剧改编项目上跑通全链路：剧本 → 视觉设定 → 图片提示词 → 分镜 → 视频提示词 → 图片/视频生产 → 成片回审。

## 它解决什么问题

模型已经能画出漂亮的单帧，但撑不起一部连续剧。真正卡住工业化的是三个结构性问题：

| 问题 | 表现 |
|---|---|
| **一致性会漂移** | 同一角色跨镜头换脸、换服装、换道具位置；同一场景换个角度就变成另一个房间。单帧越精致，序列上的崩坏越刺眼。 |
| **创作与生产断层** | 剧本、设定、分镜、提示词散落在不同工具里。改一处不知道会波及哪些下游产物，返工全凭记忆。 |
| **质量不可追溯** | 「AI 味」「不够电影感」这类判断没有证据链，问题定位不到具体环节，只能整批重抽。 |

这三个问题都不是靠「换更强的模型」能解决的——它们需要流程约束。

## 工作流全貌

```
长篇小说 / 连载网文 / 故事想法
            │
            ▼
   ┌────────────────┬────────────────┐
   │   原著分析      │   开发立契      │
   │ 章节索引        │ 故事引擎        │
   │ 功能提取        │ 分集地图        │
   │ 分集候选        │ 改编边界        │
   └────────────────┴────────────────┘
            │
            ▼
   ┌──────┬──────┬──────┬──────┬──────┐
   │ 剧本 │视觉  │图片  │ 分镜 │视频  │   ← 五份 creator-first 文档
   │      │设定  │提示词│      │提示词│
   └──────┴──────┴──────┴──────┴──────┘
            │
            ▼
   ┌────────────────┬────────────────┐
   │   生产执行      │  Flow 生产包    │
   │ 图片/视频/配音  │ 连续性台账      │
   │ 音乐            │ 逐镜卡 / 回审   │
   └────────────────┴────────────────┘
            │
            ▼
        成片 · 交付包
```

**两个横切能力**：核心路由 Skill 负责项目初始化、跨阶段调度与本地创作台；审查 Skill 可在任意阶段介入，只输出问题、证据与修订要求，不代替任何 owner 修改来源文件。

## 11 个 Skill

| Skill | 职责 | 产物 |
|---|---|---|
| `short-drama` | 项目初始化、跨阶段路由、Dashboard、制作形态决策 | 项目配置与目录结构 |
| `short-drama-novel-analyze` | 长篇小说拆解为可追溯的分析层 | 章节索引、功能提取、分集候选 |
| `short-drama-develop` | 创作者契约、故事引擎、分集地图 | 创作简报、故事引擎、分集地图 |
| `short-drama-write` | 单集剧本写作与修订 | `剧本.md` |
| `short-drama-assets` | 从剧本拆出人物、地点、道具与连续性 | `视觉设定.md` |
| `short-drama-image-prompts` | 资产图片提示词 | `图片提示词.md` |
| `short-drama-storyboard` | 分镜与冻结关键帧 | `分镜.md` |
| `short-drama-video-prompts` | 视频运动与时间线音乐提示词 | `视频提示词.md` |
| `short-drama-produce` | 确认后执行媒体生产 | `制作成果/` |
| `short-drama-review` | 全链路审查，带证据链定位问题 | 审查报告 |
| `google-flow-manga` | 把分镜转成 Google Flow 可执行生产包 | `flow/<EP>/` |

## 五个核心设计

**1. 文件系统即唯一真相**
每集只维护五份创作者可读的 Markdown，明确拒绝另建卡片、节拍表、QA 记录这类「并行的结构化真相」。一份事实只有一个 owner，修改只有一条路径。

**2. 连续性来自显式状态**
每个镜头必须写 `start_state` / `end_state` / `continuity_delta`，任何可见变化都要有 `why_changed`。连续性不依赖模型记忆，而是可校验的显式契约——这是针对生成式一致性漂移的直接解法。

**3. owner 边界**
每个 Skill 只写自己的产物。发现上游有问题时，写审查报告并路由回正确的层，而不是顺手改掉。

**4. 机械门禁 + 人类停靠**
每阶段都有脚本校验，错误则 fail closed，绝不带着错表进入下一阶段。而真正的创作分叉点强制停下来问人——机器只负责证明，人负责判断。生产环节是四步硬闸门：`job → prepare → confirm → run`。

**5. 规则四级分级**

| 级别 | 含义 | 阻断权限 |
|---|---|---|
| `structural_invariant` | 引用完整性、ID、显式状态矛盾 | 脚本可阻断 |
| `reviewed_invariant` | 升级是否真实、承诺是否兑现 | 审查带证据判定 |
| `craft_default` | 通常有帮助的做法 | 创作者说明理由后可覆盖 |
| `taste_option` | 钩子、弧线、结局气质 | 不得单独阻断 |

## 快速开始

```bash
git clone https://github.com/jjjjjinshan/aigc-drama-workflow.git
cd aigc-drama-workflow
```

安装到你的 Agent 宿主，见 [安装说明](docs/installation.md)。

最小流程：

```
用 $short-drama 初始化一个漫剧项目
用 $short-drama-write 写 EP001 的剧本
用 $short-drama-assets 从剧本拆出视觉设定
用 $short-drama-storyboard 完成分镜和冻结关键帧
用 $short-drama-video-prompts 把分镜写成视频提示词
```

完整示例见 [`examples/minimal-drama/`](examples/minimal-drama/)。示例里附了两条校验命令，clone 后可直接跑，预期输出 `OK: 剧集/EP001` 与 `OK: 0 error(s), 0 warning(s), 3 shot(s)`。

## 运行要求

- **Python 3.13+** —— 校验脚本使用了 `Path.is_dir(follow_symlinks=)` 等 3.13 才有的 API
- 仅使用 Python 标准库，无第三方依赖
- 跨平台（Windows / macOS / Linux）

> Windows 下若没有 `python3` 命令，请使用 `py -3`；或为 Python 3.13 建立 `python3` 别名。

## 文档

- [架构说明](docs/architecture.md) —— 五层流水线与横切能力
- [工作流详解](docs/workflow.md) —— 六阶段逐个展开
- [设计原则](docs/design-principles.md) —— 五个核心机制的来由
- [安装说明](docs/installation.md) —— pi / Codex / Claude Code 等宿主

## 项目结构

```
aigc-drama-workflow/
├── skills/          # 11 个 Skill（Agent Skills 标准）
├── docs/            # 设计文档
├── examples/        # 原创示例项目
├── LICENSE
├── NOTICE.md        # 第三方来源声明
└── CHANGELOG.md
```

## 兼容性

本工作流基于 [Agent Skills 标准](https://agentskills.io/specification) 实现，可在任何支持该标准的宿主上运行，包括但不限于 pi、Codex、Claude Code。Skill 不绑定特定模型或供应商，生产端通过 adapter 契约对接。

## 许可

[MIT](LICENSE)。本项目部分内容改编自第三方 MIT 项目，详见 [NOTICE.md](NOTICE.md)。
