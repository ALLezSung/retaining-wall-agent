# 脚本输入、状态提案与成果协议 v1

协议与业务数据版本独立。模型只能提供 action 的 input；snapshot、shared、resources、runtime 由宿主注入。下面每个示例均给出完整结构；机器可读版本在 tests/fixtures/protocol_examples.json，并由自动测试校验。

## 请求

一次执行的会话、所有者和基准修订由 snapshot 决定，不接收模型提供的权威快照。runtime.staging_dir 是宿主创建的本次输出目录，响应中的附件路径必须在此目录内。

```json
{
  "protocol_version": 1,
  "request_id": "req-001",
  "skill_id": "reading_list",
  "action_id": "add",
  "input": {
    "title": "算法导论"
  },
  "runtime": {
    "run_id": "run-001",
    "run": 1,
    "call_id": "call-001",
    "operation_id": "run-001:call-001",
    "source": "model",
    "user_text": "把算法导论加入清单",
    "staging_dir": "."
  },
  "snapshot": {
    "session_id": "session-001",
    "owner": "reading_list",
    "protocol_version": 1,
    "data_version": 1,
    "revision": "rev-001",
    "payload": {
      "books": []
    },
    "artifact_refs": []
  },
  "shared": {},
  "resources": {}
}
```

## 无状态成功

```json
{
  "protocol_version": 1,
  "request_id": "req-001",
  "status": "ok",
  "model_result": {
    "message": "已读取清单"
  },
  "state_proposal": null,
  "artifacts": [],
  "resource_proposals": [],
  "error": null
}
```

## 业务失败

```json
{
  "protocol_version": 1,
  "request_id": "req-001",
  "status": "business_error",
  "model_result": {},
  "state_proposal": null,
  "artifacts": [],
  "resource_proposals": [],
  "error": {
    "code": "INVALID_INPUT",
    "message": "书名必须是非空字符串。"
  }
}
```

## 参数不足

```json
{
  "protocol_version": 1,
  "request_id": "req-001",
  "status": "missing_input",
  "model_result": {
    "missing": [
      "title"
    ]
  },
  "state_proposal": null,
  "artifacts": [],
  "resource_proposals": [],
  "error": {
    "code": "MISSING_INPUT",
    "message": "还缺少书名。"
  }
}
```

## 状态变化与表格

```json
{
  "protocol_version": 1,
  "request_id": "req-001",
  "status": "ok",
  "model_result": {
    "count": 1,
    "message": "已添加书目"
  },
  "state_proposal": {
    "session_id": "session-001",
    "owner": "reading_list",
    "base_revision": "rev-001",
    "data_version": 1,
    "payload": {
      "books": [
        {
          "title": "算法导论",
          "read": false
        }
      ]
    },
    "artifact_refs": [
      "books"
    ]
  },
  "artifacts": [
    {
      "key": "books",
      "kind": "table",
      "title": "阅读清单",
      "summary": "共 1 本书",
      "rows": [
        {
          "序号": 1,
          "书名": "算法导论",
          "状态": "未读"
        }
      ]
    }
  ],
  "resource_proposals": [],
  "error": null
}
```

## 报告附件

```json
{
  "protocol_version": 1,
  "request_id": "req-001",
  "status": "ok",
  "model_result": {
    "message": "报告已生成"
  },
  "state_proposal": null,
  "artifacts": [
    {
      "key": "report",
      "kind": "report",
      "title": "阅读报告",
      "summary": "1 本未读书目",
      "content": "本清单含 1 本书。",
      "sections": [
        {
          "title": "书目",
          "content": "算法导论：未读"
        }
      ],
      "attachments": [
        {
          "path": "reading-report.md",
          "filename": "reading-report.md",
          "mime": "text/markdown",
          "label": "下载阅读报告"
        }
      ]
    }
  ],
  "resource_proposals": [],
  "error": null
}
```

## 协议错误（故意无效）

