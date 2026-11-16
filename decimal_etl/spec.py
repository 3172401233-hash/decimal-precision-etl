"""字段级精度规范。"""

from dataclasses import dataclass, field

#: null 策略
NULL_REJECT = "reject"  # 遇到空值即拒绝该记录
NULL_ZERO = "zero"      # 空值按 0 处理
NULL_SKIP = "skip"      # 空值跳过该度量（不参与聚合）

DEFAULT_NULL_TOKENS = ("", "NULL", "null", "N/A", "NA", "n/a", "-", "None")


@dataclass(frozen=True)
class FieldSpec:
    """单个数值字段的精度预算。

    precision: 允许的最大有效位数（整数位 + 小数位）。
    scale:     定点小数位数，解析与运算结果都量化到该位数。
    rounding:  decimal 舍入模式名（HALF_UP/HALF_EVEN/DOWN/UP/CEILING/FLOOR/
               HALF_DOWN/05UP），或 UNNECESSARY 表示超出 scale 必须拒绝。
    null_policy / null_tokens: 空值处理策略与空值字面量集合。
    """

    name: str
    precision: int
    scale: int
    rounding: str = "HALF_UP"
    null_policy: str = NULL_REJECT
    null_tokens: tuple = field(default=DEFAULT_NULL_TOKENS)

    def __post_init__(self):
        if self.precision < 1:
            raise ValueError("precision must be >= 1")
        if self.scale < 0:
            raise ValueError("scale must be >= 0")
        if self.scale > self.precision:
            raise ValueError("scale must be <= precision")
        if self.null_policy not in (NULL_REJECT, NULL_ZERO, NULL_SKIP):
            raise ValueError(f"unknown null_policy: {self.null_policy}")
