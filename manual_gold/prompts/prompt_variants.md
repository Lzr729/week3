# Prompt Variants and Sensitivity

## 使用背景

最终批量流水线为规则方法，LLM只用于代码开发辅助、字段定义讨论和人工Gold草案整理。由于采用交互式ChatGPT而非程序化API，未设置或记录temperature、max_tokens等API参数；该限制在`ai_usage_statement.md`中披露。

## Variant A：直接抽取全部字段

```text
请从这些页面中抽取公司的增资、股权转让和股权结构，输出完整JSON。
```

观察到的问题：

- 事件边界容易混合；
- 增资和股权转让可能放在同一条记录；
- 容易使用第一处金额而不是事件汇总金额；
- 缺少Schema约束；
- 不确定字段可能被过度补全；
- 不利于区分人工Gold和自动输出。

## Variant B：事件包、Schema和证据优先

```text
只处理指定package_id和event_family。
严格按照给定Schema输出。
每个字段必须有候选页中的证据。
未直接披露的字段填null或标记calculated。
先确认发行人主体和事件边界，再抽取参与方及数值。
```

观察到的改进：

- 输出结构更稳定；
- 主体混淆减少；
- 日期、金额和单位更容易逐字段比较；
- 能生成明确的review_reasons；
- 便于将自动输出与人工Gold分离；
- 仍无法稳定解决跨页复杂表格和多阶段混合事件。

## 最终采用方式

- 页面定位和事件包切分：规则；
- 最终批量字段抽取：冻结规则脚本；
- Schema校验：JSON Schema + Pydantic；
- 数值核验：确定性Cross-check；
- LLM：开发辅助和人工标注草案，不直接写入最终批量自动结果；
- 不确定记录：人工复核队列。
