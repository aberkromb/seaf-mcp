# SEAF ArchTool Backend — API Reference

**Base URL:** `http://localhost:8080`

All routes are available in two forms:
- Full: `/seaf-core/api/...`
- Short (legacy): without prefix, e.g. `/core/storage/reload`

The server is ready to accept requests once this line appears in the logs:  
`The application is ready to accept requests`  
(warm-up on Apple Silicon via Rosetta takes ~7 minutes)

---

## 1. Health / Probes

### GET /health
Server readiness check. Available before warm-up completes.

```bash
curl http://localhost:8080/health
```

**Response 200 (ready):**
```json
{ "message": "app is alive" }
```

**Response 500 (not ready):**
```json
{ "message": "app is dead <error message>" }
```

---

## 2. Core

### GET /seaf-core/api/title
Portal title from the `VUE_APP_DOCHUB_TITLE` environment variable.

```bash
curl http://localhost:8080/seaf-core/api/title
```

**Response:**
```json
{ "title": "SEAF" }
```

---

### GET /seaf-core/api/env-config
Frontend configuration: authorization settings, PlantUML, Clickstream.

```bash
curl http://localhost:8080/seaf-core/api/env-config
```

**Response:**
```json
{
  "clickstreamReportUrl": null,
  "clickstreamApiKey": null,
  "roleModeEnabled": null,
  "authorityServer": null,
  "authorityClientId": null,
  "authorityScope": null,
  "plantUmlServer": "seaf.su/seafplantuml/svg/",
  "plantUmlRequestType": null,
  "archChooser": false
}
```

---

### GET /seaf-core/api/core/storage/jsonata/:query
Execute a JSONata query against the loaded architecture manifest.

> **Note:** Free-form JSONata via this GET endpoint is rejected with `403 Access denied`.
> It only accepts a 36-character UUID of a built-in (predefined) query.
> For arbitrary JSONata use `POST /seaf-core/api/core/storage/jsonata` with body
> `{"query": "(<expression>)"}` — the expression must be wrapped in parentheses,
> otherwise it is treated as a predefined-query ID.

**Path params:**
- `:query` — a 36-character UUID of a built-in query

**Query params:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `params` | JSON string | Variables to substitute into the query |
| `subject` | JSON string | Request subject context |
| `baseuri` | string | Base URI for resolving relative paths |
| `envelope` | `"true"` / `"false"` | Wrap the response in envelope format |

```bash
# Arbitrary JSONata — POST, expression wrapped in parentheses
curl -X POST "http://localhost:8080/seaf-core/api/core/storage/jsonata" \
  -H "Content-Type: application/json" \
  -d '{"query": "($count($keys(`seaf.company.systems`)))"}'
```

**Response:** JSONata query result (any JSON structure).

---

### PUT /seaf-core/api/core/storage/reload
Reload the architecture manifest from disk. Synchronous operation (~7 min on Rosetta).

**Query params:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `secret` | string | Must match `VUE_APP_DOCHUB_RELOAD_SECRET`. If the variable is not set — `secret` is not required |

```bash
# Without secret (if VUE_APP_DOCHUB_RELOAD_SECRET is not set)
curl -X PUT http://localhost:8080/seaf-core/api/core/storage/reload

# With secret
curl -X PUT "http://localhost:8080/seaf-core/api/core/storage/reload?secret=mysecret"
```

**Response 200:**
```json
{ "message": "success" }
```

**Response 403:**
```json
{ "error": "<traceId>: Error reload secret is not valid [...]" }
```

---

### GET /seaf-core/api/core/storage/release-data-profile/:query
Retrieve data by dataset path or inline profile.

**Path params (3 variants):**
- `:query` starts with `/` — dataset path, e.g. `/datasets/myDataset`
- `:query` starts with `{` — inline JSON profile
- Otherwise — base64-encoded compressed profile

**Query params:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `params` | JSON string | Parameters for the dataset |
| `envelope` | `"true"` | Wrap in envelope format |

