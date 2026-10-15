# 示例项目：末班

这是一个**完全原创**的最小示例，用来演示五份 creator-first 文档如何互相咬合。

> 故事、角色、场景均为虚构，不基于任何第三方作品。

## 故事

深夜，末班车驶离后的地铁站台。站务员沈青在清站时发现长椅上还有一位乘客——一个握着旧信封、始终没有离开的中年男人。

二十七秒，三个镜头，一次未说出口的交接。

## 文件结构

```
minimal-drama/
├── short-drama.json                    ← 项目标记文件，也是项目级配置
├── EP001/
│   ├── 剧本.md          ← short-drama-write
│   ├── 视觉设定.md      ← short-drama-assets
│   ├── 图片提示词.md    ← short-drama-image-prompts
│   ├── 分镜.md          ← short-drama-storyboard
│   └── 视频提示词.md    ← short-drama-video-prompts
└── flow/EP001/
    ├── flow-package.json      ← google-flow-manga
    ├── continuity-ledger.json
    ├── shot-cards/
    └── review/
```

> 这个示例是**最小化**的：剧集目录 `EP001/` 直接放在项目根下，省掉 `episodes/` 这一层。
> 正式项目按 `episodes/<EP>/` 组织（旧名 `剧集/<EP>/` 仍然可读）。
>
> `short-drama.json` 让这个示例成为一个**自洽的项目**：生产工具靠它定位项目根。
> 少了它，工具会沿目录树向上找，可能落到外层无关的项目上。

> 本示例**不附带图片**。`flow-package.json` 里的资产只按 `flow_asset_name` 登记（参考图留在 Flow 侧），
> 所以仓库里没有任何二进制文件，clone 下来即可直接跑校验。
> 如果你想让资产指向本地文件，把 `path` 填成相对 `flow/EP001/` 的路径即可——校验器会检查该文件是否存在。
>
> `shot-cards/` 只附一张样例卡 `SHOT-EP001-002.md`，完整一集应为每个镜头各出一张。

## 怎么读这个示例

建议按这个顺序看，能看出每份文档**为什么存在**、以及它给下游提供了什么：

1. **`剧本.md`** —— 一切的原点。注意每场都必须改变某种状态。
2. **`视觉设定.md`** —— 从剧本里**提取**视觉事实，不是重新创作。注意它区分了「身份」和「变体」。
3. **`分镜.md`** —— 每镜有唯一职责和明确起止状态。注意 `SHOT-EP001-002` 的终点如何成为下一镜的起点。
4. **`图片提示词.md`** —— 资产板提示词。注意 `IMG-*` 只是提示词条目的 ID，**不代表图片已生成**。
5. **`视频提示词.md`** —— 从分镜的「输入参考图」决定走图生视频还是文生视频。
6. **`flow/EP001/`** —— 生产包。注意 `continuity-ledger.json` 里的状态轴，以及 `shot-cards/SHOT-EP001-002.md` 这张逐镜可执行卡。

## ID 约定

五份文档通过稳定 ID 互相引用，这是它们能「咬合」的关键：

| 前缀 | 含义 | 定义在 |
|---|---|---|
| `EP001-SC001` | 场景 | `剧本.md` |
| `SHOT-EP001-001` | 镜头 | `分镜.md` |
| `MOTION-EP001-001` | 运动提示词 | `视频提示词.md` |
| `IMG-...` | 图片提示词条目 | `图片提示词.md` |
| `CHAR-...` / `LOC-...` / `PROP-...` | 角色 / 地点 / 道具 | `视觉设定.md` |

## 连续性怎么体现

看 `SHOT-EP001-001` 和 `SHOT-EP001-002` 的衔接：

- 001 的 `End`：沈青停在距长椅三步处
- 002 的 `Start`：沈青从三步处继续靠近

这不是靠模型记忆，而是**显式写在文件里的状态**。如果 002 的开头变成「沈青站在长椅旁」，连续性门禁会报出无法解释的状态跳变。

## 校验

本示例的文档结构可以用 Skill 自带的校验器检查。**在 `examples/minimal-drama/` 目录下执行**：

```bash
# 跨文档结构与 ID 一致性
python ../../skills/short-drama/scripts/creator_markdown_check.py EP001 --project-root .
# 预期输出：OK: 0 error(s), 0 warning(s)

# Flow 生产包结构与连续性
python ../../skills/google-flow-manga/scripts/validate_flow_package.py flow/EP001/flow-package.json
# 预期输出：OK: 0 error(s), 0 warning(s), 3 shot(s)
```

> 校验脚本要求 **Python 3.13+**（`flow-package` 校验依赖 3.13 的 `Path.is_dir(follow_symlinks=)`）。
> Windows 上若没有 `python3`，用 `py -3` 或把 `python` 指向 3.13 解释器，详见 [`../../docs/installation.md`](../../docs/installation.md)。

第二条命令会把结果写入 `flow/EP001/review/flow-readiness-report.json`。仓库里附带的那份是**样例**，
其中 `package` 字段被改成了相对路径（校验器实际写出的是绝对路径），其余字段与校验器输出一致。
