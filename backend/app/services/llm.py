import json
import re
import time
from typing import Optional

from openai import OpenAI

from ..config import settings

_client: Optional[OpenAI] = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError(
                "未配置 LLM API Key：请在后端环境变量中设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY"
            )
        _client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout,
            max_retries=0,  # 重试自己控制，便于记录
        )
    return _client


class LLMResult:
    def __init__(self, text: str, tokens: int, latency_ms: int):
        self.text = text
        self.tokens = tokens
        self.latency_ms = latency_ms


def _extra_body(model: str) -> dict:
    is_deepseek = settings.llm_provider == "deepseek" or model.startswith("deepseek-")
    if not is_deepseek:
        return {}
    body = {}
    thinking = settings.deepseek_thinking or "disabled"
    if thinking:
        body["thinking"] = {
            "type": thinking,
            "reasoning_effort": settings.deepseek_reasoning_effort,
        }
    return body


def chat(
    system: str,
    user: str,
    model: str = "",
    temperature: float = 0.2,
    json_mode: bool = False,
) -> LLMResult:
    model = model or settings.openai_model
    last_err = None
    for attempt in range(settings.openai_max_retries):
        start = time.time()
        try:
            kwargs = {
                "model": model,
                "temperature": temperature,
                "max_tokens": settings.openai_max_tokens,  # 不设会用网关默认上限，长 JSON 被截断
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if json_mode and settings.llm_json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            extra_body = _extra_body(model)
            if extra_body:
                kwargs["extra_body"] = extra_body
            resp = client().chat.completions.create(**kwargs)
            latency = int((time.time() - start) * 1000)
            tokens = resp.usage.total_tokens if resp.usage else 0
            return LLMResult(resp.choices[0].message.content or "", tokens, latency)
        except Exception as e:  # noqa: BLE001 网络/限流等统一退避重试
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(
        "LLM 调用失败"
        f"（provider={settings.llm_provider}, model={model}, base_url={settings.openai_base_url}, "
        f"{settings.openai_max_retries} 次重试后）：{last_err}"
    )


def chat_stream(
    system: str,
    user: str,
    model: str = "",
    temperature: float = 0.2,
):
    model = model or settings.openai_model
    kwargs = {
        "model": model,
        "temperature": temperature,
        "max_tokens": settings.openai_max_tokens,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    extra_body = _extra_body(model)
    if extra_body:
        kwargs["extra_body"] = extra_body
    stream = client().chat.completions.create(**kwargs)
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, "content", None)
        if text:
            yield text


def extract_json(text: str):
    """从模型输出中提取 JSON（容忍 ```json 围栏和前后缀文字）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 找第一个 { 或 [ 到最后一个 } 或 ]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"模型输出不是合法 JSON: {text[:200]}")


def chat_json(system: str, user: str, model: str = "", temperature: float = 0.2):
    """要求 JSON 输出；解析失败时带错误信息重试一次（修复重试）。

    返回 (parsed, raw_text, tokens, latency_ms)。"""
    wants_json_object = "JSON 对象" in system or "JSON object" in system.lower()
    result = chat(system, user, model=model, temperature=temperature, json_mode=wants_json_object)
    try:
        return extract_json(result.text), result.text, result.tokens, result.latency_ms
    except ValueError as first_err:
        repair_user = (
            user
            + "\n\n你上一次的输出无法解析为 JSON，错误："
            + str(first_err)[:300]
            + "\n请重新输出，只输出合法 JSON，不要任何其他文字。"
        )
        retry = chat(system, repair_user, model=model, temperature=temperature, json_mode=wants_json_object)
        parsed = extract_json(retry.text)
        return (
            parsed,
            retry.text,
            result.tokens + retry.tokens,
            result.latency_ms + retry.latency_ms,
        )
