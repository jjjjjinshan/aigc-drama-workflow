# 成片编排契约 v1.0

`成片编排.md` 是成片阶段唯一人工维护的编辑决定。Markdown 正文只放审片注记；唯一的 `json` 代码块承载编译器读取的结构化编排。重复 JSON 块、未知字段、重复 JSON 键或非法 JSON 均拒绝。

## 结构

顶层字段必须且只能是：

- `schema_version`: 固定为 `1.0`。
- `episode_id`: `EP001` 格式，并与 `episodes/<EP>/成片编排.md` 一致。
- `review_status`: 只有创作者完成编排审阅后填写 `approved` 才能编译。
- `frame`: `width`、`height`（偶数，320–3840）、`fps_num`（1–60）与 `fps_den`（1–1001）；帧率比值须在 1–60 fps。
- `subtitle_mode`: `none`、`sidecar` 或 `burn_in`。
- `voice_source`: 字幕或对白音频存在时必填，包含项目相对路径 `sheet`、`index` 和 `screenplay`；编译前调用现有 voice sheet checker，确保台词仍逐字投影自当前剧本。
- `video_clips`: 有序片段；每段只允许硬切，下一段紧接上一段。条目含唯一 `clip_id`、`source`、`source_in_ms`、`duration_ms`。
- `audio_clips`: 可重叠的音轨片段；条目含唯一 `clip_id`、`role`、`source`、`source_in_ms`、`timeline_start_ms`、`duration_ms`、`gain_db`、`fade_in_ms`、`fade_out_ms`。`role` 为 `dialogue`、`music`、`sfx` 或 `ambience`。对白必须绑定 `voice_line_id`；其他角色不得填写。
- `captions`: 每条 `{voice_line_id, timeline_start_ms, duration_ms}`。文字由 voice sheet 投影，不在这里复制。对白音频与字幕必须按 `voice_line_id` 一一对应，字幕时间必须位于对应对白音频范围内。
- `output`: 唯一字段 `filename`，必须是安全的 `.mp4` 文件名。

`source` 必须恰有 `job_id`、`run_id`、`path`。编译器只接受当前 job 的最新成功运行，检查输出路径、媒体类型、字节数和 SHA-256；被覆盖、修改、过期或来源不符的文件 fail closed。剪入点和时长使用整数毫秒，时长大于零；片段总时长最多 24 小时。无任意 FFmpeg filter、shell 字符串、外部路径、重叠视频轨或任意插件。

## 编译产物

- `timeline.edl.json`: 只含归一化、来源已核验的剪辑与混音参数；视频片段的 `start_ms` / `end_ms` 由顺序和时长推导。
- `subtitles.srt`: 从经 voice sheet checker 验证的 `line_text` 投影；仅在 `subtitle_mode` 不是 `none` 且有字幕时生成。
- 清单包含时间线、声音表、剧本索引、剧本及每个媒体源的 SHA-256 与字节数。编译成功不代表成片已渲染或内容质量已通过。

编译输出写入明确指定的新目录。实际组装由 `media_assembler.py` 重新编译并核验来源，使用 FFmpeg 完成剪切、画幅适配、音频混合与可选字幕烧录；ffprobe 检查输入流、裁切范围和输出容器/流/尺寸/帧率/时长，FFmpeg 完整解码成片。组装器在同一项目目录写临时版本，所有检查通过后才发布为新版本；渲染失败不得损坏既有母版。成片 manifest 记录源文件摘要、工具版本、输出配置和技术检查，质量仍为 `not_assessed`。

首版要求 FFmpeg 支持 `-/filter_complex` 文件参数；烧录字幕还依赖 subtitles/libass filter。输出固定为 H.264/AAC MP4（无音轨时只输出视频），画面按目标尺寸等比缩放并以黑边补齐，视频硬切拼接；对白、音乐、音效和环境声按已审阅的增益、淡入淡出与时间偏移混音，混音峰值由 limiter 控制。播放器兼容性与实际视听质量仍需在目标设备上由创作者验收。
