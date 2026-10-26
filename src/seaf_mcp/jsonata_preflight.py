"""Preflight checks for agent-written JSONata expressions.

Runs BEFORE calling the portal; returns one concrete hint or None.
Own implementation; only rules confirmed on a real portal are enforced.

Applied only to expressions written by the agent (seaf_jsonata_query,
query_dataset, describe_query_result) — never to server-built queries.
"""

import re

from .jsonata_errors import MISSING_FUNCTION_HINTS

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_BACKTICK_RE = re.compile(r"`(?:[^`\\]|\\.)*`")
_REGEX_LITERAL_RE = re.compile(r"/(?:[^/\\]|\\.)+/[a-z]*")
_REGEX_PREFIX_CHARS = set("(,=:~<>!&|?+-*{;")


def _mask_literals(expr: str) -> str:
    """Replace literals with single-char placeholders so checks never fire
    inside them: strings -> S, backtick names -> B, regex literals -> R.
    Comments are removed. A '/' is a regex literal only when the previous
    non-space char is an operator (JSONata also uses '/' for division)."""
    text = _COMMENT_RE.sub(" ", expr)
    text = _STRING_RE.sub("S", text)
    text = _BACKTICK_RE.sub("B", text)
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "/":
            prev = next((c for c in reversed(out) if c != " "), "(")
            if prev in _REGEX_PREFIX_CHARS:
                m = _REGEX_LITERAL_RE.match(text, i)
                if m:
                    out.append("R")
                    i = m.end()
                    continue
        out.append(ch)
        i += 1
    return "".join(out)


def _check_bare_root(expr: str, root_label: str) -> str | None:
    text = _COMMENT_RE.sub(" ", expr).strip()
    # unwrap outer parens and trailing semicolons: (($);) -> $
    prev = None
    while prev != text:
        prev = text
        text = text.strip().rstrip(";").strip()
        if text.startswith("(") and text.endswith(")"):
            depth = 0
            for i, ch in enumerate(text):
                depth += ch == "("
                depth -= ch == ")"
                if depth == 0 and i < len(text) - 1:
                    break
            else:
                text = text[1:-1]
                continue
            break
    if text in ("$", "$$"):
        return (f"Bare '$' downloads {root_label}. Narrow it down: "
                "count or list keys first ($count($keys($)) / $keys($)[[0..n]]), "
                "filter ($filter/$sift), or select specific fields.")
    return None


_AGG_CHAIN_RE = re.compile(r"~>\s*\$(count|sum|average|min|max)\s*\(\s*\$\s*\)")

# bracket opened right after a value (positional context), not [[ and not a constructor
_VALUE_CHAR_RE = re.compile(r"[A-Za-z0-9_$)\]BS]")
_SIMPLE_RANGE_RE = re.compile(r"^\s*\d*\s*\.\.\s*\d*\s*$")
_SIMPLE_SLICE_RE = re.compile(r"^\s*[^,{}()\"']*:[^,{}()\"']*\s*$")


def _check_aggregate_chain(text: str) -> str | None:
    m = _AGG_CHAIN_RE.search(text)
    if m:
        fn = m.group(1)
        return (f"$ in a ~> chain refers to the ROOT context, not the chain value "
                f"(portal quirk, T0410). Rewrite: ${fn}($x) or $x ~> ${fn}().")
    return None


def _check_range_and_slice(text: str) -> str | None:
    for m in re.finditer(r"\[(?!\[)([^\[\]]*)\]", text):
        if m.start() == 0 or not _VALUE_CHAR_RE.match(text[m.start() - 1]):
            continue  # array constructor, not applied to a value
        inner = m.group(1)
        if _SIMPLE_RANGE_RE.match(inner):
            return ("Positional range x[a..b] is not supported on this portal (S0202). "
                    "Use an index filter: x[[a..b]].")
        if _SIMPLE_SLICE_RE.match(inner) and ".." not in inner:
            return ("Slice x[a:b] is not supported on this portal (S0202). "
                    "Use an index filter: x[[a..b]].")
    return None


# function(...) сразу со строкой-ключом в теле: {"k": v} без скобок/двойного брейса.
# ':' сразу за литералом отделяет объект от легитимного тела {"str" in arr}.
_FN_BODY_OBJECT_RE = re.compile(r"function\s*\([^)]*\)\s*\{\s*S\s*:")


def _check_function_body_object(text: str) -> str | None:
    """function($x){"k": v}: парсер читает { как тело, "k" как выражение
    и падает на первом ":" с S0202. Двойной брейс function($x){{ и
    скобки function($x){( {...} )} не матчатся (после { идёт не строка)."""
    if _FN_BODY_OBJECT_RE.search(text):
        return ("Object literal as a function body: wrap in parentheses — "
                "function($x){( {...} )} — the first { is the function body (portal: S0202).")
    return None


_LOOKUP_CALL_RE = re.compile(r"\$lookup\s*\(\s*S")


def _check_lookup_string_arg(text: str) -> str | None:
    for m in _LOOKUP_CALL_RE.finditer(text):
        if re.search(r"~>\s*$", text[:m.start()]):
            continue  # chain form: the chain value becomes the first (object) argument
        return ("$lookup's first argument must be the OBJECT, not a string: "
                "$lookup($, \"seaf.company.systems\"). On this portal a string first "
                "argument silently returns undefined. Chain form x ~> $lookup(\"key\") is fine.")
    return None


_CUSTOM_FN_RE = re.compile(r"\$(values|take|slice|group)\s*\(")
_CUSTOM_DECL_RE = re.compile(r"\$(values|take|slice|group)\s*:=")


def _check_custom_functions(text: str) -> str | None:
    declared = set(_CUSTOM_DECL_RE.findall(text))
    for fn in _CUSTOM_FN_RE.findall(text):
        if fn in declared:
            continue
        hint = MISSING_FUNCTION_HINTS.get(f"${fn}", f"${fn} does not exist on this portal")
        return hint + " (or declare it in the expression itself)."
    return None


def preflight(expression: str,
              root_label: str = "the whole manifest (~4 MB)") -> str | None:
    """One concrete hint for a known-bad/expensive construct, or None."""
    hint = _check_bare_root(expression, root_label)
    if hint:
        return hint
    masked = _mask_literals(expression)
    for check in (_check_aggregate_chain, _check_range_and_slice,
                  _check_function_body_object,
                  _check_lookup_string_arg, _check_custom_functions):
        hint = check(masked)
        if hint:
            return hint
    return None
