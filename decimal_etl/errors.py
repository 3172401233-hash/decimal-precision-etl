"""拒绝记录原因码与异常。"""


class RejectReason:
    PARSE_ERROR = "PARSE_ERROR"                      # 无法解析为十进制数
    TYPE_ERROR = "TYPE_ERROR"                        # 输入为 binary float 等禁用类型
    NULL_VALUE = "NULL_VALUE"                        # null 策略为 reject 时遇到空值
    MISSING_FIELD = "MISSING_FIELD"                  # 记录缺少必需字段
    OVERFLOW = "OVERFLOW"                            # 有效位数超过 precision 预算
    SCALE_UNREPRESENTABLE = "SCALE_UNREPRESENTABLE"  # 小数位超出 scale 且不允许舍入
    DIVIDE_BY_ZERO = "DIVIDE_BY_ZERO"                # 除数为零


class RejectError(Exception):
    """解析 / 校验 / 运算过程中产生的可拒绝错误。"""

    def __init__(self, reason, detail="", field=None, raw=None):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.field = field
        self.raw = raw
