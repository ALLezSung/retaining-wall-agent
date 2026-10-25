# 重力式挡土墙初步设计智能体

基于兼容 OpenAI Chat Completions 的模型服务、确定性 Python 算法和 Streamlit 的对话式教学初算应用。当前仅支持小于 5 m、干燥、水平无黏性填土、光滑直立墙背、水平基底和非地震工况，可输入均布附加荷载。结果用于教学初算，不能替代完整工程设计。

## 启动

Python 3.10+、Streamlit 1.66+（底部固定输入区需要此版本），进入 `work/` 后执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:DEEPSEEK_API_KEY = "你的密钥"
python -m streamlit run app.py
```

也可执行 `python launch.py`；默认地址为 `http://localhost:8501`。端口冲突时使用 `--server.port 8502`。

复制 `config.example.toml` 为 `config.toml`，填写 `base_url` 和 `api_key` 后重启。启动时读取 `/models`，可在侧边栏底部选择模型和思考深度。列表接口不可用时回退到 `llm.model`，并可手动刷新。项目配置示例：

```toml
[llm]
api_key = ""
model = "deepseek-flash"
base_url = "https://api.deepseek.com"
timeout_seconds = 45
max_tokens = 1200
reasoning_effort = "default"

[agent]
max_rounds = 6

[storage]
cases_database = "data/cases.sqlite3"
conversations_database = "data/conversations.sqlite3"
```

模型配置优先级为 **LLM_* 环境变量 > 旧 DEEPSEEK_* 环境变量 > config.toml > 内置默认值**。配置格式、类型、未知字段和取值范围会在启动时检查，错误直接显示在页面。

| 配置项 | 环境变量 | 含义 |
|---|---|---|
| llm.api_key | LLM_API_KEY / DEEPSEEK_API_KEY | 服务密钥 |
| llm.model | LLM_MODEL / DEEPSEEK_MODEL | 模型名 |
| llm.base_url | LLM_BASE_URL / DEEPSEEK_BASE_URL | 服务基础地址，可含 `/v1`；适配器自动追加 `/chat/completions` |
| llm.timeout_seconds | LLM_TIMEOUT_SECONDS / DEEPSEEK_TIMEOUT_SECONDS | 单次请求超时秒数，正数 |
| llm.max_tokens | LLM_MAX_TOKENS / DEEPSEEK_MAX_TOKENS | 输出 token 上限，正整数 |
| llm.reasoning_effort | LLM_REASONING_EFFORT / DEEPSEEK_REASONING_EFFORT | 初始思考深度，页面可调整 |
| llm.reasoning_overrides | — | 按模型覆盖思考深度到 reasoning_effort 的值映射 |
| agent.max_rounds | AGENT_MAX_ROUNDS | 每轮对话中允许调用工具的模型请求轮数，正整数 |
| storage.cases_database | CASES_DATABASE | 案例库文件；相对路径以 `work/` 为基准，也支持绝对路径 |
| storage.conversations_database | CONVERSATIONS_DATABASE | 聊天及完整设计状态的 SQLite 数据库 |

思考深度优先读取服务返回的 `effort.supported_levels`。DeepSeek 的关闭/低/中/高/最高分别映射为 disabled / low / high / high / max，默认沿用快速模式。已知 OpenAI 推理模型族使用保守档位，未知模型只提供默认设置，不发送未经确认的思考参数。高思考深度可能消耗更多输出 token；可提高 `max_tokens`（例如 8192 或以上）。

特殊兼容服务可以在配置中覆盖 `reasoning_effort` 的值映射：

```toml
[llm.reasoning_overrides."your-model-id"]
low = "minimal"
medium = "medium"
high = "high"
```

