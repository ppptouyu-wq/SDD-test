# 数据模型定义（data-models.md）

> 契约来源：书中图 5-6（数据模型）、图 5-7（成员身份映射表）、图 5-8（模块接口契约）。
> 本文件是 `specs/design.md` §3、§4 的字段级展开，供采集层与生成层对齐。

## 1. 采集层记录

### CommitRecord（代码提交记录）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| author | str | 是 | 提交者（GitHub 用户名） |
| message | str | 是 | 提交信息（Commit Message） |
| timestamp | datetime | 是 | 提交时间 |
| repo | str | 是 | 仓库名称 |
| additions | int | 是 | 新增行数 |
| deletions | int | 是 | 删除行数 |
| files_changed | int | 是 | 变更文件数 |

### TaskRecord（任务变更记录）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| assignee | str | 是 | 负责人（飞书用户名） |
| title | str | 是 | 任务标题 |
| status_from | str | 是 | 原状态 |
| status_to | str | 是 | 新状态 |
| updated_at | datetime | 是 | 变更时间 |

### MessageRecord（消息记录）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| sender | str | 是 | 发送者（飞书用户名） |
| content | str | 是 | 消息内容（纯文本） |
| timestamp | datetime | 是 | 发送时间 |
| chat_name | str | 是 | 群名称 |

## 2. 聚合层模型

### MemberReport（成员报告）
| 字段 | 类型 | 说明 |
|---|---|---|
| name | str | 成员姓名 |
| github_username | str | GitHub 用户名 |
| commits | list[CommitRecord] | 代码提交记录 |
| tasks | list[TaskRecord] | 任务变更记录 |
| messages | list[MessageRecord] | 相关消息记录 |
| attendance | AttendanceRecord \| None | 考勤记录（v1.1 新增） |

### AttendanceRecord（考勤记录）— v1.1 新增
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| employee_id | str | 是 | 飞书用户 ID |
| date | date | 是 | 考勤日期 |
| check_in | datetime \| None | 否 | 签到时间；缺失时为 None |
| check_out | datetime \| None | 否 | 签退时间；缺失时为 None |
| work_hours | float | 是 | 工时（小时）= check_out - check_in，保留一位小数；缺任一时刻则为 0 |
| status | str | 是 | **枚举：正常 / 迟到 / 早退 / 缺勤 / 休假 / 签退缺失** |

> `status` 的取值受枚举约束：**缺失签退数据时必须为"签退缺失"**（tasks.md Task 11）。
> 该枚举在 `api-spec.yaml` 中同样声明，两处不得矛盾。
> 示例：某人 09:32 签到但无签退记录 → `check_out=None`、`work_hours=0.0`、`status="签退缺失"`。

### DailyReport（每日报告）
| 字段 | 类型 | 说明 |
|---|---|---|
| date | date | 日报日期 |
| team_name | str | 团队名称 |
| members | list[MemberReport] | 各成员们的日报段落 |
| generated_at | datetime | 生成时间 |
| markdown | str | 完整的 Markdown 格式日报 |
| html | str | 完整的 HTML 格式日报 |

## 3. 聚合关系

```
DailyReport ──1:N──▶ MemberReport ──1:N──▶ CommitRecord
                                  ├─1:N──▶ TaskRecord
                                  ├─1:N──▶ MessageRecord
                                  └─1:1──▶ AttendanceRecord (v1.1)
```

## 4. 采集层统一返回类型（ADR-003）

v1.0 的 `collect()` 返回 `list[Record]`，无法区分"今日确实没有数据"与"API 调用失败"。
v1.1 按 `proposal.md` § 3.2"采集失败必须显式标注、严禁静默跳过"的要求改为：

```python
CollectResult[T]:
  success: bool              # 采集是否成功
  records: list[T]           # 采集到的记录（失败时为空列表）
  error_message: str | None  # 失败原因（成功时为 None）
```

## 5. 成员身份映射（config.yaml 片段）

```yaml
members:
  - name: "张三"
    github: "zhangsan"
    lark: "zhangsan@company.com"
  - name: "李四"
    github: "lisi-dev"
    lark: "lisi@company.com"
  - name: "王五"
    github: "wangwu"
    lark: "wangwu@company.com"
```

**映射规则**：`CommitRecord.author ↔ members[].github`；`TaskRecord.assignee`、
`MessageRecord.sender ↔ members[].lark`；`AttendanceRecord.employee_id ↔ members[].lark`
（飞书用户 ID 与邮箱在本项目内视为同一标识，由配置统一维护）。
