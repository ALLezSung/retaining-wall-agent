# 编写普通 Python 脚本型技能

## 最小组成

把技能目录放到配置的 skills.directories 下。只有 `SKILL.md` 就是有效的说明型技能。文件名使用大写；YAML 前置元数据的 name 与目录名一致，使用小写字母、数字和单连字符，长度不超过 64；description 为非空字符串，最长 1024 字符，支持 YAML 多行写法。说明正文描述适用条件、业务流程、参数来源、调用和结果解释。

推荐按用途组织，有内容才创建子目录，不强制所有技能相同布局：

```text
reading-list/
  SKILL.md
  scripts/runner.py                 普通业务脚本
  integrations/design-agent.toml    本应用自动执行与状态提交契约（可选）
```

复杂技能可以在 scripts 内使用普通 Python 包；较长参数表、操作说明、边界解释放入 references，模板或静态数据放入 assets。SKILL.md 直接链接所需参考文件，按需读取，避免复制同一份说明。挡墙示例的内部包位于 scripts/wall_design。

## 可选的应用集成声明

Agent Skills 通用格式不要求 `skill.toml` 或本项目的执行契约。没有宿主扩展的技能仍可被发现和阅读，即使带有 scripts，宿主也不会自动猜测、执行它的入口；本应用不提供任意 shell 模型工具。

需要本应用的动作执行、权威状态或界面集成时，用 SKILL.md 的标准 metadata 扩展字段指向声明文件：

```yaml
---
name: reading-list
description: 记录用户明确提供的书名、标记已读并展示阅读清单。
metadata:
  design-agent-display-name: "阅读清单"
  design-agent-integration: "integrations/design-agent.toml"
---
```

metadata 是字符串映射。解析采用 PyYAML SafeLoader，拒绝重复键、对象构造及 YAML 别名，元数据最多 64 KiB。`design-agent-display-name` 为可选界面展示名，默认使用 name。

`design-agent-integration` 是项目自身约定，不是通用标准字段。指向技能目录内的 TOML 文件，文件名与子目录可以自定；不存在、越界、绝对路径或越界链接均拒绝发现。以下为 integrations/design-agent.toml：

```toml
id = "reading_list"
protocol_version = 1
entry = "scripts/runner.py"
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

`entry` 相对于技能根目录解析，而非 TOML 所在目录；子进程工作目录也是技能根目录。脚本使用 `__file__` 定位内部静态资源，避免依赖启动者的工作目录。可以任意组织脚本内部文件，不要求 tools.py、models.py 等统一布局。

宿主运行 id 默认使用 name，显式 id 必须匹配 `[a-z0-9][a-z0-9_-]*`，跨目录唯一。id、name、中文展示名和状态 owner 分工不同：内置技能为兼容历史继续使用 retaining_wall、reading_list 等运行 id 及 wall_design 等 owner，目录/name 则为 retaining-wall、reading-list；新技能通常让 id 与 name 相同即可。只有状态所有者声明 owner 和完整生命周期；无状态脚本不需要它们。模型不能调用未声明为 action 的生命周期入口。

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

示例位于 `src/skills/reading-list`，脚本仅依赖 Python 标准库，可以复制到项目外运行：

```powershell
Get-Content -Raw -Encoding utf8 examples/reading-list-request.json |
  .venv/Scripts/python.exe -X utf8 src/skills/reading-list/scripts/runner.py --action add
```

结果包含 Book / ReadingList 类型模型产生的状态提案和表格，没有宿主导入。接入应用只需目录和配置，不改内核、协调代码或模型工具列表。

运行 `python -m unittest discover -s tests -p test_skill_protocol.py -v` 检查完整链路与故障边界；`test_skill_layout.py` 验证目录、YAML、集成路径和布局迁移兼容；运行全套测试检查现有业务回归。support.py 仅为测试添加脚本目录导入路径、构造 wire 调用和读取数值证据，不参与应用运行。

## 旧技能兼容

仍支持旧根目录 skill.toml，包含旧的中文 name 和下划线目录名；这是兼容通路，不作为新技能示例。迁移时保留 TOML 中 id、owner 和资源命名空间，将 entry 改成相对技能根目录的新路径，再在标准 SKILL.md 中添加元数据指针。新指针与旧根清单同时存在会报错，避免静默选择其中一份。只有旧清单所在技能沿用旧命名容错。

不会自动修改任何外部技能或本机配置。目录名变化不等于状态版本变化，已有会话的运行 id、已读技能、状态 owner、成果引用和附件目录均保持不变。已有调用 `run_skill_action("retaining_wall", ...)` 不需要调整。旧 Python 实现导入路径已移除；宿主必须继续通过协议执行，算法测试可以从 scripts 导入内部业务包。

## 宿主目录归属

本地目录发现位于 design_agent/infrastructure/catalog.py；元数据、wire 协议和调用协调位于 design_agent/skills，状态与提交规则位于 design_agent/workspace。子进程、SQLite 和附件文件均实现 design_agent/ports.py 的接口，由 bootstrap 装配。技能作者仍只增加技能目录和必要配置，不需要导入任何宿主模块。

输入中的 runtime 是协议字段，与已移除的顶层 Python runtime 包无关。本次只改变技能布局与集成声明定位方式，JSON 协议版本和动作语义不变。
