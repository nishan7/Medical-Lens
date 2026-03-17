import csv
import os
from typing import Any, Dict, List, Optional

import pandas as pd


DATA_DIR = os.path.dirname(__file__)

CSV_FILES = [
    "946000533_o-connor-hospital_standardcharges.csv",
    "946000533_regional-medical-center_standardcharges.csv",
    "946000533_santa-clara-valley-medical-center_standardcharges.csv",
]

# NOTE: For now this mapping is hard-coded. Later we can populate it
# from the PDF that documents the hospital datasets.
HOSPITAL_META: Dict[str, Dict[str, str]] = {
    "946000533_o-connor-hospital_standardcharges.csv": {
        "hospital_name": "O'Connor Hospital",
        "city": "San Jose",
        "state": "CA",
    },
    "946000533_regional-medical-center_standardcharges.csv": {
        "hospital_name": "Regional Medical Center",
        "city": "San Jose",
        "state": "CA",
    },
    "946000533_santa-clara-valley-medical-center_standardcharges.csv": {
        "hospital_name": "Santa Clara Valley Medical Center",
        "city": "San Jose",
        "state": "CA",
    },
}

_DF_CSV_CACHE: pd.DataFrame | None = None
_DF_JSON_CACHE: pd.DataFrame | None = None


def _detect_header_row(path: str) -> int:
    """
    Return zero-based row index for the CSV header row.

    Some files start directly with the header (description, code|1, ...).
    Others may have one or two metadata rows before the header. We detect
    the header by looking for a row that contains the 'description' column.
    """
    with open(path, newline="", encoding="latin-1") as f:
        reader = csv.reader(f)
        for i in range(3):  # check first up to three rows
            row = next(reader, None)
            if row and "description" in row:
                return i
    return 0


def load_all_hospitals(force_reload: bool = False) -> pd.DataFrame:
    """
    Load the three hospital CSVs into a single pandas DataFrame.

    The result is cached at module level so repeated calls are cheap.
    """
    global _DF_CSV_CACHE
    if _DF_CSV_CACHE is not None and not force_reload:
        return _DF_CSV_CACHE

    frames: List[pd.DataFrame] = []

    for filename in CSV_FILES:
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            continue
        header_row = _detect_header_row(path)

        df = pd.read_csv(
            path,
            encoding="latin-1",
            header=header_row,
        )

        # Attach hospital metadata columns (from PDF-derived mapping).
        meta = HOSPITAL_META.get(filename, {})
        for key, value in meta.items():
            df[key] = value

        frames.append(df)

    if not frames:
        _DF_CSV_CACHE = pd.DataFrame()
        return _DF_CSV_CACHE

    combined = pd.concat(frames, ignore_index=True)

    # Normalize core columns used in searches to strings.
    if "description" in combined.columns:
        combined["description"] = combined["description"].astype(str)

    for i in range(1, 5):
        code_col = f"code|{i}"
        type_col = f"code|{i}|type"
        if code_col in combined.columns:
            combined[code_col] = combined[code_col].astype(str)
        if type_col in combined.columns:
            combined[type_col] = combined[type_col].astype(str)

    combined["source"] = "csv"

    _DF_CSV_CACHE = combined
    return combined


def _parse_address_city_state(addresses: List[str]) -> Dict[str, str]:
    """
    Best-effort extraction of city/state from hospital_address strings.
    """
    if not addresses:
        return {"city": "", "state": ""}
    first = str(addresses[0])
    parts = [p.strip() for p in first.split(",")]
    if len(parts) >= 3:
        return {"city": parts[-3], "state": parts[-2]}
    return {"city": "", "state": ""}


