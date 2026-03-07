# Browser MCP 接入说明（你的项目版）

> 目标：在不影响现有功能的前提下，为你的 Agent 增加“通过 Browser MCP 自动操作网页”的能力。

## 1. 先理解三个概念

1. **Chrome 插件**：只负责把浏览器实例暴露给 Browser MCP 生态（或配合权限/UI）。
2. **Browser MCP Server（Node）**：真正提供 MCP 工具（click/type/navigate/snapshot 等）的服务端进程。
3. **你的 Agent（Python）**：作为 MCP Client，连接 MCP Server 后由大模型调用工具。

所以你看到教程里“先启动 Node API，再启动 MCP server”是对的：
- Node 进程负责浏览器自动化能力；
- 你的 Python 服务继续负责天气/文件等本地能力；
- 两者可以并存，Agent 一次性连接多个 MCP Server。

## 2. Playwright 是什么？

- **Playwright** 是浏览器自动化框架，不是 MCP 协议本身。
- Browser MCP 的很多实现底层会用 Playwright 驱动浏览器。
- 所以：
  - “MCP”解决的是**模型如何调用工具**；
  - “Playwright”解决的是**工具如何控制浏览器**。

## 3. 本项目已做的改造

`client/client.py` 已支持：
- 默认连接你原有本地 Python MCP Server；
- 通过环境变量**可选**再连接一个 Browser MCP（Node）服务；
- Browser MCP 启动失败时自动降级，不影响你现有功能。

## 4. 你需要怎么配置

在 `.env` 中增加（示例）：

```env
ENABLE_BROWSER_MCP=1
BROWSER_MCP_COMMAND=npx
BROWSER_MCP_ARGS=@playwright/mcp@latest
```

> 如果你使用的是别的 Browser MCP 包，把 `BROWSER_MCP_ARGS` 改成对应启动参数即可。

## 5. 启动顺序（推荐）

1. 先确保浏览器插件可用并授权。
2. 启动你的应用（它会自动按配置拉起/连接 Browser MCP）。
3. 在对话里明确说“请用浏览器操作……”并描述步骤目标。

## 6. 验证是否接入成功

应用日志会出现类似：
- `已接入 Browser MCP，新增工具: [...]`
- `已连接到服务器，支持以下工具: [...]`

若失败会看到：
- `⚠️ Browser MCP 启动失败，已降级为仅本地工具: ...`

此时天气、文件、打开应用等原有功能仍可正常使用。
