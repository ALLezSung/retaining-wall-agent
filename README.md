# 重力式挡土墙初步设计智能体

基于兼容 OpenAI Chat Completions 的模型服务、确定性 Python 算法和 Streamlit 的对话式教学初算应用。当前仅支持小于 5 m、干燥、水平无黏性填土、光滑直立墙背、水平基底和非地震工况，可输入均布附加荷载。结果用于教学初算，不能替代完整工程设计。

## 启动

Python 3.10+；依赖范围见 `requirements.txt`（Streamlit >=1.66,<2）。本次验证环境为 Python 3.13.5、Streamlit 1.66.0、NumPy 2.5.4、pyarrow 26.0.0，其他版本组合未逐一验证。进入 `work/` 后执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:LLM_API_KEY = "你的密钥"
python -m streamlit run app.py
```

也可执行 `python launch.py`；默认地址为 `http://localhost:8501`。端口冲突时执行 `python -m streamlit run app.py --server.port 8502`。Linux/macOS 可用 `source .venv/bin/activate` 激活环境。

可执行 `Copy-Item config.example.toml config.toml`（已有本机配置时不要覆盖），填写 `base_url` 和 `api_key` 后重启。也支持仅通过环境变量配置；未配置密钥时可浏览页面和使用本地案例，不能调用在线模型。配置密钥后，页面初始化模型设置时读取 `/models`，可在侧边栏底部选择模型和思考深度。列表接口不可用时回退到 `llm.model`，并可手动刷新。以下展示内置默认值；配置模板推荐较大的 `max_tokens=4096`、`max_rounds=10`：

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

模型配置优先级为 **LLM 环境变量 > 旧 DEEPSEEK 环境变量 > config.toml > 内置默认值**。配置格式、类型、未知字段和取值范围会在启动时检查，错误直接显示在页面。

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
- 输入框固定在页面底部；只有生成回答时自动跟随新内容。主动向上滚动或展开计算书后保持阅读位置，章节切换不再触发全页追底。
- 正文实时流式输出：当前正在生成的文本从第一个字起使用正常正文样式；只有工具事件确认该 Run 是中间说明后，才移入较小、低对比度的无序列表。最终回答沿用原正文槽，中断片段保留正文样式并显示提示；等待区展示当前 Run、执行阶段与已完成工具。保存后载入仍保留同样的层次。
- 模型设置固定在侧边栏底部：第一行下拉选择“模型”，第二行用“思考深度”滑块选择档位，修改立即生效。
- 历史列表独立滚动，展示全部已保存聊天；侧栏上部内容较长时可独立滚动，底部模型设置保持可见。

每轮成果收纳为“计算书”和“系统状态”两个折叠卡片。计算书内包含参数表、背景、逐项计算、基底压力、所选规范校核、补充资料与 JSON/Markdown 下载；系统状态包含 Run 执行情况和工具调用明细。历史卡片保留该消息生成时的数据。侧栏提示当前方案是否仍有有效结果；历史卡片不代表修改后的方案。

## 当前架构

采用“通用 Agent 框架＋通用原子能力＋业务 Skills 与普通业务脚本”。DesignAgent 只协调模型、技能目录、执行器、工作区、存储和界面；业务包定义、校验和转换权威业务载荷，宿主检查归属与修订并统一提交。

- 通用模型工具只有四项：read_skill、read_skill_resource、run_skill_action、read_artifact。挡墙的 set_parameters、calculate_design 等是脚本 action，不再注册为独立模型工具。
- 技能目录由 skills.directories 配置，支持纯说明和脚本型技能；启动时稳定发现，不热加载。
- 技能以 SKILL.md 为根入口，代码放 scripts，较长说明放 references；本应用的可选执行契约由 metadata 指向 integrations/design-agent.toml，不是 Agent Skills 标准必需文件。示例见 src/skills/reading-list，挡墙业务见 src/skills/retaining-wall；原运行 id 和状态所有者不变。
- wall_design 唯一拥有完整挡墙状态；规范技能只读其 StandardContextV1 投影。阅读清单示例拥有独立类型模型，可以在同一聊天使用。
- 业务脚本仅使用 JSON 协议，不导入 Agent、界面、宿主配置或存储。原确定性算法和几何搜索保持不变。
- 通用界面读取业务描述中的选项、动作、章节和表格，通过成果类型渲染；模型与思考配置仍由宿主管理。

宿主分为 llm、agent、design_agent、webui 四个层次；skills 是独立业务包。顶层 runtime 已收进 design_agent：skills 管协议和调用编排，workspace 管状态与提交规则，infrastructure 管子进程、SQLite 和文件实现。

应用通过 ports.py 中的接口使用外部能力，由 bootstrap 创建并注入实现。普通调用改用 `from src.bootstrap import create_agent; agent = create_agent(...)`；直接构造 DesignAgent 时必须显式传入 model、config、ApplicationPorts。现有 app.py、launch.py 启动入口保留。

完整说明：

- [架构边界与状态所有权](docs/architecture.md)
- [输入、输出、状态和成果协议（含完整示例）](docs/skill-protocol.md)
- [技能编写与独立运行指南](docs/skill-authoring.md)
- [迁移和兼容说明](docs/migration.md)

## 数据与恢复

现有 data/cases.sqlite3 和 data/conversations.sqlite3 保留原位置。版本 1 聊天由业务脚本恢复为版本 2 工作区；只载入不重写旧行，下一次正常保存才升级。历史正文、trace、候选、计算书和精确下载内容保留。案例原表不删除，保存新案例时按业务规则转换为附加的通用文档集合。

成果附件保存在 skills.workspace 指定目录（默认 data/workspace），采用内容校验和稳定引用。备份必须同时保存两个数据库与整个工作区；仅复制 SQLite 不能带走新附件。文件写入先于数据库引用，异常中断可能留下未引用文件，本轮不自动回收。

SQLite 保存使用修订检查；数据库失败保留内存并提示重试，旧实例不能覆盖新记录。空聊天仍是草稿，首次发送消息才进入聊天历史；案例、规范和模型设置不会制造空聊天。工作区文件可能在草稿阶段产生，但不会新增历史聊天行。

## 验证

使用项目虚拟环境运行：

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -v
.venv/Scripts/python.exe -m compileall -q src tests app.py launch.py
```

测试仅使用假模型与临时数据库。Windows 受限环境若在 asyncio socketpair 初始化处等待，需在允许本机回环连接的环境运行 Streamlit 页面测试。独立协议测试无需 Streamlit：

```powershell
python -m unittest discover -s tests -p test_skill_protocol.py -v
```

覆盖原算法基准、状态失效、候选、旧记录迁移、数据库失败、独立新增技能、协议错误、超时、非零退出、越界路径及过期提交。另有纯内存端口替换、应用禁止直接 I/O、递归依赖边界和模块依赖无环检查。人工假模型预览见 tests/manual/visual/README.md。

本地技能是可信 Python 代码，进程启动与协议限制不构成操作系统安全沙箱。不支持任意代码生成、远程插件安装或热加载。真实模型认证和在线服务行为应单独验证，离线测试不声称覆盖它们。
