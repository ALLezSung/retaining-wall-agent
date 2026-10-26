# 通用运行与业务 Skills 的边界

## 四个宿主层次与业务边界

宿主仍分为模型适配（llm）、通用 Agent 内核（agent）、应用宿主（design_agent）、界面（webui）。业务 Skills 是通过 JSON 接入的独立业务包。目录数不等于架构层数，基础设施适配器是应用宿主的内部实现，不再使用顶层 runtime 包。

```text
src/
  llm/                       模型接口与供应商适配
  agent/                     循环、工具、事件、严格 JSON 编解码
  design_agent/
    application.py           对话、恢复、检查点与应用协调
    ports.py                 应用定义的五类依赖接口
    errors.py                应用错误契约
    skills/                  技能元数据、协议与调用编排
    workspace/               状态外壳、成果与提交规则
    infrastructure/          目录、子进程、SQLite 和文件适配器
  skills/
    retaining-wall/          SKILL.md、scripts/wall_design、references、integrations
    reading-list/            SKILL.md、scripts、integrations
    standards/               SKILL.md、scripts、integrations
  webui/                     通用动作与成果展示
  bootstrap.py               本地实现选择和依赖注入
```

agent 只管理模型调用、历史、工具调度、轮数、流式事件及运行上下文。它不发现本地技能，不导入 design_agent 或任何基础设施。严格 JSON 编解码留在 agent.serialization，供宿主复用；技能协议不再被内核反向导入。

DesignAgent 负责对话生命周期与应用协调。skills/capabilities 负责技能生命周期、共享投影、协议检查和动作编排。workspace/service 负责快照、修订、状态归属、成果标识和提交规则；它没有文件系统读写。业务包仍负责业务载荷的内容与校验。

## 依赖倒置与装配

ports.py 由应用定义 ConversationStore、SkillCatalog、SkillExecutor、ResourceRepository、ArtifactStore；ApplicationPorts 将它们组合为明确的构造参数。

```text
DesignAgent / Capabilities / Workspace → 应用接口 ← 本地适配器
                                       ↑
                         bootstrap 创建并注入具体实现
```

- LocalSkillCatalog 实现目录发现与资源读取；元数据和模型工具注册分离。
- ScriptExecutor 只负责有界子进程与 JSON 传输，返回解码结果和独立日志；响应协议由应用服务检查。替换执行器不能绕过协议检查。
- SQLiteConversationStore / SQLiteResourceStore 实现会话与资源存储，保留原事务和修订语义。
- LocalArtifactStore 管理会话目录、暂存、路径保护、附件大小限制、原子写入、SHA-256 和清理；不决定业务状态如何变化。
- Workspace 先验证会话、修订和引用，再请求附件登记；登记失败不更新内存状态和成果引用。

应用策略不导入 infrastructure、bootstrap、subprocess、sqlite3 或 shutil。具体实现只在 bootstrap 选择；新建、载入和旧成果迁移沿用同一组注入依赖，不会偷偷创建默认执行器或存储。

正常本地启动及测试替代模型使用组合根：

```python
from src.bootstrap import create_agent
agent = create_agent()  # 读取配置，装配本地实现
# agent = create_agent(model=fake_model, config=config)
```

需要替换依赖时，使用 DesignAgent(model, config=config, ports=ApplicationPorts(...))，或将 ports 传给 create_agent。DesignAgent 构造函数不再读取配置文件或创建默认适配器；app.py、launch.py 及界面启动入口保留。

测试使用五类纯内存实现，显式禁止 pathlib 文件操作、subprocess.Popen 和 sqlite3.connect，仍能完成聊天、提交、成果下载、保存、载入、资源操作及失败重试。递归静态检查保证内核不依赖应用、应用策略不依赖适配器，生产模块依赖图无环。

技能目录只按声明发现，Python 业务实现从不导入宿主进程。业务脚本不能依赖 Agent、UI、宿主配置或存储，技能之间也不能通过实现导入共享状态。

