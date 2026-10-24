# 重力式挡土墙初步设计智能体

基于 DeepSeek、确定性 Python 算法和 Streamlit 的对话式教学初算应用。当前仅支持小于 5 m、干燥、水平无黏性填土、光滑直立墙背、水平基底和非地震工况，可输入均布附加荷载。结果用于教学初算，不能替代完整工程设计。

## 启动

Python 3.10+，进入 `work/` 后执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:DEEPSEEK_API_KEY = "你的密钥"
python -m streamlit run app.py
```

也可执行 `python launch.py`；默认地址为 `http://localhost:8501`。端口冲突时使用 `--server.port 8502`。

项目配置统一保存在 `config.toml`；

```toml
[llm]
api_key = ""
model = "deepseek-flash"
base_url = "https://api.deepseek.com"
timeout_seconds = 45
max_tokens = 1200

[agent]
max_rounds = 6

[storage]
cases_database = "data/cases.sqlite3"
```

优先级为 **环境变量 > config.toml > 内置默认值**。配置格式、类型、未知字段和取值范围会在启动时检查，错误直接显示在页面。

| 配置项 | 环境变量 | 含义 |
|---|---|---|
| llm.api_key | DEEPSEEK_API_KEY | 服务密钥 |
| llm.model | DEEPSEEK_MODEL | 模型名 |
| llm.base_url | DEEPSEEK_BASE_URL | 服务基础地址，可含 `/v1`；适配器自动追加 `/chat/completions` |
| llm.timeout_seconds | DEEPSEEK_TIMEOUT_SECONDS | 单次请求超时秒数，正数 |
| llm.max_tokens | DEEPSEEK_MAX_TOKENS | 输出 token 上限，正整数 |
| agent.max_rounds | AGENT_MAX_ROUNDS | 每轮对话中允许调用工具的模型请求轮数，正整数 |
| storage.cases_database | CASES_DATABASE | 案例库文件；相对路径以 `work/` 为基准，也支持绝对路径 |

## 使用

可以分两轮输入：

> 墙高4米，墙底宽2.4米。
>
> 墙顶宽0.5米，填土重度18，内摩擦角30度，墙体重度24，基底摩擦系数0.55，无附加荷载。

助手按需读取技能说明、保存条件、追问缺项并调用计算。修改参数后旧结果和候选失效，需要重算；同值重复保存不会清除有效成果。没有强制参数确认表单。

- “把墙高改成4.2米，其他不变”：更新参数，再按要求计算。
- “给几个不同方案”：计算当前方案后进行几何网格比选。
- “墙底宽不能动”：本轮保护底宽，并在比选中锁定。
- “试试第二个方案”：按候选的连续编号选用并重算。
- “为什么抗滑系数是这个值”：读取当前结果及计算步骤解释。
- 侧栏支持载入教学案例、载入已保存案例、保存当前参数与结果。

结果卡片保留该消息生成时的数据，支持 JSON 和 Markdown 下载。侧栏提示当前方案是否仍有有效结果；历史卡片不代表修改后的方案。

## 架构与目录

```text
work/
  app.py                    Streamlit 兼容启动入口
  launch.py
  config.toml               本机配置，不入 Git
  config.example.toml       配置模板
  src/
    config.py               TOML 读取、类型校验及环境变量覆盖
    llm/
      protocol.py           Message / ToolCall / ModelResponse / Model 接口
      deepseek.py           HTTP、配置、供应商消息映射、ModelError
    agent/
      loop.py               无领域分支的顺序执行循环
      tools.py              注册表、运行时 schema 校验、统一工具协议
      skills.py             稳定目录、按标识读取完整说明
      events.py             同进程事件
    design_agent/
      session.py            权威参数表、结果、候选、来源、墙型、规范选择
      application.py        应用装配、状态更新、上下文与成果入口
    skills/
      retaining_wall/
        SKILL.md            挡墙流程、参数、范围和局限
        tools.py            工具声明、执行、业务适配
        models.py           输入模型与字段
        validation.py       共用校验
        calculation.py      原确定性计算
        variants.py         原几何搜索
        report.py           Markdown 报告
        cases.py            SQLite 案例
      standards/
        SKILL.md            标准摘要查询说明
        tools.py            标准工具注册
        index.py            原摘要、元数据与覆盖说明
    webui/app.py            Streamlit 组件及成果展示
  tests/
    fixtures/legacy_cases.json
    test_refactor.py
    test_config.py
    test_webui.py
  docs/migration.md          迁移清单、基准和已知边界
```

依赖方向：网页只调用设计业务入口；设计业务装配通用 agent、模型和 skill；通用 agent 不知道具体业务含义。算法、校验和报告可以不经过 LLM 独立调用。

## 模型与工具循环

统一模型接口：

