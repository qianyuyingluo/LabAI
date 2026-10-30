# LabAI

LabAI 是一个本地运行的文件夹 Agent 和实验室 AI 工作台。绑定本机文件夹后，AI 可以浏览、搜索、修改项目文件，执行 PowerShell / Shell 命令，并根据执行结果继续工作。项目保留文档解析、图片输入、Python 数据处理及 Plan Mode，使用 FastAPI + SQLite 后端和 Next.js 前端。

## 功能特性

- 本地工作区：每个聊天绑定一个现有文件夹；支持目录浏览、按行读取、文本搜索、创建文件及精确替换，编辑返回差异。
- 浏览文件夹：Windows 使用原生系统选择器，自动填入完整路径；支持中文路径、取消保留原值，并保留手动输入。
- 真实终端：Windows 使用 PowerShell，其他系统使用本机 Shell；显示命令、工作目录、输出和退出码，支持超时与停止进程树。
- 执行权限：默认每次文件写入、修改及命令执行都需确认；也可以为当前工作区选择自动执行。
- 执行检查点：持久化模型消息、工具结果及待执行调用，页面重新打开可查看状态；后端中断后可以从检查点继续。
- 流式 AI 对话：前端通过 SSE 接收模型输出，后端保存聊天、消息和运行状态。
- 模型配置管理：支持 OpenAI-compatible API，可在界面中新增、测试、切换模型档案，并单独启用或关闭原生工具调用。
- 文件与图片输入：支持图片，以及 PDF、DOCX、PPTX、XLSX、CSV/TSV、JSON、文本等文件上传；文档按内容块提取，保留来源位置、解析状态和缺失警告。
- 原文查阅：解析结果本地缓存；短文档直接提供原文，长文档支持关键词搜索和分段读取，不再为每个文件预先调用一次模型。
- Python 沙箱：AI 可以在独立虚拟环境和受限本机子进程中处理数据、绘图，并生成 Word、Excel、PowerPoint、PDF 等文件；生成结果会在聊天中提供下载链接。
- Plan Mode：先生成分析计划，用户可以批准或要求修订，批准后再继续执行。
- Prompt 查看：前端提供 Plan Mode prompt 查看入口，后端记录 prompt 版本 hash。
- 本地持久化：默认使用 SQLite，聊天、模型、上传文件和计划状态保存在本地存储目录中。

## 在文件夹中工作

### 本次更新（2026-10-30）

- 重构文件解析为保留来源位置的结构化内容块，加入进程隔离、解析缓存、资源限制和原文读取/搜索工具。
- 新增持久工作区、文件编辑与真实命令执行，提供实时输出、操作审批、停止和检查点恢复。
- 增加 Windows 原生文件夹选择器；仅接受本机请求，同时只打开一个窗口，超时或页面断连时清理选择器进程。
- 补充上下文压缩、工具调用完整性检查和恢复防重放处理；兼容现有 SQLite 数据库，保留原有文档分析与计划模式。
- 已完成真实模型的项目修复与命令验证，以及 Windows 文件夹选择、取消、路径回填和绑定的实际界面验证。复杂任务效果仍取决于配置的模型；非 Windows 原生窗口尚未实机验证。

### 使用步骤

1. 运行 `npm start`，在设置中配置支持原生工具调用的模型。
2. 展开聊天顶部的工作区入口，点击“浏览文件夹”在系统窗口中选择目录，再点击“绑定文件夹”；也可以手动填写**后端所在机器上已存在的绝对路径**，例如 `C:\Users\your-name\Desktop\my-project`。取消选择会保留原路径；选择窗口在运行后端的本机桌面显示。文件夹绑定不会复制或上传整个目录。
3. 保持默认“逐次确认”，或选择对当前工作区“自动执行”。绑定后即可浏览文件夹，并发送任务，例如：“查看这个项目，修复计算总价的错误，运行相关测试。”
4. 在工具记录中查看读取结果、文件差异和命令输出。逐次确认模式下，可以批准或拒绝具体动作。
5. 执行过程中可以停止任务。停止会终止正在运行的命令进程树；已经写入的文件会保留，不会自动撤销。可继续发送消息要求修正。

工作区模式使用真实项目目录，修改会直接保存。文件工具限制路径在绑定目录内，并检查符号链接越界；**终端命令以当前用户权限运行，不是操作系统沙箱**，命令本身仍可访问其他目录或网络。自动执行代表允许模型直接运行这些本机命令，建议在可信项目中使用。服务默认只监听回环地址，不能当作有认证的公网执行服务部署。

