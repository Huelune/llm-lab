"""C 소스를 가볍게 훑는 도구: 주석 지우기, 최상위 블록, 정의 찾기, 요청에 넣을 정의 고르기.

컴파일러가 아니라 중괄호 세기와 정규식으로 찾는다. 틀려도 참고용 글이 조금 비는 것뿐이다.
"""

from __future__ import annotations


def split_lines(text: str) -> list[str]:
    """\\n으로만 나눈다 (splitlines는 C 소스의 \\f 같은 문자에서도 나눠 줄 번호가 틀어진다)."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def blank_comments(text: str) -> str:
    """주석·문자열·문자 상수를 공백으로 바꾼 글. 줄 수와 각 줄 길이가 그대로라 줄 번호가 맞는다."""
    out: list[str] = []
    state = "code"
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            out.append("\n")
            if state == "line_comment":
                state = "code"
            i += 1
        elif state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                out.append("  ")
                i += 2
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                out.append("  ")
                i += 2
            elif ch == '"':
                state = "string"
                out.append(" ")
                i += 1
            elif ch == "'":
                state = "char"
                out.append(" ")
                i += 1
            else:
                out.append(ch)
                i += 1
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
            else:
                out.append(" ")
                i += 1
        elif state == "line_comment":
            out.append(" ")
            i += 1
        else:  # string / char
            if ch == "\\":
                # 이스케이프 다음 글자는 건너뛴다. 줄 이음(\ 뒤 줄바꿈)은 줄 수를 지킨다
                out.append(" ")
                if nxt == "\n":
                    out.append("\n")
                elif nxt:
                    out.append(" ")
                i += 2
            else:
                if (ch == '"' and state == "string") or (ch == "'" and state == "char"):
                    state = "code"
                out.append(" ")
                i += 1
    return "".join(out)


def top_level_blocks(text: str) -> list[tuple[int, int]]:
    """최상위 { … } 블록의 (시작 줄, 끝 줄) 목록. 주석·문자열·문자 상수 안의 중괄호는 무시한다."""
    blocks: list[tuple[int, int]] = []
    depth = 0
    line = 1
    start = 0
    for ch in blank_comments(text):
        if ch == "\n":
            line += 1
        elif ch == "{":
            if depth == 0:
                start = line
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                blocks.append((start, line))
    return blocks
