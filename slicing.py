"""Deterministic answer slicing. CLI previews text without opening bridge state."""
import argparse
import json
from pathlib import Path
import re
import sys

from configuration import Slicing, TEXT_LIMIT_BYTES

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+")


def hard_chunks(text, limit=TEXT_LIMIT_BYTES):
    """Lossless byte cap; prefer a newline, never split a UTF-8 codepoint."""
    if limit < 4:
        raise ValueError("text byte budget must allow one Unicode codepoint")
    parts, start, size, boundary = [], 0, 0, None
    index = 0
    while index < len(text):
        width = len(text[index].encode("utf-8"))
        if size + width > limit:
            end = boundary if boundary is not None and boundary > start else index
            parts.append(text[start:end])
            start, index, size, boundary = end, end, 0, None
            continue
        size += width
        index += 1
        if text[index - 1] == "\n":
            boundary = index
    if start < len(text):
        parts.append(text[start:])
    return parts


def units(text):
    """Keep paragraphs, lists and tables whole; fenced blank lines are content."""
    blocks, lines, fence = [], [], None
    for line in text.splitlines(keepends=True):
        marker = FENCE.match(line)
        if fence:
            lines.append(line)
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = None
                blocks.append("".join(lines))
                lines = []
        elif marker:
            if lines:
                blocks.append("".join(lines))
            lines = [line]
            fence = marker.group(1)
        elif not line.strip():
            if lines:
                blocks.append("".join(lines) + line)
                lines = []
            elif blocks:
                blocks[-1] += line
            else:
                lines.append(line)
        elif HEADING.match(line):
            if lines:
                blocks.append("".join(lines))
            lines = [line]
        else:
            lines.append(line)
    if lines:
        blocks.append("".join(lines))
    # A heading-only unit belongs with the following content.
    joined, pending = [], ""
    for block in blocks:
        nonempty = [line for line in block.splitlines() if line.strip()]
        if nonempty and all(HEADING.match(line) for line in nonempty):
            pending += block
        else:
            joined.append(pending + block)
            pending = ""
    if pending:
        if joined:
            joined[-1] += pending
        else:
            joined.append(pending)
    return joined


def bounded_unit(text):
    if len(text.encode()) <= TEXT_LIMIT_BYTES:
        return [text]
    lines = text.splitlines(keepends=True)
    opening = FENCE.match(lines[0]) if lines else None
    # Only reconstruct a complete, conventional fenced block. Other structures
    # fall back to lossless line/codepoint splitting, never guessed rewriting.
    last = len(lines) - 1
    while last > 0 and not lines[last].strip():
        last -= 1
    if opening and last > 0:
        fence = opening.group(1)
        if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", lines[last]):
            prefix = lines[0]
            suffix = "\n" + fence
            tail = "".join(lines[last + 1:])
            budget = TEXT_LIMIT_BYTES - len((prefix + suffix).encode())
            if budget >= 4:
                body = "".join(lines[1:last])
                chunks = [prefix + part + suffix for part in hard_chunks(body, budget)]
                if chunks:
                    if len((chunks[-1] + tail).encode()) <= TEXT_LIMIT_BYTES:
                        chunks[-1] += tail
                    else:
                        chunks.extend(hard_chunks(tail))
                    return chunks
    return hard_chunks(text)


def split_text(text, settings=None, *, semantic=False):
    settings = settings or Slicing()
    if not text:
        return []
    if not semantic or settings.mode == "length":
        return hard_chunks(text)
    result, current = [], ""
    for block in units(text):
        for piece in bounded_unit(block):
            combined = current + piece
            over_bytes = len(combined.encode()) > TEXT_LIMIT_BYTES
            over_target = len(combined) > settings.target_chars * 1.25
            if current and (over_bytes or (over_target and len(current) >= settings.target_chars / 2)):
                result.append(current)
                current = piece
            else:
                current = combined
    if current:
        # Avoid a tiny last bubble if the previous one can safely absorb it.
        if (result and len(current) < settings.target_chars / 4
                and len(result[-1] + current) <= settings.target_chars * 1.5
                and len((result[-1] + current).encode()) <= TEXT_LIMIT_BYTES):
            result[-1] += current
        else:
            result.append(current)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, help="UTF-8 text; default stdin")
    parser.add_argument("--smart", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--target-chars", type=int, default=Slicing.target_chars)
    parser.add_argument("--json", action="store_true", help="emit an array of exact message bodies")
    args = parser.parse_args()
    settings = Slicing.from_config({"smart": args.smart, "targetChars": args.target_chars})
    text = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
    parts = split_text(text, settings, semantic=True)
    if args.json:
        print(json.dumps(parts, ensure_ascii=False, indent=2))
    else:
        for index, part in enumerate(parts, 1):
            print(f"--- {index}/{len(parts)} · {len(part.encode())} bytes ---\n{part}")


if __name__ == "__main__":
    main()
