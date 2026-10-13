# 安装说明

## 运行要求

| 项 | 要求 |
|---|---|
| Python | **3.13 或更高** |
| 依赖 | 无第三方依赖，仅使用标准库 |
| 平台 | Windows / macOS / Linux |

> **为什么需要 3.13**：校验脚本使用了 `Path.is_dir(follow_symlinks=)`、`Path.is_file(follow_symlinks=)` 等 Python 3.13 才引入的 API。3.9 / 3.12 会在运行时报 `TypeError: is_dir() got an unexpected keyword argument 'follow_symlinks'`。
>
> `Path.stat(follow_symlinks=)` 需要 3.10+，`is_dir` / `is_file` 的同名参数需要 3.13+，因此实际门槛是 3.13。

检查版本：

```bash
python3 --version    # 应输出 Python 3.13.x 或更高
```

---

## 通用安装

本工作流基于 [Agent Skills 标准](https://agentskills.io/specification) 实现。任何支持该标准的宿主，只需把 `skills/` 下的目录拷进对应 skill 目录即可。

```bash
git clone https://github.com/jjjjjinshan/aigc-drama-workflow.git
cd aigc-drama-workflow
```

下面按宿主说明。

---

## pi

pi 从以下位置加载 Skill：

- 全局：`~/.pi/agent/skills/`、`~/.agents/skills/`
- 项目：`.pi/skills/`、`.agents/skills/`

### 方式一：复制到全局目录

```bash
mkdir -p ~/.pi/agent/skills
cp -r skills/* ~/.pi/agent/skills/
```

### 方式二：在设置里指向本仓库（推荐，便于更新）

编辑 `~/.pi/agent/settings.json`：

```json
{
  "skills": [
    "/absolute/path/to/aigc-drama-workflow/skills"
  ]
}
```

这样 `git pull` 后无需重新复制。

### 方式三：临时加载

```bash
pi --skill /absolute/path/to/aigc-drama-workflow/skills/short-drama
```

`--skill` 可重复使用，是叠加式的。

---

## Codex

Codex 从 `~/.codex/skills/` 加载：

```bash
mkdir -p ~/.codex/skills
cp -r skills/* ~/.codex/skills/
```

---

## Claude Code

Claude Code 从 `~/.claude/skills/` 加载：

```bash
mkdir -p ~/.claude/skills
cp -r skills/* ~/.claude/skills/
```

若宿主支持在配置里引用其他目录，也可以直接指向本仓库的 `skills/` 路径，避免复制。

---

## 验证安装

每个 Skill 都自带离线自检脚本。以 `short-drama` 为例：

```bash
cd skills/short-drama
python3 scripts/selftest.py
# 期望输出：17 self-tests passed
```

批量验证全部 11 个：

```bash
for d in skills/*/; do
  [ -f "$d/scripts/selftest.py" ] || continue
  printf "%-34s " "$(basename "$d")"
  (cd "$d" && python3 scripts/selftest.py >/dev/null 2>&1 && echo PASS || echo FAIL)
done
```

全部通过即安装成功。

---

## Windows 注意事项

### 没有 `python3` 命令

Windows 的官方 Python 安装包通常只提供 `python.exe` 和 `py.exe`，**不提供 `python3.exe`**。而 Skill 文档里的命令统一写作 `python3`。

三种解法，任选其一：

1. **用 `py -3` 代替** —— 部分 Skill 文档已注明这一点
2. **建立别名** —— 在 PowerShell 配置里加：
   ```powershell
   Set-Alias python3 python
   ```
3. **复制一份可执行文件** —— 把 `python.exe` 复制为同目录下的 `python3.exe`

### Git Bash 路径转换

在 Git Bash 中向 Windows 程序传以 `/` 开头的参数时，MSYS 会尝试做路径转换。例如给安装程序传 `/quiet` 会被改写成 `C:/Program Files/Git/quiet`。

需要时设置：

```bash
MSYS_NO_PATHCONV=1 <command>
```

### 推荐环境

由于本工作流要求 Python 3.13+，若机器上只有较低版本，建议安装官方 Python 3.13 的 **embeddable 版本**（免安装，解压即用）：

1. 从 [python.org](https://www.python.org/downloads/windows/) 下载 `python-3.13.x-embed-amd64.zip`
2. 解压到任意目录，例如 `D:\Programs\Python313`
3. **删除解压目录下的 `python313._pth` 文件**

> 第 3 步是关键。保留 `._pth` 会让 Python 进入 isolated 模式，导致脚本无法导入同目录下的模块（报 `ModuleNotFoundError`）。删除后 Python 恢复标准布局——标准库仍会从同目录的 `python313.zip` 自动加载，脚本目录也能正常进入 `sys.path`。

4. 把该目录加入 `PATH`，并把 `python.exe` 复制为 `python3.exe`

---

## 常见问题

**Q：运行脚本报 `ModuleNotFoundError: No module named 'production_tool'`**

Python 处于 isolated 模式。检查 Python 目录下是否存在 `python3XX._pth` 文件，若存在请删除。

**Q：报 `TypeError: is_dir() got an unexpected keyword argument 'follow_symlinks'`**

Python 版本低于 3.13。请升级。

**Q：需要联网吗？**

Skill 本体不需要。只有生产环节（`short-drama-produce`）会调用你在 adapter 配置里指定的外部服务，凭据由 adapter 自行从环境变量或系统凭据存储读取，**不写入项目**。

**Q：会消耗生成额度吗？**

不会自动消耗。所有付费生成都必须经过四步硬闸门，且需要你在看到预览后明确确认。

---

## 相关文档

- [架构说明](architecture.md)
- [工作流详解](workflow.md)
- [设计原则](design-principles.md)
