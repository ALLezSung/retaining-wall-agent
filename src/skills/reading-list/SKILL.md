---
name: reading-list
description: 记录用户明确提供的书名，标记已读，生成阅读清单表格时使用。
metadata:
  design-agent-display-name: "阅读清单"
  design-agent-integration: "integrations/design-agent.toml"
---

# 阅读清单（独立示例）

通过 `run_skill_action("reading_list", "add", {"title":"书名"})` 添加书目；
通过 `mark_read`，输入 `{"index":1}` 标记已读；通过 `show`，输入 `{}` 生成表格。
只有用户明确提供的书名才能添加。序号从 1 开始，重复书名不重复添加。
脚本 stdin 接收协议 v1 JSON，stdout 返回单个 JSON；日志只写 stderr。
无需导入宿主，执行 `python scripts/runner.py --action add < request.json` 即可独立测试。
状态采用明确的 Book / ReadingList 模型，与挡墙状态无关。
