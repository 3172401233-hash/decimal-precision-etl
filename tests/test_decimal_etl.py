"""decimal_etl 单元测试。

聚合断言均与测试内独立的 decimal.Decimal 参考计算核对。
"""

import unittest
from decimal import Decimal, ROUND_HALF_UP, localcontext

from decimal_etl import (
    Fact,
    FactStore,
    FieldSpec,
    RejectReason,
    StagingPipeline,
    add,
    divide,
    multiply,
    parse_value,
    subtract,
)
from decimal_etl.errors import RejectError

AMOUNT = FieldSpec("amount", precision=18, scale=2, rounding="HALF_UP")
QTY = FieldSpec("qty", precision=12, scale=4, rounding="HALF_EVEN",
                null_policy="skip")
SPECS = {"amount": AMOUNT, "qty": QTY}


def ref_sum(values, scale):
    """独立参考计算：纯 Decimal 求和后量化。"""
    with localcontext() as ctx:
        ctx.prec = 100
        total = sum((Decimal(v) for v in values), Decimal(0))
    return total.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


class TestParse(unittest.TestCase):
    def test_exponential_notation(self):
        self.assertEqual(parse_value("1.2345E+3", AMOUNT), Decimal("1234.50"))
        self.assertEqual(parse_value("1.2e-3", QTY), Decimal("0.0012"))
        self.assertEqual(parse_value("-2.5E1", AMOUNT), Decimal("-25.00"))

    def test_negative_zero_normalized(self):
        value = parse_value("-0.00", AMOUNT)
        self.assertEqual(value, Decimal("0.00"))
        self.assertFalse(value.is_signed())
        # 舍入后归零的负数同样规范化
        rounded = parse_value("-0.001", AMOUNT)
        self.assertEqual(rounded, Decimal("0.00"))
        self.assertFalse(rounded.is_signed())

    def test_rounding_modes(self):
        spec = lambda r: FieldSpec("v", precision=10, scale=0, rounding=r)
        self.assertEqual(parse_value("2.5", spec("HALF_UP")), Decimal("3"))
        self.assertEqual(parse_value("2.5", spec("HALF_EVEN")), Decimal("2"))
        self.assertEqual(parse_value("3.5", spec("HALF_EVEN")), Decimal("4"))
        self.assertEqual(parse_value("2.5", spec("DOWN")), Decimal("2"))
        self.assertEqual(parse_value("-2.5", spec("DOWN")), Decimal("-2"))
        self.assertEqual(parse_value("-2.5", spec("UP")), Decimal("-3"))
        self.assertEqual(parse_value("-2.5", spec("CEILING")), Decimal("-2"))
        self.assertEqual(parse_value("-2.5", spec("FLOOR")), Decimal("-3"))
        # 05UP：保留位末位为 0/5 时才远离零舍入
        self.assertEqual(parse_value("2.5", spec("05UP")), Decimal("2"))
        self.assertEqual(parse_value("5.1", spec("05UP")), Decimal("6"))

    def test_unnecessary_rounding_rejects(self):
        spec = FieldSpec("v", precision=10, scale=2, rounding="UNNECESSARY")
        self.assertEqual(parse_value("1.25", spec), Decimal("1.25"))
        with self.assertRaises(RejectError) as ctx:
            parse_value("1.234", spec)
        self.assertEqual(ctx.exception.reason, RejectReason.SCALE_UNREPRESENTABLE)

    def test_extreme_integers(self):
        big = FieldSpec("big", precision=38, scale=0)
        raw = "9" * 38
        self.assertEqual(parse_value(raw, big), Decimal(raw))
        # 超出 precision 预算 -> OVERFLOW
        with self.assertRaises(RejectError) as ctx:
            parse_value("9" * 39, big)
        self.assertEqual(ctx.exception.reason, RejectReason.OVERFLOW)
        # 科学计数法表示的超大整数同样校验
        with self.assertRaises(RejectError):
            parse_value("1E+39", big)
        # 量化后整数位撑爆 precision 也算溢出
        tight = FieldSpec("t", precision=5, scale=2)
        with self.assertRaises(RejectError) as ctx:
            parse_value("9999.999", tight)  # 舍入为 10000.00，7 位 > 5
        self.assertEqual(ctx.exception.reason, RejectReason.OVERFLOW)

    def test_null_policies(self):
        reject_spec = FieldSpec("a", 10, 2, null_policy="reject")
        zero_spec = FieldSpec("b", 10, 2, null_policy="zero")
        skip_spec = FieldSpec("c", 10, 2, null_policy="skip")
        for token in ("", "NULL", "N/A", "-"):
            with self.assertRaises(RejectError) as ctx:
                parse_value(token, reject_spec)
            self.assertEqual(ctx.exception.reason, RejectReason.NULL_VALUE)
            self.assertEqual(parse_value(token, zero_spec), Decimal("0.00"))
            self.assertIsNone(parse_value(token, skip_spec))

    def test_binary_float_rejected(self):
        with self.assertRaises(RejectError) as ctx:
            parse_value(0.1, AMOUNT)
        self.assertEqual(ctx.exception.reason, RejectReason.TYPE_ERROR)

    def test_garbage_rejected(self):
        for raw in ("abc", "1.2.3", "NaN", "Infinity", "1,234.56"):
            with self.assertRaises(RejectError) as ctx:
                parse_value(raw, AMOUNT)
            self.assertEqual(ctx.exception.reason, RejectReason.PARSE_ERROR)


