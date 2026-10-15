#!/usr/bin/env python3
"""可灵（Kling AI）Adapter 示例实现。

演示一条完整的真实 HTTP 链路：现签 JWT → 提交异步任务 → 轮询状态 → 下载产物 →
写回 adapter 契约 JSON。三条接口都按可灵开放平台的实际路径与字段实现：

===========================  ==========================================  ==========================
modality                     提交                                         查询
===========================  ==========================================  ==========================
``video``（无参考图）          ``POST /v1/videos/text2video``              ``GET /v1/videos/text2video/{id}``
``video``（有参考图）          ``POST /v1/videos/image2video``             ``GET /v1/videos/image2video/{id}``
``image``                     ``POST /v1/images/generations``             ``GET /v1/images/generations/{id}``
===========================  ==========================================  ==========================

可灵用 Access Key / Secret Key 现签 HS256 JWT 鉴权，凭据只从环境变量读取，
不写入 `config.json`、项目文件或运行记录。

环境变量
--------

``KLING_ACCESS_KEY`` / ``KLING_SECRET_KEY``
    必填。可灵开放平台的密钥对。
``KLING_MODEL``
    必填。模型名，例如 ``kling-v3``。刻意不给默认值：不同账号可用的模型不同，
    写死默认值会让付费任务打到错误的模型上。
``KLING_MODE``
    可选，``std`` 或 ``pro``。
``KLING_ALLOWED_RATIOS``
    可选，逗号分隔的允许比例。留空表示不限制（仅做基础合法性检查）。
``KLING_ALLOWED_DURATIONS``
    可选，逗号分隔的允许时长，默认 ``5,10``。
``KLING_BASE_URL`` / ``KLING_POLL_INTERVAL`` / ``KLING_TIMEOUT_SECONDS``
    可选，接口地址与轮询配置。

用法
----

作为生产 Adapter 被 ``production_tool.py`` 调用（stdin 读确认文档）：:

    python3 adapters/kling_adapter.py

离线自检（不联网）：:

    python3 adapters/kling_adapter.py --selftest
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from base_adapter import (  # noqa: E402  (需要先补 sys.path)
    AdapterFailure,
    BaseAdapter,
    ProductionJob,
    ProviderTask,
    jwt_hs256,
    safe_token,
)

KLING_BASE_URL = "https://api-beijing.klingai.com/v1"
KLING_RATIOS = frozenset({"16:9", "9:16", "1:1"})
KLING_IMAGE_RATIOS = frozenset({"16:9", "9:16", "1:1", "4:3", "3:4", "2:3", "3:2", "21:9"})
DEFAULT_ALLOWED_DURATIONS = (5, 10)
TOKEN_TTL_SECONDS = 1800
TOKEN_CLOCK_SKEW_SECONDS = 5


class KlingTask(ProviderTask):
    """可灵异步任务。

    可灵的 ``task_status`` 取值是 ``submitted`` / ``processing`` / ``succeed`` /
    ``failed``。注意成功值是 ``succeed``，不是 ``succeeded``。
    """

    @classmethod
    def from_document(cls, document: Mapping[str, Any], *, request_id: Optional[str]) -> "KlingTask":
        data = document.get("data")
        if not isinstance(data, Mapping):
            raise AdapterFailure(
                "可灵响应缺少 data",
                category="provider_response",
                code="invalid_response",
                request_id=request_id,
            )
        task_id = data.get("task_id")
        status = data.get("task_status")
        if not isinstance(task_id, str) or not task_id:
            raise AdapterFailure(
                "可灵未返回 task_id",
                category="provider_response",
                code="missing_task_id",
                request_id=request_id,
            )
        if not isinstance(status, str) or not status:
            raise AdapterFailure(
                "可灵未返回 task_status",
                category="provider_response",
                code="missing_task_status",
                request_id=task_id,
            )
        return cls(
            "kling",
            task_id,
            status,
            result_urls=cls._result_urls(data),
            request_id=task_id,
        )

    @staticmethod
    def _result_urls(data: Mapping[str, Any]) -> list[str]:
        result = data.get("task_result")
        if not isinstance(result, Mapping):
            return []
        urls: list[str] = []
        for key in ("videos", "images"):
            entries = result.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                url = entry.get("url") if isinstance(entry, Mapping) else None
                if isinstance(url, str) and url:
                    urls.append(url)
        return urls


class KlingAdapter(BaseAdapter):
    provider = "kling"
    modalities = frozenset({"image", "video"})
    poll_interval_env = "KLING_POLL_INTERVAL"
    timeout_env = "KLING_TIMEOUT_SECONDS"
    default_poll_interval = 5.0
    default_timeout_seconds = 1800.0

    # ---- 配置 ----

    def model(self) -> str:
        value = os.environ.get("KLING_MODEL", "").strip()
        if not value:
            raise AdapterFailure(
                "未配置 KLING_MODEL",
                category="configuration",
                code="missing_model",
            )
        return value

    def mode(self) -> Optional[str]:
        value = os.environ.get("KLING_MODE", "").strip()
        if not value:
            return None
        if value not in {"std", "pro"}:
            raise AdapterFailure(
                "KLING_MODE 只能是 std 或 pro",
                category="configuration",
                code="invalid_mode",
            )
        return value

    def allowed_ratios(self) -> Optional[frozenset[str]]:
        raw = os.environ.get("KLING_ALLOWED_RATIOS", "").strip()
        if not raw:
            return None
        ratios = frozenset(item.strip() for item in raw.split(",") if item.strip())
        if not ratios or not ratios <= KLING_IMAGE_RATIOS:
            raise AdapterFailure(
                "KLING_ALLOWED_RATIOS 含不受支持的比例",
                category="configuration",
                code="invalid_ratio_profile",
            )
        return ratios

    def allowed_durations(self) -> frozenset[int]:
        raw = os.environ.get("KLING_ALLOWED_DURATIONS", "").strip()
        if not raw:
            return frozenset(DEFAULT_ALLOWED_DURATIONS)
        try:
            durations = frozenset(int(item.strip()) for item in raw.split(",") if item.strip())
        except ValueError as exc:
            raise AdapterFailure(
                "KLING_ALLOWED_DURATIONS 必须是整数列表",
                category="configuration",
                code="invalid_duration_profile",
            ) from exc
        if not durations or any(not 1 <= value <= 15 for value in durations):
            raise AdapterFailure(
                "KLING_ALLOWED_DURATIONS 超出 1-15 秒",
                category="configuration",
                code="invalid_duration_profile",
            )
        return durations

    def base(self) -> str:
        return self.base_url("KLING_BASE_URL", KLING_BASE_URL)

    def token(self) -> str:
        """每次请求前重新签发 JWT，避免复用过期 Token 触发 401。"""
        access_key = self.credential("KLING_ACCESS_KEY")
        secret_key = self.credential("KLING_SECRET_KEY")
        now = int(time.time())
        return jwt_hs256(
            {
                "iss": access_key,
                "nbf": now - TOKEN_CLOCK_SKEW_SECONDS,
                "exp": now + TOKEN_TTL_SECONDS,
                "iat": now,
            },
            secret_key,
        )

    # ---- 请求体编译 ----

    def compile_body(self, *, external_task_id: str) -> dict[str, Any]:
        if self.job.modality == "image":
            return self._compile_image_body(external_task_id)
        return self._compile_video_body(external_task_id)

    def _common(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model_name": self.model(), "prompt": self.job.provider_prompt()}
        mode = self.mode()
        if mode is not None:
            body["mode"] = mode
        negative = parameters.get("negative_prompt")
        if isinstance(negative, str) and negative.strip():
            body["negative_prompt"] = negative.strip()
        return body

    def _ratio(self, parameters: Mapping[str, Any], *, image: bool) -> Optional[str]:
        ratio = parameters.get("ratio")
        if ratio is None:
            return None
        if not isinstance(ratio, str) or ratio.strip() not in (
            KLING_IMAGE_RATIOS if image else KLING_RATIOS
        ):
            raise AdapterFailure(
                "可灵不支持该画面比例",
                category="contract",
                code="unsupported_ratio",
            )
        allowed = self.allowed_ratios()
        if allowed is not None and ratio.strip() not in allowed:
            raise AdapterFailure(
                "该比例不在 KLING_ALLOWED_RATIOS 内",
                category="configuration",
                code="ratio_outside_profile",
            )
        return ratio.strip()

    def _compile_video_body(self, external_task_id: str) -> dict[str, Any]:
        parameters = self.job.take_parameters({"duration", "ratio", "cfg_scale", "negative_prompt"})
        body = self._common(parameters)
        ratio = self._ratio(parameters, image=False)
        if ratio is not None:
            body["aspect_ratio"] = ratio
        duration = parameters.get("duration")
        if duration is not None:
            if not isinstance(duration, int) or isinstance(duration, bool):
                raise AdapterFailure(
                    "duration 必须是整数秒",
                    category="contract",
                    code="invalid_duration",
                )
            if duration not in self.allowed_durations():
                raise AdapterFailure(
                    "该时长不在 KLING_ALLOWED_DURATIONS 内",
                    category="configuration",
                    code="duration_outside_profile",
                )
            body["duration"] = str(duration)
        cfg_scale = parameters.get("cfg_scale")
        if cfg_scale is not None:
            if (
                not isinstance(cfg_scale, (int, float))
                or isinstance(cfg_scale, bool)
                or not 0 <= float(cfg_scale) <= 1
            ):
                raise AdapterFailure(
                    "cfg_scale 必须在 0 到 1 之间",
                    category="contract",
                    code="invalid_cfg_scale",
                )
            body["cfg_scale"] = float(cfg_scale)
        if self.job.references:
            if len(self.job.references) > 2:
                raise AdapterFailure(
                    "可灵图生视频最多接受首帧与尾帧两张参考图",
                    category="contract",
                    code="too_many_references",
                )
            body["image"] = self.job.reference_base64(0)
            if len(self.job.references) == 2:
                body["image_tail"] = self.job.reference_base64(1)
        body["external_task_id"] = external_task_id
        return body

    def _compile_image_body(self, external_task_id: str) -> dict[str, Any]:
        parameters = self.job.take_parameters({"ratio", "n", "negative_prompt"})
        body = self._common(parameters)
        ratio = self._ratio(parameters, image=True)
        if ratio is not None:
            body["aspect_ratio"] = ratio
        count = parameters.get("n", 1)
        if count != 1:
            raise AdapterFailure(
                "本工作流的 job 一次只允许一个输出，n 必须为 1",
                category="contract",
                code="invalid_output_count",
            )
        body["n"] = 1
        if len(self.job.references) > 1:
            raise AdapterFailure(
                "可灵图片生成最多接受一张参考图",
                category="contract",
                code="too_many_references",
            )
        if self.job.references:
            body["image"] = self.job.reference_base64(0)
        body["external_task_id"] = external_task_id
        return body

    # ---- 接口 ----

    def endpoints(self) -> tuple[str, str]:
        if self.job.modality == "image":
            resource = "images/generations"
        elif self.job.references:
            resource = "videos/image2video"
        else:
            resource = "videos/text2video"
        return resource, f"{self.base()}/{resource}"

    def envelope(self, document: Mapping[str, Any], *, request_id: Optional[str]) -> None:
        """可灵的响应外壳：``code`` 非 0 即为失败。"""
        code = document.get("code")
        if code == 0:
            return
        raise AdapterFailure(
            "可灵返回了错误码",
            category="provider_response",
            code=safe_token(code) or "provider_error",
            request_id=request_id or safe_token(document.get("request_id")),
            retryable=False,
        )

    def create_task(self) -> ProviderTask:
        _, url = self.endpoints()
        body = self.compile_body(external_task_id=self.job.external_task_id(self.fingerprint))
        document, headers = self.request_json(url, body=body, token=self.token())
        request_id = self.request_id(headers)
        self.envelope(document, request_id=request_id)
        return KlingTask.from_document(document, request_id=request_id)

    def fetch_task(self, task: ProviderTask) -> ProviderTask:
        resource, _ = self.endpoints()
        task_id = urllib.parse.quote(task.task_id, safe="")
        url = f"{self.base()}/{resource}/{task_id}"
        document, headers = self.request_json(url, method="GET", token=self.token())
        request_id = self.request_id(headers)
        self.envelope(document, request_id=request_id)
        return KlingTask.from_document(document, request_id=request_id)

    # ---- 自检 ----

    @staticmethod
    def selftest() -> None:
        """离线自检：不联网、不读 stdin，覆盖鉴权、请求体、响应解析与指纹校验。"""
        previous = {
            key: os.environ.get(key)
            for key in (
                "KLING_ACCESS_KEY",
                "KLING_SECRET_KEY",
                "KLING_MODEL",
                "KLING_MODE",
                "KLING_ALLOWED_RATIOS",
                "KLING_ALLOWED_DURATIONS",
            )
        }
        try:
            os.environ.update(
                {
                    "KLING_ACCESS_KEY": "ak-selftest",
                    "KLING_SECRET_KEY": "sk-selftest",
                    "KLING_MODEL": "kling-selftest",
                    "KLING_MODE": "std",
                    "KLING_ALLOWED_RATIOS": "9:16",
                    "KLING_ALLOWED_DURATIONS": "5,10",
                }
            )
            with tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                project = root / "project"
                staging = root / "staging"
                project.mkdir()
                staging.mkdir()
                reference = project / "输入" / "hero.png"
                reference.parent.mkdir(parents=True)
                reference.write_bytes(
                    b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 16
                )

                video_payload = {
                    "schema_version": "1.0",
                    "job_id": "EP001-MOTION001-video",
                    "modality": "video",
                    "adapter": "kling-video",
                    "prompt": "站台空了，固定机位",
                    "source": None,
                    "source_entry": None,
                    "references": ["输入/hero.png"],
                    "reference_bindings": [
                        {
                            "slot_id": "REF-HERO",
                            "order": 1,
                            "path": "输入/hero.png",
                            "label": "女主定妆照",
                            "role": "identity_and_look",
                            "may_control": ["身份", "造型"],
                            "must_not_control": ["构图", "动作"],
                        }
                    ],
                    "outputs": ["剧集/EP001/制作成果/video/SHOT-EP001-001.mp4"],
                    "parameters": {"duration": 5, "ratio": "9:16", "cfg_scale": 0.5},
                    "overwrite": False,
                    "run_id": "run-selftest",
                    "project_root": str(project),
                    "output_root": str(staging),
                }
                video_job = ProductionJob(video_payload)
                video_adapter = KlingAdapter(video_job)

                resource, url = video_adapter.endpoints()
                if resource != "videos/image2video":
                    raise AssertionError("有参考图时应走 image2video")
                if url != f"{KLING_BASE_URL}/videos/image2video":
                    raise AssertionError("image2video 端点错误")

                body = video_adapter.compile_body(external_task_id="sd-selftest")
                if body["model_name"] != "kling-selftest":
                    raise AssertionError("模型名未透传")
                if body["aspect_ratio"] != "9:16" or body["duration"] != "5":
                    raise AssertionError("比例或时长未透传")
                if "Reference image 1 (女主定妆照)" not in body["prompt"]:
                    raise AssertionError("参考图契约未进入提示词")
                if not body["image"]:
                    raise AssertionError("参考图未转为 Base64")
                if "image_tail" in body:
                    raise AssertionError("只有一张参考图时不应带尾帧")
                if body["external_task_id"] != "sd-selftest":
                    raise AssertionError("external_task_id 未透传")

                token = video_adapter.token()
                if token.count(".") != 2:
                    raise AssertionError("JWT 结构错误")

                no_reference = dict(video_payload)
                no_reference["references"] = []
                no_reference["reference_bindings"] = []
                if KlingAdapter(ProductionJob(no_reference)).endpoints()[0] != "videos/text2video":
                    raise AssertionError("无参考图时应走 text2video")

                image_payload = dict(no_reference)
                image_payload["modality"] = "image"
                image_payload["outputs"] = ["剧集/EP001/制作成果/images/SHOT-EP001-001.png"]
                image_payload["parameters"] = {"ratio": "9:16"}
                if KlingAdapter(ProductionJob(image_payload)).endpoints()[0] != "images/generations":
                    raise AssertionError("图片任务端点错误")

                succeed = KlingTask.from_document(
                    {
                        "code": 0,
                        "message": "SUCCEED",
                        "data": {
                            "task_id": "864320677733101648",
                            "task_status": "succeed",
                            "task_result": {
                                "videos": [{"id": "v1", "url": "https://example.invalid/v.mp4"}]
                            },
                        },
                    },
                    request_id=None,
                )
                if not succeed.is_success or succeed.result_urls != ["https://example.invalid/v.mp4"]:
                    raise AssertionError("成功响应解析错误")

                processing = KlingTask.from_document(
                    {
                        "code": 0,
                        "message": "SUCCEED",
                        "data": {"task_id": "t", "task_status": "processing"},
                    },
                    request_id=None,
                )
                if not processing.is_pending or processing.result_urls:
                    raise AssertionError("处理中状态解析错误")

                # 指纹必须能重算，且与输入字节绑定
                first = video_job.verify_fingerprint()
                if len(first) != 64:
                    raise AssertionError("指纹长度错误")
                if video_job.verify_fingerprint() != first:
                    raise AssertionError("同一输入的指纹不稳定")
                reference.write_bytes(reference.read_bytes() + b"\x00")
                if video_job.verify_fingerprint() == first:
                    raise AssertionError("参考图变化后指纹未失效")

                # 期望指纹不一致时必须 fail closed
                mismatched = dict(video_payload)
                mismatched["fingerprint"] = "0" * 64
                try:
                    KlingAdapter(ProductionJob(mismatched))
                except AdapterFailure as exc:
                    if exc.code != "fingerprint_mismatch":
                        raise AssertionError("指纹不一致的失败码错误") from exc
                else:
                    raise AssertionError("指纹不一致时未拒绝提交")

                # 超出 profile 的时长必须拒绝
                bad_duration = dict(video_payload)
                bad_duration["parameters"] = {"duration": 7}
                try:
                    KlingAdapter(ProductionJob(bad_duration)).compile_body(external_task_id="x")
                except AdapterFailure as exc:
                    if exc.code != "duration_outside_profile":
                        raise AssertionError("时长校验失败码错误") from exc
                else:
                    raise AssertionError("超出 profile 的时长未被拒绝")

                # 未支持的 modality 必须拒绝
                try:
                    KlingAdapter(ProductionJob({**video_payload, "modality": "music", "outputs": ["a.mp3"]}))
                except AdapterFailure as exc:
                    if exc.code != "unsupported_modality":
                        raise AssertionError("modality 校验失败码错误") from exc
                else:
                    raise AssertionError("music 任务未被拒绝")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(KlingAdapter.run_cli())
