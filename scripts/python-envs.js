const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const MINIMUM_PYTHON = Object.freeze([3, 11, 0]);
const STAMP_FILENAME = ".labai-environment.json";
const INSTALL_SCHEMA_VERSION = 2;
const DEFAULT_LOCK_TIMEOUT_MS = 15 * 60 * 1000;
const DEFAULT_STALE_LOCK_MS = 30 * 60 * 1000;
const DEFAULT_INVALID_LOCK_GRACE_MS = 5 * 1000;
const activeProcesses = new Set();

const PYTHON_PROBE = [
  "import json, platform, sys",
  "print(json.dumps({'executable': sys.executable, 'implementation': platform.python_implementation(), 'version': platform.python_version(), 'cache_tag': getattr(sys.implementation, 'cache_tag', '')}))",
].join(";");

const REQUIREMENTS_PROBE = String.raw`
import json
import re
import sys
from importlib import metadata
from pip._vendor.packaging.requirements import Requirement

errors = []
for requirements_source in json.loads(sys.argv[1]):
    source_name = requirements_source["name"]
    logical_lines = []
    pending = ""
    for raw_line in requirements_source["content"].lstrip("\ufeff").splitlines():
        stripped = raw_line.strip()
        pending = f"{pending}{stripped}"
        if pending.endswith("\\"):
            pending = pending[:-1]
            continue
        logical_lines.append(pending)
        pending = ""
    if pending:
        logical_lines.append(pending)

    for line_number, raw_requirement in enumerate(logical_lines, start=1):
        requirement_text = re.split(r"\s+#", raw_requirement, maxsplit=1)[0].strip()
        if not requirement_text or requirement_text.startswith("#"):
            continue
        if requirement_text.startswith(("-r ", "--requirement ", "-c ", "--constraint ")):
            errors.append(f"{source_name}:{line_number}: nested requirement files are not supported by the startup validator")
            continue
        if requirement_text.startswith("-"):
            continue
        try:
            requirement = Requirement(requirement_text)
        except Exception as exc:
            errors.append(f"{source_name}:{line_number}: invalid requirement: {exc}")
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            errors.append(f"{requirement.name}: not installed")
            continue
        if requirement.specifier and not requirement.specifier.contains(installed):
            errors.append(f"{requirement.name}: installed {installed}, required {requirement.specifier}")

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
`.trim();

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function stableStringify(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const entries = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value);
}

function parsePythonVersion(value) {
  const match = String(value || "").trim().match(/^(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3] || 0)];
}

function isVersionAtLeast(version, minimum = MINIMUM_PYTHON) {
  if (!Array.isArray(version) || version.length < 2) return false;
  for (let index = 0; index < 3; index += 1) {
    const actual = Number(version[index] || 0);
    const required = Number(minimum[index] || 0);
    if (actual > required) return true;
    if (actual < required) return false;
  }
  return true;
}

function getVenvPythonPath(venvDir, platform = process.platform) {
  const pathApi = platform === "win32" ? path.win32 : path.posix;
  return platform === "win32"
    ? pathApi.join(venvDir, "Scripts", "python.exe")
    : pathApi.join(venvDir, "bin", "python");
}

function getPythonCandidates({ env = process.env, platform = process.platform, preferred = [] } = {}) {
  const candidates = [];
  const add = (command, args = []) => {
    if (!command) return;
    const key = `${command}\0${args.join("\0")}`;
    if (candidates.some((candidate) => candidate.key === key)) return;
    candidates.push({ args, command, key });
  };

  for (const command of preferred) add(command);
  add(env.LABAI_BOOTSTRAP_PYTHON);
  add(env.PYTHON);
  if (platform === "win32") add("py", ["-3"]);
  add(platform === "win32" ? "python" : "python3");
  if (platform !== "win32") add("python");
  return candidates.map(({ args, command }) => ({ args, command }));
}

function buildEnvironmentStamp({ environmentName, imports, python, requirements }) {
  const requirementsPayload = requirements.map((requirement) => ({
    content: requirement.content.replace(/\r\n/g, "\n"),
    name: requirement.name,
  }));
  const requirementsHash = sha256(stableStringify(requirementsPayload));
  const pythonIdentity = {
    cacheTag: python.cacheTag || python.cache_tag || "",
    implementation: python.implementation,
    version: python.version,
  };
  const pythonHash = sha256(stableStringify(pythonIdentity));
  const schemaHash = sha256(
    stableStringify({
      environmentName,
      imports,
      installCommand: "python -m pip install --disable-pip-version-check --no-input -r <requirements>",
      schemaVersion: INSTALL_SCHEMA_VERSION,
      validation: ["python -m pip check", "validate requirement specifiers", "import modules"],
    }),
  );
  return {
    fingerprint: sha256(`${requirementsHash}\n${pythonHash}\n${schemaHash}`),
    python: pythonIdentity,
    pythonHash,
    requirementsHash,
    schemaHash,
    schemaVersion: INSTALL_SCHEMA_VERSION,
  };
}

