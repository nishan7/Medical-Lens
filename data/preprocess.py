import csv
import os
from collections import defaultdict
from typing import Dict, List, Tuple


CSV_FILES = [
    "946000533_o-connor-hospital_standardcharges.csv",
    "946000533_regional-medical-center_standardcharges.csv",
    "946000533_santa-clara-valley-medical-center_standardcharges.csv",
]


def read_header(path: str) -> List[str]:
    """
    Return the true header row.

    Some files start directly with the header (description, code|1, ...).
    Others may have two metadata rows before the header. Detect by
    looking for 'description' in the row.
    """
    with open(path, newline="", encoding="latin-1") as f:
        reader = csv.reader(f)
        first = next(reader, None)
        if not first:
            return []
        if "description" in first:
            return first

        second = next(reader, None)
        if not second:
            return []
        if "description" in second:
            return second

        third = next(reader, None)
        if third and "description" in third:
            return third

        # Fallback: assume the first row is the header
        return first


def analyze_header(cols: List[str]) -> Tuple[List[str], Dict[Tuple[str, str], List[str]]]:
    """
    Return:
      - base columns (non payer-specific)
      - payer-specific columns grouped by (payer_name, plan_name)
    """
    base_cols: List[str] = []
    payer_cols: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for c in cols:
        if "|" not in c:
            base_cols.append(c)
            continue

        parts = c.split("|")
        if parts[0] in ("standard_charge", "estimated_amount", "additional_payer_notes"):
            # payer-specific column; expected form:
            # standard_charge|<PAYER>|<PLAN>|<field>
            if len(parts) >= 4:
                payer = parts[1]
                plan = parts[2]
                payer_cols[(payer, plan)].append(c)
            else:
                base_cols.append(c)
        else:
            base_cols.append(c)

    return base_cols, payer_cols


def main() -> None:
    here = os.path.dirname(__file__)

    for name in CSV_FILES:
        path = os.path.join(here, name)
        cols = read_header(path)
        base_cols, payer_cols = analyze_header(cols)

        print("=" * 80)
        print(f"File: {name}")
        print(f"  total columns : {len(cols)}")
        print(f"  base columns  : {len(base_cols)}")
        print("  base columns (first 15):")
        for c in base_cols[:15]:
            print(f"    - {c}")

        print(f"  distinct payer-plan combos: {len(payer_cols)}")
        for (payer, plan), c_list in list(payer_cols.items())[:5]:
            print(f"    * {payer} | {plan} ({len(c_list)} columns)")


if __name__ == "__main__":
    main()