此适配器支持 `/models` 和流式 `/chat/completions`，模型需支持工具调用。不支持原生 Anthropic、Gemini 或仅支持 Responses 工具调用的协议；请使用相应的兼容网关。列表按服务声明和已知限制过滤，不额外发送付费探测请求。部分网关只返回模型 ID，无法通用地推断全部能力，此时使用默认设置或显式配置映射。

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
- 侧栏依次为“新建对话”“载入对话”“规范加载”“教学案例库”。
- 新建对话清空参数、当前结果、候选、模型历史和已读技能；模型及规范选择沿用当前设置。
- “载入对话”区域保持约五行高度，内部滚动可浏览全部历史聊天；当前对话高亮。点击标题在原行编辑，回车或移开焦点保存并立即持久化；左侧箭头载入对话。默认标题为创建时间 `年-月-日 时:分:秒`（北京时间），旁边的 × 删除整条聊天。删除当前聊天后自动创建空白对话。
- 教学案例通过“从案例新建对话”载入预存参数，不会自动消耗模型请求；保存参数案例的功能也收纳在这里。
- 新建对话先作为内存草稿，首次发送消息后才自动保存到 SQLite；仅改名、切换模型/规范、载入案例不会新建持久化聊天记录。当前草稿仍可在侧栏显示与改名，放弃草稿不会堆积空历史。
- 已开始的对话与参数、结果、候选、模型历史、已读技能、所选规范一起自动保存到 SQLite；模型请求失败也保留交互记录，重新启动后可从侧栏继续。
- 正文实时流式输出：当前正在生成的文本从第一个字起使用正常正文样式；只有工具事件确认该 Run 是中间说明后，才移入较小、低对比度的无序列表。最终回答沿用原正文槽，中断片段保留正文样式并显示提示；等待区展示当前 Run、执行阶段与已完成工具。保存后载入仍保留同样的层次。
- 模型设置固定在侧边栏底部：第一行下拉选择“模型”，第二行用“思考深度”滑块选择档位，修改立即生效。
- 点击侧栏对话标题可原位改名，回车或移开焦点保存；左侧箭头载入聊天，× 删除。当前聊天以主题色高亮，改名只更新标题，不改动聊天内容和参数。
- 历史列表独立滚动，展示全部已保存聊天；侧栏上部内容较长时可独立滚动，底部模型设置保持可见。

每轮成果收纳为“计算书”和“系统状态”两个折叠卡片。计算书内包含参数表、背景、逐项计算、基底压力、所选规范校核、补充资料与 JSON/Markdown 下载；系统状态包含 Run 执行情况和工具调用明细。历史卡片保留该消息生成时的数据。侧栏提示当前方案是否仍有有效结果；历史卡片不代表修改后的方案。

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
      compatible.py         SSE、模型发现、思考映射、HTTP 与错误转换
      deepseek.py           旧适配器兼容入口
    agent/
      loop.py               无领域分支的顺序执行循环
      tools.py              注册表、运行时 schema 校验、统一工具协议
      skills.py             稳定目录、按标识读取完整说明
      events.py             同进程事件
    design_agent/
      session.py            权威参数表、结果、候选、来源、墙型、规范选择
      application.py        设计状态、聊天操作、上下文与成果入口
      conversations.py      应用定义的会话存储接口
    bootstrap.py            组合根：注入模型与 SQLite 适配器
    persistence/sqlite.py   会话存储接口的 SQLite 实现
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
    test_interaction.py
  docs/migration.md          迁移清单、基准和已知边界
```

依赖方向：网页只调用设计业务入口；设计业务装配通用 agent 和 skill，通过模型协议及 ConversationStore 接口访问外部能力；组合根注入模型与 SQLite 适配器；通用 agent 不知道具体业务含义。算法、校验和报告可以不经过 LLM 独立调用。

## 模型与工具循环

统一模型接口保持向后兼容：

```python
class Model:
    def complete(self, messages, tools) -> ModelResponse:
        ...
