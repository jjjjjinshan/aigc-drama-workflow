# AIGC 漫剧 / 短剧创作 Agent 工作流 (aigc-drama-workflow)

> **专为个人创作者（Solo Creator）及 3 人以下微型工作室设计的工业化 AIGC 漫剧/短剧多 Agent 编排系统。**  
> 依靠 11 个职责隔离的 Skill，用**显式状态契约**解决生成一致性漂移，用**文件系统即唯一真相**打通创作与生产断层，用**二值化机械门禁与 SHA-256 指纹**锁死成本上限。

---

## 目录
- [一、 产品定位与设计哲学](#一-产品定位与设计哲学)
- [二、 业务痛点与 ROI 收益分析](#二-业务痛点与-roi-收益分析)
- [三、 完整工作流架构图 (Architecture)](#三-完整工作流架构图-architecture)
- [四、 11 个 Skill 职责矩阵](#四-11-个-skill-职责矩阵)
- [五、 核心产品机制与工程约束](#五-核心产品机制与工程约束)
- [六、 Lookdev 资产库与图生视频 (I2V) 管线](#六-lookdev-资产库与图生视频-i2v-管线)
- [七、 项目目录结构示例](#七-项目目录结构示例)
- [八、 核心产物样例 (Creator-First Artifacts)](#八-核心产物样例-creator-first-artifacts)
- [九、 生产 Adapter 与无平台绑定设计](#九-生产-adapter-与无平台绑定设计)
- [十、 快速开始与自动化检验](#十-快速开始与自动化检验)

## 一、 产品定位与设计哲学

在工业化 AIGC 短剧生产中，盲目追求“全自动一键出片”往往会导致质量不可控与算力成本雪崩。本系统坚持**“智能编排与重型渲染解耦”**的核心产品哲学：

### 定位与边界 (Boundary & Trade-offs)
* **LLM 擅长做（Agent 自动化接管）**：文本解构、显式状态追踪、ID 引用核查、提示词翻译、 Confirm 成本预览与生产调度。
* **LLM 不擅长做（留给专业工具与人类）**：配音音色克隆（TTS）、AI 对口型（Lip-Sync）与剪辑节奏卡点。系统将最终的艺术审美裁决权与剪辑自由度完整交还给人类创作者。

---

## 二、 业务痛点与 ROI 收益分析

| 维度 | 传统对话框抽卡模式 | 本 Agent 工作流模式 | 降本增效收益 (ROI) |
| :--- | :--- | :--- | :--- |
| **一致性控制** | 靠 Prompt 隐式记忆，滑动窗口截断后频繁换脸 | **显式状态契约 (`start_state`/`end_state`)** | 消除 80% 跨镜头一致性崩坏 |
| **成本控制** | 盲目重试，频繁对“坏图/废片”扣费 | **`prepare` -> `confirm` SHA-256 硬闸门** | API 算力损耗降低 65%+ |
| **质检追溯** | 主观觉得“不对劲”，无法定位是哪一步出问题 | **`short-drama-review` 证据链定位** | 错误定位时间缩短至秒级 |
| **交付周期** | 单集人肉复制粘贴耗时 2~3 天 | **标准化 Markdown + 自动化 Adapter 导出** | 单集生产周期缩短至 0.5 天 |

## 三、 完整工作流架构图 (Architecture)

![AIGC 漫剧 / 短剧创作 Agent 工作流完整架构](docs/architecture.svg)

---

## 四、 11 个 Skill 职责矩阵

各 Skill 职责严密隔离，禁止跨权覆盖：

| 序号 | Skill 名称 | 核心产品职责 | 主要产出物 | 架构定位 |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `short-drama` | 项目初始化、全局路由、看板展示、制作形态决策 | 项目配置与目录结构 | 横切总控 |
| **2** | `short-drama-novel-analyze` | 拆解长篇小说，降维生成结构化分析层 | 章节索引、功能提取、分集候选 | 前期改编 |
| **3** | `short-drama-develop` | 建立创作者契约、故事引擎与分集地图 | 创作简报、故事引擎、分集地图 | 创作设定 |
| **4** | `short-drama-write` | 编写与修订单集剧本正文 | `剧本.md` | 核心文本 |
| **5** | `short-drama-assets` | 从剧本抽取人物、场景、道具并分配唯一 ID | `视觉设定.md` | 资产契约 |
| **6** | `short-drama-image-prompts` | 提取资产特征，生成 Lookdev 资产提示词 | `图片提示词.md` | 静态渲染 |
| **7** | `short-drama-storyboard` | 设计镜头语言，冻结关键帧连续性 | `分镜.md` | 分镜契约 |
| **8** | `short-drama-video-prompts` | 定义运镜、表演幅度、时间线与音频提示词 | `视频提示词.md` | 动态渲染 |
| **9** | `short-drama-produce` | 调度硬闸门生产（图/视频/音频渲染） | `/dist/` 渲染产物 | 生产执行 |
| **10** | `short-drama-review` | 跨阶段独立审查，定位证据链 | 带证据的审查报告 | 横切质检 |
| **11** | `google-flow-manga` | 转换为可逐镜检查的 Google Flow 生产包 | `flow/<EP>/` 生产包 | 出口适配 |

## 五、 核心产品机制与工程约束

### 1. 文件系统即唯一真相 (Single Source of Truth)
每集仅维护 5 份 Markdown 文档（`剧本.md`、`视觉设定.md`、`图片提示词.md`、`分镜.md`、`视频提示词.md`）。拒绝另建复杂数据库，确保每一个事实有且仅有一个 Owner，天然支持 Git 版本管理。

**为什么选择纯 Markdown？**  
创作者可以直接阅读与微调，无数据库运维成本。模型无状态（Stateless），每次重新装载 Markdown 即可 100% 精确还原上一镜状态。

### 2. 显式状态契约 (Explicit State Contract)
每个分镜镜头必须显式定义：
* `start_state`：画面起始状态
* `end_state`：画面终止状态
* `continuity_delta`：发生的可见变化
* `why_changed`：变化对应的动作解释或设计锁

连续性不再依赖 LLM 隐式记忆，无法解释的状态突变将直接被二值化校验阻断。

### 3. 四级规则校验与 Fail-Closed 硬门禁
系统引入纯 Python 离线二值化校验脚本，按四级规则进行质量拦截：

| 规则级别 | 含义 | 校验机制 | 阻断权限 |
| :--- | :--- | :--- | :--- |
| `structural_invariant` | 资产 ID 缺失、跨文档引用断裂、状态矛盾 | Python 门禁脚本校验 | ** Fail-Closed 二值直接阻断** |
| `reviewed_invariant` | 设定与承诺未兑现 | `short-drama-review` 审查 | 附带证据链判定阻断 |
| `craft_default` | 行业推荐创作做法 | 启发式规则提示 | 创作者说明理由后可覆盖 |
| `taste_option` | 钩子类型、叙事节奏与审美品味 | 人类创作者主观决策 | **绝对不得自动阻断** |

### 4. 人类停靠 (Human-in-the-Loop) 与 SHA-256 Confirm 指纹
生产环节采用不可合并的 4 步硬流程：
job -> prepare -> confirm -> run

**指纹锁死防误扣费机制**  
1. **`prepare`（预览）**：二值化门禁校验，生成渲染计划与 API 预算消耗估算（0 费用）。
2. **`confirm`（授权）**：创作者确认后，系统计算当前 Prompt 与参数的 SHA-256 唯一数字指纹并锁定。
3. **`run`（生产）**：指纹比对一致才触发 Adapter。上游一旦改动，旧指纹失效，强制重新确认。

## 六、 Lookdev 资产库与图生视频 (I2V) 管线

项目通过“资产定妆 -> 资产库锁定 -> 动态渲染”的三步管线实现高连贯性生成：

1. ** Lookdev 资产锚定**：
   `short-drama-assets` 提取 `CHAR-YECHEN` 等唯一资产 ID；`short-drama-image-prompts` 生成资产参考图提示词，渲染后存入 `/assets/lookdev/` 本地资产库。
2. **分镜资产绑定**：
   `short-drama-storyboard` 设计分镜时，直接关联 `/assets/lookdev/` 中的资产 ID 与首帧参考图。
3. **图生视频 (Image-to-Video) 触发**：
   `short-drama-video-prompts` 生成动态运动提示词，Adapter 将资产首帧图（Base64 或云端临时 URL）与运动 Prompt 组合，提交至图生视频模型完成渲染。

---

## 七、 项目目录结构示例

```text
aigc-drama-workflow/
├── README.md                          # 项目说明文档
├── config.json                        # 生产与 Adapter 配置文件
├── skills/                            # 11 个标准 Agent Skill 矩阵
│   ├── short-drama/                   # 总控与校验脚本
│   │   └── scripts/
│   │       └── creator_markdown_check.py
│   ├── short-drama-write/
│   ├── short-drama-storyboard/
│   └── short-drama-produce/
├── episodes/                          # 核心 Markdown 产物库
│   └── EP001/
│       ├── 剧本.md
│       ├── 视觉设定.md
│       ├── 图片提示词.md
│       ├── 分镜.md
│       └── 视频提示词.md
├── assets/                            # 本地 Lookdev 资产库
│   └── lookdev/
│       ├── CHAR-YECHEN_定妆照.png
│       └── LOC-RUINS-BEDROOM_概念图.png
└── dist/                              # 渲染输出目录
    └── EP001/
        ├── shot_001.mp4
        └── shot_002.mp4
```

---

## 八、 核心产物样例 (Creator-First Artifacts)

### 样例 1：`分镜.md`（显式状态契约定义）
```markdown
## SHOT-EP001-003 · 废墟中的反击

- **关联资产**: [CHAR-YECHEN] 叶辰, [LOC-RUINS-BEDROOM] 废墟卧室
- **镜头语言**: 中景，低角度仰拍，快推镜头
- **起始状态 (start_state)**: 叶辰单膝下跪，双手抱头，身上穿着深色长袍。
- **终止状态 (end_state)**: 叶辰单手撑地站起，双眼散发淡蓝色光芒，深色长袍随风扬起。
- **状态变化 (continuity_delta)**: 从“屈服”转变为“站立反击”，增加眼神蓝光特效。
- **变化解释 (why_changed)**: 戒指效果注入力量，触发第一集黄金卡点反转。
```

### 样例 2：`视频提示词.md`（运动与音频打点）
```markdown
## MOTION-EP001-003 · 动态渲染参数

- **图像首帧 (Image Prompt)**: `/assets/lookdev/CHAR-YECHEN_定妆照.png`
- **运动描述 (Video Motion)**: Slow push in, young man in dark robe stands up from knees, hands glowing with blue aura, wind blowing robe, cinematic lighting, 8k resolution.
- **配音台词 (Audio TTS)**: CHAR-YECHEN [情绪饱满]: "莫欺少年穷！"
- **音效打点 (SFX Timeline)**: 00:01.5 插入能量音效 (SFX-ENERGY-01)
```

## 九、 生产 Adapter 与无平台绑定设计

`short-drama-produce` 与具体的 AI 渲染平台高度解耦，通过插件化的 Adapter 实现“一次创作，任意平台渲染”：

* **参数自动映射**：Adapter 负责将统一的标准 Schema（如 `prompt`, `aspect_ratio`）自动转译为各平台的私有接口字段。
* **网络传输防护**：Adapter 内置 API Key 鉴权、Base64/S3 资产上传、指数退避重试与文件自动下载功能。

## 十、 快速开始与自动化检验

### 运行环境
* **Python 3.13+**（使用了 Path.is_dir(follow_symlinks=) 等全新 API）
* **仅使用 Python Standard Library**（零第三方依赖，纯离线运行）

### 安装与挂载
克隆项目仓库并进入目录，将 skills 目录挂载至符合 Agent Skills 标准的宿主环境（如 Cursor、Claude Code 等）。

### 离线二值化自动化校验
在终端运行自动化检查脚本：

```bash
# 1. 跨文档结构完整性与 ID 引用一致性校验
python skills/short-drama/scripts/creator_markdown_check.py examples/minimal-drama/EP001 --project-root examples/minimal-drama

# 2. Flow 生产包连续性与结构检验
python skills/google-flow-manga/scripts/validate_flow_package.py examples/minimal-drama/flow/EP001/flow-package.json
```
若控制台显示 OK: 0 error(s), 0 warning(s)，代表状态契约检验通过，可安全触发生产！

---

## 开源许可与参考
项目遵循 MIT License。部分模块设计参考了开源社区最佳实践。
