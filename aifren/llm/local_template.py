"""Small reviewed capability table for installed embedded chat templates.

No hand templating and no inference. Unknown/missing metadata keeps the legacy
adapter role. Only the initial application policy changes role; canonical and
retrieved message roles are untouched.
"""
import hashlib
from functools import lru_cache
from pathlib import Path

SYSTEM_PREFIX_TEMPLATES = frozenset({
    "55572b8d3c8342044e25874c73fe5234b661fa0a57a57f6ef75b58e03d7d959a",
})


def installed_policy_role(model: str) -> str:
    from aifren.runtime.config import LOCAL_LLM_MODEL_DIR
    root = Path(LOCAL_LLM_MODEL_DIR).resolve()
    path = (root / model).resolve()
    try:
        path.relative_to(root)
        stat = path.stat()
        if path.suffix.lower() != ".gguf" or not path.is_file():
            return "user"
        return _read_role(str(path), stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError):
        return "user"


@lru_cache(maxsize=4)
def _read_role(path: str, size: int, modified: int) -> str:
    try:
        # Optional installed metadata reader: read-only mmap, no tensor load or
        # tokenizer/model allocation. The cache retains only the role string.
        from gguf import GGUFReader
        reader = GGUFReader(path, mode="r")
        field = reader.get_field("tokenizer.chat_template")
        template = field.contents() if field is not None else ""
        digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
        return "system" if digest in SYSTEM_PREFIX_TEMPLATES else "user"
    except Exception:
        return "user"
