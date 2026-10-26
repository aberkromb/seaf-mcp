"""Index of JSONata examples from a local SEAF metamodel repository.

Enabled when SEAF_METAMODEL_PATH points at the repo; without it
find_jsonata_examples keeps working on the built-in cookbook (016).

Safety: only *.yaml inside the configured directory is read via
yaml.safe_load; symlinks escaping the directory are not followed; foreign
(kadzo.*, seaf.company.ds.kadzo) and __dev__ modules are excluded; file
count and size are capped. One bad file never breaks the whole index.
The index is cached and rebuilt when file mtimes change.
"""

import logging
import os
import re

import yaml

logger = logging.getLogger(__name__)

SECTION_RE = re.compile(r"seaf\.company\.[a-z0-9_]+(?:\.[a-z0-9_]+)*")

DEFAULT_EXCLUDED_DIRS = ("__dev__",)
DEFAULT_EXCLUDED_ID_PREFIXES = (
    "kadzo.", "seaf.company.ds.kadzo",
    "eco_facades.", "docs.kadzo.", "to_kadzo",  # фасады/экспорт чужого модуля
)


class MetamodelIndex:
    def __init__(self, path: str, max_files: int = 2000,
                 max_file_bytes: int = 1_000_000,
                 max_expression_chars: int = 10_000,
                 excluded_dirs: tuple = DEFAULT_EXCLUDED_DIRS,
                 excluded_id_prefixes: tuple = DEFAULT_EXCLUDED_ID_PREFIXES):
        self.path = path
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_expression_chars = max_expression_chars
        self.excluded_dirs = frozenset(excluded_dirs)
        self.excluded_id_prefixes = tuple(excluded_id_prefixes)
        self._fingerprint = None
        self._entries: list[dict] = []
        self.skipped_files = 0

    @property
    def available(self) -> bool:
        return bool(self.path) and os.path.isdir(self.path)

    def entries(self) -> list[dict]:
        """Cached entries; rebuilt when any file mtime changes."""
        if not self.available:
            return []
        fingerprint = self._scan_files()
        if fingerprint != self._fingerprint:
            self._entries = self._extract(fingerprint)
            self._fingerprint = fingerprint
            logger.info(f"Metamodel index rebuilt: {len(self._entries)} examples "
                        f"from {len(fingerprint)} files ({self.path})")
        return self._entries

    def _scan_files(self) -> tuple:
        """(relpath, mtime) for every candidate YAML, capped at max_files."""
        root = os.path.realpath(self.path)
        found = []
        for dirpath, dirnames, filenames in os.walk(self.path, followlinks=False):
            dirnames[:] = [d for d in dirnames
                           if d not in self.excluded_dirs
                           and not os.path.islink(os.path.join(dirpath, d))]
            for name in filenames:
                if not name.endswith((".yaml", ".yml")):
                    continue
                full = os.path.join(dirpath, name)
                if os.path.islink(full) and not os.path.realpath(full).startswith(root + os.sep):
                    continue
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                if stat.st_size > self.max_file_bytes:
                    continue
                found.append((os.path.relpath(full, self.path), stat.st_mtime_ns))
                if len(found) >= self.max_files:
                    return tuple(sorted(found))
        return tuple(sorted(found))

    def _extract(self, fingerprint: tuple) -> list[dict]:
        entries = []
        self.skipped_files = 0
        for relpath, _ in fingerprint:
            full = os.path.join(self.path, relpath)
            try:
                with open(full, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
                self.skipped_files += 1
                logger.warning(f"Skipping unreadable YAML {relpath}: {type(e).__name__}")
                continue
            if isinstance(data, dict):
                self._walk(data, relpath, [], entries)
        return entries

    def _excluded_id(self, text: str) -> bool:
        return any(text.startswith(p) for p in self.excluded_id_prefixes)

    def _walk(self, node, relpath: str, stack: list, entries: list) -> None:
        if isinstance(node, dict):
            source = node.get("source")
            if isinstance(source, str) and source.strip():
                if len(source) > self.max_expression_chars:
                    return  # сгенерированные гиганты — плохой пример для агента
                origin = node.get("origin")
                context = ".".join(stack[-2:]) if stack else relpath
                ids = [str(origin)] if origin else []
                ids += stack + SECTION_RE.findall(source)
                if any(self._excluded_id(i) for i in ids):
                    return
                title = node.get("title")
                entries.append({
                    "id": f"yaml:{relpath}#{context}",
                    "question": str(title) if title else context,
                    "group": "metamodel",
                    "sections": sorted(set(SECTION_RE.findall(source))),
                    "expression": source.strip(),
                    "explanation": f"Из метамодели SEAF ({relpath}, {context}); "
                                   "может требовать $params — это фрагмент представления/датасета, "
                                   "адаптируйте под свой запрос",
                    "source_file": relpath,
                    "origin": origin,
                })
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    self._walk(value, relpath, stack + [str(key)], entries)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    self._walk(item, relpath, stack, entries)