工作区根目录的 `AGENTS.md` 会进入初始上下文；模型被要求在编辑子目录前检查该目录适用的规则。工具输出与选中的文件片段会发送到配置的模型服务。生成文件保存在项目目录内；不绑定工作区时仍使用原有附件和临时 Python 沙箱流程。

执行检查点及命令日志位于 `backend/app/storage/agent_runs/<消息ID>/`。恢复时不会重新执行已经记录完成的工具；如果后端在命令或写入中途终止，结果可能未知，系统将未知结果交回模型要求先检查，而不会直接重放该动作。停止、重启和恢复都不是文件事务回滚，也不能保证被外部程序分离出去的任务跨进程重启可控。

工作区 agent 每次执行默认最多 60 次工具调用，超过后可以从检查点继续。上下文按字符预算缩减旧记录，保留当前请求及完整的近期工具调用/结果配对；这不是精确 token 计算，长任务仍可能需要重新读取文件。当前采用已有模型适配器和本地执行循环，没有引入 LangChain / LangGraph 服务依赖。

## 文件解析与问答

默认流程是：**上传并保存原文件 → 隔离进程提取结构化内容 → 本地缓存 → 按问题提供原文 → 必要时继续查阅或用 Python 计算**。文字提取和关键词查找不调用模型服务；主模型仍使用你配置的模型服务，发送的原文片段也会进入该服务的上下文。

每个内容块包含稳定的块编号（如 `b000001`）、内容类型、正文和来源位置。PDF 使用物理页码，PPT 使用实际幻灯片顺序，Word 使用段落/表格位置，电子表格保留工作表和单元格范围。Word 的段落编号不等同于 Word 排版后的页码。

| 格式 | 当前读取方式与边界 |
| --- | --- |
| DOCX | 按原文顺序读取标题、段落和表格，保留表格结构；图片、复杂公式等未覆盖内容会给出警告，不保证完整还原版面 |
| PPTX | 按实际幻灯片顺序读取文本、表格、分组对象和演讲者备注；图片、图表等视觉内容不自动转换成完整语义 |
| XLSX | 同时读取公式文本和缓存值，保留坐标、数字格式及合并信息；公式没有缓存结果时明确警告，解析器不会计算公式，也不保证缓存结果是最新值 |
| CSV / TSV | 按记录读取，保留列关系和记录位置；表格概览不能代替全量计算 |
| JSON | 校验 JSON 并提取带路径的内容；格式错误返回解析失败 |
| TXT / Markdown | 解码并保留行位置和必要空白；文件过大时明确标记截断 |
| PDF | 使用 pypdf 按物理页提取文字；无文字页会提示缺失，扫描页、图片、复杂表格和公式可能需要 OCR 或视觉识别 |
| DOC / XLS / PPT | 可以保存原文件，但自动解析标记为 `unsupported`；建议先转为 DOCX / XLSX / PPTX |
| 图片 | PNG / JPEG / WebP 由支持视觉输入的主模型读取；文档中的内嵌图片不会自动作为视觉输入。SVG 可以保存，但不属于直接视觉输入支持范围 |

### 上下文和按需读取

- 短文档在文件上下文预算内直接提供原文，不经过子模型摘要。
- 长文档提供内容概览及与当前问题相关的原文块；支持工具的主模型可以调用 `search_file` 查找关键词，再用 `read_file` 读取相邻内容。读取结果带块编号和来源位置，回答应引用这些位置。
- `read_file` 支持块索引和块内字符偏移，并返回继续读取的位置。整篇总结应按顺序覆盖全文；达到读取次数或上下文限制时，必须说明未覆盖的范围，不能把少量片段当作全文。
- 表格默认提供结构和样例。统计、筛选、拟合、绘图等操作使用 `execute_python` 读取原始文件，不从截断的表格预览推算结果。
- `read_file` / `search_file` 只能读取当前聊天或本轮附件的文件 ID，不接受任意磁盘路径。文档内容作为资料处理，不作为执行指令。
- 关闭模型工具调用后，只能使用已经提供的文件上下文；不能继续查阅省略部分。文档读取工具不要求 Python 沙箱就绪，实际计算和文件生成仍要求沙箱可用。

文件上下文默认上限为 **60,000 字符**；单次文档工具响应默认上限为 **12,000 字符**，可请求的最大值为 **16,000 字符**；每轮最多 **12 次**文档工具调用。这里是字符预算，不是精确 token 预算，也不包含整个聊天历史和累计工具结果；模型上下文窗口较小时应降低配置。关键词搜索是本地词项匹配，尚未使用语义向量检索。

### 缓存、状态与资源限制

