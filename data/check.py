import csv
import json
import os
from pprint import pprint
from typing import Any, Dict, Optional


def one_standard_charge_from_json(json_path: str) -> Optional[Dict[str, Any]]:
    """Return one normalized standard_charge dict from a JSON hospital file."""
    with open(json_path, "r") as f:
        data = json.load(f)

    charges = data.get("standard_charge_information")
    if not isinstance(charges, list) or not charges:
        return None

    first = charges[0]
    std_list = first.get("standard_charges") if isinstance(first, dict) else None
    std = std_list[0] if isinstance(std_list, list) and std_list else {}

    return {
        "source_file": os.path.basename(json_path),
        "hospital_name": data.get("hospital_name"),
        "last_updated_on": data.get("last_updated_on"),
        "version": data.get("version"),
        "description": first.get("description"),
        "codes": first.get("code_information"),
        "setting": std.get("setting"),
        # JSON schema has min/max and payer-specific dollars; expose min/max here
        "standard_charge_gross": std.get("minimum"),
        "standard_charge_discounted_cash": std.get("maximum"),
        "raw_standard_charge": std,
    }


def one_standard_charge_from_csv(csv_path: str) -> Optional[Dict[str, Any]]:
    """Return one normalized standard_charge dict from a CSV hospital file."""
    with open(csv_path, newline="", encoding="latin-1") as f:
        reader = csv.reader(f)
        meta_header = next(reader, None)
        meta_values = next(reader, None)
        header = next(reader, None)
        first_row = next(reader, None)

    if not (meta_header and meta_values and header and first_row):
        return None

    col_index = {name: i for i, name in enumerate(header)}

    def gv(name: str) -> Any:
        idx = col_index.get(name)
        return first_row[idx] if idx is not None and idx < len(first_row) else ""

    return {
        "source_file": os.path.basename(csv_path),
        "hospital_name": meta_values[0] if len(meta_values) > 0 else None,
        "last_updated_on": meta_values[1] if len(meta_values) > 1 else None,
        "version": meta_values[2] if len(meta_values) > 2 else None,
        "description": gv("description"),
        "codes": [
            {"code": gv("code|1"), "type": gv("code|1|type")},
            {"code": gv("code|2"), "type": gv("code|2|type")},
        ],
        "setting": gv("setting"),
        "standard_charge_gross": gv("standard_charge|gross"),
        "standard_charge_discounted_cash": gv("standard_charge|discounted_cash"),
        "raw_row": dict(zip(header, first_row)),
    }


def summarize_hospital_json(json_path: str) -> None:
    print("=" * 80)
    print(f"File (JSON): {os.path.basename(json_path)}")

    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ERROR reading JSON: {e}")
        return

    hospital_name = data.get("hospital_name")
    last_updated_on = data.get("last_updated_on")
    version = data.get("version")
    locations = data.get("hospital_location")
    addresses = data.get("hospital_address")
    charges = data.get("standard_charge_information")

    print(f"  hospital_name    : {hospital_name}")
    print(f"  last_updated_on  : {last_updated_on}")
    print(f"  version          : {version}")
    if isinstance(locations, list):
        print(f"  locations        : {len(locations)} entries")
    else:
        print(f"  locations        : {locations}")
    if isinstance(addresses, list):
        print(f"  addresses        : {len(addresses)} entries")
    else:
        print(f"  addresses        : {addresses}")

    if isinstance(charges, list) and charges:
        pprint(charges[0])
        pprint(charges[100])
        print(f"  #standard_charges: {len(charges)}")
        first = charges[0]
        std_list = first.get("standard_charges") if isinstance(first, dict) else None
        std = std_list[0] if isinstance(std_list, list) and std_list else {}

        # Normalize gross / discounted fields across JSON variants
        gross = std.get("gross_charge", std.get("minimum"))
        disc = std.get("discounted_cash", std.get("maximum"))
        setting = std.get("setting")

        print("  first_charge:")
        print(f"    description                : {first.get('description')}")
        print(f"    setting                    : {setting}")
        print(f"    standard_charge_gross      : {gross}")
        print(f"    standard_charge_discounted : {disc}")
    else:
        print(f"  standard_charge_information type: {type(charges)}")


