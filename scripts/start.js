const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const {
  ensurePythonEnvironments,
  sha256,
  stableStringify,
  terminateActiveProcesses: terminatePythonProcesses,
  withInstallLock,
} = require("./python-envs");

const rootDir = path.resolve(__dirname, "..");
const backendDir = path.join(rootDir, "backend");
const frontendDir = path.join(rootDir, "frontend");

const backendHost = process.env.BACKEND_HOST || "127.0.0.1";
const backendPort = Number(process.env.BACKEND_PORT || 8000);
const frontendHost = process.env.FRONTEND_HOST || "127.0.0.1";
const frontendPort = Number(process.env.FRONTEND_PORT || 3000);
const backendUrl = `http://${backendHost}:${backendPort}`;
const frontendUrl = `http://${frontendHost}:${frontendPort}`;
const frontendPageUrl = `${frontendUrl}/lab`;
const nextCliPath = path.join(rootDir, "node_modules", "next", "dist", "bin", "next");
const nodeStampPath = path.join(rootDir, "node_modules", ".labai-dependencies.json");
const dependencyLockDir = path.join(backendDir, ".runtime", "dependency-locks");
const NODE_DEPENDENCY_CHECK_ARGS = Object.freeze(["ls", "--include=dev", "--depth=0", "--json"]);
const NODE_DEPENDENCY_INSTALL_ARGS = Object.freeze(["install", "--include=dev", "--no-audit", "--no-fund"]);

const children = new Set();
let shuttingDown = false;
let shutdownPromise = null;
let mainPromise = null;

function npmCliCommand(args) {
  if (process.platform === "win32") {
    return { command: "cmd.exe", args: ["/d", "/s", "/c", `npm ${args.join(" ")}`] };
  }
  return { command: "npm", args };
}

function checkUrl(url, timeoutMs = 900) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      response.resume();
      resolve(Boolean(response.statusCode && response.statusCode >= 200 && response.statusCode < 400));
    });

    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
  });
}

function fetchJson(url, timeoutMs = 20000) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        if (body.length < 64 * 1024) body += chunk;
      });
      response.on("end", () => {
        let payload = null;
        try {
          payload = JSON.parse(body);
        } catch {
          payload = null;
        }
        resolve({ payload, reachable: true, statusCode: response.statusCode || 0 });
      });
    });
    request.on("timeout", () => {
      request.destroy();
      resolve({ payload: null, reachable: false, statusCode: 0 });
    });
    request.on("error", () => resolve({ payload: null, reachable: false, statusCode: 0 }));
  });
}

function isCompatibleBackendHealth(payload) {
  return Boolean(
    payload &&
      payload.status === "ok" &&
      payload.capabilities?.python_sandbox === true &&
      payload.sandbox?.ready === true,
  );
}

async function checkBackendReady() {
  const health = await fetchJson(`${backendUrl}/api/health`);
  if (!health.reachable) return false;
  if (health.statusCode >= 200 && health.statusCode < 300 && isCompatibleBackendHealth(health.payload)) {
    return true;
  }
  throw new Error(
    `[backend] ${backendUrl} is occupied by an incompatible or unready service. ` +
      "Stop the old process, then run npm start again. Expected health capabilities.python_sandbox=true and sandbox.ready=true.",
  );
}

async function waitForCompatibleBackend(timeoutMs = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const health = await fetchJson(`${backendUrl}/api/health`);
    if (
      health.reachable &&
      health.statusCode >= 200 &&
      health.statusCode < 300 &&
      isCompatibleBackendHealth(health.payload)
    ) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 600));
  }
  return false;
}

async function waitForUrl(url, timeoutMs = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await checkUrl(url)) return true;
    await new Promise((resolve) => setTimeout(resolve, 600));
  }
  return false;
}

function trackChild(child) {
  children.add(child);
  child.once("close", () => children.delete(child));
  return child;
}

function childIsRunning(child) {
  return Boolean(child?.pid && child.exitCode === null && child.signalCode === null);
}

