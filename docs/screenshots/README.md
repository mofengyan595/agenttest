# 真实运行截图

2026-10-12，Playwright 驱动本机 Edge，连接实际 Vite → FastAPI → ReportStore。
这些是实现后截图，不是设计稿；数据由本轮隔离离线 showcase 生成，不代表真实 Java 或收费模型实测。

| 文件 | 内容 / viewport |
| --- | --- |
| workbench-desktop.png | 草稿下载与真实契约导入成功；1440 × 1080 |
| history-desktop.png | 当前目录 4 条真实报告；1440 × 1080 |
| run-detail-desktop.png | 真实 ASSERTION_FAILED + 1 次调查；展开已有登录证据；1440 × 1080 |
| run-detail-768.png / workbench-768.png | 平板布局；768 × 1024 |
| run-detail-390.png / workbench-390.png | 手机布局；390 × 844 |

全部使用 fullPage 保存，文件高度可能大于 viewport。截图前回到页面顶部，以正确呈现固定导航。
复现方法见 [Web Console](../web-console.md)，测试源码在 `web/e2e/console.spec.ts`。
保留这些截图作为本轮验收证据；未来重跑浏览器测试会更新同名截图。
