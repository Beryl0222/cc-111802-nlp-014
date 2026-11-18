import re
import unicodedata
from dataclasses import dataclass


TOKEN_RE = re.compile(r"[㐀-鿿]|[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+(?:\.\d+)?|[^\w\s]", re.UNICODE)

NEGATORS = "不没无别未非"
# 这些词含否定字，但本身不构成否定，避免把“非常喜欢”“特别喜欢”判反。
NEGATION_EXCEPTIONS = ("非常", "特别", "分别", "区别", "告别", "个别", "别人", "别的", "非但")
QUOTE_MARKS = set("“”‘’「」『』\"'《》")
AMBIGUITY_CONTEXT_MARKERS = (
    "玩笑", "玩梗", "调侃", "梗", "反讽", "反话", "阴阳",
    "术语", "引用", "歌词", "课文", "例句",
)
TERM_WINDOW_MARKERS = ("术语", "引用", "例句", "歌词")
SARCASM_MARKERS = ("呵呵", "真棒", "太棒了", "好极了", "真行", "佩服", "厉害了", "谢谢老师")


@dataclass(frozen=True)
class Cue:
    category: str
    phrase: str
    weight: int
    kind: str = "risk"


CUES = (
    Cue("受挫", "受挫", 3),
    Cue("受挫", "挫败", 3),
    Cue("受挫", "崩溃", 3),
    Cue("受挫", "撑不住", 3),
    Cue("受挫", "学不会", 2),
    Cue("受挫", "听不懂", 2),
    Cue("受挫", "搞不懂", 2),
    Cue("受挫", "太难", 2),
    Cue("受挫", "难受", 2),
    Cue("受挫", "难", 1),
    Cue("受挫", "烦", 1),
    Cue("受挫", "烦死", 2),
    Cue("受挫", "想哭", 3),
    Cue("受挫", "绝望", 3),
    Cue("受挫", "焦虑", 2),
    Cue("受挫", "压力大", 2),
    Cue("受挫", "痛苦", 2),
    Cue("求助", "求助", 3),
    Cue("求助", "帮我", 2),
    Cue("求助", "谁来教", 2),
    Cue("求助", "不会", 2),
    Cue("求助", "不会做", 2),
    Cue("求助", "怎么办", 2),
    Cue("求助", "救救", 3),
    Cue("求助", "需要帮助", 3),
    Cue("求助", "请教", 1),
    Cue("回避", "放弃", 3),
    Cue("回避", "不想学", 3),
    Cue("回避", "摆烂", 2),
    Cue("回避", "算了", 1),
    Cue("回避", "无所谓", 1),
    Cue("回避", "随便吧", 1),
    Cue("回避", "逃避", 2),
    Cue("积极", "喜欢", 1, "positive"),
    Cue("积极", "有趣", 1, "positive"),
    Cue("积极", "开心", 1, "positive"),
    Cue("积极", "懂了", 1, "positive"),
    Cue("积极", "明白", 1, "positive"),
    Cue("积极", "轻松", 1, "positive"),
    Cue("积极", "期待", 1, "positive"),
    Cue("积极", "进步", 1, "positive"),
    Cue("积极", "好玩", 1, "positive"),
)


