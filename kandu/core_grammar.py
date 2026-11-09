"""将语法成分锚定到原句；旧图解只采用说明中的明确引用，不猜空格列。"""
import re


def _canonical(text):
    chars, positions = [], []
    for index, char in enumerate(text):
        char = {"’": "'", "‘": "'", "“": '"', "”": '"'}.get(char, char)
        if char.isspace():
            char = " "
            if chars and chars[-1] == " ":
                continue
        chars.append(char)
        positions.append(index)
    return "".join(chars), positions


def _locate(source, excerpt, cursor=0):
    clean, positions = _canonical(source)
    needle, _ = _canonical(excerpt.strip())
    if not needle:
        return None
    offset = clean.find(needle, next((i for i, p in enumerate(positions) if p >= cursor), len(clean)))
    while offset >= 0:
        end = offset + len(needle)
        if not ((needle[0].isalnum() and offset and clean[offset - 1].isalnum()) or
                (needle[-1].isalnum() and end < len(clean) and clean[end].isalnum())):
            return positions[offset], positions[end - 1] + 1
        offset = clean.find(needle, offset + 1)
    return None


def normalize_components(raw, sentence):
    """仅接收原句中实际存在、同级顺序正确的片段；子成分限于父片段内。"""
    remaining = [100]

    def visit(nodes, source, base, depth):
        if not isinstance(nodes, list) or depth > 5:
            return []
        result, cursor = [], 0
        for node in nodes[:100]:
            if remaining[0] <= 0:
                break
            remaining[0] -= 1
            if not isinstance(node, dict):
                continue
            text, label = node.get("text"), node.get("label")
            if not isinstance(text, str) or not isinstance(label, str) or not label.strip():
                continue
            anchor = _locate(source, text, cursor)
            if anchor is None:
                continue
            start, end = anchor
            exact = source[start:end]
            component = {"text": exact, "label": re.sub(r"\s+", " ", label).strip()[:80],
                         "start": base + start, "end": base + end}
            children = visit(node.get("children"), exact, base + start, depth + 1)
            if children:
                component["children"] = children
            result.append(component)
            cursor = end
        return result

    return visit(raw, sentence, 0, 0) if isinstance(sentence, str) and sentence else []


def legacy_components(analysis, sentence):
    """从已有语法说明的引文建立对应；不从错位的中文标注推断语法。"""
    if not sentence:
        return []
    diagram = analysis.get("grammarDiagram") or ""
    first_line = diagram.replace("\\n", "\n").splitlines()[:1]
    brackets, stack = [], []
    for index, char in enumerate(first_line[0] if first_line else ""):
        if char == "[":
            stack.append(index)
        elif char == "]" and stack:
            start = stack.pop()
            brackets.append(first_line[0][start + 1:index].replace("[", "").replace("]", ""))
    result, seen = [], set()
    for point in analysis.get("grammarPoints") or []:
        if not isinstance(point, dict) or not isinstance(point.get("title"), str):
            continue
        quoted = re.findall(r'“([^”]+)”|「([^」]+)」|"([^"\n]+)"', str(point.get("text") or ""))
        candidates = []
        for quote in quoted:
            excerpt = next((part for part in quote if part), "").strip()
            if not re.search(r"[A-Za-z]", excerpt) or re.search(r"[\u3400-\u9fff]", excerpt):
                continue
            prefix = re.split(r"\.{3}|…", excerpt, maxsplit=1)[0].strip()
            # 省略号引文可使用原图中明确框出的完整同一片段。
            if prefix != excerpt and len(prefix) >= 12:
                for group in brackets:
                    if _canonical(group)[0].startswith(_canonical(prefix)[0]) and _locate(sentence, group):
                        candidates.append(group)
            candidates.append(prefix)
        for excerpt in sorted(candidates, key=len, reverse=True):
            anchor = _locate(sentence, excerpt)
            if anchor is None or len(excerpt) < 3:
                continue
            start, end = anchor
            if _locate(sentence, excerpt, end) is not None:
                continue  # 多处相同引文缺少位置证据，不替旧模型猜测。
            label = point["title"].strip()
            if label and (start, end, label) not in seen:
                result.append({"text": sentence[start:end], "label": label, "start": start, "end": end})
                seen.add((start, end, label))
            break
    roots, parents = [], []
    for component in sorted(result, key=lambda c: (c["start"], -c["end"])):
        while parents and not (parents[-1]["start"] <= component["start"] and
                               component["end"] <= parents[-1]["end"] and
                               (parents[-1]["start"], parents[-1]["end"]) !=
                               (component["start"], component["end"])):
            parents.pop()
        (parents[-1].setdefault("children", []) if parents else roots).append(component)
        parents.append(component)
    return roots
