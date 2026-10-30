import { expect, test, type Page } from "@playwright/test";

const workspace = "C:\\Projects\\演示项目";
const now = "2026-10-30T10:00:00";
const chat = { id: "chat-1", title: "检查项目", created_at: now, updated_at: now, model_profile_id: "model-1", workspace_path: workspace, workspace_mode: "approve", message_count: 2, last_message_preview: "检查项目", has_active_run: true };

async function mockBackend(page: Page, initial: "new" | "interrupted" = "new") {
  const state = { phase: initial === "new" ? "waiting_approval" : "interrupted", exists: initial !== "new", approvals: [] as boolean[], cancelled: false, resumed: false, sent: null as Record<string, unknown> | null, streamCount: 0, failFirstStream: false };
  const message = () => ({
    id: "message-1", chat_id: chat.id, role: "assistant", type: "text", model_profile_id: "model-1", created_at: now,
    status: state.phase === "done" ? "done" : state.phase === "cancelled" ? "cancelled" : state.phase === "interrupted" ? "interrupted" : "streaming",
    content: state.phase === "done" ? "测试命令执行完成。" : "正在检查项目。",
    metadata: { workspaceAgent: { workspacePath: workspace, status: state.phase, calls: [{
      id: "call-1", tool: "run_command", arguments: { command: "python -m unittest", cwd: "." },
      status: state.phase === "done" ? "done" : state.phase,
      ...(state.phase === "done" ? { result: { ok: true, stdout: "Ran 3 tests\nOK", stderr: "", exit_code: 0, cwd: workspace, duration_seconds: 0.2 } } : {}),
    }] } },
  });
  if (initial !== "new") await page.addInitScript(() => localStorage.setItem("labai.currentChatId", "chat-1"));
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown) => route.fulfill({ json: body });
    if (url.pathname === "/api/models") return json([{ id: "model-1", name: "Test model", provider: "openai_compat", model_name: "test", supports_tools: true, supports_stream: true, supports_vision: false, has_api_key: true }]);
    if (url.pathname === "/api/chats") return json(state.exists ? [chat] : []);
    if (url.pathname === "/api/chats/chat-1") return json({ ...chat, messages: [message()] });
    if (url.pathname === "/api/workspaces/inspect" || url.pathname === "/api/chats/chat-1/workspace") {
      if (route.request().method() === "PATCH") return json({ workspace_path: workspace, workspace_mode: "approve" });
      return json({ path: workspace, name: "演示项目", relative_path: ".", entries: [{ name: "README.md", path: "README.md", kind: "file", size: 30 }, { name: "src", path: "src", kind: "directory" }], truncated: false });
    }
    if (url.pathname === "/api/chats/chat-1/workspace/file") return json({ path: "README.md", content: "# Demo project\nRun tests with python.", truncated: false });
    if (url.pathname === "/api/runs/chat") {
      state.exists = true; state.sent = route.request().postDataJSON();
      return json({ chat_id: chat.id, chat, assistant_message: message() });
    }
    if (url.pathname.endsWith("/approval")) {
      state.approvals.push(route.request().postDataJSON().approved);
      state.phase = "done";
      return json({ ok: true });
    }
    if (url.pathname.endsWith("/cancel")) { state.cancelled = true; state.phase = "cancelled"; return json({ ok: true, status: "cancelled" }); }
    if (url.pathname.endsWith("/resume")) { state.resumed = true; state.phase = "waiting_approval"; return json({ chat_id: chat.id, chat, assistant_message: message() }); }
    if (url.pathname.endsWith("/stream")) {
      state.streamCount += 1;
      if (state.failFirstStream && state.streamCount === 1) return route.abort("failed");
      const msg = message();
      const snapshot = { ...msg, message_id: msg.id };
      const terminal = ["done", "cancelled", "interrupted"].includes(state.phase);
      return route.fulfill({ contentType: "text/event-stream", body: `event: snapshot\ndata: ${JSON.stringify(snapshot)}\n\n${terminal ? `event: done\ndata: ${JSON.stringify({ message_id: msg.id, chat_id: chat.id, status: msg.status })}\n\n` : ""}` });
    }
    return route.fulfill({ status: 404, json: { message: `Unexpected API: ${url.pathname}` } });
  });
  return state;
}

async function bindAndSend(page: Page) {
  await page.goto("/lab");
  await page.getByRole("button", { name: "绑定本地文件夹，让 Agent 在项目中工作" }).click();
  await page.getByRole("textbox", { name: "工作区路径" }).fill(workspace);
  await page.getByRole("button", { name: "绑定文件夹", exact: true }).click();
  await expect(page.getByRole("button", { name: /演示项目.*操作前审批/ })).toBeVisible();
  await page.getByPlaceholder("描述任务，让 Agent 阅读文件、修改代码、执行命令").fill("运行项目测试");
  await page.getByRole("button", { name: "发送", exact: true }).click();
}

