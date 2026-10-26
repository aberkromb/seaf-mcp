# SEAF MCP Server

MCP-сервер для доступа к SEAF ArchTool REST API через протокол Model Context Protocol.

## Быстрый старт

```bash
docker build -t seaf-mcp:latest .
```

Подробная инструкция по установке, запуску локального портала SEAF и подключению MCP-клиентов (OpenCode, Claude Desktop, Cursor): **[INSTALL.md](INSTALL.md)**.

## Доступные tools

**Высокоуровневые (рекомендуются агенту):**
| Tool | Назначение |
|---|---|
| `list_sections` | Разделы `seaf.company.*` с числом объектов и описанием |
| `list_entities` | Объекты раздела с фильтрами (eq/ne/contains/missing/exists); фильтрация и пагинация на портале |
| `describe_section` | Атрибуты раздела (с частотой), поля-связи (исходящие/входящие), примеры ID |
| `inspect_sections` | 1-5 разделов за вызов: структура + до 3 образцов на раздел (образцы ограничены) |
| `find_entities` | Поиск по подстроке в ID/title/description |
| `get_entity` | Объект по ID |
| `get_relations` | Связи объекта: outgoing и incoming, сгруппированные по полю |
| `metamodel_guide` | Справочник метамодели (также MCP-ресурс `seaf://metamodel-guide`) |
| `get_dataset` | Именованный датасет `seaf.company.ds.*` целиком (например `seaf.company.ds.people`) |
| `query_dataset` | JSONata поверх датасета: `origin` — ID или `{алиас: id}`, `source` — выражение (включая служебные `seaf.ds.*`) |
| `describe_query_result` | Описание результата JSONata-запроса: тип, count, поля с покрытием по выборке 100, до 3 примеров |
| `seaf_jsonata_query` | Произвольный JSONata (POST) |
| `find_jsonata_examples` | Проверенные примеры JSONata: keyword-поиск по вопросу/группе/разделам |
| `find_relation_paths` | Многошаговые пути связей между разделами (BFS по карте `$rels`, до 10 маршрутов ≤5 шагов) |

Плюс raw: `get_health`, `get_seaf_core_api_core_storage_problems`, `get_seaf_core_api_core_storage_versions`. Остальные endpoints каталога помечены `"hidden": true`.

**Какой tool выбрать (пересекающиеся пары):**
- `describe_section` — один раздел без образцов; `inspect_sections` — 1-5 разделов с образцами (перед запросом со связями).
- `seaf_jsonata_query` — JSONata по манифесту модели; `query_dataset` — JSONata по датасету (другой корневой `$`).
- `find_entities` — примерное название → ID; `list_entities` — точные фильтры по полям в известном разделе.

Каждое описание tool построено как «Когда вызывать → что вернёт → Дальше».

Связи берутся из формальной схемы: при первом вызове сервер читает `$manifestschema()` и строит карту `поле → раздел` по `$ref: #/$rels/...` (с учётом `allOf`/`$defs`), затем кэширует её. Пользовательский ввод подставляется в JSONata только как JSON-литерал.

Справочник метамодели — `src/seaf_mcp/data/metamodel_guide.md`.
Cookbook проверенных запросов — `src/seaf_mcp/data/jsonata_cookbook.json` (интеграционный тест выполняет
каждую нешаблонную запись на портале, так что примеры не устаревают); пополняется по JSONL-журналу
(`SEAF_JSONATA_LOG_FILE` + `SEAF_JSONATA_LOG_INCLUDE_QUERY=true`).

### Исключенные endpoints
- **PUT/DELETE/PATCH** и **POST без `readOnly=true`** — записывающие операции
- **Wildcard paths** (`/:hash/*`)
- **SSE endpoints** (`/chat/message`)

### Правила
- Схемы аргументов строгие (`additionalProperties: false`): неизвестные/недостающие параметры возвращаются агенту как ошибка.
- Path-параметры URL-кодируются полностью (`/` → `%2F`), т.е. JSONata-запрос можно передавать как есть.
- При старте сервер падает, если в каталоге есть дублирующиеся имена tools или один параметр объявлен в нескольких местах (path/query/body).

## Формат ответов

