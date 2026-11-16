"""分组聚合与事实存储：支持更正撤回、批次乱序与跨批次合并。"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, localcontext

from .core import coerce

#: 聚合累加的工作精度预算（有效位数），足够容纳大批量定点数求和
AGGREGATE_WORKING_PRECISION = 100


@dataclass(frozen=True)
class Fact:
    """一条已 staging 的事实记录。

    version: (batch_seq, row_index) 元组，用于更正与乱序批次的先后判定，
    数值越大越新。
    """

    key: str
    group: str
    measures: dict
    version: tuple


class GroupAggregator:
    """按 group 维护各度量的精确和与计数，支持 apply / retract。"""

    def __init__(self, measure_specs):
        self.measure_specs = dict(measure_specs)
        self._sums = defaultdict(
            lambda: {name: Decimal(0) for name in self.measure_specs}
        )
        self._counts = defaultdict(int)

    def _accumulate(self, fact, sign):
        with localcontext() as ctx:
            ctx.prec = AGGREGATE_WORKING_PRECISION
            bucket = self._sums[fact.group]
            for name, value in fact.measures.items():
                if value is None:
                    continue
                bucket[name] += value if sign > 0 else -value
            self._counts[fact.group] += sign

    def apply(self, fact):
        self._accumulate(fact, +1)

    def retract(self, fact):
        self._accumulate(fact, -1)

    def result(self):
        """返回 (results, aggregate_rejects)。

        results: {group: {"count": int, measure: Decimal}}，求和结果
        量化到各字段 scale；聚合和超出 precision 预算时该度量进入
        aggregate_rejects，不出现在结果中。
        """
        results = {}
        rejects = []
        for group, sums in self._sums.items():
            if self._counts[group] <= 0:
                continue
            row = {"count": self._counts[group]}
            for name, total in sums.items():
                spec = self.measure_specs[name]
                try:
                    row[name] = coerce(total, spec)
                except Exception as exc:
                    rejects.append(
                        {
                            "group": group,
                            "field": name,
                            "reason": getattr(exc, "reason", "OVERFLOW"),
                            "detail": str(exc),
                            "raw": str(total),
                        }
                    )
            results[group] = row
        return results, rejects


class FactStore:
    """按业务键保存当前生效事实，驱动聚合器的写入 / 撤回。

    同一 key 出现更高 version 的记录时，先撤回旧值再写入新值；
    更低 version（乱序迟到的旧批次）直接丢弃并计入 stale。
    """

    def __init__(self, measure_specs):
        self.aggregator = GroupAggregator(measure_specs)
        self._current = {}
        self.stale = 0

    def load(self, fact):
        existing = self._current.get(fact.key)
        if existing is None:
            self.aggregator.apply(fact)
            self._current[fact.key] = fact
            return "applied"
        if fact.version <= existing.version:
            self.stale += 1
            return "stale"
        self.aggregator.retract(existing)
        self.aggregator.apply(fact)
        self._current[fact.key] = fact
        return "corrected"

    def load_many(self, facts):
        return [self.load(fact) for fact in facts]

    def current(self, key):
        return self._current.get(key)

    def merge(self, other):
        """跨批次合并：按 version 顺序重放两个存储的当前事实。"""
        merged = FactStore(self.aggregator.measure_specs)
        facts = list(self._current.values()) + list(other._current.values())
        for fact in sorted(facts, key=lambda f: f.version):
            merged.load(fact)
        return merged

    def result(self):
        return self.aggregator.result()
