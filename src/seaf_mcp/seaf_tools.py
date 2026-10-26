"""High-level SEAF tools built on JSONata templates (Stage 4).

User input is always embedded as a JSON string literal (json.dumps), which is
also a valid JSONata string literal, so it cannot alter the query structure.
"""

import asyncio
import json
import logging
import re
from importlib import resources
from typing import Any, Awaitable, Callable

from .describe_query import build_analyze_query
from .jsonata_preflight import preflight
from .metamodel_index import MetamodelIndex
from .relation_paths import build_section_graph, find_paths

logger = logging.getLogger(__name__)

SECTION_PREFIX = "seaf.company."
GUIDE_URI = "seaf://metamodel-guide"

DATASET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DATASET_ID_MAX_LEN = 200
# Служебные датасеты (seaf.ds.*, seaf.toolkit.ds.*) — фактически вся модель (~20 MB),
# поэтому get_dataset по умолчанию разрешает только прикладные seaf.company.ds.*.
DATASET_ALLOWED_PREFIX = "seaf.company.ds."
DATASET_ALIAS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Короткие описания разделов (по каталогу метамодели SEAF).
SECTION_DESCRIPTIONS: dict[str, str] = {
    "seaf.company.ba.clients": "Клиенты: группы внутренних/внешних получателей продуктов и услуг.",
    "seaf.company.ba.channels": "Каналы: точки взаимодействия с клиентом (цифровые, физические).",
    "seaf.company.ba.products": "Продукты: что компания предлагает клиенту.",
    "seaf.company.ba.processes": "Процессы: повторяемая деятельность бизнеса, производит продукты, использует сервисы.",
    "seaf.company.systems": "Автоматизированные системы: реестр ИТ-систем. Интеграции хранятся внутри системы в поле integrations.",
    "seaf.company.services": "Сервисы: опубликованная функциональность/контракт (API), предоставляется системой.",
    "seaf.company.sys_components": "Компоненты систем (уровень C4 Container); связь с системой хранится в компоненте: realizes_system.",
    "seaf.company.functions": "Функции: справочник прикладной функциональности, реализуется системами.",
    "seaf.company.app.diagrams.asis": "Служебное: сохранённые представления AS-IS прикладного ландшафта.",
    "seaf.company.da.aggregats": "Агрегаты: иерархия доменов/пакетов данных.",
    "seaf.company.da.business_terms": "Бизнес-термины: глоссарий.",
    "seaf.company.da.logical_entities": "Логические сущности данных; master_system — мастер-система.",
    "seaf.company.da.logical_links": "Логические связи между логическими сущностями (entity_a / entity_b).",
    "seaf.company.da.physical_tables": "Физические таблицы, реализуют логические сущности, находятся в системе.",
    "seaf.company.ai.models": "ИИ-модели: реестр ML/GenAI-моделей.",
    "seaf.company.ai.solutions": "ИИ-решения: прикладное использование ИИ.",
    "seaf.company.ai.agents": "ИИ-агенты — УСТАРЕВШАЯ сущность, используйте ai.solutions.",
    "seaf.company.change.goals": "Цели изменений.",
    "seaf.company.change.activities": "Change-активности: инициативы/проекты, реализуют цели.",
    "seaf.company.categories": "Категории: универсальная древовидная классификация.",
    "seaf.company.technologies": "Технологии: технологический радар, используются системами и компонентами.",
    "seaf.company.artefacts": "Артефакты: ADR, концепции, паттерны, отчёты.",
    "seaf.company.people": "Люди: владельцы и стейкхолдеры (ссылки через stakeholders.name и др.).",
}

STRUCTURAL_KEYS = {
    "properties", "items", "allOf", "anyOf", "oneOf", "patternProperties",
    "additionalProperties", "if", "then", "else", "not",
}


def lit(value: Any) -> str:
    """Encode a Python value as a JSONata literal."""
    return json.dumps(value, ensure_ascii=False)


def load_guide() -> str:
    return resources.files("seaf_mcp").joinpath("data/metamodel_guide.md").read_text("utf-8")


def load_cookbook() -> list[dict]:
    return json.loads(
        resources.files("seaf_mcp").joinpath("data/jsonata_cookbook.json").read_text("utf-8"))


def _normalize_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-zа-я]+", text.lower().replace("ё", "е")) if w]


def _word_matches(query_word: str, text_word: str) -> bool:
    """Prefix match of at least 5 chars (or the whole word if shorter):
    «системы» finds «систем»."""
    if min(len(query_word), len(text_word)) >= 5:
        return query_word[:5] == text_word[:5]
    return query_word == text_word


def search_cookbook(entries: list[dict], queries: list[str],
                    limit: int = 5) -> list[dict]:
    """Deterministic keyword search over cookbook entries.

    Ranking: exact section-ID hit in the raw query first, then the number of
    distinct matched query words, then the entry ID.
    """
    query_words = []
    for q in queries:
        query_words.extend(_normalize_words(str(q)))
    if not query_words:
        return []
    raw = " ".join(str(q).lower().replace("ё", "е") for q in queries)
    scored = []
    for entry in entries:
        haystack = " ".join([entry.get("question", ""), entry.get("group", ""),
                             " ".join(entry.get("sections", [])),
                             entry.get("expression", ""),
                             entry.get("explanation", "")])
        words = _normalize_words(haystack)
        matched = {qw for qw in query_words
                   if any(_word_matches(qw, w) for w in words)}
        if not matched:
            continue
        section_hit = any(sec.lower() in raw for sec in entry.get("sections", []))
        scored.append((not section_hit, -len(matched), entry["id"], entry))
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored[:limit]]