```
Request:
GET /seaf-core/api/core/storage/versions

Status:
200

Response:
{
  "archHash": "...",
  "coreVersion": "2026.31.0",
  "enableChecker": true
}
```

Длинные ответы обрезаются по `MAX_RESPONSE_CHARS` (лимит — на весь текст ответа, включая
заголовки и примечание): `[truncated: showing N of M chars — <подсказка>]`. Для массивов
примечание сообщает исходное число элементов и советует фильтр/проекцию или индексный
фильтр `[[a..b]]`; для объектов — число ключей и первые из них.

Пустой успешный результат (undefined, `null`, `[]`, `{}`) помечается явно:
`The request succeeded, but the result is empty (...)` — `false` и `0` пустыми не считаются.

Ошибки API:
```
API request failed

Tool: get_seaf_core_api_core_storage_versions
Status: 404
Message:
Not found
```

Сетевые ошибки (нет соединения, таймаут) возвращаются как `Tool execution failed` с подсказкой.

Preflight: известные дорогие/ошибочные конструкции отклоняются до запроса к порталу
(голый `$`, `~> $count($)`, позиционный диапазон `x[a..b]`, slice `x[a:b]`,
`$lookup("строка", …)`, `$take`/`$slice`/`$group`/`$values` без объявления).
Строки, комментарии, backtick-имена и regex-литералы при проверке игнорируются;
индексный фильтр `x[[a..b]]` разрешён.

Ошибки JSONata (коды S0xxx/T0xxx) сопровождаются подсказкой по исправлению — только в tools,
где выражение пишет агент (`seaf_jsonata_query`, `query_dataset`): неподдерживаемый синтаксис
(позиционный диапазон, slice, `~`, `?.`), отсутствующие функции (`$take`/`$slice`/`$group`/`$values`
с полифиллами), баг `$` в цепочке `~>` и т.д. Стек и дамп контекста из тела ошибки срезаются.

## Структура проекта

```
seaf-mcp/
├── src/seaf_mcp/
│   ├── __main__.py            # python -m seaf_mcp
│   ├── server.py              # MCP сервер
│   ├── config.py              # Конфигурация из env vars
│   ├── endpoint_catalog.py    # Загрузка endpoints.json
│   ├── tool_factory.py        # Фильтрация, генерация tools, валидация аргументов
│   ├── seaf_tools.py          # Высокоуровневые tools (разделы, поиск, связи)
│   ├── rest_client.py         # Async HTTP клиент
│   ├── response_formatter.py  # Форматирование и обрезка ответов
│   ├── data/
│   │   └── metamodel_guide.md # Справочник метамодели SEAF
│   └── errors.py
├── tests/
├── endpoints.json
├── requirements.txt / requirements-dev.txt
└── Dockerfile
```

## Особенности SEAF API (проверено на 2026.31.0)
- `GET .../jsonata/:query` с произвольным выражением → **403 Access denied**; используйте `seaf_jsonata_query` (POST).
- Запрос без внешних скобок SEAF трактует как ID предопределённого запроса (`Cannot read properties of undefined`). `seaf_jsonata_query` оборачивает выражение в `(...)` автоматически (поле каталога `jsonataParams`).
- POST не поддерживает `params` — подставляйте значения прямо в выражение.
- Весь манифест (`$`) ≈ 4 MB — будет обрезан по `MAX_RESPONSE_CHARS`.
- Датасеты (`get_dataset`): POST `release-data-profile`, только `seaf.company.ds.*` (служебные `seaf.ds.*` — десятки МБ). Неизвестный ID → HTTP 200 с `null`.

Интеграционные тесты:
- локальный портал (example manifest): `SEAF_IT_URL=http://localhost:8080 .venv/bin/pytest tests/test_integration_portal.py`
- любой развёрнутый портал: `SEAF_CORP_IT_URL=https://seaf-portal.example.com .venv/bin/pytest tests/test_integration_corp.py`

## Дальше

- Авторизация при обращении к API
- Импорт каталога endpoints из OpenAPI, если спецификация появится

## Разработка

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest

# Локальный запуск
API_BASE_URL=https://seaf-portal.example.com ENDPOINT_CATALOG_PATH=endpoints.json \
  PYTHONPATH=src .venv/bin/python -m seaf_mcp
```
