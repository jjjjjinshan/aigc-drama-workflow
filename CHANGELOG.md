# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [0.1.0] - 2026-10-13

首个公开版本。

### 新增

**11 个协作 Skill**

- `short-drama` —— 项目初始化、跨阶段路由、Dashboard、制作形态决策
- `short-drama-novel-analyze` —— 长篇小说拆解为可追溯的分析层（6 阶段管道）
- `short-drama-develop` —— 创作者契约、故事引擎、分集地图
- `short-drama-write` —— 单集剧本写作与修订
- `short-drama-assets` —— 从剧本拆出人物、地点、道具与连续性
- `short-drama-image-prompts` —— 资产图片提示词
- `short-drama-storyboard` —— 分镜与冻结关键帧
- `short-drama-video-prompts` —— 视频运动与时间线音乐提示词
- `short-drama-produce` —— 确认后执行媒体生产
- `short-drama-review` —— 全链路审查
- `google-flow-manga` —— Google Flow 生产包与连续性台账

**核心机制**

- 五份 creator-first 创作文档，拒绝并行的结构化真相
- 显式状态连续性契约（`start_state` / `end_state` / `continuity_delta`）
- owner 边界：每个 Skill 只写自己的产物
- 机械门禁（fail closed）+ 人类停靠点
- 规则四级分级：structural / reviewed / craft / taste
- 生产四步硬闸门：`job → prepare → confirm → run`

**工程**

- 每个 Skill 自带 `selftest.py` 与校验脚本，合计 100+ 项离线断言
- 仅依赖 Python 标准库，无第三方依赖
- 跨平台支持（含 Windows 兼容处理）

### 已知限制

- 需要 **Python 3.13+**（校验脚本使用了 `Path.is_dir(follow_symlinks=)` 等 3.13 才有的 API）
- 生产环节需要自行配置 adapter，仓库不包含任何供应商凭据
