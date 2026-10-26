# SEAF MCP: установка и подключение

> TODO: подставить реальные URL репозиториев вместо `<...>`.

## 1. Требования
- Docker Desktop (запущен).
- Для локального портала SEAF: Docker с памятью ≥ 8 GB (Settings → Resources), портал потребляет ~5.5 GB.
- MCP-клиент: OpenCode, Claude Desktop, Cursor и т.п.

## 2. Сборка образа MCP
```bash
git clone <seaf-mcp repo URL>
cd seaf-mcp
docker build -t seaf-mcp:latest .
```
Образ не публикуется в registry — собирается локально. После `git pull` пересоберите.

## 3. Источник данных SEAF

### A. Готовый развёрнутый портал
```
API_BASE_URL=https://seaf-portal.example.com
```
Авторизация пока не поддерживается.

Нюансы:
- `/health` через ingress не открыт (404) — health-check MCP по умолчанию ходит в `/seaf-core/api/core/storage/versions`, так что это не мешает.
- GET `.../jsonata/:query` принимает только UUID предопределённого запроса (free-form → 403); произвольные запросы — через tool `seaf_jsonata_query` (POST).

### B. Локальный портал со своим репозиторием архитектуры
```bash
git clone --recurse-submodules <architecture-repo> ~/seaf/example

docker run -d --name seaf-example -p 8080:3030 \
  -v ~/seaf/example:/var/www/public/workspace:ro \
  -e VUE_APP_DOCHUB_BACKEND_PORT=3030 \
  -e VUE_APP_DOCHUB_ROOT_MANIFEST=workspace/dochub.yaml \
  -e VUE_APP_DOCHUB_APPEND_DOCHUB_DOCS=n \
  -e NODE_OPTIONS=--max-old-space-size=6144 \
  seaf/seaf-archtool-core:2026.31.0
```
- Готовность: `docker logs -f seaf-example` → `The application is ready to accept requests`. На Apple Silicon ~13 минут (образ amd64, эмуляция).
- Проверка: `curl localhost:8080/health` → `{"message":"app is alive"}`.
- После правок YAML: `curl -X PUT localhost:8080/core/storage/reload`.
- Остановка/запуск: `docker stop seaf-example` / `docker start seaf-example` (повторный прогрев).
- Для MCP используйте `API_BASE_URL=http://host.docker.internal:8080` — внутри контейнера `localhost` указывает на сам контейнер.

## 4. Подключение MCP-клиента

### OpenCode
`opencode.json` в проекте или `~/.config/opencode/opencode.json`:
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "seaf": {
      "type": "local",
      "enabled": true,
      "timeout": 30000,
      "command": ["docker", "run", "-i", "--rm",
        "-e", "API_BASE_URL=https://seaf-portal.example.com",
        "seaf-mcp:latest"]
    }
  }
}
```
Проверка: `opencode mcp list` → `✓ seaf connected`.

- `opencode.json` в проекте действует только при запуске opencode из этой папки; чтобы MCP был доступен везде — добавьте блок в `~/.config/opencode/opencode.json`. Конфиг читается при старте — после правки перезапустите opencode.
- `timeout` (мс) нужен: холодный старт контейнера может не уложиться в дефолтные 5 с, и сервер молча не подключится.
- Для локального портала (3.B) подставьте `API_BASE_URL=http://host.docker.internal:8080`.

### Claude Desktop / Cursor и др. (формат `mcpServers`)
```json
{
  "mcpServers": {
    "seaf": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "API_BASE_URL=https://seaf-portal.example.com", "seaf-mcp:latest"]
    }
  }
}
```
Флаг `-i` обязателен: MCP работает через stdin/stdout.

### Отладка в UI
```bash
npx @modelcontextprotocol/inspector docker run -i --rm \
  -e API_BASE_URL=https://seaf-portal.example.com seaf-mcp:latest
```

## 4a. Удалённый доступ (Streamable HTTP)

По умолчанию MCP работает через stdio. Для удалённого клиента есть Streamable HTTP
(endpoint `/mcp`, режим stateless):

```bash
docker run -i --rm \
  -e API_BASE_URL=https://seaf-portal.example.com \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HTTP_HOST=0.0.0.0 \
  -p 127.0.0.1:14280:14280 \
  seaf-mcp:latest
```
Клиент подключается к `http://127.0.0.1:14280/mcp`.
`MCP_HTTP_HOST=0.0.0.0` нужен внутри контейнера, чтобы принимать проброшенный порт;
снаружи он доступен только на localhost хоста благодаря `-p 127.0.0.1:...`.

> **Безопасность: входящей авторизации нет.** Любой, кто достучался до порта,
> читает модель архитектуры, включая данные о людях. Поэтому:
> - `MCP_HTTP_HOST` по умолчанию `127.0.0.1`; `0.0.0.0` открывает доступ всей сети;
> - публикуйте порт только на localhost хоста (`-p 127.0.0.1:14280:14280`) или
>   ставьте за обратным прокси с авторизацией;
> - включена проверка `Host`/`Origin` (защита от DNS rebinding) — браузерный
>   скрипт с чужого origin получит 403.

