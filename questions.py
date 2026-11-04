"""Native user questions rendered as text, with explicit reply tokens."""
import json
import re

from prompts import panel, prompt_text

QUESTION_METHOD = "item/tool/requestUserInput"
ASYNC_QUESTION_METHOD = "agentMessage/questions"
ASYNC_REPLY_OPEN = "<send_user_message_question_reply>"
ASYNC_REPLY_CLOSE = "</send_user_message_question_reply>"


def async_questions(item):
    """Return normalized App questions, or None for malformed/untrusted input."""
    item_id = item.get("id")
    raw = item.get("questions")
    if not isinstance(item_id, str) or not item_id or not isinstance(raw, list) or not raw:
        return None
    questions = []
    for index, question in enumerate(raw):
        if not isinstance(question, dict):
            return None
        title = question.get("title")
        options = question.get("options")
        if options is None:
            options = []
        if (not isinstance(title, str) or not title
                or not isinstance(options, list)
                or not all(isinstance(option, str) for option in options)):
            return None
        questions.append({
            "id": async_question_id(item_id, index),
            "question": title,
            "options": [{"label": option} for option in options],
        })
    return questions


def async_question_id(item_id, index):
    return json.dumps(["request_user_input_async", item_id, index],
                      ensure_ascii=False, separators=(",", ":"))


def async_question_reply(answers):
    body = json.dumps(answers, ensure_ascii=False, separators=(",", ":"))
    return f"{ASYNC_REPLY_OPEN}\n{body}\n{ASYNC_REPLY_CLOSE}"


def async_reply_question_ids(item):
    """Read question IDs from App steering text for stale-prompt invalidation."""
    if item.get("type") not in ("userMessage", "steeringUserMessage"):
        return set()
    content = item.get("content")
    if not isinstance(content, list):
        return set()
    pattern = re.compile(
        re.escape(ASYNC_REPLY_OPEN) + r"\s*(.*?)\s*" + re.escape(ASYNC_REPLY_CLOSE),
        re.S)
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        text = part.get("text")
        if not isinstance(text, str):
            continue
        for match in pattern.finditer(text):
            try:
                entries = json.loads(match.group(1))
            except (TypeError, ValueError):
                continue
            if not isinstance(entries, list):
                continue
            return {entry["questionItemId"] for entry in entries
                    if isinstance(entry, dict)
                    and isinstance(entry.get("questionItemId"), str)}
    return set()


def question_prompt(question, token, index, total, *, text=prompt_text):
    title = question["question"]
    if total > 1:
        title = f"{index + 1}/{total} {title}"
    options = question.get("options") or []
    rows = [f"{number}  {option['label']}" +
            (f"\n   {option['description']}" if option.get("description") else "")
            for number, option in enumerate(options, 1)]
    return (title + ("\n" + panel(*rows) if rows else "") +
            f"\n`/answer {token} {{{text('ANSWER_ARGUMENT')}}}`（也可用 `/a {token}`）")


def question_answer(question, text, *, render=prompt_text):
    options = question.get("options") or []
    if text.isascii() and text.isdecimal() and options:
        number = int(text)
        if not 1 <= number <= len(options):
            raise ValueError(render("ANSWER_OPTION_RANGE", {"count": len(options)}))
        return options[number - 1]["label"]
    return text
