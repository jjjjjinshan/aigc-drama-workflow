#!/usr/bin/env python3
"""短剧生产的 Adapter 抽象基类。

平台 Adapter 的契约只有三条：

1. 从 stdin 读一份 UTF-8 JSON 确认文档（`adapter-contract.md` 定义的 job，
   外加 `run_id` / `project_root` / `output_root`）；
2. 把结果写成 `output_root` 下的普通文件，再把 ``{"outputs": [...], "provider_job_id": ...}``
   写到 stdout；
3. 失败时只输出白名单字段，绝不回传供应商响应体、提示词、路径或凭据。

本模块把三件事做成可复用的基类，平台适配器只需要实现"提交任务"和"查询任务"两步：

- :class:`ProductionJob` —— 校验确认文档，解析参考图绑定，重算执行指纹；
- :class:`ProviderTask` —— 异步任务的通用状态机（排队 / 生成中 / 成功 / 失败）；
- :class:`BaseAdapter` —— 提交、轮询、下载、落盘的模板方法，以及 stdin/stdout 主循环。

关于 SHA-256 指纹：``prepare`` 阶段把 ``ALLOWED_JOB_KEYS`` 加输入文件字节哈希算成一个
64 位十六进制指纹。Adapter 拿到的 `project_root` 是本次运行的私有快照，因此可以按同样
的规范化方式重算这个指纹，确认"将要付费提交的字节"就是创作者确认过的那一份。重算结果
用于绑定供应商侧的任务标识；若调用方在文档里额外带上了 `fingerprint`，则做严格比对，
不一致直接 fail closed。

只使用 Python 标准库。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Optional

MINIMUM_PYTHON = (3, 9)
if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "base_adapter.py requires Python {}.{}, running {}.{}".format(
            *MINIMUM_PYTHON, sys.version_info.major, sys.version_info.minor
        )
    )

JOB_SCHEMA = "1.0"
MODALITIES = ("image", "video", "tts", "music")

#: ``prepare`` 落盘指纹时使用的键集合，重算指纹必须逐字对齐。
EXECUTION_KEYS = (
    "schema_version",
    "job_id",
    "modality",
    "adapter",
    "prompt",
    "source",
    "source_entry",
    "references",
    "reference_bindings",
    "outputs",
    "parameters",
    "overwrite",
)

MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".mov", ".webm"},
    "tts": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".opus"},
    "music": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".opus"},
}

MEDIA_SIGNATURES = {
    ".png": lambda head: head.startswith(b"\x89PNG\r\n\x1a\n"),
    ".jpg": lambda head: head.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda head: head.startswith(b"\xff\xd8\xff"),
    ".webp": lambda head: len(head) >= 12
    and head.startswith(b"RIFF")
    and head[8:12] == b"WEBP",
    ".mp3": lambda head: head.startswith(b"ID3")
    or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0),
    ".wav": lambda head: len(head) >= 12
    and head.startswith(b"RIFF")
    and head[8:12] == b"WAVE",
    ".mp4": lambda head: len(head) >= 12 and head[4:8] == b"ftyp",
    ".mov": lambda head: len(head) >= 12 and head[4:8] in {b"ftyp", b"moov", b"mdat"},
    ".webm": lambda head: head.startswith(b"\x1a\x45\xdf\xa3"),
}

SECRET_KEYS = {
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "access_token",
    "api_key",
    "apikey",
}

BINDING_KEYS = {
    "slot_id",
    "order",
    "path",
    "label",
    "role",
    "may_control",
    "must_not_control",
}

MAX_JSON_RESPONSE = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_REFERENCE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_REFERENCE_BYTES = 200 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_JSON_REQUEST = 64 * 1024 * 1024
MAX_REFERENCE_BINDINGS = 16
HTTP_TIMEOUT_SECONDS = 180


class AdapterFailure(RuntimeError):
    """可以安全上报的失败：不带供应商响应体、提示词、路径或凭据。"""

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_response",
        code: str = "adapter_failure",
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.code = safe_token(code) or "adapter_failure"
        self.http_status = http_status
        self.request_id = safe_token(request_id)
        self.retryable = retryable

    def public(self, provider: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "provider": provider,
            "category": self.category,
            "code": self.code,
            "retryable": self.retryable,
        }
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.request_id is not None:
            result["request_id"] = self.request_id
        return result


def safe_token(value: object) -> Optional[str]:
    """只保留可安全回传的短标识，其余一律丢弃。"""
    if isinstance(value, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value
    ):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def canonical(document: object) -> bytes:
    """与 ``production_tool._canonical`` 完全一致的规范化 JSON。"""
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_REFERENCE_BYTES:
                    raise ValueError("参考文件超出体积上限")
                digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _is_regular_file(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(details.st_mode) and not stat.S_ISLNK(details.st_mode)


def _portable_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if re.match(r"^[A-Za-z]:", value):
        return False
    parts = value.split("/")
    return not PurePosixPath(value).is_absolute() and not any(
        part in {"", ".", ".."} for part in parts
    )


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in SECRET_KEYS or _contains_secret_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_key(child) for child in value)
    return False


def _string_list(value: object, *, label: str, limit: int) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AdapterFailure(
            f"{label} 必须是字符串数组",
            category="contract",
            code="invalid_job",
        )
    if len(value) > limit:
        raise AdapterFailure(
            f"{label} 条目过多",
            category="contract",
            code="invalid_job",
        )
    return list(value)


def _scopes(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AdapterFailure(
            f"{label} 必须是非空数组",
            category="contract",
            code="invalid_job",
        )
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise AdapterFailure(
            f"{label} 必须是非空字符串数组",
            category="contract",
            code="invalid_job",
        )
    return [item.strip() for item in value]


class ProductionJob:
    """一份已确认的生产任务，附带运行时上下文。

    校验范围与 ``adapter-contract.md`` 对齐：modality、输出路径与扩展名、
    参考图绑定语义、参数不得夹带凭据、运行时目录必须是普通目录。
    """

    def __init__(self, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise AdapterFailure(
                "adapter 输入必须是 JSON 对象",
                category="invalid_request",
                code="invalid_job",
            )
        self.raw: dict[str, Any] = dict(payload)
        self.schema_version = self._text("schema_version")
        self.job_id = self._text("job_id")
        self.modality = self._text("modality")
        self.adapter = self._text("adapter")
        self.prompt = self._text("prompt")
        if self.modality not in MODALITIES:
            raise AdapterFailure(
                "不支持的 modality",
                category="contract",
                code="invalid_modality",
            )
        if self.schema_version != JOB_SCHEMA:
            raise AdapterFailure(
                "不支持的 job schema_version",
                category="contract",
                code="invalid_schema",
            )
        self.source = self._optional_text("source")
        self.source_entry = self._optional_text("source_entry")
        self.run_id = self._text("run_id")
        self.overwrite = self.raw.get("overwrite", False)
        if not isinstance(self.overwrite, bool):
            raise AdapterFailure(
                "overwrite 必须是布尔值",
                category="contract",
                code="invalid_job",
            )
        self.parameters = self.raw.get("parameters", {})
        if not isinstance(self.parameters, Mapping):
            raise AdapterFailure(
                "parameters 必须是对象",
                category="contract",
                code="invalid_job",
            )
        if _contains_secret_key(self.parameters):
            raise AdapterFailure(
                "parameters 不得携带凭据",
                category="contract",
                code="secret_in_parameters",
            )
        self.parameters = dict(self.parameters)
        self.outputs = self._outputs()
        self.references = _string_list(
            self.raw.get("references", []), label="references", limit=MAX_REFERENCE_BINDINGS
        )
        for reference in self.references:
            if not _portable_relative(reference):
                raise AdapterFailure(
                    "references 必须是项目相对路径",
                    category="contract",
                    code="invalid_reference_path",
                )
        if len(set(self.references)) != len(self.references):
            raise AdapterFailure(
                "references 存在重复路径",
                category="contract",
                code="invalid_reference_path",
            )
        self.reference_bindings = self._bindings()
        self.project_root = self._runtime_directory("project_root")
        self.output_root = self._runtime_directory("output_root")

    # ---- 字段读取 ----

    def _text(self, key: str) -> str:
        value = self.raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise AdapterFailure(
                f"job 缺少 {key}",
                category="contract",
                code="invalid_job",
            )
        return value.strip()

    def _optional_text(self, key: str) -> Optional[str]:
        value = self.raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise AdapterFailure(
                f"{key} 必须是非空字符串",
                category="contract",
                code="invalid_job",
            )
        return value.strip()

    def _outputs(self) -> list[str]:
        outputs = _string_list(self.raw.get("outputs"), label="outputs", limit=MAX_REFERENCE_BINDINGS)
        if not outputs or len(set(outputs)) != len(outputs):
            raise AdapterFailure(
                "outputs 必须唯一且非空",
                category="contract",
                code="invalid_outputs",
            )
        allowed = MEDIA_EXTENSIONS[self.modality]
        for output in outputs:
            if not _portable_relative(output):
                raise AdapterFailure(
                    "outputs 必须是项目相对路径",
                    category="contract",
                    code="invalid_outputs",
                )
            if PurePosixPath(output).suffix.casefold() not in allowed:
                raise AdapterFailure(
                    "outputs 扩展名与 modality 不匹配",
                    category="contract",
                    code="invalid_outputs",
                )
        return outputs

    def _bindings(self) -> list[dict[str, Any]]:
        raw = self.raw.get("reference_bindings", [])
        if raw in (None, []):
            return []
        if not isinstance(raw, list) or len(raw) > MAX_REFERENCE_BINDINGS:
            raise AdapterFailure(
                "reference_bindings 必须是数组",
                category="contract",
                code="invalid_bindings",
            )
        if len(raw) != len(self.references):
            raise AdapterFailure(
                "reference_bindings 必须与 references 等长",
                category="contract",
                code="invalid_bindings",
            )
        bindings: list[dict[str, Any]] = []
        for index, entry in enumerate(raw, 1):
            if not isinstance(entry, Mapping) or set(entry) != BINDING_KEYS:
                raise AdapterFailure(
                    "reference_bindings 字段不完整",
                    category="contract",
                    code="invalid_bindings",
                )
            if entry.get("order") != index:
                raise AdapterFailure(
                    "reference_bindings 顺序必须从 1 连续编号",
                    category="contract",
                    code="invalid_bindings",
                )
            if entry.get("path") != self.references[index - 1]:
                raise AdapterFailure(
                    "reference_bindings 路径与 references 不一致",
                    category="contract",
                    code="invalid_bindings",
                )
            label = entry.get("label")
            role = entry.get("role")
            slot_id = entry.get("slot_id")
            if not isinstance(label, str) or not re.search(r"[\u4e00-\u9fff]", label):
                raise AdapterFailure(
                    "reference_bindings 缺少中文名称",
                    category="contract",
                    code="invalid_bindings",
                )
            if not isinstance(role, str) or not role.strip():
                raise AdapterFailure(
                    "reference_bindings 缺少 role",
                    category="contract",
                    code="invalid_bindings",
                )
            if not isinstance(slot_id, str) or not slot_id.strip():
                raise AdapterFailure(
                    "reference_bindings 缺少 slot_id",
                    category="contract",
                    code="invalid_bindings",
                )
            may_control = _scopes(entry.get("may_control"), label="may_control")
            must_not_control = _scopes(
                entry.get("must_not_control"), label="must_not_control"
            )
            if set(may_control) & set(must_not_control):
                raise AdapterFailure(
                    "reference_bindings 的控制范围冲突",
                    category="contract",
                    code="invalid_bindings",
                )
            bindings.append(
                {
                    "slot_id": slot_id.strip(),
                    "order": index,
                    "path": entry["path"],
                    "label": label.strip(),
                    "role": role.strip(),
                    "may_control": may_control,
                    "must_not_control": must_not_control,
                }
            )
        return bindings

    def _runtime_directory(self, key: str) -> Path:
        value = self.raw.get(key)
        if not isinstance(value, str):
            raise AdapterFailure(
                f"job 缺少 {key}",
                category="invalid_request",
                code="invalid_job",
            )
        path = Path(value)
        if not path.is_absolute():
            raise AdapterFailure(
                f"{key} 必须是绝对路径",
                category="invalid_request",
                code="invalid_job",
            )
        try:
            details = path.lstat()
        except OSError as exc:
            raise AdapterFailure(
                f"{key} 不可访问",
                category="invalid_request",
                code="invalid_job",
            ) from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise AdapterFailure(
                f"{key} 必须是普通目录",
                category="invalid_request",
                code="invalid_job",
            )
        return path

    # ---- 派生数据 ----

    def reference_paths(self) -> list[Path]:
        """把 references 解析成快照内的真实文件，拒绝符号链接与越界路径。"""
        root = self.project_root.resolve()
        result: list[Path] = []
        for reference in self.references:
            candidate = (root / reference).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise AdapterFailure(
                    "参考图路径越出 project_root",
                    category="contract",
                    code="reference_escapes_root",
                ) from exc
            if not _is_regular_file(candidate):
                raise AdapterFailure(
                    "参考图不是普通文件",
                    category="contract",
                    code="reference_not_regular_file",
                )
            result.append(candidate)
        return result

    def read_reference(self, index: int) -> bytes:
        path = self.reference_paths()[index]
        before = path.lstat()
        if before.st_size > MAX_REFERENCE_BYTES:
            raise AdapterFailure(
                "参考图超出体积上限",
                category="contract",
                code="reference_too_large",
            )
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise AdapterFailure(
                    "参考图在打开过程中发生变化",
                    category="contract",
                    code="reference_changed",
                )
            chunks: list[bytes] = []
            size = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                size += len(chunk)
                if size > MAX_REFERENCE_BYTES:
                    raise AdapterFailure(
                        "参考图超出体积上限",
                        category="contract",
                        code="reference_too_large",
                    )
                chunks.append(chunk)
        finally:
            os.close(descriptor)
        return b"".join(chunks)

    def reference_base64(self, index: int) -> str:
        """参考图的原始 Base64（不带 data: 前缀），供接受内联图片的平台使用。"""
        return base64.b64encode(self.read_reference(index)).decode("ascii")

    def reference_contract(self) -> str:
        """把中文名称、role、可控制 / 不得控制范围渲染成确定性文本。

        ``adapter-contract.md`` 要求外部 adapter 保留等价语义，或直接拒绝任务；
        把参考图降级成一串无标签文件名是不允许的。
        """
        if not self.reference_bindings:
            return ""
        lines = []
        for binding in self.reference_bindings:
            lines.append(
                "Reference image {order} ({label}), role {role}. "
                "May control: {may}. Must not control: {must}.".format(
                    order=binding["order"],
                    label=binding["label"],
                    role=binding["role"],
                    may=", ".join(binding["may_control"]),
                    must=", ".join(binding["must_not_control"]),
                )
            )
        return "\n".join(lines)

    def provider_prompt(self) -> str:
        contract = self.reference_contract()
        if not contract:
            return self.prompt
        return f"{self.prompt}\n\nReference contract:\n{contract}"

    def input_hashes(self) -> dict[str, str]:
        """按 ``prepare`` 的规则哈希 source 与 references。"""
        root = self.project_root.resolve()
        inputs = ([self.source] if self.source else []) + self.references
        hashes: dict[str, str] = {}
        for relative in inputs:
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise AdapterFailure(
                    "输入文件越出 project_root",
                    category="contract",
                    code="input_escapes_root",
                ) from exc
            if not _is_regular_file(path):
                raise AdapterFailure(
                    "输入文件不是普通文件",
                    category="contract",
                    code="input_not_regular_file",
                )
            hashes[relative] = hash_file(path)
        return hashes

    def fingerprint(self) -> str:
        """重算本次确认的 SHA-256 执行指纹。

        规范化方式与 ``production_tool`` 落盘时一致：取 ``EXECUTION_KEYS``
        子集，补上输入文件字节哈希，再做 sort_keys 紧凑 JSON 序列化。
        """
        execution = {
            key: self.raw[key] for key in EXECUTION_KEYS if key in self.raw
        }
        execution["inputs"] = self.input_hashes()
        return sha256_bytes(canonical(execution))

    def verify_fingerprint(self) -> str:
        """返回本次运行的指纹；调用方若提供了期望值则做严格比对。"""
        fingerprint = self.fingerprint()
        expected = self.raw.get("fingerprint")
        if expected is None:
            return fingerprint
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise AdapterFailure(
                "fingerprint 字段格式非法",
                category="contract",
                code="invalid_fingerprint",
            )
        if expected != fingerprint:
            raise AdapterFailure(
                "输入字节与已确认指纹不一致，拒绝提交付费任务",
                category="contract",
                code="fingerprint_mismatch",
            )
        return fingerprint

    def external_task_id(self, fingerprint: str) -> str:
        """由指纹派生的确定性任务标识，供支持 external_task_id 的供应商做幂等键。"""
        return f"sd-{fingerprint[:32]}"

    def take_parameters(self, allowed: Collection[str]) -> dict[str, Any]:
        unknown = set(self.parameters) - set(allowed)
        if unknown:
            raise AdapterFailure(
                "平台不支持这些参数：" + "、".join(sorted(unknown)),
                category="contract",
                code="unsupported_parameters",
            )
        return dict(self.parameters)


class ProviderTask:
    """异步任务的通用状态机。"""

    PENDING_STATES = frozenset({"submitted", "queued", "pending", "processing", "running"})
    SUCCESS_STATES = frozenset({"succeed", "succeeded", "success", "completed"})
    FAILURE_STATES = frozenset({"failed", "cancelled", "canceled", "timeout", "expired"})

    def __init__(
        self,
        provider: str,
        task_id: str,
        status: str,
        *,
        result_urls: Sequence[str] = (),
        request_id: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.task_id = task_id
        self.status = status
        self.result_urls = list(result_urls)
        self.request_id = request_id

    @property
    def is_pending(self) -> bool:
        return self.status.casefold() in self.PENDING_STATES

    @property
    def is_success(self) -> bool:
        return self.status.casefold() in self.SUCCESS_STATES

    @property
    def is_failure(self) -> bool:
        return self.status.casefold() in self.FAILURE_STATES

    def failure(self) -> AdapterFailure:
        return AdapterFailure(
            f"{self.provider} 任务失败",
            category="provider_response",
            code="task_" + self.status.casefold(),
            request_id=self.request_id or self.task_id,
        )

    def __repr__(self) -> str:  # pragma: no cover - 便于排查
        return f"<ProviderTask {self.provider} {self.task_id} {self.status}>"


class BaseAdapter(ABC):
    """平台适配器的模板方法骨架。

    子类只需要实现 :meth:`create_task` 与 :meth:`fetch_task`；提交、轮询、
    下载、落盘、错误上报都由基类统一处理，保证各平台的对外行为一致。
    """

    #: 供应商标识，用于失败上报与 `config.json` 的 profile 命名。
    provider: str = ""
    #: 本适配器支持的 modality。
    modalities: frozenset[str] = frozenset()
    #: 轮询间隔（秒），可用环境变量覆盖。
    poll_interval_env: str = ""
    timeout_env: str = ""
    default_poll_interval: float = 5.0
    default_timeout_seconds: float = 1800.0

    def __init__(self, job: ProductionJob) -> None:
        if job.modality not in self.modalities:
            raise AdapterFailure(
                f"{self.provider} 不支持 {job.modality}",
                category="contract",
                code="unsupported_modality",
            )
        self.job = job
        self.fingerprint = job.verify_fingerprint()

    # ---- 子类接口 ----

    @abstractmethod
    def create_task(self) -> ProviderTask:
        """提交一次生成任务，返回供应商侧的任务状态。"""

    @abstractmethod
    def fetch_task(self, task: ProviderTask) -> ProviderTask:
        """查询任务状态，返回更新后的任务对象。"""

    # ---- 模板方法 ----

    def polling_profile(self) -> tuple[float, float]:
        interval = self.default_poll_interval
        timeout = self.default_timeout_seconds
        if self.poll_interval_env:
            raw = os.environ.get(self.poll_interval_env)
            if raw:
                try:
                    interval = float(raw)
                except ValueError as exc:
                    raise AdapterFailure(
                        "轮询间隔配置非法",
                        category="configuration",
                        code="invalid_poll_profile",
                    ) from exc
        if self.timeout_env:
            raw = os.environ.get(self.timeout_env)
            if raw:
                try:
                    timeout = float(raw)
                except ValueError as exc:
                    raise AdapterFailure(
                        "轮询超时配置非法",
                        category="configuration",
                        code="invalid_poll_profile",
                    ) from exc
        if interval <= 0 or timeout <= 0:
            raise AdapterFailure(
                "轮询配置必须为正数",
                category="configuration",
                code="invalid_poll_profile",
            )
        return interval, timeout

    def execute(self) -> tuple[Path, Optional[str]]:
        """提交 → 轮询 → 下载 → 落盘，返回（暂存文件路径, 供应商任务 ID）。"""
        task = self.create_task()
        interval, timeout = self.polling_profile()
        deadline = time.monotonic() + timeout
        while True:
            if task.is_success:
                return self._collect(task)
            if task.is_failure:
                raise task.failure()
            if not task.is_pending:
                raise AdapterFailure(
                    f"{self.provider} 返回了未知任务状态",
                    category="provider_response",
                    code="unknown_task_status",
                    request_id=task.request_id or task.task_id,
                )
            if time.monotonic() >= deadline:
                raise AdapterFailure(
                    f"{self.provider} 任务轮询超时",
                    category="timeout",
                    code="task_poll_timeout",
                    request_id=task.request_id or task.task_id,
                    retryable=True,
                )
            time.sleep(interval)
            task = self.fetch_task(task)

    def _collect(self, task: ProviderTask) -> tuple[Path, Optional[str]]:
        if len(task.result_urls) != 1:
            raise AdapterFailure(
                f"{self.provider} 未返回唯一的生成结果",
                category="provider_response",
                code="unexpected_result_count",
                request_id=task.request_id or task.task_id,
            )
        path = self.download(task.result_urls[0], self.job.outputs[0])
        return path, task.task_id

    # ---- 可复用基元 ----

    def credential(self, *names: str) -> str:
        for name in names:
            value = os.environ.get(name)
            if value:
                return value
        raise AdapterFailure(
            "未配置供应商凭据",
            category="configuration",
            code="missing_credential",
        )

    def base_url(self, env_name: str, default: str) -> str:
        value = os.environ.get(env_name, default).rstrip("/")
        parsed = urllib.parse.urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise AdapterFailure(
                "供应商 base URL 非法",
                category="configuration",
                code="invalid_base_url",
            )
        return value

    def request_json(
        self,
        url: str,
        *,
        method: str = "POST",
        body: Optional[Mapping[str, Any]] = None,
        token: Optional[str] = None,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> tuple[dict[str, Any], Mapping[str, str]]:
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if len(data) > MAX_JSON_REQUEST:
                raise AdapterFailure(
                    "请求体超出体积上限",
                    category="contract",
                    code="request_too_large",
                )
        request = urllib.request.Request(url, data=data, method=method)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_JSON_RESPONSE + 1)
                headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raise self.http_failure(exc) from exc
        except TimeoutError as exc:
            raise AdapterFailure(
                f"{self.provider} 请求超时",
                category="timeout",
                code="request_timeout",
                retryable=True,
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AdapterFailure(
                f"{self.provider} 请求失败",
                category="network",
                code="network_error",
                retryable=True,
            ) from exc
        if len(raw) > MAX_JSON_RESPONSE:
            raise AdapterFailure(
                f"{self.provider} 响应过大",
                category="provider_response",
                code="response_too_large",
            )
        try:
            document = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdapterFailure(
                f"{self.provider} 返回了非法 JSON",
                category="provider_response",
                code="invalid_json",
            ) from exc
        if not isinstance(document, dict):
            raise AdapterFailure(
                f"{self.provider} 返回了非对象响应",
                category="provider_response",
                code="invalid_response",
            )
        return document, headers

    def http_failure(self, error: urllib.error.HTTPError) -> AdapterFailure:
        status = error.code
        try:
            raw = error.read(MAX_ERROR_BODY_BYTES + 1)
            document = json.loads(raw) if len(raw) <= MAX_ERROR_BODY_BYTES else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            document = None
        if status == 401:
            category = "authentication"
        elif status == 403:
            category = "permission"
        elif status == 429:
            category = "rate_limit"
        elif 500 <= status <= 599:
            category = "server"
        elif 400 <= status <= 499:
            category = "invalid_request"
        else:
            category = "provider_response"
        headers = dict(error.headers.items()) if error.headers is not None else {}
        return AdapterFailure(
            f"{self.provider} HTTP 请求失败",
            category=category,
            code=self.provider_code(document) or f"http_{status}",
            http_status=status,
            request_id=self.request_id(headers),
            retryable=status == 429 or 500 <= status <= 599,
        )

    def provider_code(self, document: object) -> Optional[str]:
        if not isinstance(document, Mapping):
            return None
        error = document.get("error")
        if isinstance(error, Mapping):
            value = safe_token(error.get("code"))
            if value is not None:
                return value
        base_resp = document.get("base_resp")
        if isinstance(base_resp, Mapping):
            value = safe_token(base_resp.get("status_code"))
            if value not in {None, "0"}:
                return value
        return safe_token(document.get("code"))

    def request_id(self, headers: Mapping[str, Any]) -> Optional[str]:
        folded = {str(key).casefold(): value for key, value in headers.items()}
        for name in ("x-request-id", "x-tt-logid", "x-trace-id", "trace-id"):
            value = safe_token(folded.get(name))
            if value is not None:
                return value
        return None

    def staging_path(self, target: str) -> Path:
        """output_root 下的目标暂存文件，必须是直接子文件。"""
        path = self.job.output_root / ("result" + PurePosixPath(target).suffix.casefold())
        return path

    def write_output(self, target: str, content: bytes) -> Path:
        if not content or len(content) > MAX_OUTPUT_BYTES:
            raise AdapterFailure(
                "供应商产物体积非法",
                category="provider_response",
                code="output_size_invalid",
            )
        self.validate_media(target, content[:16])
        path = self.staging_path(target)
        with path.open("xb") as handle:
            handle.write(content)
        return path

    def validate_media(self, target: str, head: bytes) -> None:
        suffix = PurePosixPath(target).suffix.casefold()
        check = MEDIA_SIGNATURES.get(suffix)
        if check is None or not check(head):
            raise AdapterFailure(
                "供应商产物与目标媒体类型不符",
                category="provider_response",
                code="output_media_mismatch",
            )

    def download(self, url: str, target: str) -> Path:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise AdapterFailure(
                "供应商产物 URL 非法",
                category="provider_response",
                code="invalid_output_url",
            )
        path = self.staging_path(target)
        size = 0
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response, path.open(
                "xb"
            ) as handle:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_OUTPUT_BYTES:
                        raise AdapterFailure(
                            "供应商产物超出体积上限",
                            category="provider_response",
                            code="output_too_large",
                        )
                    handle.write(chunk)
        except AdapterFailure:
            path.unlink(missing_ok=True)
            raise
        except urllib.error.HTTPError as exc:
            path.unlink(missing_ok=True)
            raise self.http_failure(exc) from exc
        except TimeoutError as exc:
            path.unlink(missing_ok=True)
            raise AdapterFailure(
                "产物下载超时",
                category="timeout",
                code="download_timeout",
                retryable=True,
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            path.unlink(missing_ok=True)
            raise AdapterFailure(
                "产物下载失败",
                category="network",
                code="download_failed",
                retryable=True,
            ) from exc
        if size == 0:
            path.unlink(missing_ok=True)
            raise AdapterFailure(
                "供应商产物为空",
                category="provider_response",
                code="empty_output",
            )
        with path.open("rb") as handle:
            self.validate_media(target, handle.read(16))
        return path

    # ---- 主循环 ----

    @classmethod
    def run_cli(cls, argv: Optional[Sequence[str]] = None) -> int:
        parser = argparse.ArgumentParser(description=(cls.__doc__ or cls.provider))
        parser.add_argument(
            "--selftest",
            action="store_true",
            help="运行离线自检，不读取 stdin、不访问网络",
        )
        parser.add_argument(
            "--print-fingerprint",
            action="store_true",
            help="只计算并打印确认文档的 SHA-256 执行指纹",
        )
        args = parser.parse_args(argv)
        if args.selftest:
            selftest = getattr(cls, "selftest", None)
            if selftest is None:
                parser.error("该适配器未提供自检")
            selftest()
            print(f"{cls.provider}: selftest passed")
            return 0
        try:
            job = ProductionJob(json.load(sys.stdin.buffer))
            if args.print_fingerprint:
                print(job.verify_fingerprint())
                return 0
            adapter = cls(job)
            path, provider_job_id = adapter.execute()
        except AdapterFailure as exc:
            json.dump(
                {"error": exc.public(cls.provider or "unknown")},
                sys.stdout,
                ensure_ascii=True,
            )
            print("adapter 已安全失败", file=sys.stderr)
            return 1
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            json.dump(
                {
                    "error": {
                        "provider": cls.provider or "unknown",
                        "category": "invalid_request",
                        "code": "invalid_job",
                        "retryable": False,
                    }
                },
                sys.stdout,
                ensure_ascii=True,
            )
            print("adapter 已安全失败", file=sys.stderr)
            return 1
        response: dict[str, Any] = {
            "outputs": [{"target": job.outputs[0], "source": str(path)}]
        }
        if provider_job_id:
            response["provider_job_id"] = provider_job_id
        json.dump(response, sys.stdout, ensure_ascii=True)
        return 0


def jwt_hs256(payload: Mapping[str, Any], secret: str) -> str:
    """标准库实现的 HS256 JWT，供用 JWT 鉴权的平台复用。"""
    import hmac

    def segment(document: object) -> str:
        return base64.urlsafe_b64encode(canonical(document)).rstrip(b"=").decode("ascii")

    signing_input = f"{segment({'alg': 'HS256', 'typ': 'JWT'})}.{segment(payload)}"
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


__all__ = [    "AdapterFailure",
    "BaseAdapter",
    "ProductionJob",
    "ProviderTask",
    "canonical",
    "hash_file",
    "jwt_hs256",
    "safe_token",
    "sha256_bytes",
    "MODALITIES",
    "MEDIA_EXTENSIONS",
]
