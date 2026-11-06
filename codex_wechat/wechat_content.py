"""Pure helpers for prompt content and quoted WeChat messages."""
import hashlib
import hmac

QUOTE_CACHE_LIMIT = 200
QUOTE_TEXT_LIMIT = 20_000


def message_key(message):
    """Return the most stable protocol identifier for dedup and quote lookup."""
    values = [message.get("message_id")]
    values.extend(
        item.get("msg_id")
        for item in (message.get("item_list") or [])
        if isinstance(item, dict)
    )
    values.append(message.get("client_id"))
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def item_text(item):
    if not isinstance(item, dict):
        return ""
    if item.get("type") == 1:
        value = item.get("text_item", {}).get("text")
    elif item.get("type") == 3:
        value = item.get("voice_item", {}).get("text")
    else:
        return ""
    return value.strip() if isinstance(value, str) else ""


def message_text(message):
    parts = (item_text(item) for item in message.get("item_list", []))
    return "\n".join(part for part in parts if part).strip()


def item_summary(item):
    text = item_text(item)
    if text:
        return text
    if not isinstance(item, dict):
        return ""
    kind = item.get("type")
    if kind == 2:
        return "[图片]"
    if kind == 3:
        return "[语音]"
    if kind == 4:
        name = item.get("file_item", {}).get("file_name")
        return f"[文件: {name}]" if isinstance(name, str) and name.strip() else "[文件]"
    if kind == 5:
        return "[视频]"
    return ""


def message_summary(message):
    parts = (item_summary(item) for item in message.get("item_list", []))
    return "\n".join(part for part in parts if part).strip()


def _nth_index(text, value, occurrence, start=0):
    if not value or type(occurrence) is not int or occurrence < 0:
        return -1
    position = start
    for current in range(occurrence + 1):
        position = text.find(value, position)
        if position < 0:
            return -1
        if current < occurrence:
            position += len(value)
    return position


def _partial_candidate(text, partial, mode):
    start = _nth_index(text, partial.get("start"), partial.get("startindex"))
    if start < 0:
        return None
    origin = start + len(partial["start"]) if mode == "relative" else 0
    end = _nth_index(text, partial.get("end"), partial.get("endindex"), origin)
    if end < start:
        return None
    return text[start:end + len(partial["end"])]


def partial_quote(text, partial):
    """Resolve both end-index interpretations and verify the optional MD5."""
    if not isinstance(text, str) or not isinstance(partial, dict):
        return None
    start, end = partial.get("start"), partial.get("end")
    if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
        return None
    candidates = []
    for mode in ("global", "relative"):
        candidate = _partial_candidate(text, partial, mode)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    digest = partial.get("quotemd5")
    if isinstance(digest, str) and len(digest) == 32:
        digest = digest.lower()
        for candidate in candidates:
            actual = hashlib.md5(candidate.encode()).hexdigest()
            if hmac.compare_digest(actual, digest):
                return candidate
        return None
    return candidates[0] if candidates else None


def quote_context(message, cache):
    """Return the referenced message ID and best available display text."""
    for item in message.get("item_list", []):
        reference = item.get("ref_msg") if isinstance(item, dict) else None
        if not isinstance(reference, dict):
            continue
        embedded = reference.get("message_item")
        embedded_id = embedded.get("msg_id") if isinstance(embedded, dict) else None
        reference_id = reference.get("svr_id") or embedded_id
        if reference_id is not None:
            reference_id = str(reference_id)
        remembered = cache.get(reference_id) if isinstance(cache, dict) and reference_id else None
        body = partial_quote(remembered, reference.get("partial_text"))
        if not body:
            body = remembered if isinstance(remembered, str) else ""
        if not body:
            inline = []
            title = reference.get("title")
            if isinstance(title, str) and title.strip():
                inline.append(title.strip())
            if isinstance(embedded, dict):
                summary = item_summary(embedded)
                if summary and summary not in inline:
                    inline.append(summary)
            body = " | ".join(inline)
        if not body and reference_id:
            body = "[引用消息内容未缓存]"
        if body:
            return {"id": reference_id, "body": body}
    return None


def quoted_prompt(text, quote):
    if not quote or not quote.get("body"):
        return text
    block = f"[引用消息]\n{quote['body']}\n[/引用消息]"
    return f"{block}\n\n{text}" if text else block


def remember_quote(state, message_id, body):
    if message_id is None or not isinstance(body, str) or not body.strip():
        return False
    cache = state.get("quoteCache")
    if not isinstance(cache, dict):
        cache = {}
        state["quoteCache"] = cache
    key = str(message_id)
    cache.pop(key, None)
    cache[key] = body.strip()[:QUOTE_TEXT_LIMIT]
    while len(cache) > QUOTE_CACHE_LIMIT:
        cache.pop(next(iter(cache)))
    return True


def entry_summary(entry):
    text = entry.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    if "item" in entry:
        return item_summary(entry["item"])
    asset = entry.get("asset")
    if isinstance(asset, dict):
        return item_summary({"type": 1 if asset.get("kind") == "image" else
                                     5 if asset.get("kind") == "video" else 4,
                             "file_item": {"file_name": asset.get("name")}})
    return ""