## 5. Настройки (`-e`)
| Переменная | По умолчанию | Описание |
|---|---|---|
| `API_BASE_URL` | обязательна | Адрес портала SEAF |
| `REQUEST_TIMEOUT` | 30 | Таймаут запроса, сек |
| `API_MIN_INTERVAL_SECONDS` | 0 (выкл) | Мин. интервал между запросами к порталу; влияет на все вызовы, включая retry |
| `MAX_RESPONSE_CHARS` | 50000 | Макс. размер ответа агенту; `0` — без лимита |
| `HEALTH_CHECK_PATH` | /seaf-core/api/core/storage/versions | Проверка при старте; пусто — пропустить |
| `LOG_LEVEL` | INFO | Логи в stderr |
| `SEAF_LOG_FILE` | пусто (только stderr) | Диагностический лог в файл (ротация 10 МБ × 3), пишется и в stderr |
| `SEAF_METAMODEL_PATH` | пусто (только cookbook) | Каталог локального репозитория метамодели: `find_jsonata_examples` добавляет примеры из YAML |
| `MCP_TRANSPORT` | stdio | `stdio` или `streamable-http` (см. 4a) |

Пример с метамоделью (каталог монтируется read-only):
```json
"command": ["docker", "run", "-i", "--rm",
  "-e", "API_BASE_URL=https://seaf-portal.example.com",
  "-e", "SEAF_METAMODEL_PATH=/metamodel",
  "-v", "/path/to/seaf-metamodel:/metamodel:ro",
  "seaf-mcp:latest"]
```
| `MCP_HTTP_HOST` | 127.0.0.1 | Адрес привязки HTTP; `0.0.0.0` — доступ всей сети, см. 4a |
| `MCP_HTTP_PORT` | 14280 | Порт HTTP; endpoint `/mcp` |

Диагностический лог пишется внутрь контейнера: чтобы файл переживал перезапуск, примонтируйте volume:
```json
"command": ["docker", "run", "-i", "--rm",
  "-e", "API_BASE_URL=https://seaf-portal.example.com",
  "-e", "SEAF_LOG_FILE=/logs/seaf-mcp.log",
  "-v", "seaf-mcp-logs:/logs",
  "seaf-mcp:latest"]
```
При недоступном пути сервер пишет одно сообщение в stderr и продолжает работу без файла.
Тела запросов/ответов и значения параметров в лог не пишутся (в DEBUG — только имена и длина).
| `SEAF_JSONATA_LOG_FILE` | пусто (выкл) | JSONL-журнал запросов агента (seaf_jsonata_query, query_dataset, describe_query_result) |
| `SEAF_JSONATA_LOG_INCLUDE_QUERY` | false | Писать полный текст запроса в журнал (нужно для cookbook; иначе только SHA-256) |
| `SEAF_JSONATA_LOG_MAX_BYTES` | 10000000 | Ротация журнала в `.1` при превышении |

JSONL-журнал пишется внутрь контейнера: чтобы файл переживал перезапуск, примонтируйте volume:
```json
"command": ["docker", "run", "-i", "--rm",
  "-e", "API_BASE_URL=https://seaf-portal.example.com",
  "-e", "SEAF_JSONATA_LOG_FILE=/logs/jsonata.jsonl",
  "-v", "seaf-mcp-logs:/logs",
  "seaf-mcp:latest"]
```
Запись одна на запрос агента (timestamp, tool, status `empty|non_empty|error`, код ошибки,
длительность, SHA-256 запроса); params, ответы и чувствительные данные не пишутся.
Серверные (собранные самим MCP) запросы высокоуровневых tools в журнал не попадают.

## 6. Что получает агент
| Tool | Назначение |
|---|---|
| `list_sections` | Разделы модели с количеством объектов |
| `describe_section` | Атрибуты и поля-связи раздела |
| `find_entities` | Поиск по подстроке в ID/title/description |
| `get_entity` | Объект по ID |
| `list_entities` | Объекты раздела с фильтрами (eq/ne/contains/missing/exists) |
| `get_relations` | Связи в обе стороны; для крупных объектов — `direction`, `field`, `limit` |
| `metamodel_guide` | Справочник метамодели (также ресурс `seaf://metamodel-guide`) |
| `find_jsonata_examples` | Проверенные примеры JSONata (cookbook) |
| `find_relation_paths` | Многошаговые пути связей между разделами (для составления JOIN) |
| `seaf_jsonata_query` | Произвольный JSONata |
| `get_health`, problems, versions | Служебные |

Примеры вопросов: «Из каких подсистем состоит АС Бронирование?», «Какие системы без стейкхолдеров?», «Какие процессы производят продукт Отель?»

## 7. Решение проблем
| Симптом | Решение |
|---|---|
| `Cannot connect to ...` | Портал не запущен или указан `localhost` вместо `host.docker.internal` |
| Tools `seaf` не видны | Проверьте `-i`, наличие образа (`docker images seaf-mcp`), перезапустите клиент |
| Портал падает с `heap out of memory` | `NODE_OPTIONS=--max-old-space-size=6144`, память Docker ≥ 8 GB |
| `503 no healthy upstream` | Бэкенд портала в кластере недоступен (не ошибка MCP) — повторите позже или сообщите владельцам портала |
| `Request timed out` | Сузьте запрос или увеличьте `REQUEST_TIMEOUT` |
| `[truncated: ...]` в ответе | Сузьте запрос (`field`/`limit`) или увеличьте `MAX_RESPONSE_CHARS` |
| Портал показывает старые данные | `curl -X PUT localhost:8080/core/storage/reload` |
