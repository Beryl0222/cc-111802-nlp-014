# 课堂反馈情绪复核台

课堂反馈情绪复核台用于处理本地合成教研文本，保存来源版本、文本片段和处理记录，供校内复核使用。系统把情绪线索、课程上下文和教师复核决定关联在同一份记录版本上，班级汇总与待跟进名单都只使用同一批有效判断。

## 环境

需要 Python 3.11 或更新版本。应用运行不依赖第三方包；运行测试和构建前，安装固定版本的开发工具：`python -m pip install -r requirements-dev.txt`。

## 运行

执行 `PYTHONPATH=src python -m nlp_student_feedback_sentiment.cli ingest fixtures/records.json` 导入演练记录，再执行 `PYTHONPATH=src python -m nlp_student_feedback_sentiment.cli list` 查看当前文本处理结果。运行数据保存在 `var/records.json`。

```bash
CLI="python -m nlp_student_feedback_sentiment.cli"
export PYTHONPATH=src
$CLI ingest fixtures/records.json
$CLI list                           # 不返回原文；--reviewer <id> 才按授权返回
$CLI evaluate                       # 每条记录的有效判断（自动 + 教师复核）
$CLI review --record <id> --reviewer teacher-a --decision dismiss --note "同伴玩笑"
$CLI follow-ups                     # 待跟进名单：仅风险与待判断
$CLI summary --group-by course_id   # 匿名分组汇总（默认小于 2 人的组被抑制）
$CLI evidence --record <id> --reviewer teacher-a   # 授权回看原文与复核依据
```

`--state` 可指定持久化路径。记录可带 `course_id`、`class_id` 和 `authorized_reviewers` 字段；未配置授权名单时，任何填写了身份的复核人可操作。

## 判断规则

- **情绪线索**：受挫、求助、回避等负向线索与积极线索分开记录，逐条标注位置、是否被否定、是否处于引用/术语语境。
- **上下文**：`context` 为空时不会把“没有发现风险”当作结论，而是标为待判断；术语、引用、同伴玩笑、反讽会降低字面判断的可信度。
- **教师复核**：`confirm`（确认风险）、`dismiss`（确认特殊语境、非风险）、`needs_context`（待补充信息）。确认与排除必须写明依据。同一版本以最近一次复核为准，历史事件保留可审计。
- **版本联动**：复核事件绑定记录内容摘要（正文 + 上下文）。反馈正文或上下文被新版本替换后，旧结论自动失效，按新版本重新评估并标记需再次复核；仅修订号变化而内容未变时，教师结论继续有效。
- **批次隔离**：复核事件只追加，不随导入重建；无关批次或同版本重放不会覆盖教师确认的特殊语境。
- **一致汇总**：`summary` 与 `follow-ups` 读取同一套有效判断。待判断单独计数，不计入非风险，也不从风险名单中消失。
- **隐私**：匿名汇总只含分组计数，不含记录 ID、原文或复核人；`list` 默认不含原文，个案原文仅通过 `evidence` 在授权范围内返回。

## 测试

执行 `python -m pytest -q` 检查文本规范化、情绪线索、上下文缺失、教师复核联动、版本重评估、汇总一致性与匿名化。
