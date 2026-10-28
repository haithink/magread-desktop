"""LLM 客户端：OpenAI 兼容 / Anthropic 原生，SSE 流式优先，
推理模型额度自适应重试，cc-switch JSON 粘贴识别（移植 Android 版 LlmClient）。"""
import json
import re

import requests


class LlmError(Exception):
    pass


TIMEOUT = (20, 300)


def endpoint(cfg: dict) -> str:
    base = (cfg.get("baseUrl") or "").strip().rstrip("/")
    if cfg.get("provider") == "anthropic":
        if base.endswith("/v1/messages"):
            return base
        if re.search(r"/v\d+$", base):
            return base + "/messages"
        return base + "/v1/messages"
    if base.endswith("/chat/completions"):
        return base
    if re.search(r"/v\d+$", base):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def is_complete(cfg: dict) -> bool:
    return all((cfg.get(k) or "").strip() for k in ("baseUrl", "apiKey", "model"))


def validate(cfg: dict) -> str | None:
    if not (cfg.get("baseUrl") or "").strip():
        return "URL 不能为空"
    if not str(cfg["baseUrl"]).strip().startswith(("http://", "https://")):
        return "URL 需以 http:// 或 https:// 开头"
    if not (cfg.get("apiKey") or "").strip():
        return "API Key 不能为空"
    if not (cfg.get("model") or "").strip():
        return "模型名不能为空"
    return None


def _body(cfg, system, user, max_tokens, temperature, stream):
    b = {
        "model": cfg["model"].strip(),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if stream:
        b["stream"] = True
    if cfg.get("provider") == "anthropic":
        if system:
            b["system"] = system
        b["messages"] = [{"role": "user", "content": user}]
    else:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user})
        b["messages"] = msgs
    return b


def _headers(cfg):
    if cfg.get("provider") == "anthropic":
        return {"x-api-key": cfg["apiKey"].strip(), "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {cfg['apiKey'].strip()}"}


def _first_line(s: str) -> str:
    return (s or "").splitlines()[0][:300] if s else ""


def _str(v):
    if isinstance(v, str):
        return v
    return None


class _StreamState:
    __slots__ = ("parts", "saw_reasoning", "length_cut")

    def __init__(self):
        self.parts = []
        self.saw_reasoning = False
        self.length_cut = False

    @property
    def text(self):
        return "".join(self.parts)


def _stream_once(cfg, system, user, max_tokens, temperature, state):
    payload = _body(cfg, system, user, max_tokens, temperature, stream=True)
    with requests.post(
        endpoint(cfg),
        headers={**_headers(cfg), "Accept": "text/event-stream"},
        json=payload,
        stream=True,
        timeout=TIMEOUT,
    ) as resp:
        if resp.status_code != 200:
            raise LlmError(f"HTTP {resp.status_code}（{endpoint(cfg)}）：{_first_line(resp.text)}")
        for raw in resp.iter_lines(chunk_size=1):
            if isinstance(raw, bytes):
                # 网关可能不标 charset，requests 默认按 latin-1 解码会产出 mojibake
                line = raw.decode("utf-8", errors="replace")
            else:
                line = raw
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if cfg.get("provider") == "anthropic":
                t = obj.get("type")
                if t == "content_block_delta":
                    txt = (obj.get("delta") or {}).get("text")
                    if txt:
                        state.parts.append(txt)
                elif t == "message_delta":
                    if (obj.get("delta") or {}).get("stop_reason") == "max_tokens":
                        state.length_cut = True
                elif t == "error":
                    raise LlmError(f"接口报错：{json.dumps(obj.get('error'), ensure_ascii=False)[:200]}")
            else:
                choices = obj.get("choices") or []
                if choices:
                    ch = choices[0]
                    delta = ch.get("delta") or {}
                    c = delta.get("content")
                    if c:
                        state.parts.append(c)
                    if "reasoning_content" in delta:
                        state.saw_reasoning = True
                    if ch.get("finish_reason") == "length":
                        state.length_cut = True