```

可选实现 `complete_stream(messages, tools, on_delta)` 返回最终 `ModelResponse` 并通过 `ModelDelta` 推送正文增量与思考活动通知。工具参数分片完全拼接、检查结束原因后才会执行。没有流式接口的旧模型继续使用 `complete`。

给 `DesignAgent(model=替代模型)` 注入实现即可替换模型，不需要改算法或网页。`tests/test_refactor.py` 中的假模型展示了无 API 的执行方式。

每轮请求顺序：

```text
固定 system（身份、通用行为、skill 目录）
历史 user / assistant / assistant工具调用 / tool结果
当前 context 快照
本轮 user
本轮新增 assistant / tool……
```

内部 `context` 由模型适配器映射为靠近本轮输入的 system 消息，不伪装成用户陈述。每轮只生成一次快照，不写入长期历史；工具返回最新状态。同轮后续请求以追加消息延续，assistant/tool 配对完整保留。当前版本保留本会话完整历史，尚未增加长对话压缩。

固定提示词和工具定义不随参数或 skill 加载状态变化，这有利于稳定请求前缀，但不承诺供应商缓存命中。

工具循环默认最多 6 个模型执行轮次，可通过 `agent.max_rounds` 配置；达到上限后追加明确的收尾说明；兼容适配器保留工具定义，并通过 `tool_choice="none"` 请求一次仅汇总已有证据的回答。此时状态为“已达工具轮次上限，可继续执行”，不会标为正常执行完成。原最小模型接口仍可回退为不带工具的最终请求。工具错误以统一结果反馈，允许模型继续；模型配置、网络及响应格式错误作为 `ModelError`，产生 `run_failed` 事件。请求失败前已完成的工具操作和配对历史保留。

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

通用运行事件为 `run_started`、`model_started`、`model_reasoning`、`message_delta`、`model_finished`、`message_completed`、`tool_started`、`tool_finished`、`run_finished`、`run_failed`、`run_limit_reached`、`run_stopped`，携带本轮标识、Run 序号与耗时。业务层另发 `design_updated` 和 `artifact_created`。网页订阅同进程事件并实时展示正文；推理原文不发送到 UI 事件，只为供应商的工具循环协议保留在内部历史中。流式中断会保留已显示文本和已完成操作，未完整返回的工具不会执行。同轮先生成、后失效的临时成果不会作为最终卡片返回。

结果 JSON 保持原核心字段，含输入、适用范围、计算步骤和待验算项。报告由确定性结果直接生成，与模型文字分开。

## 计算、标准与数据边界

沿用朗肯简化模型、原墙体重心公式、抗滑/抗倾覆、全底面或部分底面受压估算。几何搜索步长 0.1 m，底宽上限 10 m，以抗滑≥1.30、抗倾覆≥1.60及基底全接触作教学筛选；不包含真实造价。

基准案例 H=4、B=2.4、t=0.5、γ=18、φ=30、γw=24、μ=0.55、q=0，结果为 Ea=48.0 kN/m、G=139.2 kN/m、Ks=1.595、Ko=3.4175。

所选规范进入近输入的核算要求快照和计算报告：现有 GB 50330 抗滑/抗倾覆给出教学对照，其他规范要求明确为待补充资料、待验算。变更规范使当前结果和候选失效，历史报告保持原记录。不新增未经实现的公式或工程合规结论。

SQLite 默认使用 `work/data/cases.sqlite3`，也可通过配置改变路径。更改路径会打开另一份案例库，不会自动搬迁原数据。聊天默认另存于 `work/data/conversations.sqlite3`，可用 `storage.conversations_database` / `CONVERSATIONS_DATABASE` 配置；与原案例表独立，不改动原案例数据。应用通过 `ConversationStore` 接口注入 SQLite 实现，单个事务保存完整快照。存储失败会提示，内存中的聊天仍可见。模型请求会发送对话、参数和工具结果给配置的服务，请勿输入涉密资料。

## 扩展与验证

新增领域能力时，将纯 Python 实现和说明放入对应 skill，在其 `tools.py` 声明 `Tool(name, description, schema, execute)`，返回统一结果，再由应用启动装配注册。执行函数可直接注册；只有访问当前会话、转换输入或统一错误时才适配。不要在通用 loop 中增加按业务工具名分支。

新增模型供应商时实现 `Model` 协议，供应商消息映射及请求错误转换留在适配器内部。

离线验证：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src app.py launch.py
```

许可证：MIT。


## 多轮核算中断与继续

`agent.max_rounds` 限制一轮用户请求可执行工具的模型轮数（默认 6），第 7 次请求仅收尾，不执行额外工具。达到上限会明确显示暂停，已修改参数与已完成工具保留；如果最后只修改参数而尚未计算，当前结果仍为无效，需要继续核算。发送“继续”可开启新一轮预算。

DeepSeek 的 DSML 工具协议标记不应作为聊天正文返回。适配器会在流式分片期间识别这类异常，阻止原始标记进入显示或被误认为已执行工具；旧历史中的此类异常回复在再次发送给服务时转成明确的未执行说明，不直接修改数据库历史。

现有 compare_designs 只按三项教学条件筛选候选，不包含任意自定义压力上限。额外限制必须用实际计算结果检查；明确获准连续试算时，模型可在同一请求内依次调用改参和计算，减少轮次开销。
