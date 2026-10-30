import { expect, test } from "@playwright/test";

// Opt-in smoke checks against the running local backend. These never submit
// a model request or execute a command; they inspect folders and existing runs.
test("live backend validates a folder from the workspace UI", async ({ page }) => {
  const path = process.env.LABAI_LIVE_WORKSPACE;
  test.skip(!path, "Set LABAI_LIVE_WORKSPACE to opt into the local backend smoke test.");
  await page.goto("/lab");
  await page.getByRole("button", { name: "绑定本地文件夹，让 Agent 在项目中工作" }).click();
  await page.getByRole("textbox", { name: "工作区路径" }).fill(`${path}/.labai-does-not-exist-test`);
  await page.getByRole("button", { name: "绑定文件夹", exact: true }).click();
  await expect(page.getByRole("region", { name: "本地工作区" }).getByRole("alert")).toBeVisible();
  await page.getByRole("textbox", { name: "工作区路径" }).fill(path!);
  await page.getByRole("button", { name: "绑定文件夹", exact: true }).click();
  const bound = page.getByRole("region", { name: "本地工作区" }).getByRole("button").first();
  await expect(bound).toContainText("操作前审批");
  await bound.click();
  await expect(page.getByRole("button", { name: "README.md", exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/live-workspace.png", fullPage: true });
});

test("live backend restores a completed agent run and reads a generated project file", async ({ page }) => {
  const chatId = process.env.LABAI_LIVE_CHAT_ID;
  test.skip(!chatId, "Set LABAI_LIVE_CHAT_ID to inspect an already completed agent run.");
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") browserErrors.push(message.text()); });
  await page.addInitScript((id) => localStorage.setItem("labai.currentChatId", id), chatId!);
  await page.goto("/lab");
  await expect(page.getByTestId("agent-trace")).toBeVisible();
  await expect(page.getByRole("button", { name: "停止任务", exact: true })).toHaveCount(0);
  const trace = page.getByTestId("agent-trace");
  await expect(trace).toContainText("已完成");
  const commands = trace.locator("details").filter({ hasText: "执行命令" });
  await expect(commands.first()).toBeVisible();
  await commands.first().locator(":scope > summary").click();
  await expect(commands.first()).toContainText("exit:");
  if (process.env.LABAI_LIVE_OUTPUT) await expect(commands.first()).toContainText(process.env.LABAI_LIVE_OUTPUT);
  await page.getByRole("region", { name: "本地工作区" }).getByRole("button").first().click();
  if (process.env.LABAI_LIVE_FILE) {
    await page.getByRole("button", { name: process.env.LABAI_LIVE_FILE, exact: true }).click();
    await expect(page.getByRole("button", { name: "关闭预览" })).toBeVisible();
  }
  await page.screenshot({ path: ".playwright/real-workspace-agent.png", fullPage: true });
  await page.reload();
  await expect(page.getByTestId("agent-trace")).toContainText("已完成");
  await expect(page.getByRole("button", { name: "停止任务", exact: true })).toHaveCount(0);
  expect(browserErrors).toEqual([]);
});
