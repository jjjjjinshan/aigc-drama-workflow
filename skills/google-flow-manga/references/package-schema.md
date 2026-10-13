# Flow 生产包结构

## 目录

```text
flow/<episode-id>/
├── flow-package.json
├── continuity-ledger.json
├── shot-cards/
│   └── <shot-id>.md
├── refs/
│   └── 可选的交接副本或链接说明
├── outputs/
│   └── 已下载的采用片段或关键帧
└── review/
    ├── flow-readiness-report.json
    └── output-review.json
```

来源媒体可以留在项目原目录；不要为了打包无条件复制大文件。`flow-package.json` 中的本地路径相对该 JSON 所在目录解析。

## 顶层字段

- `schema_version`：当前为 `1.0`。
- `project_id`、`episode_id`：稳定 ID。
- `package_status`：`draft | blocked | ready_for_flow | generated | reviewed | accepted`。
- `aspect_ratio`：例如 `9:16` 或 `16:9`。
- `source_documents`：来源文件路径。
- `style_lock`：系列画风摘要、色板、材质和负面规则。
- `assets`：本地文件或 Flow 资产名的注册表。
- `characters`、`locations`：身份与环境锁。
- `shots`：逐镜生产记录。
- `approval`：本批次确认，不继承旧批次确认。

## 资产字段

每个资产至少包含：

- `asset_id`：唯一稳定 ID。
- `kind`：`character | location | prop | frame | video | voice | style | other`。
- `approved`：是否经过创作者确认。
- `path` 或 `flow_asset_name`：至少一个存在。
- `usage`：例如 `ingredient`、`start-frame`、`end-frame`、`edit-input`、`voice-reference`。

## 镜头字段

每个镜头至少包含：

- `shot_id`、`source_scene_id`、`purpose`、`duration_seconds`。
- `generation_mode`：`text | ingredients | frames | video-edit`。
- `character_ids`、`location_id`。
- `reference_asset_ids`、`start_frame_asset_id`、`end_frame_asset_id`、`input_video_asset_id`、`voice_reference_asset_id`。
- `prompt`、`negative_rules`。
- `start_state`、`end_state`、`continuity_delta`。
- `camera`、`dialogue`。
- `output`：采用片段、Flow 资产名、生成状态和审核状态。

状态对象建议包含：`who`、`where`、`what`、`when`、`screen_geography`、`story_phase`。允许增加项目特有字段。

## 校验

运行：

```powershell
python scripts/validate_flow_package.py flow-package.json
```

校验器会阻止：

- 缺失或重复的稳定 ID。
- 引用不存在、未批准或本地路径缺失。
- Ingredients 镜头没有引用资产。
- Frames 镜头没有首帧。
- Video Edit 镜头没有输入视频。
- 镜头可见变化没有原因。
- `ready_for_flow` 没有本批次创作者确认。
- `accepted` 镜头没有输出片段或接受结论。

语义冲突、表演质量和成片情绪仍需要人工/模型审核；确定性脚本不能代替导演判断。