# --------------------------------------------------------------------------
# Relation map from $manifestschema()
# --------------------------------------------------------------------------

def extract_relations(schema: dict) -> dict[str, list[dict[str, str]]]:
    """Build {section: [{"field": "a.b", "target": section}]} from $rels refs."""
    defs = schema.get("$defs", {}) or {}
    result: dict[str, list[dict[str, str]]] = {}

    def walk(node: Any, path: tuple[str, ...], out: set, seen: frozenset):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                if ref.startswith("#/$rels/"):
                    out.add((".".join(path), ref[len("#/$rels/"):].rsplit(".", 1)[0]))
                elif ref.startswith("#/$defs/") and ref not in seen:
                    walk(defs.get(ref[len("#/$defs/"):]), path, out, seen | {ref})
            for key, val in node.items():
                if key.startswith("$") or not isinstance(val, (dict, list)):
                    continue  # $ref handled above; local $defs are not attributes
                if key in STRUCTURAL_KEYS or key.startswith("^"):
                    walk(val, path, out, seen)
                else:
                    walk(val, path + (key,), out, seen)
        elif isinstance(node, list):
            for val in node:
                walk(val, path, out, seen)

    for section, node in (schema.get("properties") or {}).items():
        out: set = set()
        walk(node, (), out, frozenset())
        rels = [{"field": f, "target": t} for f, t in sorted(out) if f]
        if rels:
            result[section] = rels
    return result


def values_at(obj: Any, path: list[str]) -> list[Any]:
    """Collect leaf values at a dotted path; lists are flattened, and map-like
    objects (patternProperties, e.g. integrations.i001) are traversed."""
    if not path:
        if isinstance(obj, list):
            return [v for item in obj for v in values_at(item, [])]
        return [obj]
    if isinstance(obj, list):
        return [v for item in obj for v in values_at(item, path)]
    if isinstance(obj, dict):
        head, rest = path[0], path[1:]
        if head in obj:
            return values_at(obj[head], rest)
        return [v for item in obj.values() if isinstance(item, dict) for v in values_at(item, path)]
    return []


def outgoing_links(entity: dict, rels: list[dict[str, str]]) -> list[dict[str, str]]:
    links = []
    for rel in rels:
        for value in values_at(entity, rel["field"].split(".")):
            if isinstance(value, str) and value:
                links.append({"field": rel["field"], "target_section": rel["target"], "id": value})
    return links


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

RunQuery = Callable[[str], Awaitable[Any]]
RunDataset = Callable[[str, dict[str, Any] | None], Awaitable[Any]]
RunDatasetInline = Callable[[str | dict, str, dict[str, Any] | None], Awaitable[Any]]