def _parse_content(cfg, body: str):
    try:
        obj = json.loads(body)
    except Exception:
        return body[:2000] or None
    if not isinstance(obj, dict):
        return body[:2000] or None
    if cfg.get("provider") == "anthropic":
        content = obj.get("content")
        if isinstance(content, list):
            txt = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            ).strip()
            if txt:
                return txt
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        ch = choices[0]
        msg = ch.get("message") or {}
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            return c
        t = ch.get("text")
        if isinstance(t, str) and t.strip():
            return t
    err = obj.get("error")
    if isinstance(err, dict) and err.get("message"):
        raise LlmError(f"接口报错：{str(err['message'])[:300]}")
    return None


_THINK = re.compile(r"<think>[\s\S]*?</think>")


def _strip_think(text: str) -> str:
    return _THINK.sub("", text).strip()


def _looks_truncated(body: str) -> bool:
    return (
        "reasoning_content" in body
        or '"finish_reason":"length"' in body
        or '"finish_reason": "length"' in body
    )


def chat(cfg, system, user, max_tokens=4000, temperature=0.3, on_delta=None) -> str:
    """发一次对话。流式优先；推理模型烧额度/被 length 截断时加倍额度重试一次。"""
    if not is_complete(cfg):
        raise LlmError("大模型配置不完整，请先在设置中填写 URL / API Key / 模型名")

    budget = max_tokens
    for attempt in range(2):
        state = _StreamState()
        stream_err = None
        try:
            _stream_once(cfg, system, user, budget, temperature, state)
        except Exception as e:  # 流式不被支持/网络错误 → 退回非流式
            stream_err = e

        content = state.text.strip()
        if not content:
            # 流式失败、或网关无视 stream:true 直接回普通 JSON（200 但无 data: 行）→ 退回非流式
            try:
                resp = requests.post(
                    endpoint(cfg),
                    headers=_headers(cfg),
                    json=_body(cfg, system, user, budget, temperature, stream=False),
                    timeout=TIMEOUT,
                )
            except Exception as e:
                raise LlmError(f"网络请求失败：{e}")
            if resp.status_code != 200:
                raise LlmError(f"HTTP {resp.status_code}（{endpoint(cfg)}）：{_first_line(resp.text)}")
            resp.encoding = "utf-8"
            content = (_parse_content(cfg, resp.text) or "").strip()
            if _looks_truncated(resp.text):
                state.length_cut = True

        if on_delta:
            on_delta(len(state.text))

        if content and not state.length_cut:
            return _strip_think(content)

        burned = state.saw_reasoning or state.length_cut
        if attempt == 0 and burned:
            budget = min(budget * 2, 32768)
            continue
        if content:
            # 重试后仍截断：返回截断文本，上层 JSON 修补兜底
            return _strip_think(content)
        raise LlmError(
            "模型把输出额度耗在思考（reasoning）上且重试后仍无正文。建议换非推理模型（如 deepseek-chat）"
            if burned
            else f"响应里没有内容：{stream_err}"
        )
    raise LlmError("响应里没有内容")


def test(cfg) -> dict:
    import time

    start = time.time()
    try:
        reply = chat(
            cfg,
            "你是连通性测试助手。",
            "请只回复两个字母：ok",
            max_tokens=2000,
            temperature=0.0,
        )
        return {"ok": True, "latencyMs": int((time.time() - start) * 1000), "reply": reply[:80]}
    except Exception as e:
        return {"ok": False, "latencyMs": int((time.time() - start) * 1000), "error": str(e)}