class TestArithmetic(unittest.TestCase):
    def test_no_binary_float_contamination(self):
        a = parse_value("0.1", AMOUNT)
        b = parse_value("0.2", AMOUNT)
        self.assertEqual(add(a, b, AMOUNT), Decimal("0.30"))
        self.assertIsInstance(add(a, b, AMOUNT), Decimal)

    def test_precision_propagates(self):
        # 乘法 scale 相加后量化回字段 scale
        self.assertEqual(
            multiply(Decimal("1.05"), Decimal("1.05"), AMOUNT),
            Decimal("1.10"),  # 1.1025 -> HALF_UP
        )
        self.assertEqual(
            subtract(Decimal("0.01"), Decimal("0.02"), AMOUNT),
            Decimal("-0.01"),
        )
        self.assertEqual(
            divide(Decimal("1.00"), Decimal("3"), AMOUNT),
            Decimal("0.33"),
        )

    def test_divide_by_zero_rejected(self):
        with self.assertRaises(RejectError) as ctx:
            divide(Decimal("1.00"), Decimal("0.00"), AMOUNT)
        self.assertEqual(ctx.exception.reason, RejectReason.DIVIDE_BY_ZERO)

    def test_op_overflow_rejected(self):
        big = FieldSpec("big", precision=10, scale=0)
        with self.assertRaises(RejectError) as ctx:
            add(Decimal("9" * 10), Decimal("9" * 10), big)
        self.assertEqual(ctx.exception.reason, RejectReason.OVERFLOW)


def make_pipeline():
    return StagingPipeline("id", "region", SPECS)


class TestStaging(unittest.TestCase):
    def test_csv_source(self):
        csv_text = (
            "id,region,amount,qty\n"
            "k1,east,100.005,1.5\n"
            "k2,east,20.00,N/A\n"
            "k3,west,abc,3\n"
        )
        result = make_pipeline().stage_csv(csv_text, "b1", 1)
        self.assertEqual(len(result.facts), 2)
        self.assertEqual(result.facts[0].measures["amount"], Decimal("100.01"))
        # qty 的 null 策略是 skip -> None，不拒绝
        self.assertIsNone(result.facts[1].measures["qty"])
        self.assertEqual(len(result.rejects), 1)
        reject = result.rejects[0].as_dict()
        self.assertEqual(reject["reason"], RejectReason.PARSE_ERROR)
        self.assertEqual(reject["key"], "k3")
        self.assertEqual(reject["source"], "csv")
        self.assertEqual(reject["batch_id"], "b1")

    def test_json_source_uses_decimal(self):
        json_text = (
            '[{"id": "k1", "region": "east", "amount": 1.005, "qty": 2},'
            ' {"id": "k2", "region": "west", "amount": 1e2, "qty": null}]'
        )
        result = make_pipeline().stage_json(json_text, "b2", 2)
        self.assertEqual(len(result.facts), 2)
        # parse_float=Decimal：1.005 精确解析再 HALF_UP，而非 float 的 1.00
        self.assertEqual(result.facts[0].measures["amount"], Decimal("1.01"))
        self.assertEqual(result.facts[1].measures["amount"], Decimal("100.00"))

    def test_report_source(self):
        report = (
            "# 每日对账报表\n"
            "id | region | amount | qty\n"
            "k1 | east   | 42.10  | 7\n"
            "\n"
            "k2 | west   | -0.00  | 1.25\n"
        )
        result = make_pipeline().stage_report(report, "b3", 3)
        self.assertEqual(len(result.facts), 2)
        self.assertEqual(result.facts[0].measures["amount"], Decimal("42.10"))
        self.assertFalse(result.facts[1].measures["amount"].is_signed())

    def test_missing_key_rejected(self):
        result = make_pipeline().stage_csv(
            "id,region,amount,qty\n,east,1.00,1\n", "b4", 4
        )
        self.assertEqual(len(result.facts), 0)
        self.assertEqual(result.rejects[0].reason, RejectReason.MISSING_FIELD)


