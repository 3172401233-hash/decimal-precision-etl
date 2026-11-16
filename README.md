# decimal-precision-etl
Python 精确数值 ETL 库，处理多源小数精度、溢出、舍入和跨文件一致性

## 链路概览

```
source(CSV / JSON / 文本报表) -> staging(解析+校验+舍入) -> FactStore(更正/撤回) -> 分组聚合结果
```

整条链路只使用 `decimal.Decimal`，任何一步都不转回 binary float：

- JSON 用 `json.loads(..., parse_float=Decimal)` 解析，浮点字面量不经过 float；
- `parse_value` 显式拒绝 `float` 类型输入（`TYPE_ERROR`）；
- 四则运算（`add/subtract/multiply/divide`）在高精度 `localcontext` 中完成，
  结果量化到目标字段的 scale 并重新校验 precision。

## 字段规范 FieldSpec

```python
FieldSpec(name, precision, scale, rounding="HALF_UP",
          null_policy="reject", null_tokens=(...))
```

- `precision`：允许的最大有效位数（整数位 + 小数位），超限即 `OVERFLOW`；
- `scale`：定点小数位，解析与运算结果统一量化到该位数；
- `rounding`：`HALF_UP / HALF_EVEN / HALF_DOWN / DOWN / UP / CEILING / FLOOR / 05UP`，
  或 `UNNECESSARY`（小数位超出 scale 时不舍入，直接拒绝为 `SCALE_UNREPRESENTABLE`）；
- `null_policy`：`reject`（默认，空值拒绝）/ `zero`（按 0 处理）/ `skip`（该度量不参与聚合）；
- `null_tokens`：空值字面量，默认 `("", "NULL", "null", "N/A", "NA", "n/a", "-", "None")`。

解析细节：支持科学计数法（`1.2345E+3`）；负零统一规范化为正零（`-0.00 -> 0.00`），
保留 scale；`NaN / Infinity`、千分位、非法字面量均拒绝。

## 精度预算

- 字段级：每个度量字段自带 `precision` 预算，解析、运算、聚合输出三处都校验；
- 运算工作精度：`precision + scale + 20`（`core.working_precision`），保证乘除中间
  结果位数充足，最终再按字段 rounding 量化回 scale；
- 聚合累加精度：`AGGREGATE_WORKING_PRECISION = 100` 位有效数字，跨分区、跨批次
  求和不会产生尾差；聚合和输出时若超出字段 precision，该度量进入聚合拒绝列表，
  不写入结果。

## 更正与乱序批次

每条事实带 `version = (batch_seq, row_index)`。`FactStore` 按业务键保存当前生效记录：

- 同 key 更高 version 到达：先 `retract` 旧值（从分组和中减去）再 `apply` 新值；
- 更低 version 迟到（乱序旧批次）：丢弃并计入 `store.stale`；
- `FactStore.merge` 跨批次合并：按 version 顺序重放两个存储的当前事实。

## 拒绝数据格式

staging 拒绝记录（`Reject.as_dict()`）：

```json
{
  "source": "csv | json | report",
  "batch_id": "csv-1",
  "row": 3,
  "key": "k3",
  "field": "amount",
  "raw": "abc",
  "reason": "PARSE_ERROR",
  "detail": "cannot parse 'abc' as decimal"
}
```

`reason` 取值：`PARSE_ERROR`、`TYPE_ERROR`、`NULL_VALUE`、`MISSING_FIELD`、
`OVERFLOW`、`SCALE_UNREPRESENTABLE`、`DIVIDE_BY_ZERO`。

聚合溢出拒绝（`FactStore.result()` 返回的第二项）：

```json
{"group": "east", "field": "amount", "reason": "OVERFLOW",
 "detail": "...", "raw": "123456789.00"}
```

## 运行测试

```
python -m unittest
```

测试覆盖：指数格式、负零、各 rounding 模式、极限整数与溢出、null 策略、
重复更正、批次乱序、跨批次合并，聚合结果均与测试内独立的 Decimal 参考计算核对。
