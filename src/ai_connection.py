"""Small real requests to validate unsaved provider settings without storing credentials."""

import time

import requests

from .ai_provider import normalize_api_url, resolve_provider_key
from .processors.deepseek_summarizer import DeepSeekSummarizer


TEST_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAOUlEQVR4nO3RQQ0AMAzDwGxYC3awBsH95OcDkEruybw03ep6PLDgD5CJkImQiZCJkImQiZCJkIlCPsNhAWy8VtLWAAAAAElFTkSuQmCC"


def test_provider_connection(api_url: str, model: str, api_key: str = "", *,
                             vision_enabled: bool = False, vision_model: str = "") -> dict:
    api_url = normalize_api_url(api_url)
    key = resolve_provider_key(api_url, api_key)
    model, vision_model = model.strip(), vision_model.strip() or model.strip()
    if not model or any(ord(char) < 32 for char in model + vision_model):
        raise ValueError("请填写有效的模型名称")
    # Use the same transport as briefing generation, with a bounded single attempt.
    client = DeepSeekSummarizer()
    client.api_url = api_url
    results = []
    for mode, selected_model in [("text", model)] + ([("vision", vision_model)] if vision_enabled else []):
        content = "这是 AI 连接测试，请只回复连接成功。"
        if mode == "vision":
            content = [{"type": "text", "text": "这是图片输入测试，请只回复图片已收到。"},
                       {"type": "image_url", "image_url": {"url": TEST_IMAGE}}]
        started = time.perf_counter()
        try:
            response = client._call_api([{"role": "user", "content": content}], selected_model,
                                        api_key=key, timeout=(8, 40), attempts=1, max_tokens=512)
        except RuntimeError as exc:
            label = "图片输入测试" if mode == "vision" else "文字生成测试"
            raise RuntimeError(f"{label}失败：{exc}") from exc
        choices = response.get("choices")
        try:
            output = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("AI 接口没有返回 Chat Completions 生成结果，请检查接口 URL") from exc
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("AI 接口已响应，但未生成文字，请更换模型后重试")
        results.append({"mode": mode, "model": selected_model, "elapsed_ms": round((time.perf_counter() - started) * 1000)})
    return {"success": True, "message": "连接成功，文字模型" + ("和图片输入均" if vision_enabled else "已") + "通过实际生成测试。请保存设置后使用。", "checks": results}


def list_provider_models(api_url: str, api_key: str = "") -> dict:
    api_url = normalize_api_url(api_url)
    key = resolve_provider_key(api_url, api_key)
    try:
        response = requests.get(api_url.removesuffix("/chat/completions") + "/models",
                                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                                timeout=(8, 25), allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise RuntimeError("模型列表接口返回重定向，请检查 URL")
        if not response.ok:
            raise RuntimeError(f"读取模型失败 HTTP {response.status_code}，请检查 URL、密钥和模型访问权限")
        data = response.json()
        rows = data.get("data", []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            rows = []
        models = sorted({row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
                         and 0 < len(row["id"]) <= 200 and not any(ord(c) < 32 for c in row["id"])})[:200]
        if not models:
            raise RuntimeError("接口未返回可用模型；可按服务商说明手动填写模型 ID，再测试连接")
        return {"models": models, "message": f"已读取 {len(models)} 个模型，请选择文字模型后测试连接。"}
    except requests.RequestException as exc:
        raise RuntimeError("读取模型连接失败，请检查网络和接口 URL") from exc
    except ValueError as exc:
        raise RuntimeError("模型列表接口未返回有效 JSON，请按服务商说明手动填写模型 ID") from exc