def normalize_text(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def tokenize(text):
    return TOKEN_RE.findall(normalize_text(text))


def _is_quoted(text, start, end):
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return before in QUOTE_MARKS or after in QUOTE_MARKS


def _is_negated(text, start, phrase=""):
    if phrase and phrase[0] in NEGATORS:
        # “不会”“不想学”等词本身已含否定，不能再被当作被否定的正向线索。
        return False
    clause_start = max(text.rfind(mark, 0, start) for mark in "，。！？；,.!?;\n") + 1
    prefix = text[max(clause_start, start - 3):start]
    for exception in NEGATION_EXCEPTIONS:
        prefix = prefix.replace(exception, "")
    count = sum(prefix.count(negator) for negator in NEGATORS)
    return count % 2 == 1


def _near_marker(text, start, end, markers, window=6):
    nearby = text[max(0, start - window):min(len(text), end + window)]
    return next((marker for marker in markers if marker in nearby), None)


def _cue_occurrences(text, context):
    found = []
    for cue in CUES:
        for match in re.finditer(re.escape(cue.phrase), text):
            start, end = match.span()
            context_marker = next((marker for marker in AMBIGUITY_CONTEXT_MARKERS if marker in context), None)
            term_marker = _near_marker(text, start, end, TERM_WINDOW_MARKERS)
            found.append({
                "category": cue.category,
                "phrase": cue.phrase,
                "kind": cue.kind,
                "weight": cue.weight,
                "start": start,
                "end": end,
                "negated": _is_negated(text, start, cue.phrase),
                "quoted": _is_quoted(text, start, end),
                "context_marker": context_marker,
                "term_marker": term_marker,
            })

    # 长词优先，避免“太难”同时命中“难”而重复计数。
    found.sort(key=lambda item: (item["start"], -(item["end"] - item["start"])))
    selected = []
    occupied = set()
    for occurrence in found:
        span = set(range(occurrence["start"], occurrence["end"]))
        if span & occupied:
            continue
        occupied |= span
        selected.append(occurrence)
    selected.sort(key=lambda item: item["start"])
    return selected


def annotate(record):
    text = normalize_text(record.get("text", ""))
    context = normalize_text(record.get("context", "") or "")
    context_sufficient = bool(context)

    tokens = tokenize(text)
    unique = []
    for token in tokens:
        if token not in unique:
            unique.append(token)

    occurrences = _cue_occurrences(text, context)
    joke_context = any(marker in context for marker in ("玩笑", "玩梗", "调侃", "梗"))
    irony_context = any(marker in context for marker in ("反讽", "反话", "阴阳"))
    sarcasm_in_text = any(marker in text for marker in SARCASM_MARKERS)

    cues = []
    flags = []
    reasons = []

    if not context_sufficient:
        flags.append("缺少上下文")
        reasons.append("缺少课程上下文，不能据此判定为无风险")

    for occurrence in occurrences:
        ambiguous = bool(
            occurrence["quoted"]
            or occurrence["context_marker"]
            or occurrence["term_marker"]
            or (sarcasm_in_text and occurrence["kind"] == "risk")
        )
        occurrence["ambiguous"] = ambiguous
        cues.append({
            key: occurrence[key]
            for key in (
                "category", "phrase", "kind", "weight", "start", "end",
                "negated", "quoted", "ambiguous", "context_marker", "term_marker",
            )
        })

    if any(cue["quoted"] or cue["term_marker"] for cue in cues):
        if any(cue["kind"] == "risk" and cue["ambiguous"] for cue in cues):
            flags.append("引用或术语语境")
            reasons.append("线索出现在引用、术语或例句语境中，需要结合课程原意判断")
        else:
            flags.append("引用或术语语境（已排除）")
            reasons.append("引用或术语中的负向词未构成情绪线索")
    if joke_context:
        flags.append("同伴玩笑语境")
        reasons.append("上下文提示同伴玩笑，不能按字面判定情绪")
    if irony_context:
        flags.append("疑似反讽")
        reasons.append("上下文提示反讽或反话，需要人工确认真实情绪")
    if sarcasm_in_text and any(cue["kind"] == "risk" for cue in cues):
        if "疑似反讽" not in flags:
            flags.append("疑似反讽")
        reasons.append("文本含反讽标记，负向词可能并非字面意思")
    if any(cue["negated"] and cue["kind"] == "risk" for cue in cues):
        flags.append("否定表达")
        reasons.append("负向线索被否定词抵消")

    active_risk = [
        cue for cue in cues
        if cue["kind"] == "risk" and not cue["negated"] and not cue["ambiguous"]
    ]
    negated_positive = [
        cue for cue in cues
        if cue["kind"] == "positive" and cue["negated"] and not cue["ambiguous"]
    ]
    active_positive = [
        cue for cue in cues
        if cue["kind"] == "positive" and not cue["negated"] and not cue["ambiguous"]
    ]
    ambiguous_cues = [cue for cue in cues if cue["ambiguous"]]
    negated_risk = [
        cue for cue in cues
        if cue["kind"] == "risk" and cue["negated"] and not cue["ambiguous"]
    ]

    if active_risk or negated_positive:
        signal = "risk"
        strongest = max((cue["weight"] for cue in active_risk + negated_positive), default=1)
        risk_level = "high" if strongest >= 3 else "medium" if strongest >= 2 else "low"
        if not reasons:
            reasons.append("出现明确的受挫、求助或回避线索")
    elif ambiguous_cues:
        signal = "undetermined"
        risk_level = None
        if not reasons:
            reasons.append("情绪线索存在歧义，需要人工复核")
    elif active_positive:
        signal = "not_risk" if context_sufficient else "undetermined"
        risk_level = None
        reasons.append("出现积极表达" if context_sufficient else "缺少课程上下文，暂不能判定")
    elif negated_risk and context_sufficient:
        signal = "not_risk"
        risk_level = None
        reasons.append("负向线索均被否定，未形成风险信号")
    else:
        risk_level = None
        if context_sufficient:
            signal = "not_risk"
            reasons.append("在当前课程上下文中未发现受挫、求助或回避线索")
        else:
            signal = "undetermined"
            reasons.append("缺少课程上下文，不能算作无风险")

    if signal == "undetermined":
        flags.append("待判断")
    flags = list(dict.fromkeys(flags))
    reasons = list(dict.fromkeys(reasons))

    return {
        "tokens": tokens,
        "terms": unique,
        "character_count": len(text),
        "token_count": len(tokens),
        "cues": cues,
        "signal": signal,
        "risk_level": risk_level,
        "context_sufficient": context_sufficient,
        "flags": flags,
        "reasons": reasons,
    }
