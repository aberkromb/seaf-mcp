# Примеры использования SEAF MCP Server

## Запуск с различными конфигурациями

### Базовый запуск
```bash
docker run -i --rm \
  -e API_BASE_URL=http://localhost:8080 \
  seaf-mcp:latest
```

### Запуск с увеличенным таймаутом
```bash
docker run -i --rm \
  -e API_BASE_URL=http://localhost:8080 \
  -e REQUEST_TIMEOUT=60 \
  seaf-mcp:latest
```

### Запуск с DEBUG логированием
```bash
docker run -i --rm \
  -e API_BASE_URL=http://localhost:8080 \
  -e LOG_LEVEL=DEBUG \
  seaf-mcp:latest
```

### Запуск с кастомным каталогом endpoints
```bash
docker run -i --rm \
  -e API_BASE_URL=http://localhost:8080 \
  -e ENDPOINT_CATALOG_PATH=/custom/endpoints.json \
  -v /path/to/custom/endpoints.json:/custom/endpoints.json:ro \
  seaf-mcp:latest
```

## Примеры вызовов через MCP tools

### 1. Проверка здоровья сервера
**Tool:** `get_health`

```json
{
  "name": "get_health",
  "arguments": {}
}
```

**Ожидаемый ответ:**
```
Request:
GET /health

Status:
200

Response:
{
  "message": "app is alive"
}
```

### 2. Получение версий манифеста
**Tool:** `get_seaf_core_api_core_storage_versions`

```json
{
  "name": "get_seaf_core_api_core_storage_versions",
  "arguments": {}
}
```

**Ожидаемый ответ:**
```
Request:
GET /seaf-core/api/core/storage/versions

Status:
200

Response:
{
  "archHash": "2750d9ed630885d6a03f56c408a444bba192917a",
  "coreVersion": "3.28.0",
  "enableChecker": true
}
```

### 3. Выполнение JSONata запроса
**Tool:** `seaf_jsonata_query`

Произвольные выражения — только через POST `seaf_jsonata_query`
(GET `.../jsonata/:query` принимает только UUID предопределённого запроса,
free-form выражение отклоняется с 403 Access denied).

```json
{
  "name": "seaf_jsonata_query",
  "arguments": {
    "query": "$count($keys(`seaf.company.systems`))"
  }
}
```

**Ожидаемый ответ:**
```
Request:
POST /seaf-core/api/core/storage/jsonata

Status:
200

Response:
878
```

### 4. Поиск компонентов
**Tool:** `post_seaf_core_api_core_storage_search_search_run`

```json
{
  "name": "post_seaf_core_api_core_storage_search_search_run",
  "arguments": {
    "choice": "seaf.company.systems",
    "filters": [],
    "searchQuery": "бронирование"
  }
}
```

`choice` — ключ сущности из `searchable-entities` (например `seaf.company.systems`;
для поиска по всем — `__all__`, а не `$all`).

**Ожидаемый ответ:**
```
Request:
POST /seaf-core/api/core/storage/search/search-run

Status:
200

Response:
[
  {
    "_sfa_key": "example.app_system.hotel_booking",
    "_sfa_entity": "seaf.company.systems",
    "title": "АС Бронирование",
    ...
  }
]
```

### 5. Описание результата JSONata-запроса
**Tool:** `describe_query_result`

```json
{
  "name": "describe_query_result",
  "arguments": {
    "query": "$.\"seaf.company.people\""
  }
}
```

**Ожидаемый ответ:** `result_type` (array/object/scalar/missing), `count` по всему
результату, `fields` с покрытием `present_in k/n`, `top_keys` и до 3 `examples`
(каждый ≤1500 символов). Схема строится по первым 100 элементам — это разведка
перед `seaf_jsonata_query`, а не полная схема. Пустой результат — `missing` с
явным пояснением; терминальные агрегаты (`$count` и т. п.) — скаляр в `value`.

