# adapters/

平台 Adapter 的实现目录。生产环节与具体平台解耦，`short-drama-produce` 只认
[adapter 契约](../skills/short-drama-produce/references/adapter-contract.md)，
不认任何供应商 SDK。

## 文件

| 文件 | 作用 |
|---|---|
| `base_adapter.py` | 抽象基类。校验确认文档、重算 SHA-256 执行指纹、提供 HTTP / 轮询 / 下载 / 落盘基元 |
| `kling_adapter.py` | 可灵（Kling AI）实现。文生视频、图生视频、文生图，JWT 鉴权 + 异步轮询 |

## 接线

在项目**之外**放一份 adapter 配置，内容照抄 [`config.example.json`](../config.example.json)：

```json
{
  "adapters": {
    "kling-video": {
      "command": ["python3", "adapters/kling_adapter.py"],
      "timeout_seconds": 1800
    }
  }
}
```

job 的 `adapter` 字段填 `kling-video`，`production_tool.py run` 就会把这个 profile 拉起来：

```bash
python3 skills/short-drama-produce/scripts/production_tool.py run <project> \
  --job-id EP001-MOTION001-video \
  --adapter-config /path/outside/project/adapter-config.json
```

## 凭据

Adapter 只从环境变量读凭据，配置文件、job 文件、运行记录里都不会出现密钥。

| 变量 | 说明 |
|---|---|
| `KLING_ACCESS_KEY` / `KLING_SECRET_KEY` | 必填，可灵开放平台的密钥对 |
| `KLING_MODEL` | 必填，模型名，例如 `kling-v3` |
| `KLING_MODE` | 可选，`std` 或 `pro` |
| `KLING_ALLOWED_RATIOS` | 可选，允许的画面比例，逗号分隔 |
| `KLING_ALLOWED_DURATIONS` | 可选，允许的时长，默认 `5,10` |
| `KLING_POLL_INTERVAL` / `KLING_TIMEOUT_SECONDS` | 可选，轮询间隔与总超时 |

## 自检

两个文件都不联网即可自检：

```bash
python3 adapters/kling_adapter.py --selftest
```

覆盖鉴权签名、请求体编译、参考图 Base64 内联、响应状态解析、指纹失效与
fail-closed 分支。

## 新增一个平台

继承 `BaseAdapter`，实现两个方法就够了：

```python
class YourAdapter(BaseAdapter):
    provider = "your-platform"
    modalities = frozenset({"video"})

    def create_task(self) -> ProviderTask:
        """提交任务，返回供应商侧的状态对象。"""

    def fetch_task(self, task: ProviderTask) -> ProviderTask:
        """查询任务状态。"""
```

`execute()` 由基类提供：提交 → 轮询 → 下载 → 写进 `output_root`。失败上报的字段
白名单、产物媒体签名校验、`output_root` 直接子文件约束也都在基类里，不需要重写。
