"""source -> staging：CSV / JSON / 文本报表解析为精确事实记录与拒绝记录。"""

import csv
import io
import json
from dataclasses import dataclass, field
from decimal import Decimal

from .aggregate import Fact
from .core import parse_value
from .errors import RejectError, RejectReason


@dataclass
class Reject:
    """拒绝记录：任何无法进入精确链路的输入都落到这里。"""

    source: str
    batch_id: str
    row: int
    key: str
    field: str
    raw: str
    reason: str
    detail: str

    def as_dict(self):
        return {
            "source": self.source,
            "batch_id": self.batch_id,
            "row": self.row,
            "key": self.key,
            "field": self.field,
            "raw": self.raw,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class StagingResult:
    facts: list = field(default_factory=list)
    rejects: list = field(default_factory=list)


class StagingPipeline:
    """按字段规范把多源记录 staging 为 Fact，并收集拒绝记录。"""

    def __init__(self, key_field, group_field, measure_specs):
        self.key_field = key_field
        self.group_field = group_field
        self.measure_specs = dict(measure_specs)

    def stage_csv(self, text, batch_id, batch_seq):
        reader = csv.DictReader(io.StringIO(text))
        return self._stage_rows(reader, "csv", batch_id, batch_seq)

    def stage_json(self, text, batch_id, batch_seq):
        # parse_float=Decimal：JSON 数字绝不经过 binary float
        payload = json.loads(text, parse_float=Decimal)
        rows = payload.get("records", payload) if isinstance(payload, dict) else payload
        return self._stage_rows(rows, "json", batch_id, batch_seq)

    def stage_report(self, text, batch_id, batch_seq):
        """管道符分隔的文本报表；# 开头为注释，首个非注释行为表头。"""
        lines = [
            ln for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        if not lines:
            return StagingResult()
        header = [col.strip() for col in lines[0].split("|")]
        rows = (
            dict(zip(header, (cell.strip() for cell in ln.split("|"))))
            for ln in lines[1:]
        )
        return self._stage_rows(rows, "report", batch_id, batch_seq)

    def _stage_rows(self, rows, source, batch_id, batch_seq):
        result = StagingResult()
        for index, row in enumerate(rows):
            version = (batch_seq, index)
            try:
                fact = self._stage_row(row, version)
            except RejectError as exc:
                result.rejects.append(
                    Reject(
                        source=source,
                        batch_id=batch_id,
                        row=index,
                        key=str(row.get(self.key_field, "")),
                        field=exc.field or "",
                        raw="" if exc.raw is None else str(exc.raw),
                        reason=exc.reason,
                        detail=exc.detail,
                    )
                )
                continue
            result.facts.append(fact)
        return result

    def _require(self, row, name):
        value = row.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise RejectError(
                RejectReason.MISSING_FIELD,
                f"required field {name} missing",
                field=name,
                raw=value,
            )
        return str(value).strip()

    def _stage_row(self, row, version):
        key = self._require(row, self.key_field)
        group = self._require(row, self.group_field)
        measures = {}
        for name, spec in self.measure_specs.items():
            measures[name] = parse_value(row.get(name), spec)
        return Fact(key=key, group=group, measures=measures, version=version)
