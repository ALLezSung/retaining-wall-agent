---
name: retaining-wall
description: 收集或修改挡墙条件、检查参数、确定性计算、解释结果、几何比选、选用候选及案例复用时使用。
metadata:
  design-agent-display-name: "重力式挡土墙教学初算"
  design-agent-integration: "integrations/design-agent.toml"
---

# 重力式挡土墙教学初算

首次开展本业务先读取本说明。参数表由挡墙业务包定义和校验，宿主保存并统一提交，使用工具返回的最新状态，不从旧聊天结果推断当前有效结果。

## 调用方式与参考资料

业务操作统一通过 `run_skill_action("retaining_wall", "操作名", {本次输入})` 调用。
首次使用具体操作时读取 [动作与脚本协议](references/actions.md)；填写参数时读取
[参数与适用范围](references/parameters.md)；计算、比选和解释结论前读取
[计算与比选边界](references/calculation-scope.md)。
不得在 input 中构造 snapshot、参数来源或修订号，宿主注入权威快照与本轮原文。
脚本独立运行：`python scripts/runner.py --action calculate_design < request.json`。

仅支持墙高小于 5 m、干燥水平无黏性填土、光滑直立墙背、水平基底和非地震教学工况。
地下水、地震、黏性土及倾斜填土超出范围。教学筛选不表示完整工程合规。

## 工作流程

1. 调用 set_parameters 保存用户明确给出的参数，缺少条件就追问，不猜测。矩形可设置 wall_shape=rectangle 并按 B=t 处理。
2. 可以独立调用 validate_parameters 检查完整性、范围和几何关系；“通过当前输入规则检查”不等于设计合格。
3. 需要数值时调用 calculate_design；它会自行校验，不要求先调用校验工具，也没有新增强制确认步骤。
4. 解释时读取 get_current_result。所有工程数值、公式和已完成项目以工具证据为准；修改参数后旧结果和候选失效。
5. 需要调整时调用 compare_designs。用户明确锁定底宽或顶宽时，放入 fixed_dimensions。候选不会修改当前方案。
6. 用户同意后调用 apply_option，用返回候选的 index 选用并重算。用户只说“试一下”且未指定编号时用 1。无候选就说明搜索范围内无解，不编造候选。
7. 可通过 list_cases 和 load_case 复用案例参数；载入不自动计算。网页通过 save_current 动作保存参数与结果；仅在用户明确要求保存案例时使用该动作，input 为 {"title":"案例名称"}。它是资源保存，不改变设计状态。

## 必须保留的约束

参数只能来自用户明确条件，附加荷载为零也需明确。有效改参后重算；同值更新保留有效成果。
比选不修改当前方案，选用候选会更新参数并重算。额外限值须逐项检查，不能以默认教学筛选代替。
地基承载力、沉降、整体稳定、墙身强度、构造、排水与实际造价未验算，不得声称施工设计或完整规范合规。
规范查询使用 standards 技能；它只读取本技能的共享投影，不持有第二份设计参数。
