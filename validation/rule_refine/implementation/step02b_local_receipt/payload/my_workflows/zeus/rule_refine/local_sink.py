"""本地模拟接收建议：保存文件并去重，不执行规则修改。"""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from .contracts import RuleRecommendation


class LocalReceiptError(ValueError):
    pass


class ReceiptConflictError(LocalReceiptError):
    pass


class LocalRecommendationSink:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()

    def submit(self, payload):
        if not isinstance(payload, dict) or "recommendation_id" not in payload:
            raise LocalReceiptError(
                "提交时必须携带已生成的recommendation_id，重试沿用同一编号"
            )
        record = RuleRecommendation.model_validate(payload)
        data = record.model_dump(mode="json")
        content = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        recommendation_id = str(record.recommendation_id)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self.directory / f"{recommendation_id}.json"
        fd, temporary_name = tempfile.mkstemp(
            prefix=".receipt-", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_name, target)
                duplicate = False
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != content:
                    raise ReceiptConflictError(
                        "同一建议编号已有不同内容，原文件未覆盖"
                    ) from None
                duplicate = True
            return {
                "recommendation_id": recommendation_id,
                "delivery_mode": "mock",
                "status": "received",
                "applied": False,
                "duplicate": duplicate,
                "content_sha256": hashlib.sha256(content).hexdigest(),
            }
        finally:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning(
                    "收件临时文件清理失败", exc_info=True
                )
