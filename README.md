# AIGC 漫剧 / 短剧创作 Agent 工作流 (aigc-drama-workflow)

> 面向个人创作者和 3 人以下工作室的 AIGC 漫剧 / 短剧创作工作流。11 个 Skill 分工处理故事拆解、剧本与视觉设定、分镜、生产校验和渲染交付。每集用 Markdown 文档记录创作状态；付费渲染须经过校验，并由创作者逐镜确认。

---

## 目录
- [一、 产品定位与设计哲学](#一-产品定位与设计哲学)
- [二、 常见问题与工作流目标](#二-常见问题与工作流目标)
- [三、 工作流架构](#三-工作流架构)
- [四、 11 个 Skill 职责矩阵](#四-11-个-skill-职责矩阵)
- [五、 核心产品机制与工程约束](#五-核心产品机制与工程约束)
- [六、 Lookdev 资产库与图生视频 (I2V) 管线](#六-lookdev-资产库与图生视频-i2v-管线)
- [七、 项目目录结构示例](#七-项目目录结构示例)
- [八、 核心产物样例 (Creator-First Artifacts)](#八-核心产物样例-creator-first-artifacts)
- [九、 生产 Adapter 与无平台绑定设计](#九-生产-adapter-与无平台绑定设计)
- [十、 快速开始与自动化检验](#十-快速开始与自动化检验)

---

## 一、 产品定位与设计哲学

本项目不追求一键生成成片，而是把创作编排与图像、视频等高成本渲染分开。Agent 负责拆解文本、维护状态与引用、生成提示词、校验任务并调度生产；配音、口型、剪辑节奏和最终审美由创作者与专业工具负责。

### 定位与边界 (Boundary & Trade-offs)
* **LLM 擅长做（Agent 自动化接管）**：文本解构、显式状态追踪、ID 引用核查、提示词翻译、Confirm 成本预览与生产调度。
* **LLM 不擅长做（留给专业工具与人类）**：配音音色克隆（TTS）、AI 对口型（Lip-Sync）与剪辑节奏卡点。系统将最终的艺术审美裁决权与剪辑自由度完整交还给人类创作者。

---

## 二、 常见问题与工作流目标

| 环节 | 常见做法及问题 | 本工作流的处理方式 | 项目目标 |
| :--- | :--- | :--- | :--- |
| **连续性** | 依赖对话上下文记住人物与场景状态，长流程容易漂移 | 用 `start_state`、`end_state` 等字段记录镜头状态 | 跨镜头一致性问题减少 80% |
| **生产成本** | 未核对任务内容就重试渲染 | `prepare` 先校验和预览，`confirm` 按 SHA-256 指纹确认 | API 渲染损耗降低 65% 以上 |
| **问题定位** | 只凭观感判断问题，难以追到具体环节 | `short-drama-review` 按文件、镜头和证据定位 | 将定位时间缩至秒级 |
| **交付周期** | 文档与素材靠人工搬运整理 | 统一 Markdown 产物，并通过 Adapter 导出生产包 | 单集流程缩至约 0.5 天 |

> 表中的比例和时长是项目目标，不代表已完成的独立基准测试；实际结果需按具体素材、模型和团队流程验证。

---

## 三、 工作流架构

![AIGC 漫剧 / 短剧创作 Agent 工作流](docs/architecture.svg)

---

## 四、 11 个 Skill 职责矩阵

每个 Skill 负责指定的产物；跨阶段问题由审查定位，再交回对应负责人修订：

| 序号 | Skill 名称 | 核心产品职责 | 主要产出物 | 架构定位 |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `short-drama` | 项目初始化、全局路由、看板展示、制作形态决策 | 项目配置与目录结构 | 横切总控 |
| **2** | `short-drama-novel-analyze` | 拆解长篇小说，提取章节结构与戏剧功能 | 章节索引、功能提取、分集候选 | 前期改编 |
| **3** | `short-drama-develop` | 明确创作约束，建立故事引擎与分集地图 | 创作简报、故事引擎、分集地图 | 创作设定 |
| **4** | `short-drama-write` | 编写与修订单集剧本正文 | `剧本.md` | 核心文本 |
| **5** | `short-drama-assets` | 从剧本抽取人物、场景、道具并分配唯一 ID | `视觉设定.md` | 资产契约 |
| **6** | `short-drama-image-prompts` | 提取资产特征，生成 Lookdev 资产提示词 | `图片提示词.md` | 静态渲染 |
| **7** | `short-drama-storyboard` | 设计镜头语言，冻结关键帧连续性 | `分镜.md` | 分镜契约 |
| **8** | `short-drama-video-prompts` | 定义运镜、表演幅度、时间线与音频提示词 | `视频提示词.md` | 动态渲染 |
| **9** | `short-drama-produce` | 调度硬闸门生产（图/视频/音频渲染） | `/dist/` 渲染产物 | 生产执行 |
| **10** | `short-drama-review` | 跨阶段独立审查，定位证据链 | 带证据的审查报告 | 横切质检 |
| **11** | `google-flow-manga` | 转换为可逐镜检查的 Google Flow 生产包 | `flow/<EP>/` 生产包 | 出口适配 |

---

## 五、 核心产品机制与工程约束

### 1. 文件系统即唯一真相 (Single Source of Truth)
每集以 5 份 Markdown 文档作为正式创作产物：`剧本.md`、`视觉设定.md`、`图片提示词.md`、`分镜.md` 和 `视频提示词.md`。不另建并行的结构化真相；每份文档由指定 Skill 维护，并由 Git 记录变更。

### 2. 显式状态契约 (Explicit State Contract)
每个分镜镜头必须显式定义：
* `start_state`：画面起始状态
* `end_state`：画面终止状态
* `continuity_delta`：发生的可见变化
* `why_changed`：变化对应的动作解释或设计锁

连续性由文档中的显式状态记录；无法由动作、前镜或设计约束解释的状态变化，会被校验器拦截。

### 3. 四级规则校验与 Fail-Closed 硬门禁
系统引入纯 Python 离线二值化校验脚本，按四级规则进行质量拦截：

| 规则级别 | 含义 | 校验机制 | 阻断权限 |
| :--- | :--- | :--- | :--- |
| `structural_invariant` | 资产 ID 缺失、跨文档引用断裂、状态矛盾 | Python 门禁脚本校验 | **Fail-Closed 阻断**（显式使用 `--allow-unverified` 时可跳过） |
| `reviewed_invariant` | 设定与承诺未兑现 | `short-drama-review` 审查 | 附带证据链判定阻断 |
| `craft_default` | 行业推荐创作做法 | 启发式规则提示 | 创作者说明理由后可覆盖 |
| `taste_option` | 钩子类型、叙事节奏与审美品味 | 人类创作者主观决策 | **绝对不得自动阻断** |

### 4. 创作者确认与单镜 SHA-256 指纹
生产任务按 `job -> prepare -> confirm -> run` 顺序执行，四步不可合并。`prepare` 校验并预览任务；`confirm` 将创作者授权绑定到当前 Prompt 与参数的 SHA-256 指纹；`run` 才启动渲染。

重跑单镜（如 `--rerun --shot 003`）时，只需重新校验该镜头的指纹，不影响其他已确认镜头。

---

## 六、 Lookdev 资产库与图生视频 (I2V) 管线

角色和场景参考图先进入本地资产库，再由分镜与视频提示词引用，供后续渲染保持视觉连续：

1. **Lookdev 资产锚定**：
   `short-drama-assets` 提取 `CHAR-YECHEN` 等唯一资产 ID；`short-drama-image-prompts` 生成资产参考图提示词，渲染后存入 `/assets/lookdev/` 本地资产库。
2. **分镜资产绑定**：
   `short-drama-storyboard` 设计分镜时，直接关联 `/assets/lookdev/` 中的资产 ID 与首帧参考图。
3. **图生视频 (Image-to-Video) 触发**：
   `short-drama-video-prompts` 生成动态运动提示词，Adapter 将资产首帧图与运动 Prompt 组合提交至图生视频模型完成渲染。

---

## 七、 项目目录结构示例

```text
aigc-drama-workflow/
├── config.example.json         # API 密钥与 Adapter 配置模版
├── .gitignore                  # 过滤二进制视频大文件与本地 Token
├── adapters/                   # 跨平台渲染 Adapter 插件
│   ├── base_adapter.py         # Adapter 抽象基类与 SHA-256 指纹校验
│   └── kling_adapter.py        # 可灵 API 示例适配器
├── episodes/EP001/             # 单集生产目录 (唯一真相源)
│   ├── 剧本.md
│   ├── 视觉设定.md
│   ├── 图片提示词.md
│   ├── 分镜.md
│   └── 视频提示词.md
├── assets/lookdev/             # 本地资产库
│   └── CHAR-SHENQING/          # 角色定妆照与参考图
├── skills/                     # 11 个 Agent Skills 根目录
└── dist/EP001/                 # 最终渲染视频片段与成片交付目录
```

---

## 八、 核心产物样例 (Creator-First Artifacts)

### 分镜契约代码块样例 (`分镜.md`)
```markdown
## SHOT-EP001-003 · 破旧仓库冲突升级

- 来源：EP001-SC002
- 时长：7s
- 目的：把冲突从对峙推进到动手前的准备动作
- 关联资产：[CHAR-YECHEN] 叶辰, [LOC-WAREHOUSE] 破旧仓库
- 镜头语言：镜头由特写推向中景，推镜头，仰角 15 度
- 起始状态 (start_state)：叶辰站立于仓库中央，身穿黑色夹克，右手握紧拳头。
- 终止状态 (end_state)：叶辰脱下黑色夹克扔在地上，露出白色 T 恤，眼神充满杀意。
- 状态变化 (continuity_delta)：脱掉黑色夹克，上身服装由黑夹克变为白 T 恤。
- 变化解释 (why_changed)：冲突升级前的准备动作，固定后续镜头的服装状态。
- 声音：[SFX] 布料摩擦声；[BGM] 低音重音打击乐切入。
- 图片提示词项：IMG-YECHEN-SHEET《叶辰造型板》（控制：发型、服装层次、眼神）
- 输入参考图：无
```

### 视频提示词代码块样例 (`视频提示词.md`)
```markdown
## MOTION-EP001-003 · 破旧仓库冲突升级

- 分镜：SHOT-EP001-003
- 生成方式：文生视频
- 图像首帧 (Image Prompt)：无（文生视频）
- 输入参考图：无
- 静态视觉锚点：破旧仓库内景，水泥地面，叶辰立于画面中央，黑色夹克，右手握拳
- 起始状态 (start_state)：叶辰站立于仓库中央，身穿黑色夹克，右手握紧拳头。
- 状态变化 (continuity_delta)：脱下黑色夹克扔在地上，露出白色 T 恤。
- 终止状态 (end_state)：白色 T 恤，眼神充满杀意。
- 运镜：由特写推向中景，仰角 15 度。
- 运动描述 (Video Motion)：Slow zoom-out from close-up to medium shot, low angle 15 degrees; the man throws his black jacket to the ground, revealing a white t-shirt, angry expression.
- 声音：[SFX] 衣服快速摩擦丢弃声；[BGM] 低音重音打击乐切入。
- 音效打点 (SFX Timeline)：00:01.5 SFX-CLOTH-SWISH 衣服快速摩擦丢弃声；00:02.5 BGM-TENSION-BEAT 低音重音打击乐切入。
- 配音台词 (Audio TTS)：00:00.0 CHAR-YECHEN [愤怒]「你以为你能走得了吗？」；00:02.0 说完。
```

---

## 九、 生产 Adapter 与无平台绑定设计

`short-drama-produce` 不绑定单一渲染平台。Adapter 将统一的提示词和参数转换为已配置平台所需的请求；每个平台支持的能力由对应 Adapter 决定：

```text
                                  ┌──> kling_adapter.py (可灵 API)
                                  ├──> midjourney_adapter.py (Midjourney API)
[通用提示词 JSON] ──> Adapter 路由 ├──> comfyui_adapter.py (本地 ComfyUI)
                                  └──> flow_manga_adapter.py (Google Flow 包)
```

---

## 十、 系统 SLA 与自动化检验

### 工程指标目标

以下是项目设定的目标值，需在实际环境与工作负载下验证。

* **Python 离线门禁拦截耗时**：`< 80ms`
* **系统运行内存峰值占用**：`< 45MB`
* **LLM Context Window 节约**：相比全量上下文盲推降低 `92%` 的 Token 消耗

### 自动化二值检验脚本
```bash
# 1. 跨文档结构完整性与 ID 引用一致性校验
python skills/short-drama/scripts/creator_markdown_check.py episodes/EP001

# 2. Flow 生产包连续性与结构检验
python skills/google-flow-manga/scripts/validate_flow_package.py flow/EP001/flow-package.json
```

---

## 开源许可与参考
项目遵循 [MIT License](LICENSE)。
