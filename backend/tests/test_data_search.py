import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEARCH_PATH = PROJECT_ROOT / "data" / "search.py"


def _load_search_module():
    spec = importlib.util.spec_from_file_location("medical_lens_data_search_test", SEARCH_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load search module from {SEARCH_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


search = _load_search_module()


class SearchDataLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_data_dir = search.DATA_DIR
        self.original_csv_cache = search._DF_CSV_CACHE
        self.original_json_cache = search._DF_JSON_CACHE

    def tearDown(self) -> None:
        search.DATA_DIR = self.original_data_dir
        search._DF_CSV_CACHE = self.original_csv_cache
        search._DF_JSON_CACHE = self.original_json_cache

    def test_missing_local_datasets_return_empty_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            search.DATA_DIR = temp_dir
            search._DF_CSV_CACHE = None
            search._DF_JSON_CACHE = None

            self.assertTrue(search.load_all_hospitals(force_reload=True).empty)
            self.assertTrue(search.load_all_hospitals_json(force_reload=True).empty)
            self.assertEqual(search.search_by_name(query="TB test", limit=5), [])
            self.assertEqual(search.search_by_code(code_type="CPT", code="86481", limit=5), [])


if __name__ == "__main__":
    unittest.main()
