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
| sources | dict[str, bool] | 各数据源当日采集成功/失败（v1.2 新增），键为数据源名（`github`/`lark_task`/`lark_msg`/`lark_attendance`），值为是否成功。展示层据此推导 status；**v1.2 之前的历史记录可能为空字典** |

> `sources` 是"采集失败必须显式标注"（`proposal.md` §3.1）在数据模型上的落点：
> 只有把每个数据源的成败随日报一起持久化，展示层才能对失败数据源标注"数据获取失败"，
> 并把它与"当日确实没有数据"区分开。该字段同时在 `api-spec.yaml` 的 `DailyReport` 中声明，两处不得矛盾。

## 3. 展示层视图模型（v1.2 迭代新增）

展示层不直接把 `DailyReport` 交给页面，而是先转换成视图模型
（`design.md` §3.2）：一是把"数据源成败"翻译成页面可直接渲染的四态，
二是把日期等字段规范化，三是**明确标出哪些缺失是可解释的、哪些是异常的**。

### ReportListItem（日报列表项）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| date | date | 是 | 日报日期 |
| team_name | str | 是 | 团队名称 |
| generated_at | datetime | 是 | 生成时间 |
| member_count | int | 是 | 成员数（≥0） |
| status | str | 是 | **枚举：ok / partial / failed / unknown**，由 `sources` 推导 |

`status` 推导规则（唯一定义处，页面与测试均以此为准）：

| 条件 | status | 页面表现 |
|---|---|---|
| `sources` 非空且全部为 `True` | `ok` | 正常态 |
| 至少一个 `False` 且至少一个 `True` | `partial` | 标注"数据获取失败"的数据源名 |
| 全部为 `False` | `failed` | 显著告警：当日日报数据源全部失败 |
| `sources` 为空（v1.2 之前的历史记录未记录来源） | `unknown` | 中性标注"未记录数据源状态"；**不得**升格为 `failed` |

### SourceView（数据源结果视图）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | str | 是 | 数据源名 |
| success | bool | 是 | 是否采集成功 |
| error | str \| None | 否 | 失败原因；成功时为 None |

### MemberView / AttendanceView / CommitView / TaskView / MessageView
成员视图按 `design.md` §3.2 定义，与 `MemberReport` 的差别是：
`attendance` 为 `AttendanceView | None`，`None` 在页面上的语义是**"考勤数据暂不可用"**，
而不是"该成员今天没有考勤"。

> **时间字段的线上格式**（v1.2 补充约定）：`AttendanceView.check_in` / `check_out`、
> `CommitView.timestamp`、`TaskView.updated_at`、`MessageView.timestamp`、
> `ReportListItem.generated_at` 一律是**完整 ISO 8601 字符串**
> （如 `2026-09-30T09:02:00`）。**视图层不做截断**，由页面在渲染时取 `HH:MM`
> 或 `YYYY-MM-DD HH:MM`。
>
> 这条约定是补写的：v1.2 首版没有写明，页面的考勤格按 `"09:02"` 这种短格式解析，
> 而视图层按上面的约定发的是完整 ISO，结果签到/签退两列把
> `2026-09-30T09:02:00` 原样显示了出来。同一条日报里 `CommitView.timestamp`
> 等字段一直是完整 ISO（页面用 `value.slice(11, 16)` 的方式格式化），考勤是唯一的例外。

### ReportDetail（日报详情）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| list_item | ReportListItem | 是 | 列表项（复用状态推导结果） |
| members | list[MemberView] | 是 | 各成员视图 |
| sources | list[SourceView] | 是 | 各数据源结果 |
| generated_at | datetime | 是 | 生成时间 |

> **三种异常状态不得互相顶替**（`proposal.md` §3.1 v1.2）：
> 1. **数据源采集失败** → `status` 为 `partial`/`failed`（**不含 `unknown`**），页面标注"数据获取失败"；
> 2. **考勤数据不可用** → `MemberView.attendance is None`，页面显示"考勤数据暂不可用"；
> 3. **当日确实没有数据** → 对应板块为空列表，页面显示"今日无记录"。
>
> 三者是三个独立维度（数据源维度、字段维度、记录维度），页面必须分别呈现。
>
> **`unknown` 不在这三者之内**：它描述的是"这条日报在生成时没有记录数据源状态"（`sources` 为空），
> 属于**元数据缺失**，既不是采集失败、也不是"当天没有数据"，因此不参与告警色阶，用中性色呈现。

## 4. 聚合关系

```
DailyReport ──1:N──▶ MemberReport ──1:N──▶ CommitRecord
                                  ├─1:N──▶ TaskRecord
                                  ├─1:N──▶ MessageRecord
                                  └─1:1──▶ AttendanceRecord (v1.1)
DailyReport.sources ──1:N──▶ SourceView (v1.2，仅展示层)
```

## 5. 采集层统一返回类型（ADR-003）

v1.0 的 `collect()` 返回 `list[Record]`，无法区分"今日确实没有数据"与"API 调用失败"。
v1.1 按 `proposal.md` § 3.2"采集失败必须显式标注、严禁静默跳过"的要求改为：

```python
CollectResult[T]:
  success: bool              # 采集是否成功
  records: list[T]           # 采集到的记录（失败时为空列表）
  error_message: str | None  # 失败原因（成功时为 None）
```

## 6. 成员身份映射（config.yaml 片段）

```yaml
members:
  - name: "张三"
    github: "zhangsan"
    lark: "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"   # 飞书 open_id
    lark_employee_id: "1001"                       # 飞书员工ID（仅考勤用）
  - name: "李四"
    github: "lisi-dev"
    lark: "ou_yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
    lark_employee_id: "1002"
  - name: "王五"
    github: "wangwu"
    lark: "ou_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    lark_employee_id: "1003"
```

**映射规则**：`CommitRecord.author ↔ members[].github`；`TaskRecord.assignee`、
`MessageRecord.sender ↔ members[].lark`（飞书 open_id）；
`AttendanceRecord.employee_id ↔ members[].lark_employee_id`（飞书员工 ID）。

**字段说明（v1.3 修正）**：`lark` 与 `lark_employee_id` 是**两个不同的值**，不可互相替代 ——
飞书考勤接口只接受 `employee_id` / `employee_no`（传 `open_id` 被拒），
任务/消息接口给的是 `open_id`。`lark` 为必填，`lark_employee_id` 可选（缺省时考勤按空名单发出，
会以 `success=False` 显式失败而非静默返回空结果）。
