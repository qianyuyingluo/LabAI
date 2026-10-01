import type { Attachment, ChatMessage } from "@/lib/types";
import type { Locale } from "@/lib/i18n";

export function createId(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

export function mapMessages(messages: ChatMessage[]) {
  return messages.reduce<Record<string, ChatMessage>>((acc, message) => {
    acc[message.id] = message;
    return acc;
  }, {});
}

export function createAttachment(file: File): Attachment {
  const fallbackName = file.type.startsWith("image/") ? "pasted-image.png" : "pasted-file";
  return {
    id: createId("file"),
    name: file.name || fallbackName,
    size: file.size,
    type: file.type || "application/octet-stream",
    createdAt: Date.now(),
    rawFile: file,
    uploadStatus: "idle",
  };
}

export function buildMockAssistantResponse(
  prompt: string,
  files: Attachment[],
  planMode: boolean,
  locale: Locale,
) {
  const fileLine =
    locale === "zh"
      ? files.length
        ? `我已收到 ${files.length} 个文件：${files
            .map((file) => `\`${file.name}\``)
            .join("、")}。如果这些是指导书截图或文档，我会先提取实验目标、变量和公式。`
        : "你还没有上传指导书或数据文件，我先基于描述给出处理路线。"
      : files.length
        ? `I received ${files.length} file(s): ${files
            .map((file) => `\`${file.name}\``)
            .join(", ")}. If these are guide screenshots or documents, I will first extract the experiment goal, variables, and formulas.`
        : "No guide or data file has been uploaded yet, so I will plan from the description first.";

  if (planMode) {
    return locale === "zh"
      ? `### Plan mode：处理计划

${fileLine}

针对你的问题：**${prompt.trim() || "根据指导书规划实验数据处理步骤"}**。

#### 先做的事

1. 从指导书中识别实验目的、测量量、控制变量和记录表结构。
2. 整理需要用到的公式、单位换算和不确定度传播关系。
3. 明确数据文件应包含的列，例如电压、电流、距离、光强或角度。
4. 决定绘图和拟合策略，但暂时不写分析代码。

#### 公式草案

$$
I = I_0 e^{qV / nkT}
$$

$$
R^2 = 1 - \\frac{\\sum_i (y_i - \\hat y_i)^2}{\\sum_i (y_i - \\bar y)^2}
$$

#### 下一步

上传原始数据后，我再根据这个计划生成 Python 分析代码，并用 matplotlib 输出图像。`
      : `### Plan mode: processing plan

${fileLine}

Request: **${prompt.trim() || "Plan experiment data-processing steps from the guide"}**.

#### First pass

1. Extract the experiment goal, measured quantities, control variables, and table structure from the guide.
2. List formulas, unit conversions, and uncertainty propagation.
3. Define the expected data columns, such as voltage, current, distance, intensity, or angle.
4. Decide plotting and fitting strategy without writing analysis code yet.

#### Formula draft

$$
I = I_0 e^{qV / nkT}
$$

$$
R^2 = 1 - \\frac{\\sum_i (y_i - \\hat y_i)^2}{\\sum_i (y_i - \\bar y)^2}
$$

#### Next step

After the raw data is uploaded, I can generate Python analysis code and produce plots with matplotlib.`;
  }

  return locale === "zh"
    ? `### Python / matplotlib 数据分析草稿

${fileLine}

针对你的问题：**${prompt.trim() || "自动分析实验数据"}**，下面先给出可替换数据源的分析骨架。本站不提供模型服务，实际回答质量取决于你接入的 API。

#### 分析步骤

1. 读取 CSV / Excel 数据，统一单位并校验列名。
2. 根据 Plan mode 中整理的公式计算派生量。
3. 用稳健方法标记异常点，再执行拟合。
4. 用 matplotlib 生成图像，报告里引用导出的 PNG / SVG。

#### Python 示例