class SeafTools:
    def __init__(self, run_query: RunQuery, run_dataset: RunDataset | None = None,
                 run_dataset_inline: RunDatasetInline | None = None,
                 max_chars: int = 50000, metamodel_path: str = ""):
        self.run_query = run_query
        self.run_dataset = run_dataset
        self.run_dataset_inline = run_dataset_inline
        self.max_chars = max_chars
        self._relations: dict[str, list[dict[str, str]]] | None = None
        self._cookbook: list[dict] | None = None
        self._metamodel = MetamodelIndex(metamodel_path) if metamodel_path else None
        self._lock = asyncio.Lock()

    async def relations(self) -> dict[str, list[dict[str, str]]]:
        async with self._lock:
            if self._relations is None:
                schema = await self.run_query("$manifestschema()")
                self._relations = extract_relations(schema or {})
                logger.info(f"Relation map built for {len(self._relations)} sections")
            return self._relations

    # --- handlers ---------------------------------------------------------

    async def list_sections(self, args: dict) -> Any:
        q = (f'$keys($)[$substring($,0,{len(SECTION_PREFIX)})={lit(SECTION_PREFIX)}]'
             '.{"section":$, "count":$count($keys($lookup($$,$)))}')
        rows = _as_list(await self.run_query(q))
        for row in rows:
            row["description"] = SECTION_DESCRIPTIONS.get(row["section"], "")
        return rows

    async def describe_section(self, args: dict) -> Any:
        section = args["section"]
        q = (f'$o:=$lookup($,{lit(section)}); $ks:=$keys($o); '
             '{"count":$count($ks),"samples":[$ks[[0..2]]],"attrs":[$ks.$keys($lookup($o,$))]}')
        data = await self.run_query(q)
        if not data or not data.get("count"):
            return {"error": f"Section '{section}' not found or empty. Use list_sections."}
        freq: dict[str, int] = {}
        for attr in data.get("attrs") or []:
            freq[attr] = freq.get(attr, 0) + 1
        relations = (await self.relations()).get(section, [])
        incoming = [
            {"from_section": src, "field": r["field"]}
            for src, rels in (await self.relations()).items()
            for r in rels if r["target"] == section and src != section
        ]
        return {
            "section": section,
            "description": SECTION_DESCRIPTIONS.get(section, ""),
            "count": data["count"],
            "attributes": dict(sorted(freq.items(), key=lambda kv: -kv[1])),
            "outgoing_relation_fields": relations,
            "incoming_relation_fields": incoming,
            "sample_ids": data.get("samples") or [],
        }

    async def get_entity(self, args: dict) -> Any:
        q = (f'$id:={lit(args["id"])}; '
             f'$keys($)[$substring($,0,{len(SECTION_PREFIX)})={lit(SECTION_PREFIX)}]'
             '[$exists($lookup($lookup($$,$),$id))]'
             '.{"section":$,"entity":$lookup($lookup($$,$),$id)}')
        rows = _as_list(await self.run_query(q))
        if not rows:
            return {"error": f"Entity '{args['id']}' not found. Use find_entities."}
        return [{"id": args["id"], **row} for row in rows] if len(rows) > 1 else {"id": args["id"], **rows[0]}

    async def find_entities(self, args: dict) -> Any:
        text = args["text"]
        limit = max(1, min(int(args.get("limit") or 20), 200))
        section = args.get("section")
        secs = (f"[{lit(section)}]" if section else
                f'$keys($)[$substring($,0,{len(SECTION_PREFIX)})={lit(SECTION_PREFIX)}]')
        q = (f'$t:=$lowercase({lit(text)}); $secs:={secs}; '
             '$r:=[$secs.($s:=$; $o:=$lookup($$,$s); $keys($o)['
             '$contains($lowercase($),$t) '
             'or $contains($lowercase($string($lookup($o,$).title)),$t) '
             'or $contains($lowercase($string($lookup($o,$).description)),$t)'
             '].{"section":$s,"id":$,"title":$lookup($o,$).title})]; '
             f'{{"total":$count($r),"items":[$r[[0..{limit - 1}]]]}}')
        return await self.run_query(q)

    async def titles(self, ids: list[str]) -> dict[str, Any]:
        """Resolve titles for many IDs in one query."""
        ids = sorted({i for i in ids if isinstance(i, str)})
        if not ids:
            return {}
        q = (f'$ids:={lit(ids)}; $merge([$keys($)[$substring($,0,{len(SECTION_PREFIX)})={lit(SECTION_PREFIX)}]'
             '.($o:=$lookup($$,$); $ids[$exists($lookup($o,$))].{$: $lookup($o,$).title})])')
        return await self.run_query(q) or {}

    async def get_relations(self, args: dict) -> Any:
        entity_id = args["id"]
        direction = args.get("direction") or "both"
        field_filter = args.get("field")
        limit = max(1, min(int(args.get("limit") or 20), 500))
        relmap = await self.relations()
        found = await self.get_entity({"id": entity_id})
        if isinstance(found, dict) and "error" in found:
            return found
        rows = found if isinstance(found, list) else [found]

        outgoing: list[dict] = []
        if direction in ("out", "both"):
            for row in rows:
                outgoing += outgoing_links(row["entity"] or {}, relmap.get(row["section"], []))

        incoming: list[dict] = []
        if direction in ("in", "both"):
            # Server-side prefilter by substring, exact match locally.
            sources = sorted(relmap)
            q = (f'$id:={lit(entity_id)}; $merge([{lit(sources)}.($s:=$; $o:=$lookup($$,$s); '
                 '{$s: [$keys($o)[$contains($string($lookup($o,$)),$id)]'
                 '.{"id":$,"v":$lookup($o,$)}]})])')
            candidates = await self.run_query(q) or {}
            for src, objs in candidates.items():
                for obj in _as_list(objs):
                    for link in outgoing_links(obj.get("v") or {}, relmap.get(src, [])):
                        if link["id"] == entity_id:
                            incoming.append({"section": src, "id": obj["id"],
                                             "title": (obj.get("v") or {}).get("title"),
                                             "field": link["field"]})

        if field_filter:
            outgoing = [l for l in outgoing if l["field"] == field_filter]
            incoming = [l for l in incoming if l["field"] == field_filter]

        out_groups = _group(outgoing, ("field", "target_section"), "id")
        shown = [i for g in out_groups for i in g["items"][:limit]]
        names = await self.titles(shown)
        for g in out_groups:
            g["items"] = [{"id": i, "title": names.get(i)} for i in g["items"]]
        in_groups = _group(incoming, ("field", "section"), None)

        truncated = False
        for g in [*out_groups, *in_groups]:
            g["count"] = len(g["items"])
            if g["count"] > limit:
                g["items"] = g["items"][:limit]
                truncated = True
        result = {
            "id": entity_id,
            "section": rows[0]["section"],
            "title": (rows[0]["entity"] or {}).get("title"),
            "outgoing": out_groups,
            "incoming": in_groups,
        }
        if truncated:
            result["note"] = (f"Some groups truncated to {limit} items (see count). "
                              "Use field=<name> and a larger limit to get all.")
        return result

    async def list_entities(self, args: dict) -> Any:
        section = args["section"]
        limit = max(1, min(int(args.get("limit") or 50), 500))
        offset = max(0, int(args.get("offset") or 0))
        fields = args.get("fields") or ["title"]
        where = args.get("where") or []
        return await self.run_query(
            build_list_entities_query(section, where, fields, offset, limit))

    async def metamodel_guide(self, args: dict) -> Any:
        return load_guide()

    async def find_relation_paths(self, args: dict) -> Any:
        start, end = args.get("start_section"), args.get("end_section")
        via = args.get("via_sections") or []
        relmap = await self.relations()
        graph = build_section_graph(relmap)
        known = sorted(graph)
        if start == end:
            return {"error": "start_section и end_section совпадают: укажите два разных раздела"}
        for name, value in (("start_section", start), ("end_section", end)):
            if value not in graph:
                return {"error": f"Раздел {value} не найден в карте связей ({name})",
                        "known_sections": known}
        paths, truncated = find_paths(graph, start, end, via)
        if not paths:
            neighbors = sorted({t for _, t, _ in graph.get(start, [])})
            return {"paths": [],
                    "hint": f"Пути до {end} не найдено (≤5 шагов"
                            f"{', через ' + ', '.join(via) if via else ''}). "
                            "Уточните запрос или снимите via_sections.",
                    "start_neighbors": neighbors}
        result = {"paths": paths, "count": len(paths),
                  "note": "JOIN не выполняется: используйте поля шагов, чтобы составить "
                          "запрос (см. find_jsonata_examples → relations.*)"}
        if truncated:
            result["truncated"] = True
        return result

    async def find_jsonata_examples(self, args: dict) -> Any:
        queries = args.get("queries") or []
        limit = args.get("limit", 5)
        try:
            limit = max(1, min(10, int(limit)))
        except (TypeError, ValueError):
            limit = 5
        if self._cookbook is None:
            self._cookbook = load_cookbook()
        if not queries:
            return {"hint": "Пустой queries: передайте ключевые слова, например "
                            "[\"системы\", \"стейкхолдеры\"] или [\"seaf.company.people\"]",
                    "examples": []}
        found = search_cookbook(self._cookbook, queries, limit)
        if self._metamodel is not None and len(found) < limit:
            if self._metamodel.available:
                yaml_hits = search_cookbook(self._metamodel.entries(), queries,
                                            limit - len(found))
                found.extend(yaml_hits)
            else:
                logger.warning(f"SEAF_METAMODEL_PATH недоступен: {self._metamodel.path}")
        if not found:
            return {"hint": "Примеры не найдены: измените термины (попробуйте ID раздела "
                            "или слово из вопроса). Полный справочник приёмов — metamodel_guide.",
                    "examples": []}
        return {"examples": found, "returned": len(found)}

    async def inspect_sections(self, args: dict) -> Any:
        sections = args.get("sections")
        if not isinstance(sections, list) or not sections:
            return {"error": "Invalid sections: expected a non-empty list of 1-5 section IDs."}
        if len(sections) > 5:
            return {"error": f"Too many sections ({len(sections)}): inspect up to 5 per call."}
        if len(set(sections)) != len(sections):
            return {"error": f"Duplicate sections: {sorted({s for s in sections if sections.count(s) > 1})}."}
        if not all(isinstance(s, str) and s for s in sections):
            return {"error": "Invalid sections: every entry must be a non-empty string."}
        raw_limit = args.get("sample_limit")
        if raw_limit is None:
            sample_limit = 2
        else:
            try:
                sample_limit = int(raw_limit)
            except (TypeError, ValueError):
                return {"error": "Invalid sample_limit: expected an integer (1-3, default 2)."}
        if not 1 <= sample_limit <= 3:
            return {"error": "Invalid sample_limit: expected 1-3 (default 2)."}

        effective_max = self.max_chars if self.max_chars > 0 else 50000
        budget = max(500, effective_max // (len(sections) * sample_limit * 2))
        semaphore = asyncio.Semaphore(3)

        async def inspect_one(section: str) -> dict:
            async with semaphore:
                try:
                    info = await self.describe_section({"section": section})
                    if "error" in info:
                        return {"section": section, "error": info["error"]}
                    q = (f'$o:=$lookup($,{lit(section)}); $ks:=$keys($o)[[0..{sample_limit - 1}]]; '
                         '[$ks.$lookup($o, $)]')
                    raw = await self.run_query(q)
                    samples = [_shrink(s, budget) for s in _as_list(raw)]
                    info.pop("sample_ids", None)
                    return {**info, "samples": samples}
                except Exception as e:
                    return {"section": section, "error": f"{type(e).__name__}: {e}"}

        return {"sections": await asyncio.gather(*(inspect_one(s) for s in sections)),
                "note": "samples are capped; use describe_section for one section without samples."}

    async def get_dataset(self, args: dict) -> Any:
        dataset_id = args["dataset_id"]
        id_error = _validate_dataset_id(dataset_id)
        if id_error:
            return {"error": id_error.replace("Invalid dataset id", "Invalid dataset_id")}
        if not dataset_id.startswith(DATASET_ALLOWED_PREFIX):
            return {"error": f"Dataset '{dataset_id}' is outside the allowlist '{DATASET_ALLOWED_PREFIX}*'. "
                             "Service datasets (seaf.ds.*, seaf.toolkit.ds.*) can be tens of MB; "
                             "query them with a filter via query_dataset when it becomes available."}
        params = args.get("params")
        if params is not None and not isinstance(params, dict):
            return {"error": f"Invalid params for '{dataset_id}': expected an object."}
        if self.run_dataset is None:
            return {"error": "Dataset access is not configured."}
        result = await self.run_dataset(dataset_id, params)
        if result is None:
            return {"error": f"Dataset '{dataset_id}' not found: portal returned HTTP 200 "
                             "with an empty (null) response. Verify the dataset ID."}
        return result

    async def query_dataset(self, args: dict) -> Any:
        origin = args.get("origin")
        source = args.get("source")
        params = args.get("params")
        if not isinstance(source, str) or not source.strip():
            return {"error": "Invalid source: expected a non-empty JSONata expression."}
        origin_error = _validate_origin(origin)
        if origin_error:
            return {"error": origin_error}
        if params is not None and not isinstance(params, dict):
            return {"error": "Invalid params: expected an object."}
        if self.run_dataset_inline is None:
            return {"error": "Dataset access is not configured."}
        hint = preflight(source, root_label="the whole dataset (can be tens of MB)")
        if hint:
            return {"error": f"Query preflight: {hint}"}
        result = await self.run_dataset_inline(origin, f"({source.strip()})", params)
        if result is None:
            return {"error": "Query succeeded but returned nothing (undefined): the portal sent "
                             "HTTP 200 with an empty body. Check field names and filters."}
        return result

    async def describe_query_result(self, args: dict) -> Any:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"error": "Invalid query: expected a non-empty JSONata expression."}
        hint = preflight(query)
        if hint:
            return {"error": f"Query preflight: {hint}"}
        result = await self.run_query(build_analyze_query(query))
        if result is None:
            return {"result_type": "missing", "count": 0,
                    "note": "The query succeeded but returned nothing (undefined)."}
        if isinstance(result, dict) and result.get("result_type") == "missing":
            result["note"] = "The query succeeded but returned nothing (undefined)."
        return result

    # --- registry ----------------------------------------------------------

    def definitions(self) -> list[dict[str, Any]]:
        def schema(props: dict, required: list[str]) -> dict:
            return {"type": "object", "properties": props, "required": required,
                    "additionalProperties": False}

        id_prop = {"type": "string", "description": "ID объекта, например example.app_system.hotel_booking"}
        section_prop = {"type": "string", "description": "Раздел, например seaf.company.systems"}
        return [
            {"name": "list_sections", "title": "Разделы модели", "handler": self.list_sections,
             "description": "Когда вызывать: в начале работы или когда неизвестно, в каком разделе искать "
                            "данные. Возвращает разделы seaf.company.* с числом объектов и описанием. "
                            "Дальше: inspect_sections (структура и образцы нескольких разделов) или "
                            "describe_section (один раздел без образцов).",
             "inputSchema": schema({}, [])},
            {"name": "describe_section", "title": "Структура раздела", "handler": self.describe_section,
             "description": "Когда вызывать: нужна структура ОДНОГО раздела без образцов — атрибуты с "
                            "частотой, исходящие/входящие поля-связи, примеры ID. Для образцов данных и "
                            "нескольких разделов сразу используйте inspect_sections. "
                            "Дальше: list_entities (выборка по фильтрам) или describe_query_result "
                            "(разведка перед JSONata).",
             "inputSchema": schema({"section": section_prop}, ["section"])},
            {"name": "get_entity", "title": "Объект по ID", "handler": self.get_entity,
             "description": "Когда вызывать: известен точный ID объекта (например example.app_system.hotel_booking). "
                            "Возвращает раздел и все атрибуты объекта. ID неизвестен — сначала find_entities. "
                            "Дальше: get_relations для связей объекта.",
             "inputSchema": schema({"id": id_prop}, ["id"])},
            {"name": "find_entities", "title": "Поиск объектов", "handler": self.find_entities,
             "description": "Когда вызывать: известно только примерное название объекта (система, сервис, "
                            "человек) — поиск по подстроке в ID/title/description всех разделов (или одного). "
                            "Возвращает total и items {section, id, title}. Когда раздел и условия по полям "
                            "известны точно, эффективнее list_entities. Дальше: get_entity или get_relations "
                            "по найденному id.",
             "inputSchema": schema({
                 "text": {"type": "string", "description": "Подстрока для поиска"},
                 "section": {**section_prop, "description": "Ограничить поиск разделом (опционально)"},
                 "limit": {"type": "integer", "description": "Макс. число результатов (1-200, по умолчанию 20)"},
             }, ["text"])},
            {"name": "list_entities", "title": "Объекты раздела", "handler": self.list_entities,
             "description": "Когда вызывать: нужны объекты раздела с точными фильтрами по полям "
                            "(eq/ne/contains/missing/exists) и пагинацией — выполняется на портале. "
                            "Пример: системы без стейкхолдеров — where=[{field:'stakeholders',op:'missing'}]. "
                            "Поля через точку (stakeholders.name); для массивов eq/contains срабатывает на "
                            "любой элемент; булевы — 'true'/'false'. Не знаете раздел — list_sections; ищете "
                            "по примерному названию — find_entities. Возвращает {section,total,offset,items}. "
                            "Дальше: get_entity по id из items.",
             "inputSchema": schema({
                 "section": section_prop,
                 "where": {"type": "array", "description": "Условия (AND)", "items": {
                     "type": "object", "additionalProperties": False, "required": ["field"],
                     "properties": {
                         "field": {"type": "string"},
                         "op": {"type": "string", "enum": ["eq", "ne", "contains", "missing", "exists"],
                                "description": "по умолчанию eq"},
                         "value": {"type": "string"}}}},
                 "fields": {"type": "array", "items": {"type": "string"},
                            "description": "Возвращаемые поля (по умолчанию title)"},
                 "limit": {"type": "integer", "description": "1-500, по умолчанию 50"},
                 "offset": {"type": "integer"},
             }, ["section"])},
            {"name": "get_relations", "title": "Связи объекта", "handler": self.get_relations,
             "description": "Когда вызывать: нужны связи объекта — outgoing (на что он ссылается) и incoming "
                            "(кто ссылается на него: подсистемы — incoming is_part_of, компоненты — incoming "
                            "realizes_system, таблицы — incoming system). Возвращает группы по полю с count, "
                            "до limit элементов и title. У крупных объектов сужайте direction/field. "
                            "Дальше: get_entity на связанном id.",
             "inputSchema": schema({
                 "id": id_prop,
                 "direction": {"type": "string", "enum": ["in", "out", "both"], "description": "по умолчанию both"},
                 "field": {"type": "string", "description": "Только связи по этому полю, например is_part_of"},
                 "limit": {"type": "integer", "description": "Элементов на группу (по умолчанию 20)"},
             }, ["id"])},
            {"name": "metamodel_guide", "title": "Справочник SEAF", "handler": self.metamodel_guide,
             "description": "Когда вызывать: нужны понятия метамодели SEAF, смысл отношений и проверенные "
                            "приёмы JSONata (чек-лист, паттерны, ловушки). Возвращает текст справочника. "
                            "Дальше: list_sections.",
              "inputSchema": schema({}, [])},
            {"name": "find_relation_paths", "title": "Пути связей разделов", "handler": self.find_relation_paths,
             "description": "Когда вызывать: нужно понять, через какие поля и разделы связаны два раздела "
                            "(например, people и products), прежде чем писать JOIN. Возвращает до 10 "
                            "кратчайших маршрутов (до 5 шагов): каждый шаг — from_section, field, "
                            "to_section, direction (out/in); данные сущностей не возвращаются, JOIN не "
                            "выполняется. via_sections ограничивает промежуточные разделы. "
                            "Дальше: find_jsonata_examples (группа relations) или seaf_jsonata_query.",
             "inputSchema": schema({
                 "start_section": {"type": "string",
                                   "description": "Начальный раздел, например seaf.company.people"},
                 "end_section": {"type": "string",
                                 "description": "Конечный раздел, например seaf.company.ba.products"},
                 "via_sections": {"type": "array", "items": {"type": "string"},
                                  "description": "Разрешённые промежуточные разделы (опционально)"},
             }, ["start_section", "end_section"])},
            {"name": "find_jsonata_examples", "title": "Примеры JSONata", "handler": self.find_jsonata_examples,
             "description": "Когда вызывать: перед написанием JSONata — взять проверенный пример похожего "
                            "запроса (keyword-поиск по вопросу, группе, разделам и тексту выражения; "
                            "русские словоформы совпадают по префиксу). Возвращает до limit (по умолчанию 5) "
                            "примеров {id, question, sections, expression, explanation}; записи с "
                            "template=true требуют подстановки реального ID. При настроенном "
                            "SEAF_METAMODEL_PATH после проверенных примеров идут фрагменты YAML "
                            "метамодели с пометкой файла. Дальше: seaf_jsonata_query "
                            "с адаптированным выражением.",
             "inputSchema": schema({
                 "queries": {"type": "array", "items": {"type": "string"},
                             "description": "Ключевые слова, например [\"системы\", \"стейкхолдеры\"] "
                                            "или [\"seaf.company.people\"]"},
                 "limit": {"type": "integer", "description": "Примеров в выдаче, 1-10 (по умолчанию 5)"},
             }, ["queries"])},
            {"name": "get_dataset", "title": "Датасет целиком", "handler": self.get_dataset,
             "description": "Когда вызывать: нужен именованный датасет представлений ЦЕЛИКОМ (только "
                            "seaf.company.ds.*, например seaf.company.ds.people: записи людей с полями "
                            "title/ФИО, role, department и техническими __id__/__link_to_card__). "
                            "Возвращает объект объектов (ключ — ID записи). Для выборок по датасету и "
                            "служебных seaf.ds.* — query_dataset. Дальше: query_dataset с фильтром.",
             "inputSchema": schema({
                 "dataset_id": {"type": "string",
                                "description": "ID датасета, например seaf.company.ds.people"},
                 "params": {"type": "object",
                            "description": "Параметры датасета (опционально, передаются как есть)"},
             }, ["dataset_id"])},
            {"name": "query_dataset", "title": "Запрос к датасету", "handler": self.query_dataset,
             "description": "Когда вызывать: нужна выборка JSONata поверх именованного датасета (корневой "
                            "$ — данные датасета; для манифеста модели — seaf_jsonata_query). origin — ID "
                            "датасета или {алиас: dataset_id} (тогда $ — мапа, датасет как $.\"алиас\"); "
                            "source оборачивается в (...) автоматически. Возвращает результат выражения; "
                            "доступны любые датасеты, включая seaf.ds.* — сужайте выборку: ответ считается "
                            "на портале целиком. Дальше: seaf_jsonata_query, если данных нет в датасете.",
             "inputSchema": schema({
                 "origin": {"type": ["string", "object"],
                            "description": "ID датасета или {алиас: dataset_id}"},
                 "source": {"type": "string", "description": "Выражение JSONata над датасетом"},
                 "params": {"type": "object",
                            "description": "Параметры датасета (опционально, передаются как есть)"},
             }, ["origin", "source"])},
            {"name": "describe_query_result", "title": "Описание результата запроса", "handler": self.describe_query_result,
             "description": "Когда вызывать: ПЕРЕД seaf_jsonata_query, когда не уверены в форме результата "
                            "запроса. Возвращает тип (array/object/scalar/missing), count по всему "
                            "результату, поля с покрытием present_in k/n по первым 100 элементам и до 3 "
                            "примеров (≤1500 символов каждый); схема строится по выборке — это разведка, "
                            "а не полная схема. Терминальные агрегаты ($count, $sum) — скаляр в value. "
                            "Дальше: seaf_jsonata_query с уточнённым запросом.",
             "inputSchema": schema({
                 "query": {"type": "string", "description": "Выражение JSONata (корневой $ — манифест)"},
                 "query_goal": {"type": "string",
                                "description": "Зачем нужен запрос (опционально, для контекста)"},
             }, ["query"])},
            {"name": "inspect_sections", "title": "Обзор разделов", "handler": self.inspect_sections,
             "description": "Когда вызывать: нужны структура И образцы сразу 1-5 разделов, например перед "
                            "запросом со связями между типами. Возвращает по каждому разделу count, "
                            "атрибуты, поля-связи и до sample_limit (1-3, по умолчанию 2) ограниченных "
                            "образцов; раздел целиком не выгружается. Для одного раздела без образцов "
                             "достаточно describe_section. Дальше: seaf_jsonata_query со связывающим запросом. "
                             "При включённом API_MIN_INTERVAL_SECONDS время ответа растёт пропорционально числу разделов.",
             "inputSchema": schema({
                 "sections": {"type": "array", "items": {"type": "string"},
                              "description": "Разделы (1-5), например [\"seaf.company.systems\"]"},
                 "sample_limit": {"type": "integer",
                                  "description": "Образцов на раздел, 1-3 (по умолчанию 2)"},
             }, ["sections"])},
        ]


