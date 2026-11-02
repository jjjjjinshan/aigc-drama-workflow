---
name: short-drama-edit
description: 为已生成媒体建立创作者可审查的单集成片编排，绑定镜头片段、已接受对白音频、字幕时间与音乐/音效位置；校验来源并编译受限 EDL 与 SRT，不调用生成服务，也不替创作者判断审美质量。
license: MIT
---

# 成片编排

本技能拥有 `episodes/<EP>/成片编排.md`。它记录剪辑与合成决定，不回写剧本、分镜、视频提示词或声音身份。片段顺序、裁切、音频落点、字幕时间和输出规格都由创作者审定；模型可以给候选，不能把建议当作已接受决定。

## 工作顺序

1. 确认每个引用的生产 job 当前成功，输出字节仍与运行记录一致。
2. 依照审片选择片段与剪入/剪出点；不得把技术成功写成质量接受。
3. 对白音频关联稳定的 `VLINE-*`，字幕从对应的 `voice-record-sheet.jsonl` 投影，不在时间线重复维护台词。
4. 为音乐、环境声和音效确定时间偏移、增益与淡入淡出；不在生成视频里叠加全片配乐。
5. 审阅编排文件并将 `review_status` 设为 `approved` 后，运行编译检查。
6. 将编译结果交给本地组装器；由组装器创建新版本，不覆盖现有母版。

## 编排契约

正文保留简短的人类审片说明，并且只包含一个 `json` 代码块。字段、时间单位、来源绑定和限制见
[时间线契约](references/timeline-contract.md)；可复制样例见
[编排样例](assets/timeline.example.md)。编译器只证明结构、来源和字幕投影一致，报告始终保留
`quality_verdict: not_assessed`。

默认只检查、不写入：

```text
python3 <本技能目录>/scripts/timeline_compiler.py <project>/episodes/<EP>/成片编排.md
```

检查通过后显式指定一个全新版本目录来生成受限 EDL 和 SRT：

```text
python3 <本技能目录>/scripts/timeline_compiler.py <project>/episodes/<EP>/成片编排.md --output-dir dist/<EP>/assembly/v001
```

输出目录必须是项目内 `dist/<EP>/assembly/<version>`，且不得已存在。此阶段不调用 FFmpeg；实际母版导出和媒体探测属于本地组装阶段。

## 边界

- 不发起付费媒体任务；需要重生成素材时交给 `short-drama-produce`，继续逐项预览与确认。
- 不编辑源镜头提示词来“修剪辑”；源规格错误应返回对应 owner。
- 检查器不判人物一致性、表演、节奏、口型、混音或审美。此类结论必须来自真实观看/收听并记录证据。
- 试产时间线、声音表、运行记录和媒体留在本地；公开仓库只提交通用契约与合成样例。

## 安装维护

```text
python3 scripts/selftest.py
```
