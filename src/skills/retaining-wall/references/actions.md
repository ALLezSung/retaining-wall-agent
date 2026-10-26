# 脚本操作协议

下文 `set_parameters` 等名称都是脚本的业务 action，不是独立模型工具。
统一通过 `run_skill_action(skill_id="retaining_wall", action_id="操作名", input={本次参数})` 调用。
例如 `run_skill_action("retaining_wall", "set_parameters", {"height_m":4,"base_width_m":2.4})`。
需要计算时调用 `run_skill_action("retaining_wall", "calculate_design", {})`。
不得在 input 中构造 snapshot、参数来源或修订号；宿主注入权威快照与本轮原文。
缺项与业务失败不会提交变化。成功只表示已执行；界面另行提示尚未持久化的状态。
`scripts/wall_design/state.py` 定义完整设计状态、校验和失效规则；脚本可以独立运行：
`python scripts/runner.py --action calculate_design < request.json`，输入格式见项目协议文档。
当前支持业务数据版本 1，未知版本拒绝恢复；将来迁移由此包实现。
规范技能读取本包导出的 StandardContextV1，不拥有设计参数或规范选择。
