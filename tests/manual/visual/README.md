# 手动假模型与浏览器调试资源

这些脚本从原测试运行目录迁入，保留既有命名，不是自动测试夹具。Python 预览注入本地假模型，不调用真实服务；数据库写入 `tests/.runtime/visual/`，浏览器检查截图也写入该目录。旧运行产物单独保存在本机 `tests/.runtime/legacy/`，预览不会打开业务库或旧测试库。

从 `work/` 启动一个预览：

```powershell
python -m streamlit run tests/manual/visual/preview_live_slots.py --server.port 8520
```

`preview_live_slots.py` 支持普通输入、包含“中断”或“上限”的输入，用于观察流式正文、连接中断及轮次上限；其他 `preview*.py` 保留历史计算、侧栏和多 Run 场景。`preview_sidebar.py` / `preview_review.py` 中仅改名再新建的草稿按当前规则不会成为持久化历史，不能据此断言已覆盖长历史列表。

浏览器脚本通过本机 Chromium DevTools Protocol 工作，需另行启动 Edge/Chrome 并启用远程调试；Node.js 需提供全局 `fetch` 和 `WebSocket`（如 Node.js 22+）。示例需浏览器调试端口 9230、页面端口 8520：

```powershell
node tests/manual/visual/check_live_slots.cjs
```

如需独立浏览器目录，请放在 `tests/.runtime/visual/` 下。其余脚本使用固定端口：`check.cjs` 为 9227/8517，侧栏脚本为 9228/8518，review/run_messages 脚本为 9229/8519。请先核对脚本中的端口和页面，再运行。

部分 `check*` / `inspect*` 脚本依赖旧版 `.st-key-composer` 或弹出式模型设置，甚至临时注入 CSS，仅作为历史诊断记录，不能视为当前自动验收。本次调整其文件位置和输出路径，5 个 Python 预览已通过 `AppTest` 启动检查，12 个 JavaScript 文件通过 `node --check`；浏览器选择器与视觉效果未重新验证。日常回归使用 `python -m unittest discover -s tests -v`，其中 Streamlit `AppTest` 使用独立临时数据库。

## 当前协议复核（2026-10-26）

五个 preview*.py 已改用 run_skill_action 和通用应用接口，工作区也写入隔离测试目录。
新增 check_architecture.cjs 使用 18520/19230 端口，检查 preview.py 的流式正文、业务阶段标签、两个下载按钮、实际 JSON 下载以及 1440px / 390px 布局，结果与截图写入 tests/.runtime/visual/architecture。

此次浏览器复核已通过。其余较早的浏览器脚本仍属于历史诊断，不据此声称全部选择器已经重测。

## 计算书展开与滚动回归

使用隔离假模型运行 `preview_scroll.py`（端口 18521），以独立浏览器配置开放本机 CDP 端口 19231，再运行 `node tests/manual/visual/check_report_scroll.cjs`。

检查 1440×900、3323×1787 和 390×844 下的计算书展开、长短章节切换、滚轮上下滚动、输入框底部位置及末尾卡片可达性。另用持续输出验证自动跟随，以及主动上滚后在生成中和生成结束时均不被拉回底部。此脚本包含实际断言，截图与 JSON 结果保存在 `tests/.runtime/visual/scroll/`。

修复前，1440×900 下展开计算书会使 scrollTop 从 0 跳到 951；修复后保持 0。使用显式 st.bottom 避免启用根级无条件追底，页面自己的事件驱动滚动控制只跟随正在生成的回答。计算书保持自然高度，不添加内部滚动区。
