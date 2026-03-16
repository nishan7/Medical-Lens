import hashlib
import importlib.util
import json
import logging
import math
import pickle
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import tool

from config import settings


_SEARCH_MODULE: Any | None = None
logger = logging.getLogger(__name__)


def _resolve_tool_cache_path() -> Path:
    path = Path(settings.tool_cache_path)
    if path.is_absolute():
        return path
    project_root = Path(__file__).resolve().parents[1]
    return project_root / path


def _canonical_cache_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(k): _canonical_cache_value(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_cache_value(v) for v in value]
    if isinstance(value, set):
        normalized = [_canonical_cache_value(v) for v in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return str(value)


class _ToolCache:
    def __init__(self) -> None:
        self._enabled = bool(settings.tool_cache_enabled)
        self._ttl_seconds = max(0, int(settings.tool_cache_ttl_seconds))
        self._max_entries = max(0, int(settings.tool_cache_max_entries))
        self._db_path = _resolve_tool_cache_path()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_cache (
              cache_key TEXT PRIMARY KEY,
              created_at REAL NOT NULL,
              value_blob BLOB NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_cache_created_at ON tool_cache (created_at)"
        )
        conn.commit()
        self._conn = conn
        return conn

    def _make_key(self, name: str, params: Dict[str, Any]) -> str:
        canonical = _canonical_cache_value(params)
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(f"{name}:{payload}".encode("utf-8")).hexdigest()

    def _prune_if_needed(self, conn: sqlite3.Connection) -> None:
        if self._max_entries <= 0:
            return
        count_row = conn.execute("SELECT COUNT(*) FROM tool_cache").fetchone()
        count = int(count_row[0] if count_row else 0)
        over_by = count - self._max_entries
        if over_by <= 0:
            return
        conn.execute(
            """
            DELETE FROM tool_cache
            WHERE cache_key IN (
              SELECT cache_key FROM tool_cache
              ORDER BY created_at ASC
              LIMIT ?
            )
            """,
            (over_by,),
        )

    def get_or_compute(self, name: str, params: Dict[str, Any], compute: Callable[[], Any]) -> Any:
        if not self._enabled:
            return compute()

        cache_key = self._make_key(name=name, params=params)
        now = time.time()

        try:
            with self._lock:
                conn = self._ensure_conn()
                row = conn.execute(
                    "SELECT created_at, value_blob FROM tool_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
        except Exception as exc:
            logger.warning("Tool cache read failed for %s: %s", name, exc)
            return compute()

        if row is not None:
            created_at = float(row[0])
            value_blob = row[1]
            expired = self._ttl_seconds > 0 and (now - created_at) > self._ttl_seconds
            if not expired:
                try:
                    return pickle.loads(value_blob)
                except Exception as exc:
                    logger.warning("Tool cache deserialize failed for %s: %s", name, exc)
            try:
                with self._lock:
                    conn = self._ensure_conn()
                    conn.execute("DELETE FROM tool_cache WHERE cache_key = ?", (cache_key,))
                    conn.commit()
            except Exception as exc:
                logger.warning("Tool cache delete failed for %s: %s", name, exc)

        value = compute()

        try:
            blob = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
            with self._lock:
                conn = self._ensure_conn()
                conn.execute(
                    """
                    INSERT INTO tool_cache (cache_key, created_at, value_blob)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                      created_at=excluded.created_at,
                      value_blob=excluded.value_blob
                    """,
                    (cache_key, now, blob),
                )
                self._prune_if_needed(conn)
                conn.commit()
        except Exception as exc:
            logger.warning("Tool cache write failed for %s: %s", name, exc)

        return value


_TOOL_CACHE = _ToolCache()


def get_server_time() -> str:
    return datetime.utcnow().isoformat() + "Z"


def echo(text: str) -> str:
    return text


def _load_data_search_module():
    """
    Load `data/search.py` without requiring `data/` to be a Python package.
    """
    global _SEARCH_MODULE
    if _SEARCH_MODULE is not None:
        return _SEARCH_MODULE

    project_root = Path(__file__).resolve().parents[1]
    search_path = project_root / "data" / "search.py"

    spec = importlib.util.spec_from_file_location("rightcost_data_search", search_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {search_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _SEARCH_MODULE = module
    return module


_MONEY_RE = re.compile(r"[^\d.\-]")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+./-]*")
_QUERY_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "by",
    "cheapest",
    "cost",
    "costs",
    "find",
    "for",
    "get",
    "hospital",
    "in",
    "is",
    "lowest",
    "me",
    "of",
    "price",
    "rates",
    "show",
    "test",
    "the",
    "what",
}


def _query_candidates(query: str) -> List[str]:
    cleaned = " ".join(str(query).strip().split())
    if not cleaned:
        return []

    candidates: List[str] = [cleaned]

    test_for_match = re.search(r"\btest\s+for\s+(.+)$", cleaned, flags=re.IGNORECASE)
    if test_for_match:
        rhs = test_for_match.group(1).strip(" ?.!,:;")
        if rhs:
            candidates.append(f"{rhs} test")

    quoted = re.findall(r'"([^"]+)"', cleaned)
    candidates.extend(q.strip() for q in quoted if q.strip())

    tokens = _WORD_RE.findall(cleaned)
    meaningful = [
        t for t in tokens if len(t) >= 2 and t.lower() not in _QUERY_STOPWORDS
    ]
    if meaningful:
        joined = " ".join(meaningful)
        candidates.append(joined)
        candidates.append(meaningful[-1])
        for token in sorted(meaningful, key=len, reverse=True)[:3]:
            candidates.append(token)

    deduped: List[str] = []
    seen: set[str] = set()
    for c in candidates:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "n/a"}:
        return None
    cleaned = _MONEY_RE.sub("", text)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _effective_limit(limit: int) -> int:
    max_rows = max(1, int(settings.search_max_rows))
    try:
        requested = int(limit)
    except (TypeError, ValueError):
        requested = max_rows
    return max(1, min(requested, max_rows))


def hospital_cheapest_by_name(
    query: str,
    hospital_name: Optional[str] = None,
    insurance_provider: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """
    Compute compact cheapest-price summary for rows whose description matches query.
    """
    effective_limit = _effective_limit(limit)
    normalized_hospital = (hospital_name or "").strip() or None
    normalized_provider = (insurance_provider or "").strip() or None

    cache_params = {
        "query": query,
        "hospital_name": normalized_hospital,
        "insurance_provider": normalized_provider,
        "limit": effective_limit,
    }

    def _compute() -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        resolved_query = query
        for candidate in _query_candidates(query):
            candidate_rows = hospital_search_by_name(
                query=candidate,
                limit=effective_limit,
                insurance_provider=normalized_provider,
            )
            short_alpha = len(candidate) <= 3 and candidate.isalpha()
            if short_alpha:
                # Avoid noisy substring hits like "TB" matching "ACTB".
                pattern = re.compile(rf"\b{re.escape(candidate)}\b", flags=re.IGNORECASE)
                candidate_rows = [r for r in candidate_rows if pattern.search(str(r.get("description", "")))]
            if normalized_hospital:
                h = normalized_hospital.lower()
                candidate_rows = [r for r in candidate_rows if h in str(r.get("hospital_name", "")).lower()]
            if candidate_rows:
                rows = candidate_rows
                resolved_query = candidate
                break

        if not rows:
            return {
                "query": query,
                "resolved_query": query,
                "matches": 0,
                "hospital_name_filter": normalized_hospital,
                "insurance_provider_filter": normalized_provider,
                "message": "No matching rows found.",
            }

        cheapest_self_pay: Dict[str, Any] | None = None
        cheapest_negotiated: Dict[str, Any] | None = None

        for row in rows:
            description = row.get("description")
            setting = row.get("setting")
            hospital = row.get("hospital_name")
            location = f"{row.get('city')}, {row.get('state')}".strip(", ")
            code_pairs = []
            for i in range(1, 5):
                code = row.get(f"code|{i}")
                code_type = row.get(f"code|{i}|type")
                if code and code_type and str(code) != "nan" and str(code_type) != "nan":
                    code_pairs.append(f"{code_type}:{code}")

            cash_price = _to_float(row.get("standard_charge|discounted_cash"))
            if cash_price is not None:
                candidate = {
                    "price": cash_price,
                    "description": description,
                    "setting": setting,
                    "hospital_name": hospital,
                    "location": location,
                    "codes": code_pairs,
                    "source": "standard_charge|discounted_cash",
                }
                if cheapest_self_pay is None or cash_price < cheapest_self_pay["price"]:
                    cheapest_self_pay = candidate

            for key, value in row.items():
                if not isinstance(key, str) or not key.endswith("|negotiated_dollar"):
                    continue
                price = _to_float(value)
                if price is None:
                    continue
                parts = key.split("|")
                payer = parts[1] if len(parts) > 1 else ""
                plan = parts[2] if len(parts) > 2 else ""
                candidate = {
                    "price": price,
                    "payer": payer,
                    "plan": plan,
                    "description": description,
                    "setting": setting,
                    "hospital_name": hospital,
                    "location": location,
                    "codes": code_pairs,
                    "source": key,
                }
                if cheapest_negotiated is None or price < cheapest_negotiated["price"]:
                    cheapest_negotiated = candidate

        return {
            "query": query,
            "resolved_query": resolved_query,
            "matches": len(rows),
            "hospital_name_filter": normalized_hospital,
            "insurance_provider_filter": normalized_provider,
            "cheapest_self_pay": cheapest_self_pay,
            "cheapest_negotiated": cheapest_negotiated,
        }

    return _TOOL_CACHE.get_or_compute("hospital_cheapest_by_name", cache_params, _compute)


def hospital_search_by_name(
    query: str,
    limit: int = 20,
    insurance_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search hospital standard charges by substring match in `description`.
    """
    effective_limit = _effective_limit(limit)
    normalized_provider = (insurance_provider or "").strip() or None
    cache_params = {
        "query": query,
        "limit": effective_limit,
        "insurance_provider": normalized_provider,
    }

    def _compute() -> List[Dict[str, Any]]:
        mod = _load_data_search_module()
        return mod.search_by_name(
            query=query,
            limit=effective_limit,
            case_insensitive=True,
            insurance_provider=normalized_provider,
        )

    return _TOOL_CACHE.get_or_compute("hospital_search_by_name", cache_params, _compute)


def hospital_search_by_code(code_type: str, code: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Search hospital standard charges by code type + code.
    """
    effective_limit = _effective_limit(limit)
    cache_params = {
        "code_type": code_type,
        "code": code,
        "limit": effective_limit,
    }

    def _compute() -> List[Dict[str, Any]]:
        mod = _load_data_search_module()
        return mod.search_by_code(code_type=code_type, code=code, limit=effective_limit)

    return _TOOL_CACHE.get_or_compute("hospital_search_by_code", cache_params, _compute)


def hospital_list_insurers() -> List[str]:
    """
    List unique insurer names inferred from negotiated-rate columns.
    """
    def _compute() -> List[str]:
        mod = _load_data_search_module()
        df = mod.load_all_hospitals()

        insurers: set[str] = set()
        for col in df.columns:
            if not isinstance(col, str) or not col.endswith("|negotiated_dollar"):
                continue
            parts = col.split("|")
            if len(parts) < 2:
                continue
            payer = str(parts[1]).strip()
            if payer:
                insurers.add(payer)

        return sorted(insurers)

    return _TOOL_CACHE.get_or_compute("hospital_list_insurers", {}, _compute)


def _compact_row_for_llm(row: Dict[str, Any]) -> Dict[str, Any]:
    code_pairs = []
    for i in range(1, 5):
        code = row.get(f"code|{i}")
        code_type = row.get(f"code|{i}|type")
        if code and code_type and str(code) != "nan" and str(code_type) != "nan":
            code_pairs.append(f"{code_type}:{code}")

    negotiated_min: float | None = None
    negotiated_payer = ""
    negotiated_plan = ""
    for key, value in row.items():
        if not isinstance(key, str) or not key.endswith("|negotiated_dollar"):
            continue
        price = _to_float(value)
        if price is None:
            continue
        if negotiated_min is None or price < negotiated_min:
            negotiated_min = price
            parts = key.split("|")
            negotiated_payer = parts[1] if len(parts) > 1 else ""
            negotiated_plan = parts[2] if len(parts) > 2 else ""

    # Keep original columns, but compact verbose code key pairs into `codes`.
    enriched = dict(row)
    for i in range(1, 5):
        enriched.pop(f"code|{i}", None)
        enriched.pop(f"code|{i}|type", None)
    enriched["hospital_name"] = row.get("hospital_name")
    enriched["city"] = row.get("city")
    enriched["state"] = row.get("state")
    enriched["codes"] = code_pairs
    enriched["discounted_cash"] = _to_float(row.get("standard_charge|discounted_cash"))
    enriched["gross_charge"] = _to_float(row.get("standard_charge|gross"))
    enriched["negotiated_min"] = negotiated_min
    enriched["negotiated_min_payer"] = negotiated_payer
    enriched["negotiated_min_plan"] = negotiated_plan
    return enriched


@tool
def lc_get_server_time() -> str:
    """Get the current server time in ISO format (UTC)."""
    return get_server_time()


@tool
def lc_echo(text: str) -> str:
    """Echo back the provided text."""
    return echo(text)


@tool
def lc_hospital_search_by_name(
    query: str,
    limit: int = 20,
    insurance_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search hospital standard charges by description with optional insurance provider filter; returns compact rows for LLM use."""
    rows = hospital_search_by_name(
        query=query,
        limit=limit,
        insurance_provider=insurance_provider,
    )
    return [_compact_row_for_llm(r) for r in rows]


@tool
def lc_hospital_search_by_code(code_type: str, code: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Search hospital standard charges by code and return compact rows for LLM use."""
    rows = hospital_search_by_code(code_type=code_type, code=code, limit=limit)
    return [_compact_row_for_llm(r) for r in rows]


@tool
def lc_hospital_list_insurers() -> List[str]:
    """List all insurers found in negotiated-rate data."""
    return hospital_list_insurers()


@tool
def lc_hospital_cheapest_by_name(
    query: str,
    hospital_name: Optional[str] = None,
    insurance_provider: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """Find a compact cheapest-price summary for a procedure/test name."""
    return hospital_cheapest_by_name(
        query=query,
        hospital_name=hospital_name,
        insurance_provider=insurance_provider,
        limit=limit,
    )


def get_langchain_tools():
    return [
        lc_get_server_time,
        lc_echo,
        lc_hospital_search_by_name,
        lc_hospital_search_by_code,
        lc_hospital_list_insurers,
        lc_hospital_cheapest_by_name,
    ]


TOOLS: Dict[str, Callable[..., Any]] = {
    "get_server_time": get_server_time,
    "echo": echo,
    "hospital_search_by_name": hospital_search_by_name,
    "hospital_search_by_code": hospital_search_by_code,
    "hospital_list_insurers": hospital_list_insurers,
    "hospital_cheapest_by_name": hospital_cheapest_by_name,
}


def call_tool(name: str, **kwargs: Any) -> Any:
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f"Unknown tool: {name}")
    return tool(**kwargs)
