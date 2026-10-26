# Справочник метамодели SEAF (кратко, для запросов через MCP)

Источник: каталог метамодели SEAF (статическая копия). При расхождении приоритет у схемы (`describe_section`).

## Как устроены данные
- Корень манифеста — объект с разделами `seaf.company.*`. Каждый раздел — словарь `ID объекта → атрибуты`.
- Связь — атрибут, значение которого равно ID другого объекта (формально — `$ref: "#/$rels/..."` в схеме).
- **Направление хранения важно.** Связь часто хранится на «другом» конце: компоненты системы хранят `realizes_system`, владельцы — в `stakeholders[].name` объекта. Для вопросов «что ссылается на X» используйте `get_relations` (incoming).
- Интеграции систем в этих данных хранятся **внутри системы**: `seaf.company.systems.<id>.integrations.<iNNN>.service` (сервис поставщика). Отдельного раздела `seaf.company.systems.integrations` может не быть.
- Пакет технической архитектуры (ТА, `seaf.company.ta.*`) **отключён** — этих данных нет. Ссылки `seaf.ta.*` в артефактах — устаревший префикс.
- `seaf.company.ai.agents` — устаревшая сущность, новая — `seaf.company.ai.solutions`.

## Отношения (смысл)
**Бизнес:** продукт → ориентирован на клиентов (`target_clients`), распространяется через каналы (`distributed_via`); процесс → производит продукты, использует сервисы, оперирует бизнес-терминами; канал → обслуживает клиентов, реализован в системах.

**Прикладная:** компонент → реализует систему (`realizes_system`); система → реализует функции (`realizes_functions`), предоставляет сервисы, входит в систему (`is_part_of`), использует технологии; интеграция → использует сервис.

**Данные:** логическая сущность → входит в агрегат (`aggregat`), раскрывает бизнес-термины, мастер-система (`master_system`); логическая связь → связывает сущности (`entity_a`, `entity_b`); физическая таблица → реализует логическую сущность, находится в системе.

**ИИ:** ИИ-решение → использует модели, реализовано в системах, используется в процессах, создаётся в рамках Change-активности.

**Изменения:** активность → реализует цель; артефакт → относится к активности, описывает объекты.

## Разделы
| Раздел | Назначение |
|---|---|
| `seaf.company.ba.clients` | Группы получателей продуктов и услуг |
| `seaf.company.ba.channels` | Точки взаимодействия с клиентом |
| `seaf.company.ba.products` | Продукты компании |
| `seaf.company.ba.processes` | Бизнес-процессы |
| `seaf.company.systems` | Автоматизированные системы (реестр, критичность, жизненный цикл, интеграции) |
| `seaf.company.services` | Сервисы/API, предоставляемые системами |
| `seaf.company.sys_components` | Компоненты систем (C4 Container) |
| `seaf.company.functions` | Справочник функций |
| `seaf.company.da.aggregats` | Иерархия доменов данных |
| `seaf.company.da.business_terms` | Глоссарий |
| `seaf.company.da.logical_entities` | Логические сущности данных |
| `seaf.company.da.logical_links` | Связи логических сущностей |
| `seaf.company.da.physical_tables` | Физические таблицы |
| `seaf.company.ai.models` / `ai.solutions` | ИИ-модели / ИИ-решения |
| `seaf.company.change.goals` / `change.activities` | Цели / инициативы |
| `seaf.company.categories` | Классификатор |
| `seaf.company.technologies` | Технологический радар |
| `seaf.company.artefacts` | ADR, концепции, паттерны |
| `seaf.company.people` | Люди (владельцы, стейкхолдеры) |

## Порядок работы агента
1. `list_sections` — какие разделы реально есть и сколько в них объектов.
2. `inspect_sections` / `describe_section` — структура и примеры перед написанием JSONata.
3. `find_entities` — найти ID по названию.
4. `get_entity` / `get_relations` — атрибуты и связи.
5. `list_entities` — фильтры по полям (eq/contains/missing) без JSONata.
6. `describe_query_result` — разведка формы результата перед большим запросом.
7. `seaf_jsonata_query` — произвольные выборки по манифесту; `query_dataset` — выборки по датасету. Не запрашивайте весь манифест (`$`): preflight такой запрос отклонит.

## JSONata на портале SEAF (проверено на 2026.31.0)

### Чек-лист перед вызовом
- Выражение из нескольких операторов — только внутри блока `( ...; )`. `;` вне блока → S0201.
- Тело `function` из нескольких выражений — оборачивать: `function($x){( $y := $x * 2; $y + 1 )}`.
- Объект-литерал как тело `function` — тоже в скобках: `function($x){( {"k": v} )}` или двойной брейс `function($x){{...}}`; без скобок `function($x){"k": v}` падает с S0202.
- Ключи объекта — строковые литералы: `{"title": $}`; `{title: $}` молча даст `{}`.
- `$group`, `$take`, `$slice`, `$values` не существуют (T1006) — полифиллы ниже.
- Операторы `~` (S0204) и `?.` (S0211) не поддерживаются; `??` работает только для undefined (`null` не коалесцит).