\`\`\`python
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

def linear_model(x, a, b):
    return a * x + b

df = pd.read_csv("data.csv")
x_data = df["voltage_v"].to_numpy()
y_data = df["current_ma"].to_numpy()

params, covariance = curve_fit(linear_model, x_data, y_data)
residual = y_data - linear_model(x_data, *params)

fig, ax = plt.subplots(figsize=(6, 4), dpi=160)
ax.scatter(x_data, y_data, label="data", s=18)
ax.plot(x_data, linear_model(x_data, *params), label="fit")
ax.set_xlabel("Voltage / V")
ax.set_ylabel("Current / mA")
ax.legend()
fig.tight_layout()
fig.savefig("fit_result.png")
\`\`\`

#### 初步结果表

| 指标 | 估计值 | 说明 |
| --- | ---: | --- |
| 有效样本数 | 128 | 已排除空值 |
| 异常点数量 | 3 | 建议人工复核 |
| 拟合优度 R² | 0.992 | 可进入报告 |

图像输出统一交给 matplotlib，后续结果面板只预览 matplotlib 生成的文件。`
    : `### Python / matplotlib analysis draft

${fileLine}

Request: **${prompt.trim() || "Analyze experiment data"}**. This site does not provide model service; output quality depends on your own connected API.

#### Analysis steps

1. Read CSV / Excel data, normalize units, and validate column names.
2. Compute derived quantities from the formulas planned in Plan mode.
3. Flag outliers with robust methods, then run fitting.
4. Use matplotlib to export PNG / SVG figures for the report.

#### Python example

\`\`\`python
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

def linear_model(x, a, b):
    return a * x + b

df = pd.read_csv("data.csv")
x_data = df["voltage_v"].to_numpy()
y_data = df["current_ma"].to_numpy()

params, covariance = curve_fit(linear_model, x_data, y_data)
residual = y_data - linear_model(x_data, *params)

fig, ax = plt.subplots(figsize=(6, 4), dpi=160)
ax.scatter(x_data, y_data, label="data", s=18)
ax.plot(x_data, linear_model(x_data, *params), label="fit")
ax.set_xlabel("Voltage / V")
ax.set_ylabel("Current / mA")
ax.legend()
fig.tight_layout()
fig.savefig("fit_result.png")
\`\`\`

#### Initial result table

| Metric | Estimate | Note |
| --- | ---: | --- |
| Valid samples | 128 | Missing values removed |
| Outliers | 3 | Manual review suggested |
| Fit R² | 0.992 | Ready for report |

Plot generation is delegated to matplotlib; the result panel will only preview exported matplotlib files.`;
}

export function buildStressHistory(count = 100) {
  const now = Date.now();
  const messages: ChatMessage[] = [];

  for (let index = 0; index < count; index += 1) {
    const isUser = index % 2 === 0;
    messages.push({
      id: `stress-${index}`,
      role: isUser ? "user" : "assistant",
      content: isUser
        ? `第 ${index / 2 + 1} 组实验数据：请检查异常点并给出拟合策略。`
        : `已完成第 ${(index + 1) / 2} 组数据的预检查。数据列完整，建议继续保留原始采样点，并在报告中说明测量不确定度。`,
      status: "done",
      createdAt: now - (count - index) * 1000,
    });
  }

  return messages;
}

export function buildLongStressResponse() {
  const section = `## 流式性能测试段落

这是一段用于验证长文本流式渲染的 Markdown。它包含普通段落、表格、代码块和公式文本，用来观察输入、滚动、设置面板和历史消息是否仍然保持响应。

| 样本 | 电压 V | 电流 mA | 标记 |
| --- | ---: | ---: | --- |
| A-01 | 0.62 | 1.24 | 正常 |
| A-02 | 0.68 | 1.47 | 正常 |
| A-03 | 0.73 | 1.92 | 复核 |

\`\`\`ts
const buffered = tokens.reduce((acc, token) => acc + token, "");
requestAnimationFrame(() => setStreamingContent(buffered));
\`\`\`

公式：$y = a x + b + \\epsilon$。建议在最终报告里同时保留残差图和参数置信区间。
`;

  return Array.from({ length: 36 }, (_, index) => `${section}\n\n段落编号：${index + 1}`).join(
    "\n\n",
  );
}
