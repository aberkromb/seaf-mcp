# Фикстуры поведения JSONata на портале SEAF

Источник: развёрнутый портал SEAF ArchTool, coreVersion 2026.31.0.

Все запросы: `POST /seaf-core/api/core/storage/jsonata`, тело `{"query": "(<expr>)"}`,
заголовок `Content-Type: application/json`.

Конвенции фикстур:

- `_meta.request.query` — точное выражение, `_meta.http_status`, `_meta.body_note` для не-JSON тел.
- `error.stack` и `error.value` на портале присутствуют и могут содержать стек Node.js и
  дамп контекста (кусок манифеста!) — в фикстурах удалены, в коде срезаются.
- `undefined_result.json` — важный случай: JSONata-undefined сериализуется как
  HTTP 200 с **пустым телом** (0 байт, не `null`).