### Форма коллекций: объект объектов vs массив
Раздел — ОБЪЕКТ `{id: запись}`, не массив. Отсюда следуют основные паттерны:

```jsonata
( $p := $."seaf.company.people";
  $keys($p) )                                   /* все ID */
```
```jsonata
( $sift($."seaf.company.people", function($v){$contains($string($v.department), "разработ")})
  ~> $keys() ~> $count() )                      /* $sift возвращает ОБЪЕКТ, не массив → 66 */
```
- Итерация по объекту: `$each($p, function($v, $k){...})` → массив.
- Объект → массив записей: `$map($keys($p), function($k){$lookup($p, $k)})`.
- Доступ по имени из переменной — только `$lookup($p, $k)`, а не `$p.$k`.
- Имена с точками в обратных кавычках: `` $.`seaf.company.systems` `` — работают в путях;
  в конструкторе объекта backtick берётся литерально, там используйте строки.

### Двухшаговый поиск по имени
Сначала разведка ID (дешёво), потом точная выборка:
```jsonata
( $keys($."seaf.company.systems")[$contains($, "hotel_booking")][[0..2]] )
/* → ["example.app_system.hotel_booking.fiscalization", ...] */
```
```jsonata
( $lookup($."seaf.company.systems", "example.app_system.hotel_booking").title )
```

### JOIN через $lookup
Связь — это ID в поле; подтянуть запись — `$lookup(раздел, id)`:
```jsonata
( $s := $lookup($."seaf.company.systems", "example.app_system.hotel_booking");
  $sh := $s.stakeholders[[0]];
  {"name_id": $sh.name, "title": $lookup($."seaf.company.people", $sh.name).title} )
/* → {"name_id": "example.person.ivan_petrov", "title": "Петров Иван"} */
```
Массив ID — маппинг: `$map($s.stakeholders.name, function($id){$lookup($p, $id).title})`.

### Группировка — только по массиву
Конструктор объекта НЕ итерирует объект объектов (молча вернёт пустоту). Сначала `$map`:
```jsonata
( $arr := $map($keys($."seaf.company.people"), function($k){$lookup($."seaf.company.people", $k)});
  $g := $arr{ department : 1 };
  $map($keys($g), function($d){{"dept": $d, "n": $lookup($g, $d)}})[[0..2]] )
```
Вычисляемый ключ — любое строковое выражение: `$arr{ "dept_" & department : $ }`.
Литеральный ключ `$arr{ "dept" : $ }` сгруппирует всё в одну группу «dept».

### Часть массива — индексный фильтр [[a..b]]
```jsonata
( $keys($."seaf.company.systems")[[0..2]] )     /* первые 3 */
```
Конструктор `[0..5]` и фильтр `[[a..b]]` работают; позиционный `$x[a..b]` и slice `$x[a:b]` — нет (S0202).

### Полифиллы отсутствующих функций
```jsonata
( $take := function($a, $n){$a[[0..$n-1]]};
  $take($keys($."seaf.company.people"), 2) )    /* первые n */
```
```jsonata
( $each($lookup($."seaf.company.people", "example.person.ivan_petrov"), function($v, $k){$k}) )
                                                /* $values/$keys одной записи */
```
```jsonata
( $prefix := "example.app";
  $keys($."seaf.company.systems")[$substring($, 0, $length($prefix)) = $prefix][[0..1]] )
                                                /* $startswith */
```

### Ловушки (молчаливые или с кодом ошибки)
| Приём | Результат | Замена |
|---|---|---|
| `$x ~> $count($)` | T0410: `$` в цепочке — КОРНЕВОЙ контекст | `$count($x)` или `$x ~> $count()` |
| `$lookup("раздел", k)` | молча undefined (пустое тело, HTTP 200) | `$lookup($, "раздел")` |
| `{field: v}` | молча `{}` | `{"field": v}` |
| `function($x){"k": v}` | S0202: `{` — тело функции | `function($x){( {"k": v} )}` или `{{...}}` |
| `$x[a..b]`, `$x[a:b]` | S0202 | `$x[[a..b]]` |
| `"a" ~ "b"`, `$x?.a` | S0204 / S0211 | `$string`, обычный путь + `$exists` |
| `?? ` | работает, но `null` не коалесцит | тернарный `$exists($x) ? $x : d` |
| пустой ответ | HTTP 200 + пустое тело = undefined | не ошибка; проверяйте `$exists` |

### Контекст и массивы
- Внутри фильтра `[...]` и `$each` контекст — текущий элемент, а не корень. Обращайтесь к разделам через переменную, заданную заранее: `$S := $$.\`seaf.company.systems\`; $keys($S)[...]`.
- Многие связи — массивы: используйте `"id" in поле`, а не `поле = "id"`.

### Практика
- Перед большим запросом: `describe_query_result` (тип, count, поля по выборке 100).
- Preflight сервера отклонит до вызова: голый `$`, `~> $count($)`, позиционный диапазон, slice, `$lookup("строка", …)`, объект-литерал как тело `function`, необъявленные `$take/$slice/$group/$values`.
- Ошибки S0xxx/T0xxx приходят с подсказкой; детерминированные — исправляйте выражение, повтор не поможет.