function waitForChildClose(child, timeoutMs = 5000) {
  if (!childIsRunning(child)) return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = setTimeout(resolve, timeoutMs);
    timeout.unref?.();
    child.once("close", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

async function terminateChildProcess(child) {
  if (!childIsRunning(child)) return;
  const closed = waitForChildClose(child);
  if (process.platform === "win32") {
    await new Promise((resolve) => {
      const killer = spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
        shell: false,
        stdio: "ignore",
      });
      killer.once("error", resolve);
      killer.once("close", resolve);
    });
  } else {
    try {
      child.kill("SIGTERM");
    } catch {
      // The process may have exited between the liveness check and kill.
    }
  }
  await closed;
}

function startProcess(label, command, args, options = {}) {
  console.log(`[${label}] ${command} ${args.join(" ")}`);
  const child = spawn(command, args, {
    ...options,
    env: { ...process.env, ...(options.env || {}) },
    shell: false,
    stdio: "inherit",
  });

  child.on("error", (error) => {
    console.error(`[${label}] failed to start: ${error.message}`);
  });
  child.on("exit", (code, signal) => {
    if (signal) {
      console.log(`[${label}] stopped by ${signal}`);
      return;
    }
    if (code !== 0) console.log(`[${label}] exited with code ${code}`);
  });

  return trackChild(child);
}

function runProcess(label, command, args, options = {}) {
  const { installLock = null, logCommand = true, ...spawnOptions } = options;
  if (logCommand) console.log(`[${label}] ${command} ${args.join(" ")}`);
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      ...spawnOptions,
      env: { ...process.env, ...(spawnOptions.env || {}) },
      shell: false,
      stdio: spawnOptions.stdio || "inherit",
    });
    trackChild(child);
    installLock?.trackChild(child);
    let settled = false;
    child.once("error", (error) => {
      if (!settled) {
        settled = true;
        reject(error);
      }
    });
    child.once("close", (code, signal) => {
      if (settled) return;
      settled = true;
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`[${label}] exited with ${signal || `code ${code}`}`));
    });
  });
}

function computeNodeManifestFingerprint({
  arch = process.arch,
  manifestContents,
  nodeModulesVersion = process.versions.modules,
  platform = process.platform,
}) {
  return sha256(
    stableStringify({
      arch,
      manifestContents: manifestContents.map(({ content, name }) => ({
        content: content.replace(/\r\n/g, "\n"),
        name,
      })),
      nodeModulesVersion,
      platform,
      schemaVersion: 2,
    }),
  );
}

