"""Parsing and agent-facing hints for portal JSONata errors.

Own implementation
(docs/helper-commands-tasks/002_jsonata_api_contract.md); helper's GPLv3
code is not copied.

Codes confirmed on the live SEAF portal (2026.31.0):
- S0201: ";" outside a (...) block
- S0202: unexpected token (";" in an unbraced function body, ".." positional
  range, ":" slice or an object constructor as a function body)
- S0204: unknown operator ("~")
- S0211: symbol cannot be used as a unary operator ("?.", incomplete expression)
- T0410: function signature mismatch, notably `~> $count($)` where `$` binds to
  the root context instead of the chain value
- T1006: call of a non-existent function ($take/$slice/$group/$values/...)
- T2006: right side of ~> must be a function

Deterministic codes (S0xxx / T0xxx / D1xxx) are also used by the retry policy:
an HTTP 500 carrying one of these codes must not be retried.
"""

import difflib
import re
from typing import Any

CODE_RE = re.compile(r"^[STDA]\d{3,}$")
DETERMINISTIC_RE = re.compile(r"^[STD]\d{3,}$")

# Standard JSONata functions that work on the portal (spot-checked in 002).
STANDARD_FUNCTIONS = [
    "$abs", "$append", "$average", "$boolean", "$ceil", "$contains", "$count",
    "$distinct", "$each", "$exists", "$filter", "$floor", "$join", "$keys",
    "$length", "$lookup", "$lowercase", "$map", "$match", "$max", "$merge",
    "$min", "$not", "$now", "$number", "$power", "$random", "$replace",
    "$reverse", "$round", "$sift", "$sort", "$split", "$sqrt", "$string",
    "$substring", "$substringAfter", "$substringBefore", "$sum", "$trim",
    "$type", "$uppercase",
]

# Functions that do NOT exist on the portal (confirmed T1006 in 002) with a
# ready-made replacement pattern.
MISSING_FUNCTION_HINTS = {
    "$take": "$take(arr, n) does not exist — use an index filter: arr[[0..n-1]]",
    "$slice": "$slice does not exist — use an index filter: arr[[a..b]]",
    "$group": "$group does not exist — group with an object constructor over an "
              "array: arr{ key_expr : $ } (objects must be converted with $map first)",
    "$values": "$values(obj) does not exist — use $map($keys(obj), function($k){$lookup(obj, $k)}) "
               "or $each(obj, function($v){$v})",
}


def extract_error_info(body: Any) -> dict[str, Any]:
    """Pull {code, token, position, message} out of a portal error body.

    Safe on any input; fields default to None. Never returns stack/value —
    the portal may include a Node stack and a context dump.
    """
    info: dict[str, Any] = {"code": None, "token": None, "position": None, "message": None}
    if not isinstance(body, dict):
        return info
    if isinstance(body.get("message"), str):
        info["message"] = body["message"]
    err = body.get("error")
    if isinstance(err, dict):
        if isinstance(err.get("code"), str) and CODE_RE.match(err["code"]):
            info["code"] = err["code"]
        if isinstance(err.get("token"), str):
            info["token"] = err["token"]
        if isinstance(err.get("position"), int):
            info["position"] = err["position"]
        if not info["message"] and isinstance(err.get("message"), str):
            info["message"] = err["message"]
    return info


def is_deterministic_error(status: int, body: Any) -> bool:
    """True when retrying is pointless: coded JSONata/type error."""
    return 400 <= status < 600 and bool(extract_error_info(body)["code"])


def _close_functions(token: str | None) -> list[str]:
    if not token:
        return []
    name = token if token.startswith("$") else f"${token}"
    return difflib.get_close_matches(name, STANDARD_FUNCTIONS, n=3, cutoff=0.6)


def hint_for(code: str | None, token: str | None = None,
             message: str | None = None) -> str | None:
    """Agent-facing fix hint for agent-written JSONata (tools where the user
    writes the expression). None for unknown codes (neutral message instead).
    """
    if not code:
        return None

    if code == "S0201" and token == ";":
        return ("';' is only allowed inside a (...) block. Wrap the whole expression in "
                "parentheses, e.g. ($x := 1; $x + 1), or remove the trailing semicolon.")
    if code == "S0202":
        if token == ";":
            return ("A function body with multiple expressions must be wrapped in (...): "
                    "function($x){ ($y := $x * 2; $y + 1) }.")
        if token == "..":
            return ("Positional range x[a..b] is not supported. Use the array constructor "
                    "[a..b] in an index filter instead: x[[a..b]].")
        if token == ":":
            # ":" двусмысленно: slice $x[a:b] (Expected "]") или ключ объекта
            # как тело function($x){"k": v} (Expected "}").
            if message and 'Expected "}"' in message:
                return ('An object literal as a function body must be wrapped in parentheses: '
                        'function($x){( {"k": v} )}, or use a double brace '
                        'function($x){{...}}. The { after function(...) is the body, '
                        'not the object.')
            return ("Slice x[a:b] is not supported. Use an index filter: x[[a..b]].")
        return "Syntax error: fix the expression; retrying it unchanged will fail again."
    if code == "S0204":
        return "Operator '~' is not supported on this portal."
    if code == "S0211":
        return ("Check for unsupported optional chaining '?.' (use a plain path plus "
                "$exists) or an incomplete expression.")
    if code.startswith("S0"):
        return "Syntax error: fix the expression; retrying it unchanged will fail again."

    if code == "T1003":
        return ("Object keys must be string literals: write {\"field\": value}, not "
                "{field: value}. Bare identifiers are evaluated as expressions and "
                "silently dropped on this portal.")
    if code == "T1006":
        parts = []
        if token:
            name = token if token.startswith("$") else f"${token}"
            parts.append(f"Function {name} does not exist on this portal.")
            if name in MISSING_FUNCTION_HINTS:
                parts.append(MISSING_FUNCTION_HINTS[name] + ".")
            else:
                close = _close_functions(token)
                if close:
                    parts.append(f"Did you mean: {', '.join(close)}?")
        else:
            parts.append("The called function does not exist on this portal.")
        return " ".join(parts)
    if code == "T0410":
        return ("In a ~> chain, '$' refers to the ROOT context, not the chain value "
                "(portal quirk). Rewrite: $count($x) or $x ~> $count() instead of "
                "$x ~> $count($); same for $sum/$average/$min/$max.")
    if code == "T2006":
        return ("The right side of '~>' must be a function. Compose as "
                "expr ~> $func() or call the function directly: $func(expr).")
    if code.startswith(("T", "D")):
        return ("Type/runtime error. Step back: inspect the data shape first "
                "(describe_section or describe_query_result), then adjust the expression.")
    return None
