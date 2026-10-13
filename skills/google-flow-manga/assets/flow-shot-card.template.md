# <shot-id> — Google Flow Shot Card

## 镜头职责

- 来源场次：`<source-scene-id>`
- 戏剧职责：<this shot's single dramatic job>
- 建议时长：<seconds>s
- 画幅：<aspect-ratio>
- Flow 输入模式：`ingredients | frames | text | video-edit`

## 已批准输入

| 用途 | 本地资产 ID | Flow 资产名 | 状态锁 |
|---|---|---|---|
| 人物/造型 | `<asset-id>` | `<flow-name-or-pending>` | <identity, costume, injury> |
| 场景/视图 | `<asset-id>` | `<flow-name-or-pending>` | <time, weather, geography> |
| 首帧 | `<asset-id>` | `<flow-name-or-pending>` | <start composition> |
| 尾帧 | `<asset-id>` | `<flow-name-or-pending>` | <end composition> |
| 输入视频 | `<asset-id>` | `<flow-name-or-pending>` | <edit/extend scope> |

删除不适用的行。不得把未批准资产写成可用输入。

## 连续性状态

### Start

- Who：<characters and current appearance/state>
- Where：<location and view>
- What：<props, ownership and condition>
- When：<story time>
- Screen geography：<left/right, gaze, motion direction, depth>
- Story phase：<what has happened and what is known>

### End

- Who：<characters and current appearance/state>
- Where：<location and view>
- What：<props, ownership and condition>
- When：<story time>
- Screen geography：<left/right, gaze, motion direction, depth>
- Story phase：<what changed in knowledge/conflict>

- 可见变化：<changed fields or none>
- 变化原因：<why the change happens on screen>
- 与前镜桥接：<explicit transition if states do not match>

## Flow 提示词

<Only this shot: subject identity, visible action, performance timing, environment motion, camera behavior and sound. Name uploaded assets exactly as they appear in Flow.>

## Negative / Keep

- <identity or costume drift to avoid>
- <prop, text, anatomy or geography error to avoid>
- <elements that must remain unchanged>

## 生成前检查

- [ ] 所有引用都存在且已批准
- [ ] 输入模式符合镜头目标
- [ ] 首尾状态与相邻镜头可解释
- [ ] 提示词没有与引用图冲突
- [ ] 本批次已获得明确生成确认

## 生成后回审

- 输出文件或 Flow 资产名：<path-or-name>
- 结论：`pending | accepted | redo`
- 证据时间码：<timecodes>
- 问题层：`continuity | keyframe | prompt | flow-package | audio | subtitle | edit`
- 修订要求：<smallest correct fix>