function getExpectedNodeStamp() {
  const manifestPaths = [path.join(rootDir, "package.json"), path.join(rootDir, "package-lock.json")];
  const manifestContents = manifestPaths
    .filter((filePath) => fs.existsSync(filePath))
    .map((filePath) => ({ content: fs.readFileSync(filePath, "utf8"), name: path.basename(filePath) }));
  return {
    fingerprint: computeNodeManifestFingerprint({ manifestContents }),
    schemaVersion: 2,
  };
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

async function nodeDependenciesAreValid(installLock = null) {
  if (!fs.existsSync(nextCliPath)) return false;
  const command = npmCliCommand(NODE_DEPENDENCY_CHECK_ARGS);
  try {
    await runProcess("node-deps-check", command.command, command.args, {
      cwd: rootDir,
      installLock,
      logCommand: false,
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

async function ensureFrontendDependencies() {
  const expectedStamp = getExpectedNodeStamp();
  const currentStamp = readJson(nodeStampPath);
  if (
    currentStamp?.fingerprint === expectedStamp.fingerprint &&
    (await nodeDependenciesAreValid())
  ) {
    console.log("[frontend] Node dependencies are ready.");
    return;
  }

  await withInstallLock(path.join(dependencyLockDir, "node.lock"), async (installLock) => {
    const lockedStamp = readJson(nodeStampPath);
    if (
      lockedStamp?.fingerprint === expectedStamp.fingerprint &&
      (await nodeDependenciesAreValid(installLock))
    ) {
      console.log("[frontend] Node dependencies are ready.");
      return;
    }

    console.log("[frontend] installing or repairing root Node dependencies...");
    const install = npmCliCommand(NODE_DEPENDENCY_INSTALL_ARGS);
    await runProcess("node-deps", install.command, install.args, { cwd: rootDir, installLock });
    if (!(await nodeDependenciesAreValid(installLock))) {
      throw new Error("[frontend] npm install completed, but root dependencies are still invalid.");
    }
    writeJson(nodeStampPath, { ...expectedStamp, installedAt: new Date().toISOString() });
    console.log("[frontend] Node dependencies are ready.");
  });
}

function openBrowser(url) {
  if (process.env.OPEN_BROWSER === "0") return;

  let command;
  let args;
  if (process.platform === "win32") {
    command = "cmd";
    args = ["/c", "start", "", url];
  } else if (process.platform === "darwin") {
    command = "open";
    args = [url];
  } else {
    command = "xdg-open";
    args = [url];
  }

  const opener = spawn(command, args, {
    detached: true,
    shell: false,
    stdio: "ignore",
  });
  opener.unref();
}

async function main() {
  console.log("Starting LabAI...");
  await ensureFrontendDependencies();
  const { backendPython, sandboxPython } = await ensurePythonEnvironments({ backendDir });

  const backendReady = await checkBackendReady();
  if (backendReady) {
    console.log(`[backend] compatible sandbox backend already running at ${backendUrl}`);
  } else {
    startProcess(
      "backend",
      backendPython,
      ["-m", "uvicorn", "app.main:app", "--host", backendHost, "--port", String(backendPort)],
      {
        cwd: backendDir,
        env: { LABAI_SANDBOX_PYTHON: path.resolve(sandboxPython) },
      },
    );
    if (!(await waitForCompatibleBackend())) {
      throw new Error(
        `[backend] did not become sandbox-ready at ${backendUrl} within 30 seconds. Check the backend output above.`,
      );
    }
  }

  const frontendReady = await checkUrl(frontendPageUrl);
  if (frontendReady) {
    console.log(`[frontend] already running at ${frontendUrl}`);
  } else {
    startProcess(
      "frontend",
      process.execPath,
      [nextCliPath, "dev", frontendDir, "--hostname", frontendHost, "--port", String(frontendPort)],
      {
        cwd: rootDir,
        env: {
          NEXT_PUBLIC_BACKEND_URL: process.env.NEXT_PUBLIC_BACKEND_URL || backendUrl,
          NODE_ENV: "development",
        },
      },
    );
  }

  console.log("");
  console.log(`Backend:  ${backendUrl}`);
  console.log(`Frontend: ${frontendPageUrl}`);
  console.log("Press Ctrl+C to stop services started by this command.");

  const canOpenFrontend = frontendReady || (await waitForUrl(frontendPageUrl));
  if (canOpenFrontend) {
    openBrowser(frontendPageUrl);
  } else {
    console.log(`[frontend] not ready yet; open manually when it finishes: ${frontendPageUrl}`);
  }
}

function shutdown(exitCode = 0) {
  if (shutdownPromise) return shutdownPromise;
  shuttingDown = true;
  shutdownPromise = (async () => {
    await Promise.allSettled([
      terminatePythonProcesses(),
      ...[...children].map((child) => terminateChildProcess(child)),
    ]);

    if (mainPromise) {
      await Promise.race([
        mainPromise.catch(() => undefined),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    }
    process.exit(exitCode);
  })();
  return shutdownPromise;
}

if (require.main === module) {
  process.on("SIGINT", () => void shutdown(0));
  process.on("SIGTERM", () => void shutdown(0));
  mainPromise = main();
  mainPromise.catch((error) => {
    if (shuttingDown) return;
    console.error(error);
    void shutdown(1);
  });
}

module.exports = {
  computeNodeManifestFingerprint,
  ensureFrontendDependencies,
  isCompatibleBackendHealth,
  main,
  NODE_DEPENDENCY_CHECK_ARGS,
  NODE_DEPENDENCY_INSTALL_ARGS,
};