test("bind a folder, approve a real command request, display output, and browse a file", async ({ page }) => {
  const state = await mockBackend(page);
  await bindAndSend(page);
  await expect(page.getByRole("button", { name: "允许此次操作" })).toBeVisible();
  expect(state.sent?.workspace_path).toBe(workspace);
  expect(state.sent?.workspace_mode).toBe("approve");
  await page.getByRole("button", { name: "允许此次操作" }).click();
  await expect(page.getByText("测试命令执行完成。", { exact: true })).toBeVisible();
  expect(state.approvals).toEqual([true]);
  await page.getByText(/执行命令.*已完成/).click();
  await expect(page.getByText("Ran 3 tests\nOK", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /演示项目.*操作前审批/ }).click();
  await page.getByRole("button", { name: "README.md", exact: true }).click();
  await expect(page.getByText("# Demo project\nRun tests with python.", { exact: true })).toBeVisible();
});

test("stop invokes backend cancellation and preserves cancelled status after snapshot", async ({ page }) => {
  const state = await mockBackend(page);
  await bindAndSend(page);
  await page.getByRole("button", { name: "停止任务", exact: true }).click();
  expect(state.cancelled).toBe(true);
  await expect(page.getByRole("button", { name: "停止任务", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "允许此次操作" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "继续任务" })).toHaveCount(0);
  await expect(page.getByText("已停止").first()).toBeVisible();
});

test("interrupted history resumes and recovers pending approval", async ({ page }) => {
  const state = await mockBackend(page, "interrupted");
  await page.goto("/lab");
  await page.getByRole("button", { name: "继续任务" }).click();
  await expect(page.getByRole("button", { name: "允许此次操作" })).toBeVisible();
  expect(state.resumed).toBe(true);
  await page.getByRole("button", { name: "拒绝", exact: true }).click();
  expect(state.approvals).toEqual([false]);
});

test("lost SSE connection reconnects without falsely failing the task", async ({ page }) => {
  const state = await mockBackend(page);
  state.failFirstStream = true;
  await bindAndSend(page);
  await expect(page.getByRole("button", { name: "停止任务", exact: true })).toBeVisible();
  await expect.poll(() => state.streamCount).toBeGreaterThan(1);
  await expect(page.getByRole("button", { name: "允许此次操作" })).toBeVisible();
  await expect(page.getByRole("button", { name: "停止任务", exact: true })).toBeVisible();
});

test("native folder picker fills the path and cancellation preserves it", async ({ page }) => {
  await mockBackend(page);
  const requests: unknown[] = [];
  let cancelled = false;
  await page.route("**/api/workspaces/pick-folder", async (route) => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({ json: { path: cancelled ? null : workspace, cancelled } });
  });
  await page.goto("/lab");
  await page.getByRole("button", { name: "绑定本地文件夹，让 Agent 在项目中工作" }).click();
  await page.getByRole("button", { name: "浏览文件夹", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "工作区路径" })).toHaveValue(workspace);
  expect(requests[0]).toEqual({ initial_path: null });
  // Picking never silently changes the current folder or its execution policy.
  await expect(page.getByRole("button", { name: "绑定本地文件夹，让 Agent 在项目中工作" })).toBeVisible();
  cancelled = true;
  await page.getByRole("button", { name: "浏览文件夹", exact: true }).click();
  await expect(page.getByRole("button", { name: "浏览文件夹", exact: true })).toBeEnabled();
  await expect(page.getByRole("textbox", { name: "工作区路径" })).toHaveValue(workspace);
  expect(requests[1]).toEqual({ initial_path: workspace });
  await page.getByRole("button", { name: "绑定文件夹", exact: true }).click();
  await expect(page.getByRole("button", { name: /演示项目.*操作前审批/ })).toBeVisible();
});

test("folder picker errors leave manual path entry available", async ({ page }) => {
  await mockBackend(page);
  await page.route("**/api/workspaces/pick-folder", (route) => route.fulfill({ status: 503, json: { message: "无法打开系统选择窗口，请手动输入路径。" } }));
  await page.goto("/lab");
  await page.getByRole("button", { name: "绑定本地文件夹，让 Agent 在项目中工作" }).click();
  await page.getByRole("button", { name: "浏览文件夹", exact: true }).click();
  await expect(page.getByRole("region", { name: "本地工作区" }).getByRole("alert")).toContainText("请手动输入路径");
  await page.getByRole("textbox", { name: "工作区路径" }).fill(workspace);
  await page.getByRole("button", { name: "绑定文件夹", exact: true }).click();
  await expect(page.getByRole("button", { name: /演示项目.*操作前审批/ })).toBeVisible();
});