```bash
# By dataset path
curl "http://localhost:8080/seaf-core/api/core/storage/release-data-profile/%2Fdatasets%2FmyDataset"
```

---

### GET /seaf-core/api/core/storage/user-menu
Navigation menu tree for the portal.

```bash
curl http://localhost:8080/seaf-core/api/core/storage/user-menu
```

**Response:** JSON array of menu items (sections, subsections, links).

---

### GET /seaf-core/api/core/storage/versions
Current manifest and core versions.

```bash
curl http://localhost:8080/seaf-core/api/core/storage/versions
```

**Response:**
```json
{
  "archHash": "2750d9ed630885d6a03f56c408a444bba192917a",
  "coreVersion": "3.28.0",
  "enableChecker": true
}
```

---

### GET /seaf-core/api/core/storage/problems/
List of validation issues found during manifest loading.

```bash
curl http://localhost:8080/seaf-core/api/core/storage/problems/
```

**Response:** Array of problem objects (empty array if no issues).

---

### POST /seaf-core/api/core/storage/put-content/:hash
Save changes to a Bitbucket repository. Used for editing architecture via the UI.

**Path params:**
- `:hash` — md5 hash of the root manifest file path

**Body:**
```json
{
  "content": {
    "path/to/file.yaml": "yaml content here",
    "another/file.json": "{\"key\": \"value\"}"
  }
}
```

---

### GET /seaf-core/api/core/user-info
Current user information (when role model is enabled).

```bash
curl http://localhost:8080/seaf-core/api/core/user-info
```

**Response:** `{ "user": {...} }` or empty response (if roles are not configured).

---

### GET /seaf-core/api/core/user/rights
User permissions. Always returns an empty array in backend mode.

```bash
curl http://localhost:8080/seaf-core/api/core/user/rights
```

**Response:**
```json
[]
```

---

## 3. Search

### GET /seaf-core/api/core/storage/search/searchable-entities
List of entity types available for search.

```bash
curl http://localhost:8080/seaf-core/api/core/storage/search/searchable-entities
```

**Response:** Array of objects `[{ id, title, ... }]`.

---

### GET /seaf-core/api/core/storage/search/entity-filters
List of available filters for a given entity type.

**Query params:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `choice` | string | yes | Entity type ID from `/searchable-entities` |

```bash
curl "http://localhost:8080/seaf-core/api/core/storage/search/entity-filters?choice=components"
```

**Response:** Array of filters `[{ field, type, ... }]`.  
Returns `400` if `choice` is not provided.

---

### GET /seaf-core/api/core/storage/search/rel-suggestions
Value suggestions for a "relation" type filter.

**Query params:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `relTarget` | string | yes | Target relation entity |
| `query` | string | yes | Search text (min. 1 character) |

```bash
curl "http://localhost:8080/seaf-core/api/core/storage/search/rel-suggestions?relTarget=components&query=seaf"
```

**Response:** Array of suggestions.

---

### POST /seaf-core/api/core/storage/search/search-run
Execute a search across manifest entities.

**Body:**
```json
{
  "choice": "components",
  "filters": [],
  "searchQuery": "gateway"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `choice` | string | yes | Entity type ID or `"$all"` to search across all types |
| `filters` | array | yes | List of active filters (may be empty) |
| `searchQuery` | string | no | Full-text search query |

```bash
curl -X POST http://localhost:8080/seaf-core/api/core/storage/search/search-run \
  -H "Content-Type: application/json" \
  -d '{"choice": "components", "filters": [], "searchQuery": "gateway"}'
