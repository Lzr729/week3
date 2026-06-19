# Manual Gold说明

## 1. 分层Gold

本项目保留三层人工基准：

1. 页面Gold：确认目标章节和页面；
2. 事件包Gold：确认事件边界和事件类型；
3. 字段级Gold：确认日期、主体、数量、金额、比例和证据。

页面Gold和事件包Gold覆盖8家公司。字段级Gold采用代表性分层样本，用于开发、验证、盲测和错误分析，不把49条自动结果直接复制为Gold。

## 2. 三类字段级Gold

- `subscription_flow_gold.jsonl`：由`manual_gold/fields/subscription_flow/`汇总；
- `share_transfer_flow_gold.jsonl`：由`manual_gold/fields/share_transfer_flow/`汇总；
- `equity_snapshot_gold.jsonl`：一行一股东的平面Gold；
- `equity_snapshot_gold_nested.jsonl`：与自动Schema一致的嵌套快照记录。

## 3. 证据要求

每条Gold至少保留：

- 公司代码；
- package_id或snapshot_id；
- PDF页码；
- 原文证据；
- 原始单位；
- 归一化单位；
- 是否计算字段；
- 复核状态。

AI辅助整理的草案必须通过PDF回源后才能标记为`CONFIRMED`。