SKILL.md 使用标准 YAML 前置元数据：name 与目录名一致，中文展示名使用 metadata.design-agent-display-name。本应用通过 metadata.design-agent-integration 读取可选的 integrations/design-agent.toml，它声明动作、生命周期及状态归属，是项目扩展。入口相对技能根目录解析，不相对 TOML 所在目录。旧根 skill.toml 仍可读取，新旧声明同时存在则拒绝。

目录/name 的 retaining-wall、reading-list 与保留的运行 id retaining_wall、reading_list 分开；旧会话、默认技能配置、状态 owner 和成果引用不受布局变化影响。业务脚本是独立文件应用，不再作为 src.skills.retaining_wall 模块被导入；内部 wall_design 包从脚本所在目录加载，业务算法不变。完整布局及编写示例见 skill-authoring.md。

## 谁理解状态，谁提交状态

完整挡墙状态唯一所有者为 `wall_design`。业务包保留 WallInput、WallState 及明确字段白名单、范围、几何、来源、结果与候选校验。部分参数也接受逐字段校验，不退化为万能字典。旧结果恢复时核对原确定性结果，不引入新公式。

宿主只持有 StateEnvelope：session_id、owner、protocol_version、data_version、revision、payload、artifact_refs。业务提供的新载荷作为不透明对象保存；宿主只检查协议与归属。每个所有者有 initialize / restore / project / reset 生命周期入口，调用方式和普通脚本完全相同。

规范选择仍归 `wall_design`。它导出 StandardContextV1，字段为 version、selected、catalog、entries、requirements。规范技能在自己的清单声明只读导入；宿主取得最新投影后注入 shared.standards。无第二份权威参数表，无技能相互导入。

一条聊天支持多个所有者。阅读清单的 Book / ReadingList 模型只控制自己的载荷；默认界面继续显示挡墙。其他技能使用后可显示其业务投影和成果。

## 执行与一致性

1. 按清单解析动作，宿主捕获权威快照，注入输入、上下文和声明资源。
2. 通过参数数组启动脚本，以 UTF-8 JSON 交换；stderr 单独记录。
3. 整体验证响应、会话、所有者、基准修订、成果引用和附件路径。
4. 所有验证通过后，原子更新内存中的状态、成果引用和操作记录。旧执行、失败响应和非法输出均无权修改它们。
5. 完整工具批次后保存可恢复的中断消息、配对模型历史、状态、成果和日志；最终回复完成后替换为最终消息。工具批次内各操作顺序执行，不在 assistant/tool 配对中插入上下文。
6. SQLite 使用事务和 storage_revision 条件检查，拒绝其他实例的旧快照覆盖。数据库故障保留完整内存状态并提示重试，不重新运行脚本。

状态更新与对话保存是两个可见阶段。业务执行成功可以已经在内存生效，但尚未持久化；界面明确提示并阻止新建/切换丢失内容。遇到其他实例的更新冲突时保留内存，不提供强制覆盖按钮。

案例保存是独立资源操作，不与状态提案或成果混合。资源库使用不可覆盖文档标识和操作标识去重；资源库与聊天库不提供分布式事务。

成果 ID 由会话、请求和业务局部 key 生成；附件按 SHA-256 存放并校验。当前引用失效不删除历史成果。文件先于 SQLite 引用写入，因此异常中断可能留下未引用文件；本轮不自动清理历史文件。

## 业务语义与限制

有效改参、替换案例和更换规范会使当前结果与候选失效；同值更新保留有效结果；比选不改参数；选用候选先校验和计算，再提出完整状态。尺寸保护由业务脚本解释当前用户原文。参数不得由模型猜测，q=0 也须由用户明确给出；保留显式矩形等宽推导。

算法与几何搜索基准保持原样。规范仅保留已有摘要、教学校核及待验算说明，不增加合规结论。

本地脚本被视为已配置的可信代码。进程协议和路径限制不是操作系统沙箱；不保证恶意脚本的文件、网络、CPU、磁盘或子进程隔离。无任意代码生成、远程安装和热加载。模型历史尚无长对话压缩，成果文件需随数据库一起备份。
