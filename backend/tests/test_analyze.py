import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("NVIDIA_API_KEY", "test-key")

import analyze  # noqa: E402
import main  # noqa: E402


class AnalyzeBillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.warm_patch = patch.object(main, "hospital_search_by_name", return_value=[])
        self.warm_patch.start()

    def tearDown(self) -> None:
        self.warm_patch.stop()

    def test_parse_bill_falls_back_to_regex_parser(self) -> None:
        bill_text = """Date: 2026-01-15
Provider: General Hospital
Complete Blood Count (CBC) $450
Venipuncture $380
TOTAL $830
"""

        with patch.object(analyze.llm, "complete_text", side_effect=RuntimeError("llm unavailable")):
            items = asyncio.run(analyze.parse_bill(bill_text))

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["description"], "Complete Blood Count (CBC)")
        self.assertEqual(items[0]["charged_amount"], 450.0)
        self.assertEqual(items[1]["description"], "Venipuncture")

    def test_enrich_with_prices_adds_fair_price(self) -> None:
        items = [{"description": "CBC", "cpt_code": "85025", "charged_amount": 450.0}]
        rows = [
            {
                "standard_charge|discounted_cash": 12.0,
                "standard_charge|Aetna|Gold|negotiated_dollar": 9.5,
            }
        ]

        with patch.object(analyze, "hospital_search_by_code", return_value=rows):
            enriched = asyncio.run(analyze.enrich_with_prices(items))

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["fair_price"], 9.5)
        self.assertEqual(enriched[0]["markup_ratio"], 47.4)
        self.assertTrue(enriched[0]["price_source"].startswith("hospital_pricing_data:cpt"))

    def test_analyze_endpoint_returns_all_fields(self) -> None:
        parsed_items = [{"description": "CBC", "cpt_code": "85025", "charged_amount": 450.0}]
        enriched_items = [
            {
                "description": "CBC",
                "cpt_code": "85025",
                "charged_amount": 450.0,
                "fair_price": 12.0,
                "markup_ratio": 37.5,
                "price_source": "hospital_pricing_data:cpt:discounted_cash",
            }
        ]
        issue_analysis = {
            "issues": [
                {
                    "type": "OVERCHARGE",
                    "severity": "HIGH",
                    "item": "CBC",
                    "charged": 450.0,
                    "fair_price": 12.0,
                    "explanation": "Marked up relative to the lowest reference price.",
                }
            ],
            "total_charged": 450.0,
            "total_fair_estimate": 12.0,
            "potential_savings": 438.0,
            "savings_percentage": 97.3,
        }
        dispute = {
            "letter": "Dear Billing Department,\nPlease review this charge.",
            "phone_script": "Ask for a supervisor and cite the pricing mismatch.",
        }

        with patch.object(analyze, "parse_bill", new=AsyncMock(return_value=parsed_items)):
            with patch.object(analyze, "enrich_with_prices", new=AsyncMock(return_value=enriched_items)):
                with patch.object(analyze, "analyze_issues", new=AsyncMock(return_value=issue_analysis)):
                    with patch.object(analyze, "generate_dispute_package", new=AsyncMock(return_value=dispute)):
                        with TestClient(main.app) as client:
                            response = client.post("/analyze-bill", json={"bill_text": "CBC $450"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("summary", payload)
        self.assertIn("line_items", payload)
        self.assertIn("issues", payload)
        self.assertIn("dispute_letter", payload)
        self.assertIn("phone_script", payload)
        self.assertEqual(payload["summary"]["total_charged"], 450.0)
        self.assertEqual(payload["line_items"][0]["fair_price"], 12.0)
        self.assertEqual(payload["issues"][0]["type"], "OVERCHARGE")


if __name__ == "__main__":
    unittest.main()
