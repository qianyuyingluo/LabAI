# LabAI

LabAI 是一个本地运行的实验室 AI 工作台。项目由 FastAPI 后端和 Next.js 前端组成，支持流式对话、模型配置、文件/图片上传、聊天历史、Python 沙箱数据处理、Office 文件生成，以及需要先生成计划再审批执行的 Plan Mode。

## 功能特性

- 流式 AI 对话：前端通过 SSE 接收模型输出，后端保存聊天、消息和运行状态。
- 模型配置管理：支持 OpenAI-compatible API，可在界面中新增、测试、切换模型档案，并单独启用或关闭原生工具调用。
- 文件与图片输入：支持图片，以及 PDF、DOCX、PPTX、XLSX、CSV/TSV、文本等文件上传和内容提取。
- Python 沙箱：AI 可以在独立虚拟环境和受限本机子进程中处理数据、绘图，并生成 Word、Excel、PowerPoint、PDF 等文件；生成结果会在聊天中提供下载链接。
- Plan Mode：先生成分析计划，用户可以批准或要求修订，批准后再继续执行。
- Prompt 查看：前端提供 Plan Mode prompt 查看入口，后端记录 prompt 版本 hash。
- 本地持久化：默认使用 SQLite，聊天、模型、上传文件和计划状态保存在本地存储目录中。

## 技术栈

- 前端：Next.js、React、TypeScript、Tailwind CSS、Radix UI、lucide-react
- 后端：FastAPI、SQLAlchemy、SQLite、OpenAI Python SDK、pydantic-settings
- 启动脚本：根目录 `npm start` 会检查/安装 Node 依赖、后端 Python 依赖和独立沙箱依赖，再启动后端与前端

## 项目结构

```text
LabAI/
├─ backend/                 # FastAPI 后端
│  ├─ app/
│  │  ├─ api/               # HTTP / SSE API
│  │  ├─ adapters/          # 模型供应商适配器
│  │  ├─ core/              # 配置、数据库、错误处理
│  │  ├─ db/                # SQLAlchemy 模型与会话
│  │  ├─ prompts/           # Plain Mode 与共享 prompts
│  │  ├─ services/          # 对话、模型、计划、文件服务
│  │  ├─ skills/            # 文件理解能力
│  │  └─ storage/           # 本地运行数据，默认不提交
│  ├─ tests/
│  ├─ requirements.txt         # 后端依赖
│  └─ sandbox-requirements.txt # 沙箱数据处理与文档生成依赖
├─ frontend/                # Next.js 前端
│  ├─ src/app/
│  ├─ src/components/
│  └─ src/lib/
├─ scripts/start.js         # 一键启动脚本
├─ scripts/python-envs.js   # Python 虚拟环境检查与安装
├─ package.json
└─ README.md
```

## 环境要求

- Node.js 20+
- Python 3.11+
- 可用的 OpenAI-compatible 模型服务

## 快速开始

创建本地环境变量文件：

```powershell
Copy-Item backend\.env.example backend\.env
```

按需填写 `backend/.env`。最常用的变量是：

```env
OPENAI_COMPAT_API_KEY=your-api-key
OPENAI_COMPAT_BASE_URL=https://api.example.com/v1
OPENAI_COMPAT_DEFAULT_MODEL=your-model-name
OPENAI_COMPAT_SUPPORTS_STREAM=true
OPENAI_COMPAT_SUPPORTS_VISION=false
```

启动应用：

```powershell
npm.cmd start
```

首次启动会自动完成以下工作，无需手动激活虚拟环境：

1. 检查根目录 Node 依赖，缺失或清单变化时执行 `npm install`。
2. 查找 Python 3.11+，创建或修复 `backend/.venv` 和 `backend/.sandbox-venv`。
3. 分别安装 `requirements.txt` 与 `sandbox-requirements.txt`，执行 `pip check` 和关键模块导入检查。
4. 依赖验证成功后才启动服务；任何安装或验证失败都会终止启动。

依赖状态由 requirements、Python 版本和安装 schema 的组合 hash 记录。并发执行多个 `npm start` 时使用安装锁，避免同时修改同一环境。后端始终由 `backend/.venv` 的解释器启动，并自动收到沙箱解释器的绝对路径。

