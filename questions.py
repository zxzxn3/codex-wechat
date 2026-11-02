"""Native user questions rendered as text, with explicit reply tokens."""
from prompts import panel, prompt_text

QUESTION_METHOD = "item/tool/requestUserInput"


def question_prompt(question, token, index, total, *, text=prompt_text):
    title = question["question"]
    if total > 1:
        title = f"{index + 1}/{total} {title}"
    options = question.get("options") or []
    rows = [f"{number}  {option['label']}" +
            (f"\n   {option['description']}" if option.get("description") else "")
            for number, option in enumerate(options, 1)]
    return (title + ("\n" + panel(*rows) if rows else "") +
            f"\n`/answer {token} {{{text('ANSWER_ARGUMENT')}}}`")


def question_answer(question, text, *, render=prompt_text):
    options = question.get("options") or []
    if text.isascii() and text.isdecimal() and options:
        number = int(text)
        if not 1 <= number <= len(options):
            raise ValueError(render("ANSWER_OPTION_RANGE", {"数量": len(options)}))
        return options[number - 1]["label"]
    return text