def list_models(cfg):
    """拉取该 Key 可用的模型列表（OpenAI 与 Anthropic 的 /v1/models 同构）。"""
    if not is_complete(cfg):
        raise LlmError("配置不完整")
    base = (cfg.get("baseUrl") or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        url = base[: -len("/chat/completions")] + "/models"
    elif base.endswith("/messages"):
        url = base[: -len("/messages")] + "/models"
    elif re.search(r"/v\d+$", base):
        url = base + "/models"
    else:
        url = base + "/v1/models"
    try:
        r = requests.get(url, headers=_headers(cfg), timeout=(20, 60))
    except Exception as e:
        raise LlmError(f"网络请求失败：{e}")
    if r.status_code != 200:
        raise LlmError(f"HTTP {r.status_code}（{url}）：{_first_line(r.text)}")
    try:
        data = r.json()
    except Exception:
        raise LlmError(f"响应不是 JSON：{_first_line(r.text)}")
    ids = []
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = data.get("models") if isinstance(data, dict) else None
    if isinstance(items, list):
        for m in items:
            if isinstance(m, dict) and m.get("id"):
                ids.append(str(m["id"]))
            elif isinstance(m, str):
                ids.append(m)
    if not ids:
        raise LlmError(f"网关没有返回模型列表：{_first_line(r.text)}")
    return sorted(set(ids))


# ---------------- cc-switch / 通用 JSON 配置提取 ----------------

_URL_KEY = re.compile(r"(?i)base_?url|endpoint|^url$")
_MODEL_KEY = re.compile(r"(?i)model")
_KEY_KEY = re.compile(r"(?i)api_?key|auth|token|^key$")


def _key_priority(k: str):
    kl = k.lower()
    if re.search(r"(?i)anthropic_base_url|openai_base_url", kl):
        return ("url", 100)
    if re.fullmatch(r"(?i)base_?url|api_?base", kl):
        return ("url", 80)
    if re.search(r"(?i)base_?url", kl):
        return ("url", 60)
    if re.fullmatch(r"(?i)url", kl) or "endpoint" in kl:
        return ("url", 40)
    if re.search(r"(?i)anthropic_auth_token|openai_api_key", kl):
        return ("key", 100)
    if re.fullmatch(r"(?i)api_?key|auth_?token|token", kl):
        return ("key", 80)
    if re.search(r"(?i)api_?key|auth|token", kl):
        return ("key", 50)
    if re.fullmatch(r"(?i)key", kl):
        return ("key", 30)
    if re.search(r"(?i)^anthropic_model$|^openai_model$|^model_name$", kl):
        return ("model", 100)
    if re.fullmatch(r"(?i)model", kl):
        return ("model", 80)
    if re.search(r"(?i)model", kl):
        return ("model", 40)
    return None


def parse_pasted_json(text: str):
    """从粘贴的 JSON（cc-switch 配置/单条 provider/普通结构）启发式提取配置。
    provider 由键名决定：出现 ANTHROPIC_* 键即判定为 Anthropic 原生
    （URL/模型名内容不含 anthropic/claude 字样的中转站也会被正确识别）。"""
    try:
        root = json.loads(text.strip())
    except Exception:
        return None
    urls, keys, models = {}, {}, {}
    saw_anthropic_key = False
    saw_openai_key = False

    def walk(el):
        nonlocal saw_anthropic_key, saw_openai_key
        if isinstance(el, dict):
            for k, v in el.items():
                p = _key_priority(k)
                kl = k.lower()
                if "anthropic" in kl:
                    saw_anthropic_key = True
                if "openai" in kl:
                    saw_openai_key = True
                if p is not None and isinstance(v, str) and v.strip():
                    kind, pri = p
                    table = {"url": urls, "key": keys, "model": models}[kind]
                    cur = table.get(v.strip())
                    if cur is None or pri > cur[0]:
                        table[v.strip()] = (pri, k)
                else:
                    walk(v)
        elif isinstance(el, list):
            for v in el:
                walk(v)

    walk(root)
    if not urls:
        return None
    url, (upri, ukey) = max(urls.items(), key=lambda kv: kv[1])
    key = max(keys.items(), key=lambda kv: kv[1])[0] if keys else ""
    model = max(models.items(), key=lambda kv: kv[1])[0] if models else ""

    if saw_anthropic_key and "anthropic" in ukey.lower():
        provider = "anthropic"
    elif saw_openai_key and "openai" in ukey.lower():
        provider = "openai"
    elif saw_anthropic_key:
        provider = "anthropic"
    elif re.search(r"(?i)anthropic", url) or re.search(r"(?i)claude|anthropic", model):
        provider = "anthropic"
    else:
        provider = "openai"
    note = ""
    if not key:
        note += "未在 JSON 中找到 API Key，请手动补填。"
    if not model:
        note += " 未找到模型名，请手动补填。"
    if key and model:
        note = "已自动识别配置（%s 接口）。" % ("Anthropic 原生" if provider == "anthropic" else "OpenAI 兼容")
    return {"provider": provider, "baseUrl": url, "apiKey": key, "model": model}, note