def summarize_hospital_csv(csv_path: str) -> None:
    print("=" * 80)
    print(f"File (CSV): {os.path.basename(csv_path)}")

    # Use latin-1 to tolerate odd characters without failing
    with open(csv_path, newline="", encoding="latin-1") as f:
        reader = csv.reader(f)
        meta_header = next(reader, None)
        meta_values = next(reader, None)
        header = next(reader, None)
        first_row = next(reader, None)

    if not (meta_header and meta_values and header and first_row):
        print("  ERROR: CSV missing expected rows")
        return

    # Basic metadata from first two rows
    hospital_name = meta_values[0] if len(meta_values) > 0 else None
    last_updated_on = meta_values[1] if len(meta_values) > 1 else None
    version = meta_values[2] if len(meta_values) > 2 else None
    hospital_location = meta_values[3] if len(meta_values) > 3 else None
    hospital_address = meta_values[4] if len(meta_values) > 4 else None

    print(f"  hospital_name    : {hospital_name}")
    print(f"  last_updated_on  : {last_updated_on}")
    print(f"  version          : {version}")
    print(f"  hospital_location: {hospital_location}")
    print(f"  hospital_address : {hospital_address}")

    # Treat each subsequent row as a standard charge entry
    important_cols = [
        "description",
        "code|1",
        "code|1|type",
        "setting",
        "standard_charge|gross",
        "standard_charge|discounted_cash",
    ]
    col_index = {name: header.index(name) for name in important_cols if name in header}
    def gv(name: str) -> Any:
        idx = col_index.get(name)
        return first_row[idx] if idx is not None and idx < len(first_row) else ""

    print("  first_charge:")
    print(f"    description                : {gv('description')}")
    print(f"    setting                    : {gv('setting')}")
    print(f"    standard_charge_gross      : {gv('standard_charge|gross')}")
    print(f"    standard_charge_discounted : {gv('standard_charge|discounted_cash')}")


def hospital_1() -> None:
    """GOOD SAMARITAN HOSPITAL (JSON)."""
    here = os.path.dirname(__file__)
    json_path = os.path.join(
        here, "62-1763090_GOOD-SAMARITAN-HOSPITAL_standardcharges.json"
    )
    summarize_hospital_json(json_path)


def hospital_2() -> None:
    """O'Connor Hospital (CSV)."""
    here = os.path.dirname(__file__)
    csv_path = os.path.join(
        here, "946000533_o-connor-hospital_standardcharges.csv"
    )
    summarize_hospital_csv(csv_path)


def hospital_3() -> None:
    """El Camino Hospital (JSON)."""
    here = os.path.dirname(__file__)
    json_path = os.path.join(
        here, "943167314_el-camino-hospital_standardcharges.json"
    )
    summarize_hospital_json(json_path)


def hospital_4() -> None:
    """Stanford Health Care (JSON)."""
    here = os.path.dirname(__file__)
    json_path = os.path.join(
        here, "946174066_stanford-health-care_standardcharges.json"
    )
    summarize_hospital_json(json_path)


def hospital_5() -> None:
    """Regional Medical Center (CSV)."""
    here = os.path.dirname(__file__)
    csv_path = os.path.join(
        here, "946000533_regional-medical-center_standardcharges.csv"
    )
    summarize_hospital_csv(csv_path)


def hospital_6() -> None:
    """Santa Clara Valley Medical Center (CSV)."""
    here = os.path.dirname(__file__)
    csv_path = os.path.join(
        here, "946000533_santa-clara-valley-medical-center_standardcharges.csv"
    )
    summarize_hospital_csv(csv_path)


if __name__ == "__main__":
    hospital_1()
    hospital_2()
    hospital_3()
    hospital_4()
    hospital_5()
    hospital_6()
