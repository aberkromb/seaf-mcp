# Фикстуры контракта release-data-profile

Источник: развёрнутый портал SEAF ArchTool, coreVersion 2026.31.0.

Все запросы: `POST /seaf-core/api/core/storage/release-data-profile`,
заголовок `Content-Type: application/json`. GET на тот же путь — 403 `{"error":"Access denied"}`.

Персональные данные в `people_success.json` заменены вымышленными; структура полей и типы сохранены.
Стек-трейсы из тел ошибок удалены (на портале поле `error.stack` присутствует).
