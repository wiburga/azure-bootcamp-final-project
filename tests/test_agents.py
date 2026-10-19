import unittest
from datetime import date
from unittest.mock import patch

from src.agents import (
    Campaign,
    ClientProfile,
    PurchaseProfile,
    _load_active_campaigns,
    match_campaigns,
    run_campaign_workflow,
)


class CampaignMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ClientProfile(
            dni="12345678",
            full_name="Ana Prueba",
            age=30,
            gender="Femenino",
            marital_status="Soltera",
            children=0,
            monthly_income=3000,
            city="Lima",
            credit_score=700,
            purchase_profile=PurchaseProfile(
                recency_days=10,
                frequency_90d=9,
                monetary_90d=800,
                interests=["viajes", "bienestar"],
            ),
        )

    def test_ranks_interest_and_rfm_matches(self) -> None:
        campaigns = [
            Campaign(
                id="travel",
                name="Viajes",
                offer="Oferta de viaje",
                description="Viajes para descubrir destinos.",
                interests=["viajes"],
                rfm_segments=["champions"],
                starts_at=date(2025, 1, 1),
                ends_at=date(2027, 12, 31),
            ),
            Campaign(
                id="food",
                name="Gastronomia",
                offer="Oferta gastronomica",
                description="Experiencias gastronomicas.",
                interests=["gastronomia"],
                rfm_segments=["at_risk"],
                starts_at=date(2025, 1, 1),
                ends_at=date(2027, 12, 31),
            ),
        ]

        matches = match_campaigns(self.client, campaigns)

        self.assertEqual([match.campaign.id for match in matches], ["travel", "food"])
        self.assertGreater(matches[0].relevance_score, matches[1].relevance_score)
        self.assertIn("champions", matches[0].reasons[1])

    def test_excludes_inactive_campaigns(self) -> None:
        with patch(
            "src.agents._load_json_records",
            return_value=[
                {
                    "id": "expired",
                    "name": "Expired",
                    "offer": "Offer",
                    "description": "Description",
                    "interests": [],
                    "rfm_segments": [],
                    "starts_at": "2020-01-01",
                    "ends_at": "2020-12-31",
                }
            ],
        ):
            self.assertEqual(_load_active_campaigns(today=date(2026, 1, 1)), [])

    def test_workflow_returns_three_campaign_creatives_without_foundry(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "AZURE_AI_FOUNDRY_ENDPOINT": "",
                    "AZURE_AI_FOUNDRY_API_KEY": "",
                    "AZURE_AI_FOUNDRY_MODEL": "",
                },
            ),
        ):
            result = run_campaign_workflow("48271635")

        self.assertEqual(len(result.recommendations), 3)
        self.assertEqual(len(result.creatives), 3)
        self.assertTrue(all(creative.image_prompt for creative in result.creatives))

    def test_unknown_dni_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "No client was found"):
            run_campaign_workflow("00000000")


if __name__ == "__main__":
    unittest.main()
