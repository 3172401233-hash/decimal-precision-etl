"""精确数值核心：解析、范围校验、舍入与四则运算。

整条链路只使用 decimal.Decimal，任何一步都不经过 binary float。
"""

from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    localcontext,
)

from .errors import RejectError, RejectReason
from .spec import NULL_REJECT, NULL_SKIP, NULL_ZERO

ROUNDING_MODES = {
    "CEILING": ROUND_CEILING,
    "DOWN": ROUND_DOWN,
    "FLOOR": ROUND_FLOOR,
    "HALF_DOWN": ROUND_HALF_DOWN,
    "HALF_EVEN": ROUND_HALF_EVEN,
    "HALF_UP": ROUND_HALF_UP,
    "UP": ROUND_UP,
    "05UP": ROUND_05UP,
    "UNNECESSARY": None,  # 超出 scale 即拒绝，不舍入
}

#: 运算工作精度预算：保证中间结果位数充足，最终再量化到字段 scale
WORKING_PRECISION_SLACK = 20


def working_precision(spec):
    return spec.precision + spec.scale + WORKING_PRECISION_SLACK


def _quantum(scale):
    return Decimal(1).scaleb(-scale)


def digit_count(value):
    """有效位数（含前导整数位），用于 precision 预算校验。"""
    t = value.as_tuple()
    return len(t.digits) + max(t.exponent, 0)


def check_precision(value, spec):
    if digit_count(value) > spec.precision:
        raise RejectError(
            RejectReason.OVERFLOW,
            f"{value} exceeds precision {spec.precision}",
            field=spec.name,
            raw=str(value),
        )
    return value


def normalize_zero(value):
    """负零规范化：-0.00 -> 0.00，保留 scale。"""
    if value.is_zero() and value.is_signed():
        return value.copy_abs()
    return value


def coerce(value, spec):
    """把 Decimal 量化到字段 scale 并做 precision 校验。"""
    if spec.rounding not in ROUNDING_MODES:
        raise ValueError(f"unknown rounding mode: {spec.rounding}")
    quantum = _quantum(spec.scale)
    with localcontext() as ctx:
        ctx.prec = working_precision(spec)
        fractional_digits = max(-value.as_tuple().exponent, 0)
        if fractional_digits > spec.scale:
            if spec.rounding == "UNNECESSARY":
                raise RejectError(
                    RejectReason.SCALE_UNREPRESENTABLE,
                    f"{value} has {fractional_digits} fractional digits, "
                    f"scale {spec.scale} cannot represent it",
                    field=spec.name,
                    raw=str(value),
                )
            value = value.quantize(quantum, rounding=ROUNDING_MODES[spec.rounding])
        else:
            value = value.quantize(quantum)
    return check_precision(normalize_zero(value), spec)


def _handle_null(spec):
    if spec.null_policy == NULL_ZERO:
        return coerce(Decimal(0), spec)
    if spec.null_policy == NULL_SKIP:
        return None
    raise RejectError(
        RejectReason.NULL_VALUE,
        f"null not allowed for field {spec.name}",
        field=spec.name,
        raw=None,
    )


def parse_value(raw, spec):
    """把原始输入解析为符合字段规范的 Decimal。

    返回 None 表示 null_policy=skip 的空值。拒绝时抛 RejectError。
    显式拒绝 float，防止 binary float 污染精确链路。
    """
    if raw is None or (isinstance(raw, str) and raw.strip() in spec.null_tokens):
        return _handle_null(spec)
    if isinstance(raw, bool) or isinstance(raw, float):
        raise RejectError(
            RejectReason.TYPE_ERROR,
            f"binary float not accepted for field {spec.name}; "
            "use str/int/Decimal",
            field=spec.name,
            raw=repr(raw),
        )
    if isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, str):
        text = raw.strip()
        try:
            value = Decimal(text)  # 支持科学计数法，如 1.2345E+3
        except InvalidOperation:
            raise RejectError(
                RejectReason.PARSE_ERROR,
                f"cannot parse {text!r} as decimal",
                field=spec.name,
                raw=raw,
            )
        if value.is_nan() or value.is_infinite():
            raise RejectError(
                RejectReason.PARSE_ERROR,
                f"non-finite value {text!r}",
                field=spec.name,
                raw=raw,
            )
    else:
        raise RejectError(
            RejectReason.TYPE_ERROR,
            f"unsupported type {type(raw).__name__}",
            field=spec.name,
            raw=repr(raw),
        )
    return coerce(value, spec)


def binary_op(op, left, right, result_spec):
    """精确四则运算，结果量化到 result_spec 并做溢出校验。"""
    with localcontext() as ctx:
        ctx.prec = working_precision(result_spec)
        if op == "add":
            result = left + right
        elif op == "sub":
            result = left - right
        elif op == "mul":
            result = left * right
        elif op == "div":
            if right == 0:
                raise RejectError(
                    RejectReason.DIVIDE_BY_ZERO,
                    f"divisor is zero: {left} / {right}",
                    field=result_spec.name,
                )
            result = left / right
        else:
            raise ValueError(f"unknown op: {op}")
    return coerce(result, result_spec)


def add(left, right, result_spec):
    return binary_op("add", left, right, result_spec)


def subtract(left, right, result_spec):
    return binary_op("sub", left, right, result_spec)


def multiply(left, right, result_spec):
    return binary_op("mul", left, right, result_spec)


def divide(left, right, result_spec):
    return binary_op("div", left, right, result_spec)
