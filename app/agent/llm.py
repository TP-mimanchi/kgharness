"""
大模型初始化模块

负责从 .env 中读取模型配置，并创建项目统一复用的模型对象
后续主智能体和子智能体都从这里导入 model，避免在多个文件里重复加载环境变量
"""

import os

from dotenv import find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(find_dotenv())

# 仅复用 OpenAI 兼容协议；实际服务、模型和密钥均来自阿里云百炼。
model = ChatOpenAI(
    model=os.getenv("LLM_QWEN_MAX", "qwen-max"),
    api_key=os.getenv("DASHSCOPE_API_KEY", os.getenv("OPENAI_API_KEY")),
    base_url=os.getenv("DASHSCOPE_BASE_URL", os.getenv("OPENAI_BASE_URL")),
    timeout=60,
    max_retries=2,
)