function stampsMatch(actual, expected) {
  return Boolean(
    actual &&
      expected &&
      typeof actual.fingerprint === "string" &&
      actual.fingerprint === expected.fingerprint,
  );
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

async function terminateActiveProcesses() {
  await Promise.allSettled([...activeProcesses].map((child) => terminateChildProcess(child)));
}

function runProcess(command, args, options = {}) {
  const {
    capture = false,
    env = {},
    label = command,
    lock = null,
    logCommand = false,
    ...spawnOptions
  } = options;
  if (logCommand) console.log(`[${label}] ${command} ${args.join(" ")}`);

  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      ...spawnOptions,
      env: { ...process.env, ...env },
      shell: false,
      stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    });
    activeProcesses.add(child);
    lock?.trackChild(child);
    let stdout = "";
    let stderr = "";
    if (capture) {
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk;
      });
    }
    let settled = false;
    const cleanup = () => activeProcesses.delete(child);
    child.once("error", (error) => {
      cleanup();
      if (!settled) {
        settled = true;
        reject(error);
      }
    });
    child.once("close", (code, signal) => {
      cleanup();
      if (settled) return;
      settled = true;
      if (code === 0) {
        resolve({ stderr, stdout });
        return;
      }
      const detail = capture && stderr.trim() ? `: ${stderr.trim()}` : "";
      reject(new Error(`[${label}] exited with ${signal || `code ${code}`}${detail}`));
    });
  });
}

async function probePython(command, prefixArgs = [], cwd, lock = null) {
  const result = await runProcess(command, [...prefixArgs, "-c", PYTHON_PROBE], {
    capture: true,
    cwd,
    label: "python-probe",
    lock,
  });
  const probe = JSON.parse(result.stdout.trim());
  const parsedVersion = parsePythonVersion(probe.version);
  if (!probe.executable || !parsedVersion) {
    throw new Error(`Python probe returned invalid data from ${command}.`);
  }
  return {
    cacheTag: probe.cache_tag || "",
    executable: path.resolve(probe.executable),
    implementation: probe.implementation || "Python",
    parsedVersion,
    version: probe.version,
  };
}