```

**Response:** Array of matched entities.

---

## 4. Storage Proxy

### GET /seaf-core/api/core/storage/:hash/*
Proxy request to a file in the storage backend (GitLab, Bitbucket, filesystem).

**Path params:**
- `:hash` — md5 hash of the file URI in the manifest (from `storage.md5Map`)
- `*` — relative path to the file

**Query params:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `envelope` | `"true"` | Wrap file contents in envelope format |
| `inlineContent` | any | Use only baseURL without uri |

Primarily used by the frontend to load architecture resources. Rarely called directly.

---

## 5. Entity Presentations

### GET /seaf-core/api/entities/:entity/presentations/:presentation
Render an entity presentation. Only `upload` type is supported in backend mode.

**Path params:**
- `:entity` — entity type ID from the manifest (e.g. `seaf.ta.services`)
- `:presentation` — presentation ID (e.g. `default`, `plantuml`)

**Query params:** Depend on the `params` schema of the specific presentation in the manifest.

```bash
# Example for a custom entity
curl "http://localhost:8080/seaf-core/api/entities/seaf.ta.services/presentations/default"
```

**Response:** JSON data or text (according to `mimetype` in the presentation definition).  
`404` if entity or presentation is not found.  
`405` if presentation type is not `upload`.

---

## 6. SmartAnts

### GET /seaf-core/api/smartants/:data
Request to the SmartAnts engine for diagram rendering.

**Path params:**
- `:data` — base64-encoded + compressed JSON request (generated by the frontend)

```bash
# Used only by the frontend; direct calls are non-trivial
```

**Response 200:** `{ "result": "OK", ... }`  
**Response 503:** `{ "message": "Smartants Error", ... }`

---

## 7. Logger

All logger endpoints are protected by the `VUE_APP_DOCHUB_RELOAD_SECRET` secret.

### PUT /seaf-core/api/logger/update-level
Change the logging level without restarting the server.

**Query params:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `secret` | string | Secret from env |
| `level` | string | Level: `trace`, `debug`, `info`, `warn`, `error` |

```bash
curl -X PUT "http://localhost:8080/seaf-core/api/logger/update-level?level=debug"
```

---

### GET /seaf-core/api/logger/level
Get the current logging level.

```bash
curl "http://localhost:8080/seaf-core/api/logger/level"
```

**Response:** `{ "level": "info" }`

---

## 8. GigaChat (AI Assistant)

Requires GigaChat API connection via environment variables.

### GET /seaf-core/api/new/chat/agents
List of available AI agents.

```bash
curl http://localhost:8080/seaf-core/api/new/chat/agents
```

---

### POST /seaf-core/api/new/chat/start
Start a new chat session.

**Body:**
```json
{
  "sessionId": "optional-custom-session-id",
  "type": "AgentType",
  "systemPrompt": "You are an architecture assistant...",
  "config": {}
}
```

**Response:** `{ "sessionId": "uuid" }`

---

### POST /seaf-core/api/new/chat/message
Send a message to a session. Response is a Server-Sent Events stream.

**Body:**
```json
{
  "sessionId": "uuid",
  "message": "Which components use Kafka?"
}
```

**Response:** `Content-Type: text/event-stream`  
Chunks: `data: {...}\n\n`  
End of stream: `data: [DONE]\n\n`

---

### POST /seaf-core/api/new/chat/end
End a chat session and release resources.

**Body:**
```json
{ "sessionId": "uuid" }
```

---

## Useful JSONata Queries

All via `POST /seaf-core/api/core/storage/jsonata` with body `{"query": "(...)"}`
(free-form GET is rejected with 403):

```bash
# All top-level manifest keys
curl -X POST "http://localhost:8080/seaf-core/api/core/storage/jsonata" \
  -H "Content-Type: application/json" -d '{"query": "($keys($))"}'

# Count of systems
curl -X POST "http://localhost:8080/seaf-core/api/core/storage/jsonata" \
  -H "Content-Type: application/json" -d '{"query": "($count($keys(`seaf.company.systems`)))"}'

# One system by ID
curl -X POST "http://localhost:8080/seaf-core/api/core/storage/jsonata" \
  -H "Content-Type: application/json" \
  -d '{"query": "($lookup(`seaf.company.systems`, \"example.app_system.hotel_booking\"))"}'
```