### 6. JSONata поверх датасета
**Tool:** `query_dataset`

```json
{
  "name": "query_dataset",
  "arguments": {
    "origin": "seaf.company.ds.people",
    "source": "$keys($)[[0..2]]"
  }
}
```

`origin` — ID датасета (корневой `$` — сам датасет) или объект `{алиас: dataset_id}`
(тогда `$` — мапа, датасет доступен как `$."алиас"`). В отличие от `seaf_jsonata_query`,
корневой `$` здесь — данные датасета, а не манифест. `source` оборачивается в `(...)` автоматически.
Доступны любые датасеты, включая служебные `seaf.ds.*` — сужайте выборку: ответ считается на портале целиком.

### 7. Просмотр нескольких разделов
**Tool:** `inspect_sections`

```json
{
  "name": "inspect_sections",
  "arguments": {
    "sections": ["seaf.company.systems", "seaf.company.services"],
    "sample_limit": 2
  }
}
```

**Ожидаемый ответ:** по каждому разделу `count`, `attributes` с частотой,
исходящие/входящие поля-связи и до `sample_limit` (1-3, по умолчанию 2) образцов.
Образцы ограничены по размеру (длинные строки/массивы/карты сокращаются с пометкой);
раздел целиком не выгружается. Порядок: `list_sections` → `inspect_sections` → запрос.

### 8. Чтение именованного датасета
**Tool:** `get_dataset`

```json
{
  "name": "get_dataset",
  "arguments": {
    "dataset_id": "seaf.company.ds.people"
  }
}
```

**Ожидаемый ответ:** объект объектов (ключ — ID записи), поля записи:
`title` (ФИО), `role`, `department` и технические `__id__`/`__entity_id__`/`__object_id__`/`__link_to_card__`.

Доступны только датасеты `seaf.company.ds.*`; служебные (`seaf.ds.*`, `seaf.toolkit.ds.*`)
отклоняются до запроса (десятки МБ). Неизвестный ID — сообщение об ошибке
(портал отвечает HTTP 200 с `null`).

## Конфигурация для OpenCode

### Для локального SEAF API
```json
{
  "mcpServers": {
    "seaf": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "API_BASE_URL=http://host.docker.internal:8080",
        "seaf-mcp:latest"
      ]
    }
  }
}
```

### Для удаленного SEAF API
```json
{
  "mcpServers": {
    "seaf-prod": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "API_BASE_URL=https://seaf-portal.example.com",
        "-e", "REQUEST_TIMEOUT=60",
        "seaf-mcp:latest"
      ]
    }
  }
}
```

### С DEBUG логами
```json
{
  "mcpServers": {
    "seaf-debug": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "API_BASE_URL=http://localhost:8080",
        "-e", "LOG_LEVEL=DEBUG",
        "seaf-mcp:latest"
      ]
    }
  }
}
```

## Локальная разработка (без Docker)

### Установка зависимостей
```bash
pip install -r requirements.txt
```

### Запуск сервера
```bash
export API_BASE_URL=http://localhost:8080
export LOG_LEVEL=DEBUG
python -m seaf_mcp.server
```

### Валидация каталога
```bash
python test_catalog.py
```

## Тестирование с curl

Для тестирования REST API напрямую (без MCP):

```bash
# Проверка здоровья
curl http://localhost:8080/health

# Версии
curl http://localhost:8080/seaf-core/api/core/storage/versions

# JSONata запрос (произвольные выражения — только POST; GET с free-form выражением даёт 403)
curl -X POST http://localhost:8080/seaf-core/api/core/storage/jsonata \
  -H "Content-Type: application/json" \
  -d '{"query": "($keys($))"}'

# Поиск
curl -X POST http://localhost:8080/seaf-core/api/core/storage/search/search-run \
  -H "Content-Type: application/json" \
  -d '{"choice": "seaf.company.systems", "filters": [], "searchQuery": "бронирование"}'
```
