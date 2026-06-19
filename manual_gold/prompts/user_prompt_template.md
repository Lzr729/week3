# User Prompt Template

## 输入

公司代码：`{company_code}`  
公司名称：`{company_name}`  
候选事件包ID：`{package_id}`  
候选事件类型：`{event_family}`  
候选页码：`{pages}`

Schema：

```json
{schema}
```

候选文本：

```text
{candidate_text}
```

## 任务

1. 判断该候选包是否确属`{event_family}`。
2. 确认主体是否为发行人或发行人前身。
3. 提取日期、参与方、数量、金额、价格、比例和事件前后存量。
4. 给出页码和原文证据。
5. 只输出一个JSON对象。
6. 不能确定的字段填`null`，并在`review_reasons`中说明。
7. 计算字段不能伪装成原文披露。
