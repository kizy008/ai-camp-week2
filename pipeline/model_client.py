"""统一 LLM 调用客户端，支持 DeepSeek / Qwen / OpenAI 三个提供商。

用法::

    # 快速一句话调用
    reply = quick_chat("你好")

    # 完整调用（带重试）
    provider = OpenAICompatibleProvider(provider="deepseek")
    resp = chat_with_retry(provider, [
        {"role": "system", "content": "你是一个助手"},
        {"role": "user", "content": "你好"},
    ])
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 全局常量
# ---------------------------------------------------------------------------

# 默认提供商与超时
DEFAULT_PROVIDER = "deepseek"
DEFAULT_TIMEOUT = 60.0

# 重试策略：最多 3 次，指数退避 2s → 4s → 8s，上限 30s
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
RETRY_MAX_DELAY = 30.0

# 美元 → 人民币汇率（硬编码，便于离线估算）
USD_TO_CNY = 7.20


def usd_to_cny(usd_amount: float, rate: float = USD_TO_CNY) -> float:
    """将美元金额按当前汇率换算为人民币。

    Args:
        usd_amount: 美元金额。
        rate: 汇率，默认 7.20。

    Returns:
        人民币金额（保留 6 位小数）。
    """
    return round(usd_amount * rate, 6)


# ---------- 非 DeepSeek 模型：美元计价 ----------
# key = 模型名, value = (每百万输入 tokens 价格, 每百万输出 tokens 价格) 单位 USD
MODEL_TOKENS_PER_DOLLAR: dict[str, tuple[float, float]] = {
    "qwen-max": (2.00, 6.00),
    "qwen-plus": (0.80, 2.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

# ---------- DeepSeek 模型：人民币计价 ----------
# 价格来源: https://api-docs.deepseek.com/quick_start/pricing (2026-05)
# 使用 Cache Miss 价格做保守估算
MODEL_TOKENS_PER_CNY: dict[str, tuple[float, float]] = {
    # key = 模型名, value = (每百万输入 tokens 价格, 每百万输出 tokens 价格) 单位 CNY
    "deepseek-v4-flash": (1.00, 2.00),       # ¥1.00/1M 输入, ¥2.00/1M 输出
    "deepseek-v4-pro": (12.60, 25.20),       # ¥12.60/1M 输入, ¥25.20/1M 输出
    "deepseek-chat": (1.00, 2.00),           # deepseek-v4-flash 非思考模式别名
    "deepseek-reasoner": (1.00, 2.00),       # deepseek-v4-flash 思考模式别名
}

# ---------- 各提供商的 API 接入点 ----------
# 各提供商 API Base URL
# DeepSeek: https://api-docs.deepseek.com/zh-cn/ 官方文档，OpenAI 格式入口
# Qwen / OpenAI: 标准 OpenAI 兼容地址
PROVIDER_API_BASES: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}

# 各提供商默认模型
PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "qwen": "qwen-plus",
    "openai": "gpt-4o-mini",
}

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Token 用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """统一的 LLM 返回结构，包含响应文本与用量。"""

    content: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """LLM 提供商抽象基类，定义统一的 chat 接口。"""

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> LLMResponse:
        """发送聊天补全请求。

        Args:
            messages: 消息列表，每项含 ``role`` 和 ``content`` 字段。
            **kwargs: 额外参数（temperature、max_tokens 等）。

        Returns:
            包含回复内容和用量统计的 LLMResponse。
        """


# ---------------------------------------------------------------------------
# OpenAI 兼容 API 实现
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(LLMProvider):
    """通过 OpenAI 兼容 API 调用任意 LLM 提供商（DeepSeek / Qwen / OpenAI）。

    初始化时按以下优先级读取配置：
    1. 构造参数
    2. 环境变量 ``LLM_PROVIDER``、``<PROVIDER>_API_KEY``、``<PROVIDER>_MODEL``
    3. 模块级默认值
    """

    def __init__(
        self,
        provider: str = "",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        # 提供商名称：参数 > 环境变量 > 默认值 "deepseek"
        self.provider = provider or os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER)

        # API Key：参数 > <PROVIDER>_API_KEY > LLM_API_KEY
        self.api_key = api_key or os.getenv(
            f"{self.provider.upper()}_API_KEY",
            os.getenv("LLM_API_KEY", ""),
        )
        if not self.api_key:
            raise ValueError(
                f"Missing API key for provider '{self.provider}'. "
                f"Set {self.provider.upper()}_API_KEY or LLM_API_KEY."
            )

        # 模型名：参数 > <PROVIDER>_MODEL > 默认模型映射
        self.model = model or os.getenv(
            f"{self.provider.upper()}_MODEL",
            PROVIDER_DEFAULT_MODELS.get(self.provider, "deepseek-v4-flash"),
        )

        # API Base URL：参数 > 提供商固定地址 > LLM_API_BASE
        self.base_url = base_url or PROVIDER_API_BASES.get(
            self.provider,
            os.getenv("LLM_API_BASE", "https://api.deepseek.com"),
        )

        # HTTP 请求超时
        self.timeout = timeout

    def _build_headers(self) -> dict:
        """构造 OpenAI 兼容 API 所需的认证头。"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, messages: list[dict], **kwargs) -> LLMResponse:
        """执行一次聊天补全请求。

        Args:
            messages: 对话消息列表。
            **kwargs: 可覆盖 model、temperature、max_tokens 等参数。

        Returns:
            包含回复内容与 Token 用量的 LLMResponse。
        """
        # 从 kwargs 中提取模型名，未指定则用实例默认值
        model = kwargs.pop("model", self.model)
        payload = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        url = f"{self.base_url.rstrip('/')}/chat/completions"

        # 发送 POST 请求
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._build_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()

        # 解析返回结果
        choice = data["choices"][0]
        content = choice["message"]["content"] or ""
        usage_raw = data.get("usage", {})

        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=model,
            provider=self.provider,
        )


