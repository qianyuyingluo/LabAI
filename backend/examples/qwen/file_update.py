# 功能: 通义千问文件更新官方示例 / Purpose: Official Qwen file update example.
import os
from pathlib import Path
from openai import OpenAI

client = OpenAI(
    # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)
file_object = client.files.create(file=Path("百炼系列手机产品介绍.docx"), purpose="file-extract")
completion = client.chat.completions.create(
    model="qwen-long",  # 模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
    messages=[
        {'role': 'system', 'content': f'fileid://{file_object.id}'},
        {'role': 'user', 'content': '这篇文章讲了什么？'}
    ]
)
print(completion.model_dump_json())