def _validate_dataset_id(dataset_id: Any) -> str | None:
    if (not isinstance(dataset_id, str) or len(dataset_id) > DATASET_ID_MAX_LEN
            or not DATASET_ID_RE.fullmatch(dataset_id)):
        return (f"Invalid dataset id '{str(dataset_id)[:50]}': expected [A-Za-z0-9][A-Za-z0-9._-]*, "
                f"up to {DATASET_ID_MAX_LEN} chars.")
    return None


def _validate_origin(origin: Any) -> str | None:
    """origin: dataset id string or a non-empty {alias: dataset_id} map."""
    if isinstance(origin, str):
        return _validate_dataset_id(origin)
    if isinstance(origin, dict):
        if not origin:
            return "Invalid origin: the alias map must not be empty."
        for alias, dataset_id in origin.items():
            if not DATASET_ALIAS_RE.fullmatch(str(alias)):
                return (f"Invalid alias '{alias}': expected a JSONata identifier "
                        "[A-Za-z_][A-Za-z0-9_]*.")
            error = _validate_dataset_id(dataset_id)
            if error:
                return f"Alias '{alias}': {error}"
        return None
    return "Invalid origin: expected a dataset id string or an object {alias: dataset_id}."


_WALK_FN = """$walk := function($obj, $path) {(
  $count($path) = 0 ? (
    $type($obj) = "array" ? $reduce($map($obj, function($i){$walk($i, [])}), function($a,$b){$append($a,$b)}, []) : [$obj]
  ) : (
    $type($obj) = "array" ? $reduce($map($obj, function($i){$walk($i, $path)}), function($a,$b){$append($a,$b)}, []) : (
      $type($obj) = "object" ? (
        $seg := $path[0];
        $rest := $count($path) > 1 ? $path[[1..($count($path)-1)]] : [];
        $exists($lookup($obj, $seg)) ? $walk($lookup($obj, $seg), $rest)
        : $reduce($map($keys($obj), function($k){(
            $vv := $lookup($obj, $k);
            $type($vv) = "object" ? $walk($vv, $path) : []
        )}), function($a,$b){$append($a,$b)}, [])
      ) : []
    )
  )
)}"""

