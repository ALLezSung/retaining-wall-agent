# 编写普通 Python 脚本型技能

## 最小组成

把技能目录放到配置的 skills.directories 下。只有 `SKILL.md` 就是有效的说明型技能。说明文件须含 name、description 元数据，并说明适用条件、业务流程、参数来源、动作调用和结果解释。

需要执行时添加 `skill.toml`。这是本项目自身约定，并非通用 SKILL.md 标准：

```toml
id = "reading_list"
protocol_version = 1
entry = "runner.py"
owner = "reading_list"

[lifecycle]
initialize = "initialize"
restore = "restore"
project = "project"
reset = "reset"

[actions.add]
label = "添加书目"
[actions.show]
label = "展示阅读清单"
```

`entry` 可以放在技能内任意子目录，不要求 tools.py、models.py 等统一布局。id 必须匹配 `[a-z][a-z0-9_-]*`，跨目录唯一。只有状态所有者声明 owner 和完整生命周期；无状态脚本不需要它们。模型不能调用未声明为 action 的生命周期入口。

可选 limits 指定 timeout_seconds、stdout_bytes、stderr_bytes，只能收紧宿主的 60 秒、2 MiB、256 KiB 上限。缺失入口的技能仍可读说明，其动作显示不可执行。目录启动时冻结，添加后重启应用。

跨技能只读输入用声明连接业务投影：

```toml
[imports.standards]
owner = "wall_design"
export = "standard_context"
```

脚本从 `shared.standards` 读取数据；不导入另一技能的模块。状态所有者通过 project 返回 `exports.standard_context`。不要建立循环依赖。

## 输入、脚本和状态

脚本入口接收 `--action 操作名`，从 stdin 读一个请求，从 stdout 写一个完整 JSON 响应。日志写 stderr。业务模型、校验、迁移和转换都在自己的包内；不导入 Agent、DesignAgent、Streamlit、宿主配置或存储。

使用 request.input 作为本次输入，request.snapshot 作为权威基准。来源、当前用户原文和操作标识由宿主放在 runtime，不能相信模型自报的来源。失败时返回空提案，不能边执行边写宿主数据库。

更改状态时返回完整载荷并回显 session_id、owner、base_revision。宿主生成新 revision。artifact_refs 使用保留的旧 ID 或本次成果的局部 key；空列表表示清空当前成果引用。业务数据版本只能由业务迁移处理，未知版本应拒绝恢复。

project 必须只读，返回 `model_result.context`、`exports` 和 `view`。context 是每轮注入模型的简短投影；不要包含全报告、原始集合或密钥。reset 决定哪些业务偏好沿用，宿主不理解偏好内容。

资源操作可声明 resource_namespace 和 legacy_tables。宿主只注入该集合文档与声明的原始表行，脚本负责解释历史格式。返回 resource_proposals 时必须是独立操作，不能同时提交状态或成果。参考挡墙案例转换；不要申请对话表作为业务资源。

## 成果和最小界面

成果类型为 text、table、report、attachment。表格给 rows；报告给 content 和 sections；附件写入 runtime.staging_dir 并返回相对 path、filename、mime、label。单附件上限 16 MiB，不能引用暂存区外的文件。成果摘要默认进入模型上下文，全文按需读取。

view 可以提供标题、欢迎语、输入提示、参数表、状态说明和 panels。panel 包含标题、展开状态、说明及 controls。仅支持现有场景使用的 multiselect、select_action、text_action、button：每个控件指定 id、label、action、argument 及值/选项；按钮可声明 new_conversation。控件目标必须是本技能已声明动作。

选项采用 `{value,label,note?}`；多选采用字符串 options 和当前 value。enabled 控制动作可用性。界面只决定如何呈现控件，业务脚本仍须重新校验所有输入。

## 独立运行示例

示例位于 `src/skills/reading_list`，仅依赖 Python 标准库，可以复制到项目外运行：

```powershell
Get-Content -Raw -Encoding utf8 examples/reading-list-request.json |
  .venv/Scripts/python.exe -X utf8 src/skills/reading_list/runner.py --action add
```

结果包含 Book / ReadingList 类型模型产生的状态提案和表格，没有宿主导入。接入应用只需目录和配置，不改内核、协调代码或模型工具列表。

运行 `python -m unittest discover -s tests -p test_skill_protocol.py -v` 检查完整链路与故障边界；运行全套测试检查现有业务回归。测试中的 support.py 仅辅助构造迁移后的 wire 调用和读取数值证据，不参与应用运行。

## 宿主目录归属

本地目录发现位于 design_agent/infrastructure/catalog.py；元数据、wire 协议和调用协调位于 design_agent/skills，状态与提交规则位于 design_agent/workspace。子进程、SQLite 和附件文件均实现 design_agent/ports.py 的接口，由 bootstrap 装配。技能作者仍只增加技能目录和必要配置，不需要导入任何宿主模块。

输入中的 runtime 是协议字段，与已移除的顶层 Python runtime 包无关。协议版本、技能清单和脚本调用方式保持不变。