async function discoverPython(options = {}) {
  const candidates = getPythonCandidates(options);
  const errors = [];
  for (const candidate of candidates) {
    try {
      const probe = await probePython(candidate.command, candidate.args, options.cwd);
      if (isVersionAtLeast(probe.parsedVersion)) return probe;
      errors.push(`${candidate.command}: Python ${probe.version} is below 3.11`);
    } catch (error) {
      errors.push(`${candidate.command}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  throw new Error(
    `Python 3.11 or newer is required. Install it or set LABAI_BOOTSTRAP_PYTHON.\n${errors.join("\n")}`,
  );
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function writeJsonAtomic(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  try {
    fs.renameSync(temporaryPath, filePath);
  } catch (error) {
    if (!error || !["EEXIST", "EPERM"].includes(error.code)) throw error;
    fs.rmSync(filePath, { force: true });
    fs.renameSync(temporaryPath, filePath);
  }
}

function processIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error && error.code === "EPERM";
  }
}

function lockIsStale(
  lockPath,
  staleAfterMs,
  invalidLockGraceMs = DEFAULT_INVALID_LOCK_GRACE_MS,
) {
  try {
    const stat = fs.statSync(lockPath);
    const lock = readJson(lockPath);
    const ageMs = Date.now() - stat.mtimeMs;
    if (lock?.hostname === os.hostname() && Number.isInteger(Number(lock.pid))) {
      if (processIsAlive(Number(lock.pid))) return false;
      const childPids = Array.isArray(lock.childPids)
        ? lock.childPids.map(Number).filter(Number.isInteger)
        : [];
      return !childPids.some((pid) => processIsAlive(pid));
    }
    if (!lock) return ageMs >= invalidLockGraceMs;
    return ageMs > staleAfterMs;
  } catch {
    return true;
  }
}

async function withInstallLock(lockPath, callback, options = {}) {
  const timeoutMs = options.timeoutMs ?? DEFAULT_LOCK_TIMEOUT_MS;
  const staleAfterMs = options.staleAfterMs ?? DEFAULT_STALE_LOCK_MS;
  const invalidLockGraceMs = options.invalidLockGraceMs ?? DEFAULT_INVALID_LOCK_GRACE_MS;
  const startedAt = Date.now();
  const token = crypto.randomBytes(12).toString("hex");
  const metadata = {
    childPids: [],
    createdAt: new Date().toISOString(),
    hostname: os.hostname(),
    pid: process.pid,
    token,
  };
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });

  while (true) {
    let created = false;
    let handle = null;
    try {
      handle = fs.openSync(lockPath, "wx");
      created = true;
      fs.writeFileSync(handle, JSON.stringify(metadata));
      break;
    } catch (error) {
      if (handle !== null) {
        try {
          fs.closeSync(handle);
          handle = null;
        } catch {
          // The finally block below retries the close.
        }
      }
      if (created && handle === null) fs.rmSync(lockPath, { force: true });
      if (!error || error.code !== "EEXIST") throw error;
      if (lockIsStale(lockPath, staleAfterMs, invalidLockGraceMs)) {
        fs.rmSync(lockPath, { force: true });
        continue;
      }
      if (Date.now() - startedAt >= timeoutMs) {
        throw new Error(`Timed out waiting for dependency installation lock: ${lockPath}`);
      }
      await sleep(500);
    } finally {
      if (handle !== null) {
        try {
          fs.closeSync(handle);
        } catch {
          // Nothing else can be done if the lock handle was already closed by the OS.
        }
      }
    }
  }

  const writeOwnedMetadata = () => {
    const current = readJson(lockPath);
    if (current?.token !== token) return;
    fs.writeFileSync(lockPath, JSON.stringify(metadata), "utf8");
  };
  const lock = {
    trackChild(child) {
      if (!child?.pid || metadata.childPids.includes(child.pid)) return;
      metadata.childPids.push(child.pid);
      writeOwnedMetadata();
      child.once("close", () => {
        metadata.childPids = metadata.childPids.filter((pid) => pid !== child.pid);
        writeOwnedMetadata();
      });
    },
  };

  try {
    return await callback(lock);
  } finally {
    const current = readJson(lockPath);
    if (current?.token === token) fs.rmSync(lockPath, { force: true });
  }
}

function assertManagedVenvPath(backendDir, venvDir) {
  const backend = path.resolve(backendDir);
  const target = path.resolve(venvDir);
  if (target === backend || !target.startsWith(`${backend}${path.sep}`)) {
    throw new Error(`Refusing to manage virtual environment outside backend directory: ${target}`);
  }
}

async function ensureVenv({ backendDir, bootstrapPython, lock, venvDir }) {
  assertManagedVenvPath(backendDir, venvDir);
  const pythonPath = getVenvPythonPath(venvDir);
  if (fs.existsSync(pythonPath)) {
    try {
      const existing = await probePython(pythonPath, [], backendDir, lock);
      if (isVersionAtLeast(existing.parsedVersion)) return existing;
    } catch {
      // Recreate a broken environment below.
    }
    fs.rmSync(venvDir, { force: true, recursive: true });
  } else if (fs.existsSync(venvDir)) {
    fs.rmSync(venvDir, { force: true, recursive: true });
  }

  console.log(`[python] creating ${path.basename(venvDir)} with Python ${bootstrapPython.version}...`);
  await runProcess(bootstrapPython.executable, ["-m", "venv", venvDir], {
    cwd: backendDir,
    label: "python-venv",
    lock,
    logCommand: true,
  });
  const created = await probePython(pythonPath, [], backendDir, lock);
  if (!isVersionAtLeast(created.parsedVersion)) {
    throw new Error(`${pythonPath} is Python ${created.version}; Python 3.11 or newer is required.`);
  }
  return created;
}

function loadRequirements(requirementPaths) {
  return requirementPaths.map((requirementPath) => {
    if (!fs.existsSync(requirementPath)) {
      throw new Error(`Requirements file is missing: ${requirementPath}`);
    }
    return {
      content: fs.readFileSync(requirementPath, "utf8"),
      name: path.basename(requirementPath),
      path: requirementPath,
    };
  });
}

async function validateEnvironment(config, pythonProbe, requirements, { lock = null, quiet = false } = {}) {
  try {
    await runProcess(pythonProbe.executable, ["-m", "pip", "check"], {
      capture: true,
      cwd: config.backendDir,
      label: `${config.name}-pip-check`,
      lock,
    });
    await runProcess(
      pythonProbe.executable,
      [
        "-c",
        REQUIREMENTS_PROBE,
        JSON.stringify(requirements.map(({ content, name }) => ({ content, name }))),
      ],
      {
        capture: true,
        cwd: config.backendDir,
        label: `${config.name}-requirements`,
        lock,
      },
    );
    const importScript = [
      "import importlib",
      `modules = ${JSON.stringify(config.imports)}`,
      "[importlib.import_module(name) for name in modules]",
    ].join(";");
    await runProcess(pythonProbe.executable, ["-c", importScript], {
      capture: true,
      cwd: config.backendDir,
      env: config.name === "sandbox" ? { MPLBACKEND: "Agg" } : {},
      label: `${config.name}-imports`,
      lock,
    });
    return true;
  } catch (error) {
    if (!quiet) {
      console.warn(`[${config.name}] dependency validation failed: ${error.message}`);
    }
    return false;
  }
}

async function installRequirements(config, pythonProbe, requirements, lock) {
  for (const requirement of requirements) {
    await runProcess(
      pythonProbe.executable,
      ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", requirement.path],
      {
        cwd: config.backendDir,
        label: `${config.name}-deps`,
        lock,
        logCommand: true,
      },
    );
  }
}

async function ensureEnvironment(config, bootstrapPython) {
  const lockPath = path.join(config.lockDir, `${config.name}.lock`);
  return withInstallLock(lockPath, async (lock) => {
    const pythonProbe = await ensureVenv({
      backendDir: config.backendDir,
      bootstrapPython,
      lock,
      venvDir: config.venvDir,
    });
    const requirements = loadRequirements(config.requirementPaths);
    const expectedStamp = buildEnvironmentStamp({
      environmentName: config.name,
      imports: config.imports,
      python: pythonProbe,
      requirements,
    });
    const stampPath = path.join(config.venvDir, STAMP_FILENAME);
    const installedStamp = readJson(stampPath);

    if (
      stampsMatch(installedStamp, expectedStamp) &&
      (await validateEnvironment(config, pythonProbe, requirements, { lock, quiet: true }))
    ) {
      console.log(`[${config.name}] Python dependencies are ready.`);
      return pythonProbe.executable;
    }

    console.log(`[${config.name}] installing or repairing Python dependencies...`);
    fs.rmSync(stampPath, { force: true });
    await installRequirements(config, pythonProbe, requirements, lock);
    if (!(await validateEnvironment(config, pythonProbe, requirements, { lock }))) {
      throw new Error(`[${config.name}] dependencies are still invalid after installation.`);
    }
    writeJsonAtomic(stampPath, {
      ...expectedStamp,
      installedAt: new Date().toISOString(),
    });
    console.log(`[${config.name}] Python dependencies are ready.`);
    return pythonProbe.executable;
  });
}

async function ensurePythonEnvironments({ backendDir }) {
  const resolvedBackendDir = path.resolve(backendDir);
  const backendVenvDir = path.join(resolvedBackendDir, ".venv");
  const sandboxVenvDir = path.join(resolvedBackendDir, ".sandbox-venv");
  const lockDir = path.join(resolvedBackendDir, ".runtime", "dependency-locks");
  const preferred = [
    getVenvPythonPath(backendVenvDir),
    getVenvPythonPath(sandboxVenvDir),
  ].filter((candidate) => fs.existsSync(candidate));
  const bootstrapPython = await discoverPython({ cwd: resolvedBackendDir, preferred });
  console.log(`[python] using ${bootstrapPython.executable} (${bootstrapPython.version})`);

  const backendPython = await ensureEnvironment(
    {
      backendDir: resolvedBackendDir,
      imports: [
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "openai",
        "pydantic",
        "pydantic_settings",
        "dotenv",
        "aiofiles",
        "multipart",
        "pypdf",
        "openpyxl",
        "psutil",
      ],
      lockDir,
      name: "backend",
      requirementPaths: [path.join(resolvedBackendDir, "requirements.txt")],
      venvDir: backendVenvDir,
    },
    bootstrapPython,
  );
  const sandboxPython = await ensureEnvironment(
    {
      backendDir: resolvedBackendDir,
      imports: [
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        "seaborn",
        "openpyxl",
        "xlsxwriter",
        "docx",
        "pptx",
        "PIL",
        "pypdf",
        "reportlab",
        "xlrd",
      ],
      lockDir,
      name: "sandbox",
      requirementPaths: [path.join(resolvedBackendDir, "sandbox-requirements.txt")],
      venvDir: sandboxVenvDir,
    },
    bootstrapPython,
  );
  return { backendPython, sandboxPython };
}

module.exports = {
  DEFAULT_INVALID_LOCK_GRACE_MS,
  INSTALL_SCHEMA_VERSION,
  MINIMUM_PYTHON,
  REQUIREMENTS_PROBE,
  STAMP_FILENAME,
  buildEnvironmentStamp,
  discoverPython,
  ensurePythonEnvironments,
  getPythonCandidates,
  getVenvPythonPath,
  isVersionAtLeast,
  lockIsStale,
  parsePythonVersion,
  sha256,
  stableStringify,
  stampsMatch,
  terminateActiveProcesses,
  withInstallLock,
};