解析缓存位于 `backend/app/storage/parsed/`（设置 `BACKEND_STORAGE_DIR` 时随之改变），按文件内容哈希、格式和解析器/依赖版本区分。相同内容复用解析结果，引用仍绑定当前上传文件；解析实现或依赖版本变化后重新生成缓存。缓存损坏会重建，超时和失败结果不缓存。可以删除该目录来强制重新解析，原始上传文件不会因此删除。

解析在独立子进程中执行，默认最多同时运行 2 个，单个任务超时 45 秒；HTTP 异步处理会把等待工作移出事件循环。它是有超时的本地解析进程，不是容器级安全隔离。

解析器还设置了输出和结构上限：最多 500,000 个渲染后字符、20,000 个内容块、500 页/张；表格读取受行列和单元格数量限制，Office 压缩包受解压体积限制。达到提取上限会标记部分解析；超大或损坏输入可能直接被拒绝。**后续读取工具只能读取已经提取的块，不能恢复解析阶段已省略的内容。**

界面将以下状态分开显示：

- `success`：支持的内容已经提取；不代表视觉版面被完整理解。
- `partial`：存在未提取内容或资源限制，查看具体警告。
- `unsupported` / `error`：格式不支持或解析失败，不能当作正文读取成功。
- 初始上下文范围、解析截断和上下文省略分别记录；额外的原文查阅显示在“本轮原文查阅”中。

目前没有自动 OCR、图表理解或公式视觉识别。扫描件和复杂版式材料需要后续增加相应处理能力。

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
│  │  ├─ parsers/           # 结构化解析、来源位置、资源限制和隔离 worker
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
| `MAX_IMAGE_UPLOAD_MB` / `MAX_FILE_UPLOAD_MB` | 图片/其他文件上传大小上限，默认 10 / 25 MB |
| `MAX_FILES_SKILL_CONTEXT_CHARS` | 初始文件上下文字符上限，默认 60000，范围 1024–500000 |
| `FILES_SKILL_PREVIEW_CHARS` | 聊天中的上下文预览长度，默认 4000 |
| `FILES_SKILL_UPLOAD_SUMMARY_CHARS` | 上传响应中的解析预览长度，默认 12000 |
| `FILE_PARSE_TIMEOUT_SECONDS` | 单个解析子进程超时，默认 45 秒，范围 1–300 |
| `FILE_PARSE_MAX_CONCURRENT` | 同时执行的解析进程数，默认 2，范围 1–8 |
| `DOCUMENT_MAX_TOOL_CALLS` | 每轮文档读取/搜索工具调用上限，默认 12，与 Python 工具预算分开 |
| `AGENT_MAX_TOOL_CALLS` | 绑定工作区后，每次执行的总工具调用预算，默认 60；恢复任务时重新计数 |
| `AGENT_CONTEXT_CHARS` | 工作区 agent 请求上下文的近似字符预算，默认 120000；超大当前请求会报错而不是静默删掉要求 |
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

文件解析测试会自动生成 DOCX、PPTX、XLSX、PDF 等小样例，不需要准备私有 `testfiles/`。如需用自己的样例补充上传验证，可设置 `LABAI_TESTFILES_DIR`；其中应包含测试所需的各类文件。

仅运行本次文件处理相关测试：

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_document_*.py"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_file_converters.py"
```

这些文件测试覆盖结构和顺序、公式缓存缺失、空正文、缓存复用、超时、长文档查找/续读、文件访问范围，以及模型工具调用流程。其模型调用使用本地模拟，不代表已验证真实外部模型的回答准确率。

工作区 Agent 回归与真实模型验收：

```powershell
# 在 backend 目录：本地临时项目、真实命令、模拟模型，不产生模型费用
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_workspace*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_agent_recovery.py" -v

# 已启动服务并配置模型后，显式调用真实模型（会产生服务商正常用量）
.\.venv\Scripts\python.exe examples/verify_workspace_agent.py --live

# 在 frontend 目录：浏览器测试使用模拟 API，默认使用本机 Edge
npm run test:e2e
```

真实验收脚本创建独立测试目录，让模型读取项目规则、修复 Python 文件并执行测试；随后独立运行原测试、检查测试文件未被修改，并保存 JSON 验收记录。测试目录及记录位于 `backend/app/storage/workspace-verification/`。这验证文件操作与命令闭环，不代表模型能正确完成任意复杂任务。

工作区 HTTP 入口包括 `POST /api/workspaces/inspect`、`PATCH /api/chats/{chat_id}/workspace`、`GET /api/chats/{chat_id}/workspace` 和 `/workspace/file`；执行通过 `POST /api/runs/chat` 开始，通过 `/api/runs/messages/{message_id}/stream` 订阅，`/approval`、`/cancel`、`/resume` 分别处理授权、停止及中断恢复。旧的直接聊天流接口不接受已绑定工作区的聊天，以免绕过任务管理。
