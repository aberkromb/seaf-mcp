"""Response formatter - formats API responses for agent consumption."""

import json
from typing import Any

from .errors import PortalError
from .rest_client import APIResponse


def _format_json(data: dict | list, indent: int = 2) -> str:
    """Format JSON data with indentation.
    
    Args:
        data: JSON-serializable data
        indent: Number of spaces for indentation
    
    Returns:
        Pretty-printed JSON string
    """
    try:
        return json.dumps(data, indent=indent, ensure_ascii=False)
    except Exception:
        return str(data)


EMPTY_RESULT_NOTE = "The request succeeded, but the result is empty"
EMPTY_UNDEFINED = f"{EMPTY_RESULT_NOTE} (undefined/empty response body)."
EMPTY_NULL = f"{EMPTY_RESULT_NOTE} (null)."
EMPTY_ARRAY = f"{EMPTY_RESULT_NOTE} ([])."
EMPTY_OBJECT = f"{EMPTY_RESULT_NOTE} ({{}})."


def empty_result_note(body: Any) -> str | None:
    """Explicit marker for successful-but-empty results.

    Empty = undefined (empty body), null, [], {}. NOT empty: false, 0, ""
    (a quoted empty string is a JSON string, not emptiness).
    """
    if isinstance(body, str) and body == "":
        return EMPTY_UNDEFINED
    if body is None:
        return EMPTY_NULL
    if isinstance(body, list) and not body:
        return EMPTY_ARRAY
    if isinstance(body, dict) and not body:
        return EMPTY_OBJECT
    return None


def describe_result_size(body: Any) -> str | None:
    """Truncation advice with the original size computed from the parsed body."""
    if isinstance(body, list):
        return (f"the full result is an array of {len(body)} elements; "
                "narrow with a filter/projection or an index filter [[a..b]]")
    if isinstance(body, dict):
        keys = [str(k) for k in list(body)[:10]]
        more = f" (+{len(body) - 10} more)" if len(body) > 10 else ""
        return (f"the full result is an object with {len(body)} keys: "
                f"{', '.join(keys)}{more}; narrow with a filter/projection "
                "or select specific keys")
    return None


def truncate(text: str, max_chars: int, details: str | None = None) -> str:
    """Truncate the WHOLE text to max_chars (0 = unlimited), note included.

    The [truncated: marker stays at the start of the appended note; the note
    reports the actually shown character count. `details` carries the
    size/element-count advice (never suggest $slice — unsupported, see 002).
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    advice = details or "narrow your query"
    shown = max_chars
    note = ""
    for _ in range(3):
        note = f"\n\n[truncated: showing {shown} of {len(text)} chars — {advice}]"
        new_shown = max_chars - len(note)
        if new_shown >= shown:
            break
        shown = new_shown
    if shown <= 0:
        return text[:max_chars]
    return text[:shown] + note


def format_success(
    method: str,
    path: str,
    response: APIResponse,
    max_chars: int = 0,
) -> str:
    """Format successful API response for agent.
    
    Format (from README Этап 6):
    ```
    Request:
    GET /seaf-core/api/health

    Status:
    200

    Response:
    {"message": "app is alive"}
    ```
    
    Args:
        method: HTTP method
        path: Request path
        response: API response object
    
    Returns:
        Formatted text for agent
    """
    lines = [
        "Request:",
        f"{method.upper()} {path}",
        "",
        "Status:",
        str(response.status_code),
        "",
        "Response:",
    ]

    empty_note = empty_result_note(response.body)
    if empty_note is not None:
        lines.append(empty_note)
    elif isinstance(response.body, (dict, list)):
        lines.append(_format_json(response.body))
    elif isinstance(response.body, bool) or response.body is None:
        lines.append(json.dumps(response.body))
    else:
        lines.append(str(response.body))

    return truncate("\n".join(lines), max_chars, details=describe_result_size(response.body))


def _extract_error_message(response: APIResponse) -> str:
    """Pick a human-readable message; never include stack traces."""
    body = response.body
    if isinstance(body, dict):
        parts = []
        for key in ("message", "detail"):
            if isinstance(body.get(key), str) and body[key]:
                parts.append(body[key])
                break
        err = body.get("error")
        if isinstance(err, str) and err:
            parts.append(err)
        elif isinstance(err, dict):
            details = {k: v for k, v in err.items()
                       if k not in ("stack", "value") and v not in (None, "", {})}
            if details:
                parts.append(f"Details: {_format_json(details, indent=None)}")
        if parts:
            return "\n".join(parts)
        return _format_json(body)
    if response.error:
        return response.error
    return str(body)


def format_error(
    tool_name: str,
    response: APIResponse,
    max_chars: int = 0,
    jsonata_hint: bool = False,
) -> str:
    """Format error response for agent.

    Format (from README Этап 6):
    ```
    API request failed

    Tool: get_health
    Status: 404
    Message:
    Not found
    ```

    Args:
        tool_name: Name of the tool that was called
        response: API response object
        jsonata_hint: add a JSONata fix hint (only for tools where the agent
            writes the expression, e.g. endpoints with jsonataParams)

    Returns:
        Formatted error text for agent
    """
    lines = [
        "API request failed",
        "",
        f"Tool: {tool_name}",
        f"Status: {response.status_code}",
        "Message:",
    ]

    lines.append(_extract_error_message(response))

    if jsonata_hint:
        from .jsonata_errors import extract_error_info, hint_for
        info = extract_error_info(response.body)
        hint = hint_for(info["code"], info["token"], info["message"])
        if hint:
            lines.append("")
            lines.append(f"Hint: {hint}")

    if response.status_code in (502, 503, 504):
        lines.append("")
        lines.append("Hint: upstream unavailable; the request may succeed if retried later.")

    return truncate("\n".join(lines), max_chars)


def format_portal_error(tool_name: str, error: "PortalError", max_chars: int = 0,
                        agent_expression: bool = False) -> str:
    """Format a structured portal error from a high-level tool.

    agent_expression=True only for tools where the agent wrote the JSONata
    (query_dataset, describe_query_result): adds a fix hint. For server-built
    queries the message is neutral — no syntax advice.
    """
    from .jsonata_errors import hint_for

    lines = [
        "API request failed",
        "",
        f"Tool: {tool_name}",
        f"Status: {error.status}",
        "Message:",
        error.message,
    ]
    if error.code:
        lines.append(f"Code: {error.code}")
    if agent_expression:
        hint = hint_for(error.code, error.token, error.message)
        if hint:
            lines.append("")
            lines.append(f"Hint: {hint}")
    else:
        lines.append("")
        lines.append("This error comes from the tool's built-in query, not from your input.")
    return truncate("\n".join(lines), max_chars)


def format_client_error(tool_name: str, error_message: str) -> str:
    """Format client-side error (before API call).
    
    Used for errors like missing parameters, invalid config, etc.
    
    Args:
        tool_name: Name of the tool that was called
        error_message: Error description
    
    Returns:
        Formatted error text for agent
    """
    return f"""Tool execution failed

Tool: {tool_name}
Error: {error_message}

This error occurred before making the API request. Check tool parameters and configuration."""