```python
class Model:
    def complete(self, messages, tools) -> ModelResponse:
        ...
```

给 `DesignAgent(model=替代模型)` 注入实现即可替换模型，不需要改算法或网页。`tests/test_refactor.py` 中的假模型展示了无 API 的执行方式。

每轮请求顺序：

```text
固定 system（身份、通用行为、skill 目录）
历史 user / assistant / assistant工具调用 / tool结果
当前 context 快照
本轮 user
本轮新增 assistant / tool……
```

内部 `context` 由 DeepSeek 适配器映射为靠近本轮输入的 system 消息，不伪装成用户陈述。每轮只生成一次快照，不写入长期历史；工具返回最新状态。同轮后续请求以追加消息延续，assistant/tool 配对完整保留。当前版本保留本会话完整历史，尚未增加长对话压缩。

固定提示词和工具定义不随参数或 skill 加载状态变化，这有利于稳定请求前缀，但不承诺供应商缓存命中。

工具循环默认最多 6 个模型执行轮次，可通过 `agent.max_rounds` 配置；达到上限后请求一次不开放工具的最终回答。工具错误以统一结果反馈，允许模型继续；模型配置、网络及响应格式错误作为 `ModelError`，产生 `run_failed` 事件。请求失败前已完成的工具操作和配对历史保留。

## Skill 渐进披露

启动时发现两个 `SKILL.md` 的元数据并注册所有工具，模型初始只看到稳定目录。首次开展对应业务前，通过 `read_skill(name)` 按目录 id 读取说明；正文作为工具结果保留在本会话历史中。读取不改变工具 schema，不上传 Python 源码，也不增加“未读则禁止计算”的门禁。

稳定工具列表共 10 项：

| Skill | 工具 |
|---|---|
| 基础设施 | read_skill |
| retaining_wall | set_parameters、validate_parameters、calculate_design、compare_designs、apply_option、get_current_result、list_cases、load_case |
| standards | lookup_standard |

新建对话会同时清空设计数据、模型历史和已读 skill 状态。没有 skill 搜索、重排序、动态安装或热更新。

## 校验、成果和事件

`validate_parameters` 与 `calculate_design` 复用 `check_parameters`，计算不依赖模型先调用校验。对话沿用原约定：8 项参数都要明确给出，无附加荷载须保存 q=0；独立调用原 `validate` / `calculate_wall` 仍兼容 q 缺省为 0。

工具 schema 在运行时检查对象、必需字段、未知参数、数组、类型、枚举和数值上下限。统一返回：

```json
{"ok": true, "data": {}, "error": null}
```

或：

```json
{"ok": false, "data": null, "error": {"code": "INVALID_INPUT", "message": "说明"}}
```

错误码仅包括 `MISSING_INPUT`、`INVALID_INPUT`、`INVALID_STATE`、`EXECUTION_FAILED`。

通用运行事件为 `message_completed`、`tool_started`、`tool_finished`、`run_finished`、`run_failed`；业务层另发 `design_updated` 和 `artifact_created`。这些是同进程回调，尚无 token 流式输出。网页消费最终成果集合；同轮先生成、后失效的临时成果不会作为最终卡片返回。

结果 JSON 保持原核心字段，含输入、适用范围、计算步骤和待验算项。报告由确定性结果直接生成，与模型文字分开。

## 计算、标准与数据边界

沿用朗肯简化模型、原墙体重心公式、抗滑/抗倾覆、全底面或部分底面受压估算。几何搜索步长 0.1 m，底宽上限 10 m，以抗滑≥1.30、抗倾覆≥1.60及基底全接触作教学筛选；不包含真实造价。

基准案例 H=4、B=2.4、t=0.5、γ=18、φ=30、γw=24、μ=0.55、q=0，结果为 Ea=48.0 kN/m、G=139.2 kN/m、Ks=1.595、Ko=3.4175。

勾选规范控制查询及覆盖展示，不切换公式或教学阈值，不产生新增规范符合性结论。

SQLite 默认使用 `work/data/cases.sqlite3`，也可通过配置改变路径。更改路径会打开另一份案例库，不会自动搬迁原数据。案例是本地长期存储；会话和聊天历史仍在内存中。模型请求会发送对话、参数和工具结果给配置的服务，请勿输入涉密资料。

## 扩展与验证

新增领域能力时，将纯 Python 实现和说明放入对应 skill，在其 `tools.py` 声明 `Tool(name, description, schema, execute)`，返回统一结果，再由应用启动装配注册。执行函数可直接注册；只有访问当前会话、转换输入或统一错误时才适配。不要在通用 loop 中增加按业务工具名分支。

新增模型供应商时实现 `Model` 协议，供应商消息映射及请求错误转换留在适配器内部。

离线验证：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src app.py launch.py
```

许可证：MIT。