def load_all_hospitals_json(force_reload: bool = False) -> pd.DataFrame:
    """
    Load hospital JSON standardcharge files into a single pandas DataFrame.

    We normalize the JSON structure to match the CSV schema as closely
    as possible:
      - description
      - code|i, code|i|type
      - standard_charge|gross (from group maximum)
      - negotiated columns of the form
        'standard_charge|{payer_name}|{plan_name}|negotiated_dollar'
      - hospital_name, city, state
      - source = 'json'
    """
    import json

    global _DF_JSON_CACHE
    if _DF_JSON_CACHE is not None and not force_reload:
        return _DF_JSON_CACHE

    if not os.path.isdir(DATA_DIR):
        _DF_JSON_CACHE = pd.DataFrame()
        return _DF_JSON_CACHE

    frames: List[pd.DataFrame] = []
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith("_standardcharges.json"):
            continue
        path = os.path.join(DATA_DIR, filename)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, dict):
            continue

        hospital_name = str(raw.get("hospital_name", "") or "")
        addr_meta = _parse_address_city_state(raw.get("hospital_address") or [])
        sci_list = raw.get("standard_charge_information", [])
        rows: List[Dict[str, Any]] = []

        if isinstance(sci_list, list):
            for sci in sci_list:
                if not isinstance(sci, dict):
                    continue
                description = str(sci.get("description", "") or "")
                code_info = sci.get("code_information") or []
                base: Dict[str, Any] = {
                    "description": description,
                    "hospital_name": hospital_name,
                    "city": addr_meta.get("city", ""),
                    "state": addr_meta.get("state", ""),
                }
                # Map up to 4 code entries into code|i columns.
                if isinstance(code_info, list):
                    for idx, code_entry in enumerate(code_info[:4], start=1):
                        if not isinstance(code_entry, dict):
                            continue
                        base[f"code|{idx}"] = str(code_entry.get("code", "") or "")
                        base[f"code|{idx}|type"] = str(code_entry.get("type", "") or "")

                std_groups = sci.get("standard_charges") or []
                if not isinstance(std_groups, list):
                    continue
                for group in std_groups:
                    if not isinstance(group, dict):
                        continue
                    setting = group.get("setting")
                    gross_max = group.get("maximum")
                    payers = group.get("payers_information") or []

                    if not isinstance(payers, list) or not payers:
                        # No payer-specific rows; still keep a generic row if we have a price.
                        row = dict(base)
                        row["setting"] = setting
                        if gross_max is not None:
                            row["standard_charge|gross"] = gross_max
                        rows.append(row)
                        continue

                    for payer in payers:
                        if not isinstance(payer, dict):
                            continue
                        payer_name = str(payer.get("payer_name", "") or "")
                        plan_name = str(payer.get("plan_name", "") or "")
                        amount = payer.get("standard_charge_dollar")
                        row = dict(base)
                        row["setting"] = setting
                        if gross_max is not None:
                            row["standard_charge|gross"] = gross_max
                        if payer_name:
                            col_name = (
                                f"standard_charge|{payer_name}|{plan_name}|negotiated_dollar"
                            )
                            row[col_name] = amount
                        rows.append(row)

        if rows:
            df = pd.DataFrame(rows)
            frames.append(df)

    if not frames:
        _DF_JSON_CACHE = pd.DataFrame()
        return _DF_JSON_CACHE

    combined = pd.concat(frames, ignore_index=True)

    if "description" in combined.columns:
        combined["description"] = combined["description"].astype(str)

    for i in range(1, 5):
        code_col = f"code|{i}"
        type_col = f"code|{i}|type"
        if code_col in combined.columns:
            combined[code_col] = combined[code_col].astype(str)
        if type_col in combined.columns:
            combined[type_col] = combined[type_col].astype(str)

    combined["source"] = "json"

    _DF_JSON_CACHE = combined
    return combined