_CLEAN_FN = """$clean := function($vs) { $filter($vs, function($x){(
  $t := $type($x);
  $t = "array" ? $count($x) > 0 : ($t = "object" ? $count($keys($x)) > 0 : ($x != null and $x != ""))
)}) }"""


def build_list_entities_query(section: str, where: list[dict], fields: list[str],
                              offset: int, limit: int, obj_expr: str | None = None) -> str:
    """Server-side filter/projection/pagination query.

    obj_expr: JSONata expression for the section object; default $lookup($, section).
    Values and names are embedded as JSON literals only (lit()).
    Documented divergences from the previous client-side filtering:
    booleans compare as "true"/"false" (not "True"/"False"), floats as "5" (not "5.0"),
    contains on non-strings uses compact JSON ($string, no spaces).
    """
    conds, decls = [], []
    for i, c in enumerate(where):
        if not isinstance(c, dict) or "field" not in c:
            raise ValueError(f"Invalid condition: {c!r}")
        op = c.get("op", "eq")
        decls.append(f'$vs{i} := $clean($walk($v, {lit(str(c["field"]).split("."))}))')
        if op == "missing":
            conds.append(f'$count($vs{i}) = 0')
        elif op == "exists":
            conds.append(f'$count($vs{i}) > 0')
        elif op in ("eq", "ne"):
            cmp_ = f'$count($filter($vs{i}, function($x){{$string($x) = {lit(c.get("value"))}}}))'
            conds.append(f'{cmp_} {"= 0" if op == "ne" else "> 0"}')
        elif op == "contains":
            cmp_ = (f'$count($filter($vs{i}, function($x)'
                    f'{{$contains($lowercase($string($x)), {lit(str(c.get("value")).lower())})}}))')
            conds.append(f'{cmp_} > 0')
        else:
            raise ValueError(f"Unknown op: {op}")
    where_expr = " and ".join(f'({c})' for c in conds) if conds else "true"
    proj = ", ".join(
        f'{lit(f)}: ($f{i} := $walk($v, {lit(f.split("."))}); '
        f'$count($f{i}) = 0 ? null : ($count($f{i}) = 1 ? $f{i}[0] : $f{i}))'
        for i, f in enumerate(fields))
    return (
        f'($o := {obj_expr or f"$lookup($,{lit(section)})"}; '
        f'{_WALK_FN}; {_CLEAN_FN}; '
        f'$exists($o) ? ('
        f'$ks := $keys($o); '
        f'$matched := $filter($ks, function($k){{('
        f'$v := $lookup($o, $k); '
        f'{"; ".join(decls)}{"; " if decls else ""}{where_expr}'
        f')}}); '
        f'$page := $matched[[{offset}..{offset + limit - 1}]]; '
        f'{{"section": {lit(section)}, "total": $count($matched), "offset": {offset}, '
        f'"items": [$map($page, function($k){{('
        f'$v := $lookup($o, $k); '
        f'{{"id": $k{", " + proj if proj else ""}}}'
        f')}})]}}'
        f') : {{"error": "Section " & {lit(chr(39) + section + chr(39))} & " not found. Use list_sections."}})'
    )