# ---------------------------------------------------------------------------
# 带重试的调用包装
# ---------------------------------------------------------------------------


def chat_with_retry(
    provider: LLMProvider,
    messages: list[dict],
    max_retries: int = MAX_RETRIES,
    **kwargs,
) -> LLMResponse:
    """调用 provider.chat() 并自动重试（指数退避）。

    仅对 ``httpx`` 的 ``HTTPStatusError`` 和 ``RequestError`` 重试，
    其他异常直接透传。

    Args:
        provider: LLMProvider 实例。
        messages: 对话消息列表。
        max_retries: 最大重试次数（默认 3）。
        **kwargs: 透传给 provider.chat() 的额外参数。

    Returns:
        成功时的 LLMResponse。

    Raises:
        RuntimeError: 所有重试均失败后抛出。
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return provider.chat(messages, **kwargs)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            logger.warning(
                "Chat attempt %d/%d failed: %s", attempt, max_retries, exc
            )
            if attempt < max_retries:
                # 指数退避：2s → 4s → 8s ...
                delay = min(
                    RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY
                )
                time.sleep(delay)
    raise RuntimeError(
        f"All {max_retries} chat attempts failed. Last error: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Token 用量估算 & 成本计算
# ---------------------------------------------------------------------------


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """估算一次 API 调用的费用。

    DeepSeek 系列返回人民币（元），其他模型返回美元。

    Args:
        model: 模型名，如 ``"deepseek-chat"``、``"gpt-4o-mini"``。
        prompt_tokens: 输入 Token 数。
        completion_tokens: 输出 Token 数。

    Returns:
        估算费用（DeepSeek 单位为 CNY，其他为 USD）。
    """
    # 优先匹配人民币计价表（DeepSeek 系列）
    cny_rates = MODEL_TOKENS_PER_CNY.get(model)
    if cny_rates is not None:
        input_cost, output_cost = cny_rates
        return (prompt_tokens / 1_000_000 * input_cost) + (
            completion_tokens / 1_000_000 * output_cost
        )

    # 匹配美元计价表（Qwen / OpenAI 系列）
    usd_rates = MODEL_TOKENS_PER_DOLLAR.get(model)
    if usd_rates is None:
        logger.warning("Unknown model '%s', using default rates (USD)", model)
        usd_rates = (0.50, 1.50)
    input_cost, output_cost = usd_rates
    return (prompt_tokens / 1_000_000 * input_cost) + (
        completion_tokens / 1_000_000 * output_cost
    )


def estimate_tokens(text: str) -> int:
    """粗略估算文本对应的 Token 数量（按 4 字符 ≈ 1 Token）。

    仅用于粗略估算，精确计数需调用 API 的 tokenizer。

    Args:
        text: 待估算的文本。

    Returns:
        估算的 Token 数量。
    """
    return len(text) // 4


# ---------------------------------------------------------------------------
# 便捷函数：一句话调用 LLM
# ---------------------------------------------------------------------------


def quick_chat(
    prompt: str,
    system_prompt: str = "",
    provider: str = "",
    **kwargs,
) -> str:
    """一句话调用 LLM，直接返回响应文本。

    适用于快速测试和简单问答场景。
    内部自动构造 ``OpenAICompatibleProvider`` 并调用 ``chat_with_retry``。

    Args:
        prompt: 用户消息。
        system_prompt: 系统提示词，可选。
        provider: 提供商名称，为空则从环境变量读取。
        **kwargs: 透传给 chat_with_retry 的参数。

    Returns:
        LLM 的回复文本。
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    llm_provider = OpenAICompatibleProvider(provider=provider)
    resp = chat_with_retry(llm_provider, messages, **kwargs)
    return resp.content


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_provider(provider: str = "") -> OpenAICompatibleProvider:
    """工厂函数：创建 LLM 提供商实例。

    Args:
        provider: 提供商名称，为空则从环境变量读取。

    Returns:
        OpenAICompatibleProvider 实例。
    """
    return OpenAICompatibleProvider(provider=provider)


# ---------------------------------------------------------------------------
# 自测入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 测试 1：quick_chat 快速对话
    print("=== Test: quick_chat ===")
    result = quick_chat("Say hello in one word.")
    print(f"Response: {result}")

    # 测试 2：完整流程（带重试的 chat 调用）
    print("\n=== Test: chat_with_retry ===")
    provider = OpenAICompatibleProvider()
    resp = chat_with_retry(
        provider,
        [{"role": "user", "content": "What is 2+2? Answer with just a number."}],
        temperature=0.0,
        max_tokens=10,
    )
    print(f"Content: {resp.content}")
    print(f"Usage: {resp.usage}")
    print(f"Model: {resp.model}")

    # 测试 3：成本估算（DeepSeek 返回 CNY，GPT 返回 USD）
    print("\n=== Test: estimate_cost ===")
    cost1 = estimate_cost("deepseek-chat", 500, 200)
    cost2 = estimate_cost("gpt-4o-mini", 500, 200)
    print(f"deepseek-chat (500+200 tokens): ¥{cost1:.6f}")
    print(f"gpt-4o-mini (500+200 tokens): ${cost2:.6f}")

    # 测试 4：Token 估算
    print("\n=== Test: estimate_tokens ===")
    tokens = estimate_tokens("Hello, world!")
    print(f"Estimated tokens: {tokens}")