首次安装需要联网下载 Node 与 Python 依赖，耗时取决于网络和机器性能；离线且本地缓存不完整时，启动器会保留原始 `npm`/`pip` 错误并终止，不会留下半启动服务。依赖完整后，后续启动会命中指纹并跳过重复安装。

沙箱会清除 API Key、数据库地址和代理等父进程环境，只挂载聊天相关输入，限制输出目录、运行时间、内存、文件数量与大小，并尽力拦截联网、子进程和工作区外文件访问。它仍是以当前本机用户运行的受限进程，不是容器、虚拟机或面向恶意多租户代码的强安全边界；不要把不受信任的人提供的 Python 代码交给它执行。

如果机器上有多个 Python，可在运行前设置 `LABAI_BOOTSTRAP_PYTHON` 为希望使用的 Python 3.11+ 解释器绝对路径；启动脚本也会依次尝试现有虚拟环境、Windows `py -3`、`python3` 或 `python`。

默认地址：

- 前端：http://127.0.0.1:3000/lab
- 后端：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs

## 常用命令

前端检查：

```powershell
cd frontend
npm.cmd run typecheck
npm.cmd run lint
```

启动脚本单元测试（不会实际安装依赖）：

```powershell
npm.cmd run test:scripts
```

后端语法检查：

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m compileall app
Pop-Location
```

后端测试：

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m unittest discover tests
Pop-Location
```

## 配置说明

后端配置读取 `backend/.env`，主要变量包括：

| 变量 | 说明 |
| --- | --- |
| `OPENAI_COMPAT_API_KEY` | OpenAI-compatible API key |
| `OPENAI_COMPAT_BASE_URL` | OpenAI-compatible API base URL |
| `OPENAI_COMPAT_DEFAULT_MODEL` | 默认模型名称 |
| `OPENAI_COMPAT_SUPPORTS_STREAM` | 默认模型是否支持流式输出 |
| `OPENAI_COMPAT_SUPPORTS_VISION` | 默认模型是否支持图片输入 |
| `BACKEND_STORAGE_DIR` | 本地存储目录，默认是 `backend/app/storage` |
| `DATABASE_URL` | SQLAlchemy 数据库 URL，默认是本地 SQLite |
| `CORS_ORIGINS` | 允许访问后端的前端来源列表 |
| `LABAI_SANDBOX_PYTHON` | 沙箱 Python 解释器；`npm start` 会自动注入绝对路径 |
| `SANDBOX_TIMEOUT_SECONDS` | 每次沙箱运行的超时秒数，默认 90 |
| `SANDBOX_MAX_CODE_KB` | 单次执行代码大小上限，默认 100 KB |
| `SANDBOX_MAX_STDIO_KB` | 标准输出与错误输出大小上限，默认 64 KB |
| `SANDBOX_MAX_OUTPUT_FILE_MB` | 单个生成文件大小上限，默认 25 MB |
| `SANDBOX_MAX_OUTPUT_TOTAL_MB` | 单次运行全部生成文件大小上限，默认 50 MB |
| `SANDBOX_MAX_OUTPUT_FILES` | 单次运行生成文件数量上限，默认 20 |
| `SANDBOX_MAX_INPUT_MB` | 单次运行输入文件总大小上限，默认 200 MB |
| `SANDBOX_MAX_INPUT_FILES` | 单次运行输入文件数量上限，默认 50 |
| `SANDBOX_MAX_TOOL_CALLS` | 单条 AI 回复允许的沙箱工具调用次数，默认 6 |
| `SANDBOX_MAX_CONCURRENT_RUNS` | 同时运行的沙箱任务数，默认 2 |
| `SANDBOX_MAX_MEMORY_MB` | 支持该限制的平台上的内存上限，默认 1536 MB |
| `SANDBOX_NETWORK_ENABLED` | 是否允许沙箱网络访问，默认关闭 |

## 数据与提交注意事项

以下内容属于本地运行数据，不应提交到 GitHub：

- `backend/.env`
- `backend/app/storage/`
- `backend/.runtime/`
- `backend/.venv/`
- `backend/.sandbox-venv/`
- `node_modules/`
- `frontend/.next/`
- `frontend/.playwright/`
- `testfiles/`

如果需要在其他机器上跑后端测试，请自行准备 `testfiles/` 中的测试文件，或通过 `LABAI_TESTFILES_DIR` 指向本地样例目录。
"# LabAI" 