def _shrink(obj: Any, budget: int) -> Any:
    """Cap a sample object: long strings, arrays and nested maps are cut with a
    mark; if the JSON form still exceeds budget, hard-cut the serialized text."""
    shrunk = _shrink_value(obj, depth=0)
    text = json.dumps(shrunk, ensure_ascii=False)
    if len(text) > budget:
        return text[:budget] + f"…[cut: sample exceeded {budget} chars]"
    return shrunk


def _shrink_value(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 300 else value[:300] + f"…[cut {len(value) - 300} chars]"
    if isinstance(value, list):
        items = [_shrink_value(v, depth + 1) for v in value[:5]]
        if len(value) > 5:
            items.append(f"…(+{len(value) - 5} more)")
        return items
    if isinstance(value, dict):
        keys = list(value)[:12]
        out = {k: _shrink_value(value[k], depth + 1) for k in keys}
        if len(value) > 12:
            out["…"] = f"+{len(value) - 12} more keys"
        return out
    return value


def _match(entity: Any, cond: dict) -> bool:
    vals = [v for v in values_at(entity, str(cond.get("field", "")).split(".")) if v not in (None, "", [], {})]
    op = cond.get("op", "eq")
    target = cond.get("value")
    if op == "missing":
        return not vals
    if op == "exists":
        return bool(vals)
    if op == "eq":
        return any(str(v) == str(target) for v in vals)
    if op == "ne":
        return all(str(v) != str(target) for v in vals)
    if op == "contains":
        t = str(target).lower()
        return any(t in (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)).lower() for v in vals)
    raise ValueError(f"Unknown op: {op}")


def _as_list(value: Any) -> list:
    """JSONata returns a single item instead of a one-element array."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _group(links: list[dict], keys: tuple[str, ...], value_key: str | None) -> list[dict]:
    """Group links by keys; collect unique values (ids or {id,title}) per group."""
    groups: dict[tuple, dict] = {}
    for link in links:
        k = tuple(link[x] for x in keys)
        g = groups.setdefault(k, {**{x: link[x] for x in keys}, "items": []})
        item = link[value_key] if value_key else {"id": link["id"], "title": link.get("title")}
        if item not in g["items"]:
            g["items"].append(item)
    return list(groups.values())
