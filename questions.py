"""Native user questions rendered as text, with explicit reply tokens."""
from prompts import panel

QUESTION_METHOD = "item/tool/requestUserInput"


def question_prompt(question, token, index, total):
    title = question["question"]
    if total > 1:
        title = f"{index + 1}/{total} {title}"
    options = question.get("options") or []
    rows = [f"{number}  {option['label']}" +
            (f"\n   {option['description']}" if option.get("description") else "")
            for number, option in enumerate(options, 1)]
    return (title + ("\n" + panel(*rows) if rows else "") +
            f"\n`/answer {token} {{选项或文字}}`")


def question_answer(question, text):
    options = question.get("options") or []
    if text.isascii() and text.isdecimal() and options:
        number = int(text)
        if not 1 <= number <= len(options):
            raise ValueError(f"选项编号应为 1–{len(options)}；也可直接填写文字。")
        return options[number - 1]["label"]
    return text
