"""
LLM 客户端 - 火山引擎方舟
兼容OpenAI接口格式
"""
from openai import OpenAI
from typing import Optional


class LLMClient:
    """大模型客户端（火山引擎方舟）"""

    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key
        self.model = model  # 接入点ID（endpoint_id），格式如 ep-xxxxxx
        self.client = None
        if api_key and model:
            self._init_client()

    def _init_client(self):
        """初始化OpenAI兼容客户端"""
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )

    def set_config(self, api_key: str, model: str):
        """更新配置"""
        self.api_key = api_key
        self.model = model
        self._init_client()

    def chat(self, prompt: str, system_prompt: str = "") -> str:
        """
        调用大模型对话
        :param prompt: 用户prompt
        :param system_prompt: 系统prompt
        :return: 模型返回的文本
        """
        if not self.client:
            raise Exception("LLM未配置")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.1,  # 低温度，保证输出稳定
        )

        return response.choices[0].message.content

    def test_connection(self) -> dict:
        """测试连接"""
        try:
            reply = self.chat("你好，请回复'连接成功'")
            return {"success": True, "message": f"连接成功，模型回复：{reply}"}
        except Exception as e:
            return {"success": False, "message": f"连接失败：{str(e)}"}