```json
{
  "protocol_version": 999,
  "request_id": "req-001",
  "status": "ok",
  "model_result": {
    "message": "已读取清单"
  },
  "state_proposal": null,
  "artifacts": [],
  "resource_proposals": [],
  "error": null
}
```

宿主返回模型的错误：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "INVALID_RESPONSE",
    "message": "技能响应不符合协议，未提交业务变化。"
  }
}
```

## 成果登记与提交

脚本使用局部 key，宿主登记后分配稳定 id，补充 session_id、owner、revision。附件暂存路径转换为工作区内 files/SHA256 引用并记录摘要校验值。artifact_refs 可以引用本次局部 key 或该所有者已有 ID；未知引用、越界路径及无效附件使整次响应失败。group 是可选的界面收纳标题，不影响业务逻辑。

普通执行返回模型的是统一 `{ok,data,error}` 外壳，data 来自 model_result，另含本次成果摘要；完整提案、日志、原始资源和附件字节不默认进入模型。read_artifact 按当前会话成果 ID 读取正文、章节、表格和附件元数据；下载通过界面的通用附件入口读取字节并校验 SHA-256。

业务失败和参数不足必须满足 state_proposal=null、artifacts=[]、resource_proposals=[]；错误有 code、message。协议错误由宿主产生，不能假装业务成功。

## 生命周期与投影

清单将 initialize / restore / project / reset 映射到脚本操作。它们采用相同请求响应结构，由宿主触发。restore 校验当前数据版本或迁移旧业务载荷；project 禁止任何写入提案，model_result 返回 context、view、exports。普通动作无法绕过清单调用生命周期。

StandardContextV1 是业务之间的共享协议：version=1、selected（规范 ID 列表）、catalog（规范元数据）、entries（现有摘要）、requirements（已有校核与未覆盖项）。它由 wall_design 导出，standards 只读消费；宿主不解析规范编号或要求。

## 资源提案

清单声明 resource_namespace，可选 legacy_tables。request.resources 是 documents 数组和 legacy_tables 原始行映射。宿主不解释 values_json、结果或案例含义。独立资源写操作返回如下数组，禁止同时返回状态和成果：

```json
[{"id":"case:7","document":{"kind":"case","id":7,"title":"教学案例","operation_id":"run:call","values":{},"result":{}}}]
```

document 的业务有效性由技能校验；宿主校验数组形状、标识和 JSON。已有相同 ID 且内容相同的写入可重复提交，不同内容返回 RESOURCE_CONFLICT。上述空 values/result 仅说明通用外壳，挡墙业务会拒绝将它作为有效案例。

## 运行错误与恢复

| code | 含义 | 是否提交业务状态 |
|---|---|---|
| START_FAILED | 入口或解释器无法启动 | 否 |
| TIMEOUT | 超过执行时间 | 否 |
| OUTPUT_LIMIT | stdout/stderr 超限 | 否 |
| NONZERO_EXIT | 即使 stdout 有 JSON，退出码非零仍失败 | 否 |
| INVALID_RESPONSE | JSON、版本、字段或响应约束不合法 | 否 |
| INVALID_PATH | 资源或附件路径越界 | 否 |
| 业务自有 code | 参数不足或业务失败 | 否 |
| STALE_STATE | 会话、所有者、快照或存储修订冲突 | 不提交旧提案；不覆盖数据库 |
| PERSISTENCE_FAILED | 资源/文件写入失败，或聊天持久化失败 | 前者无新状态；聊天失败保留已接受的内存单元 |

业务成功不等于 SQLite 已落盘。聊天写入失败时界面显示未保存，并提供 save_conversation 重试；不重新执行脚本。新建、载入和删除当前聊天会保护未保存内存。Run 与工具调用通过 run_id、run、call_id，技能操作通过 skill_id、action_id、request_id、operation_id 关联；duration_seconds 和 status 记录执行结果。stderr 单独持久化，不作为模型回复。

固定 system 前缀和四个通用模型工具在一次应用实例内保持不变。只在靠近本轮输入处加入业务投影，不把投影写入长期历史，也不把密钥或端点配置发送给脚本。
