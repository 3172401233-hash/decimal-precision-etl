"""decimal_etl：数据仓精确数值 ETL 处理器。

source -> staging -> 聚合 全链路使用 decimal.Decimal，
不经过 binary float；溢出与不可表示 scale 进入拒绝记录；
同一业务键的更正通过撤回旧值再写入新值完成。
"""

from .aggregate import AGGREGATE_WORKING_PRECISION, Fact, FactStore, GroupAggregator
from .core import (
    add,
    coerce,
    divide,
    multiply,
    normalize_zero,
    parse_value,
    subtract,
)
from .errors import RejectError, RejectReason
from .spec import NULL_REJECT, NULL_SKIP, NULL_ZERO, FieldSpec
from .staging import Reject, StagingPipeline, StagingResult

__all__ = [
    "AGGREGATE_WORKING_PRECISION",
    "Fact",
    "FactStore",
    "FieldSpec",
    "GroupAggregator",
    "NULL_REJECT",
    "NULL_SKIP",
    "NULL_ZERO",
    "Reject",
    "RejectError",
    "RejectReason",
    "StagingPipeline",
    "StagingResult",
    "add",
    "coerce",
    "divide",
    "multiply",
    "normalize_zero",
    "parse_value",
    "subtract",
]