def _search_by_code_in_df(
    df: pd.DataFrame,
    code_type: str,
    code: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    # Build a boolean mask that is true when any (code|i, code|i|type) pair matches.
    mask = False
    for i in range(1, 5):
        code_col = f"code|{i}"
        type_col = f"code|{i}|type"
        if code_col not in df.columns or type_col not in df.columns:
            continue
        cond = (df[type_col] == code_type) & (df[code_col] == code)
        mask = mask | cond

    result = df[mask].head(limit)
    return result.to_dict(orient="records")


def search_by_code_csv(
    code_type: str,
    code: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    df = load_all_hospitals()
    if df.empty:
        return []
    return _search_by_code_in_df(df, code_type=code_type, code=code, limit=limit)


def search_by_code_json(
    code_type: str,
    code: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    df = load_all_hospitals_json()
    if df.empty:
        return []
    return _search_by_code_in_df(df, code_type=code_type, code=code, limit=limit)


def search_by_code(
    code_type: str,
    code: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Type-1 search: by code_type and code across CSV and JSON sources.

    Returns a list of row dicts (one dict per matching row).
    """
    rows: List[Dict[str, Any]] = []
    rows.extend(search_by_code_csv(code_type=code_type, code=code, limit=limit))
    rows.extend(search_by_code_json(code_type=code_type, code=code, limit=limit))
    return rows[:limit]


def _search_by_name_in_df(
    df: pd.DataFrame,
    query: str,
    limit: int = 20,
    case_insensitive: bool = True,
    insurance_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if "description" not in df.columns:
        return []

    desc = df["description"]
    if case_insensitive:
        mask = desc.str.contains(query, case=False, regex=False, na=False)
    else:
        mask = desc.str.contains(query, case=True, regex=False, na=False)

    provider = (insurance_provider or "").strip()
    if provider:
        provider_l = provider.lower()
        negotiated_cols = [
            c
            for c in df.columns
            if isinstance(c, str)
            and c.endswith("|negotiated_dollar")
            and provider_l in c.lower()
        ]
        if not negotiated_cols:
            return []

        provider_values = (
            df[negotiated_cols]
            .astype(str)
            .apply(lambda col: col.str.strip().str.lower())
        )
        has_provider_value = ~provider_values.isin({"", "nan", "none", "null", "n/a"})
        mask = mask & has_provider_value.any(axis=1)

    result = df[mask].head(limit)
    return result.to_dict(orient="records")


def search_by_name_csv(
    query: str,
    limit: int = 20,
    case_insensitive: bool = True,
    insurance_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    df = load_all_hospitals()
    return _search_by_name_in_df(
        df=df,
        query=query,
        limit=limit,
        case_insensitive=case_insensitive,
        insurance_provider=insurance_provider,
    )


def search_by_name_json(
    query: str,
    limit: int = 20,
    case_insensitive: bool = True,
    insurance_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    df = load_all_hospitals_json()
    if df.empty:
        return []
    return _search_by_name_in_df(
        df=df,
        query=query,
        limit=limit,
        case_insensitive=case_insensitive,
        insurance_provider=insurance_provider,
    )


def search_by_name(
    query: str,
    limit: int = 20,
    case_insensitive: bool = True,
    insurance_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Type-2 search: by name, using the `description` column across CSV and JSON.

    Returns a list of row dicts (one dict per matching row).
    """
    rows: List[Dict[str, Any]] = []
    rows.extend(
        search_by_name_csv(
            query=query,
            limit=limit,
            case_insensitive=case_insensitive,
            insurance_provider=insurance_provider,
        )
    )
    rows.extend(
        search_by_name_json(
            query=query,
            limit=limit,
            case_insensitive=case_insensitive,
            insurance_provider=insurance_provider,
        )
    )
    return rows[:limit]


if __name__ == "__main__":
    # Basic demo usage while developing.
    # 1) Code-based search: code_type=CPT, code=1324
    matches_code = search_by_code(code_type="CPT", code="1324", limit=5)
    print("Code search (CPT 1324):")
    for row in matches_code:
        print(row)

    # 2) Name-based search: substring in description
    matches_name = search_by_name(query="stent", limit=5)
    print("\nName search ('stent'):")
    for row in matches_name:
        print(row)
