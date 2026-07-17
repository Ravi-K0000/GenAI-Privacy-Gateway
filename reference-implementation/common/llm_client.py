import json
import logging
import re
from typing import Any

import requests

from common.config import RuntimeConfig


log = logging.getLogger(__name__)
_logged_provider = False


def detect_pii_json(system_prompt: str, user_prompt: str, runtime_config: RuntimeConfig) -> dict[str, list[str]]:
    raw = call_llm(system_prompt, user_prompt, runtime_config)
    return parse_llm_json(raw)


def call_llm(system_prompt: str, user_prompt: str, runtime_config: RuntimeConfig) -> str:
    global _logged_provider
    provider = runtime_config.llm.provider
    if not _logged_provider:
        log.info(
            "LLM provider selected: %s endpoint_configured=%s model=%s batch_size=%s delay_seconds=%s",
            provider,
            bool(runtime_config.llm.endpoint_url),
            runtime_config.llm.model,
            runtime_config.llm.batch_size,
            runtime_config.llm.delay_seconds,
        )
        _logged_provider = True
    if provider in {"http_json", "enterprise_llm", "custom", "custom_http", "local_llm", "azure_openai"}:
        return call_http_json_llm(system_prompt, user_prompt, runtime_config)
    raise ValueError(
        "Unsupported LLM provider. Configure a provider-neutral HTTP endpoint using "
        "provider='http_json' or an alias such as 'enterprise_llm', 'local_llm', or 'azure_openai'."
    )


def call_http_json_llm(system_prompt: str, user_prompt: str, runtime_config: RuntimeConfig) -> str:
    llm = runtime_config.llm
    if not llm.endpoint_url:
        raise RuntimeError(
            "LLM endpoint is not configured. Set configs/llm_config.json endpoint_url "
            "or the LLM_ENDPOINT_URL environment variable."
        )

    headers = {"Content-Type": "application/json", **llm.headers}
    if llm.api_key and llm.auth_header:
        auth_value = f"{llm.auth_scheme} {llm.api_key}".strip() if llm.auth_scheme else llm.api_key
        headers.setdefault(llm.auth_header, auth_value)

    payload = _build_request_payload(system_prompt, user_prompt, runtime_config)
    response = requests.post(llm.endpoint_url, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    return _extract_response_text(response.json(), llm.response_format)


def _build_request_payload(system_prompt: str, user_prompt: str, runtime_config: RuntimeConfig) -> dict[str, Any]:
    llm = runtime_config.llm
    if llm.request_format in {"openai_chat", "azure_openai_chat"}:
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
        }
        if llm.model:
            payload["model"] = llm.model
        return payload
    if llm.request_format in {"prompt", "text"}:
        return {
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            **({"model": llm.model} if llm.model else {}),
        }
    raise ValueError(f"Unsupported LLM request_format: {llm.request_format}")


def _extract_response_text(payload: Any, response_format: str) -> str:
    if response_format in {"openai_chat", "azure_openai_chat"}:
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM response did not match OpenAI-compatible chat format") from exc
    if response_format in {"text", "raw_text"}:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for key in ("text", "content", "response", "output"):
                if key in payload:
                    return str(payload[key])
        raise ValueError("LLM response did not contain a text/content field")
    if response_format == "json":
        return json.dumps(payload)
    raise ValueError(f"Unsupported LLM response_format: {response_format}")


def parse_llm_json(raw: str) -> dict[str, list[str]]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*\n", "", cleaned).rstrip("`").strip()
    decoder = json.JSONDecoder()
    objects: list[dict[str, object]] = []
    position = 0
    while position < len(cleaned):
        object_start = cleaned.find("{", position)
        if object_start < 0:
            break
        try:
            parsed, object_end = decoder.raw_decode(cleaned, object_start)
        except json.JSONDecodeError:
            position = object_start + 1
            continue
        position = object_end
        if isinstance(parsed, dict):
            objects.append(parsed)

    if not objects:
        raise ValueError("LLM response did not contain a JSON object")
    if len(objects) > 1:
        log.warning("LLM response contained %s JSON objects; merging category values", len(objects))

    merged: dict[str, list[str]] = {}
    for parsed in objects:
        for key, value in parsed.items():
            values = value if isinstance(value, list) else [value]
            target = merged.setdefault(str(key), [])
            for item in values:
                if item is None:
                    continue
                text = str(item)
                if text not in target:
                    target.append(text)
    return merged