class TestCorrectionsAndAggregation(unittest.TestCase):
    def test_repeated_corrections_retract_old_values(self):
        store = FactStore(SPECS)
        store.load(Fact("k1", "east", {"amount": Decimal("100.00"),
                                       "qty": Decimal("1")}, (1, 0)))
        store.load(Fact("k2", "east", {"amount": Decimal("50.00"),
                                       "qty": Decimal("2")}, (1, 1)))
        # 同一 key 两次更正
        self.assertEqual(
            store.load(Fact("k1", "east", {"amount": Decimal("80.00"),
                                           "qty": Decimal("1")}, (2, 0))),
            "corrected",
        )
        store.load(Fact("k1", "east", {"amount": Decimal("120.00"),
                                       "qty": Decimal("3")}, (3, 0)))
        results, rejects = store.result()
        self.assertEqual(rejects, [])
        # 独立参考：只保留各 key 最新值后求和
        self.assertEqual(results["east"]["amount"],
                         ref_sum(["120.00", "50.00"], 2))
        self.assertEqual(results["east"]["qty"], Decimal("5"))
        self.assertEqual(results["east"]["count"], 2)

    def test_correction_can_move_group(self):
        store = FactStore(SPECS)
        store.load(Fact("k1", "east", {"amount": Decimal("10.00"),
                                       "qty": None}, (1, 0)))
        store.load(Fact("k1", "west", {"amount": Decimal("10.00"),
                                       "qty": None}, (2, 0)))
        results, _ = store.result()
        self.assertNotIn("east", results)
        self.assertEqual(results["west"]["amount"], Decimal("10.00"))

    def test_out_of_order_batches(self):
        store = FactStore(SPECS)
        # 新批次先到，旧批次迟到
        store.load(Fact("k1", "east", {"amount": Decimal("120.00"),
                                       "qty": None}, (3, 0)))
        self.assertEqual(
            store.load(Fact("k1", "east", {"amount": Decimal("80.00"),
                                           "qty": None}, (2, 0))),
            "stale",
        )
        self.assertEqual(store.stale, 1)
        results, _ = store.result()
        self.assertEqual(results["east"]["amount"], Decimal("120.00"))

    def test_cross_batch_merge(self):
        store_a = FactStore(SPECS)
        store_a.load(Fact("k1", "east", {"amount": Decimal("1.10"),
                                         "qty": Decimal("1")}, (1, 0)))
        store_a.load(Fact("k2", "east", {"amount": Decimal("2.20"),
                                         "qty": Decimal("2")}, (1, 1)))
        store_b = FactStore(SPECS)
        store_b.load(Fact("k1", "east", {"amount": Decimal("9.90"),
                                         "qty": Decimal("9")}, (2, 0)))
        store_b.load(Fact("k3", "west", {"amount": Decimal("3.30"),
                                         "qty": Decimal("3")}, (2, 1)))
        merged = store_a.merge(store_b)
        results, _ = merged.result()
        # 独立参考：k1 取新值 9.90
        self.assertEqual(results["east"]["amount"],
                         ref_sum(["9.90", "2.20"], 2))
        self.assertEqual(results["west"]["amount"], ref_sum(["3.30"], 2))

    def test_aggregate_overflow_goes_to_rejects(self):
        tiny = {"amount": FieldSpec("amount", precision=3, scale=2)}
        store = FactStore(tiny)
        store.load(Fact("k1", "g", {"amount": Decimal("9.99")}, (1, 0)))
        store.load(Fact("k2", "g", {"amount": Decimal("9.99")}, (1, 1)))
        results, rejects = store.result()
        self.assertNotIn("amount", results["g"])
        self.assertEqual(rejects[0]["reason"], RejectReason.OVERFLOW)


class TestEndToEnd(unittest.TestCase):
    def test_multi_source_pipeline_against_decimal_reference(self):
        pipeline = make_pipeline()
        store = FactStore(SPECS)

        csv_batch = (
            "id,region,amount,qty\n"
            "k1,east,10.005,1.00005\n"
            "k2,east,20.00,2\n"
            "k3,west,1E1,NULL\n"
        )
        json_batch = (
            '[{"id": "k2", "region": "east", "amount": "25.00", "qty": "5"},'
            ' {"id": "k4", "region": "west", "amount": "-0.004", "qty": "0"}]'
        )
        report_batch = (
            "id|region|amount|qty\n"
            "k5|west|7.5|3.3333\n"
        )

        total_rejects = []
        for stager, text, batch_id, seq in (
            (pipeline.stage_csv, csv_batch, "csv-1", 1),
            (pipeline.stage_json, json_batch, "json-2", 2),
            (pipeline.stage_report, report_batch, "rpt-3", 3),
        ):
            staged = stager(text, batch_id, seq)
            store.load_many(staged.facts)
            total_rejects.extend(staged.rejects)

        results, agg_rejects = store.result()
        self.assertEqual(agg_rejects, [])

        # ---- 独立 Decimal 参考计算 ----
        # k2 被 json 批次更正为 25.00；k1 amount 10.005 HALF_UP -> 10.01，
        # qty 1.00005 HALF_EVEN(4 位) -> 1.0000；k4 -0.004 -> -0.00 -> 0.00
        self.assertEqual(results["east"]["amount"],
                         ref_sum(["10.01", "25.00"], 2))
        self.assertEqual(results["east"]["qty"],
                         Decimal("1.0000") + Decimal("5.0000"))
        self.assertEqual(results["west"]["amount"],
                         ref_sum(["10.00", "0.00", "7.50"], 2))
        self.assertEqual(results["west"]["qty"],
                         Decimal("3.3333"))  # k3 qty NULL skip，k4 qty 0
        self.assertEqual(results["east"]["count"], 2)
        self.assertEqual(results["west"]["count"], 3)
        self.assertEqual(total_rejects, [])


if __name__ == "__main__":
    unittest.main()
