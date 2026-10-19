"""LangGraph agents for campaign matching and personalized creative generation."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENTS_FILE = PROJECT_ROOT / "data" / "clients.json"
CAMPAIGNS_FILE = PROJECT_ROOT / "data" / "campaigns.json"

load_dotenv(PROJECT_ROOT / ".env")


class PurchaseProfile(BaseModel):
    """Recent purchase signals used for RFM scoring."""

    recency_days: int = Field(ge=0)
    frequency_90d: int = Field(ge=0)
    monetary_90d: float = Field(ge=0)
    interests: list[str] = Field(default_factory=list)


class ClientProfile(BaseModel):
    """Fictional customer record loaded from the local JSON file."""

    dni: str
    full_name: str
    age: int = Field(ge=18)
    gender: str
    marital_status: str
    children: int = Field(ge=0)
    monthly_income: float = Field(ge=0)
    city: str
    credit_score: int = Field(ge=0, le=1000)
    purchase_profile: PurchaseProfile


class Campaign(BaseModel):
    """Campaign catalogue entry and its eligible dates and segments."""

    id: str
    name: str
    offer: str
    description: str
    interests: list[str]
    rfm_segments: list[str]
    starts_at: date
    ends_at: date


class CampaignMatch(BaseModel):
    """Campaign relevance score and human-readable matching rationale."""

    campaign: Campaign
    relevance_score: float = Field(ge=0, le=1)
    reasons: list[str]


class CampaignCreative(BaseModel):
    """Email and image prompt produced for an individual campaign."""

    campaign_id: str
    campaign_name: str
    subject: str
    preheader: str
    body: str
    image_prompt: str


class WorkflowResult(BaseModel):
    """Public result returned by the workflow/API; excludes sensitive fields."""

    client_name: str
    recommendations: list[CampaignMatch]
    creatives: list[CampaignCreative]


class ClientNotFoundError(ValueError):
    """Raised when the requested DNI is not present in the client data."""


class WorkflowState(TypedDict, total=False):
    """State passed between the LangGraph agent nodes."""

    dni: str
    client: ClientProfile
    recommendations: list[CampaignMatch]
    creatives: list[CampaignCreative]


def _load_json_records(path: Path) -> list[dict[str, object]]:
    try:
        with path.open(encoding="utf-8") as json_file:
            records = json.load(json_file)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Required project data file was not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in data file {path}: {error}") from error

    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError(f"Expected a JSON array of objects in {path}")
    return records


def _load_client(dni: str) -> ClientProfile:
    clients = [ClientProfile.model_validate(row) for row in _load_json_records(CLIENTS_FILE)]
    matches = [client for client in clients if client.dni == dni]
    if not matches:
        raise ClientNotFoundError(f"No client was found for DNI {dni!r}")
    if len(matches) > 1:
        raise ValueError(f"Duplicate client records found for DNI {dni!r}")
    return matches[0]


def _load_active_campaigns(today: date | None = None) -> list[Campaign]:
    effective_date = today or date.today()
    campaigns = [
        Campaign.model_validate(row) for row in _load_json_records(CAMPAIGNS_FILE)
    ]
    return [
        campaign
        for campaign in campaigns
        if campaign.starts_at <= effective_date <= campaign.ends_at
    ]


def _rfm_segment(profile: PurchaseProfile) -> str:
    if (
        profile.recency_days <= 30
        and profile.frequency_90d >= 8
        and profile.monetary_90d >= 500
    ):
        return "champions"
    if profile.recency_days <= 60 and profile.frequency_90d >= 5:
        return "loyal"
    if profile.recency_days >= 120:
        return "at_risk"
    return "potential"


def _rfm_score(profile: PurchaseProfile) -> float:
    recency = max(0.0, 1.0 - profile.recency_days / 180)
    frequency = min(profile.frequency_90d / 12, 1.0)
    monetary = min(profile.monetary_90d / 1500, 1.0)
    return (recency + frequency + monetary) / 3


def match_campaigns(
    client: ClientProfile,
    campaigns: list[Campaign] | None = None,
    limit: int = 3,
) -> list[CampaignMatch]:
    """Rank active campaigns with a 70% interest / 30% RFM weighting."""
    if limit < 1:
        raise ValueError("The recommendation limit must be at least 1")

    available_campaigns = campaigns if campaigns is not None else _load_active_campaigns()
    if not available_campaigns:
        raise ValueError("There are no active campaigns to recommend")

    client_interests = {interest.casefold() for interest in client.purchase_profile.interests}
    segment = _rfm_segment(client.purchase_profile)
    normalized_rfm_score = _rfm_score(client.purchase_profile)
    scored: list[CampaignMatch] = []

    for campaign in available_campaigns:
        campaign_interests = {interest.casefold() for interest in campaign.interests}
        shared_interests = sorted(client_interests & campaign_interests)
        interest_score = len(shared_interests) / max(1, len(campaign_interests))
        rfm_score = (
            1.0 if segment in campaign.rfm_segments else normalized_rfm_score * 0.4
        )
        relevance = 0.7 * interest_score + 0.3 * rfm_score
        reasons = [
            f"Intereses coincidentes: {', '.join(shared_interests)}"
            if shared_interests
            else "Sin intereses coincidentes directos"
        ]
        reasons.append(
            f"Segmento RFM '{segment}' compatible"
            if segment in campaign.rfm_segments
            else f"Compatibilidad RFM parcial para el segmento '{segment}'"
        )
        scored.append(
            CampaignMatch(
                campaign=campaign,
                relevance_score=round(relevance, 4),
                reasons=reasons,
            )
        )

    return sorted(
        scored,
        key=lambda match: (-match.relevance_score, match.campaign.id),
    )[:limit]


def _fallback_creative(
    first_name: str,
    match: CampaignMatch,
) -> CampaignCreative:
    campaign = match.campaign
    interests = ", ".join(campaign.interests[:2]) or "tus intereses"
    return CampaignCreative(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        subject=f"{first_name}, descubre {campaign.name.lower()}",
        preheader=campaign.offer,
        body=(
            f"Hola {first_name},\n\n"
            f"Pensamos que esta propuesta puede interesarte: {campaign.description} "
            f"{campaign.offer}. La seleccionamos teniendo en cuenta tus preferencias "
            f"por {interests}.\n\n"
            "Conoce los detalles y aprovecha esta oportunidad mientras este vigente.\n\n"
            "Un saludo,\nTu equipo de beneficios"
        ),
        image_prompt=(
            "Create a polished, realistic advertising image for a Spanish-language "
            f"campaign called '{campaign.name}'. Feature {campaign.description} "
            "with an inclusive, optimistic visual style, natural lighting, and a "
            "clean composition. Do not include text, logos, or personal data."
        ),
    )


def _generate_creative(
    first_name: str,
    match: CampaignMatch,
) -> CampaignCreative:
    endpoint = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT", "").strip()
    if not endpoint:
        return _fallback_creative(first_name, match)

    api_key = os.getenv("AZURE_AI_FOUNDRY_API_KEY", "").strip()
    model = os.getenv("AZURE_AI_FOUNDRY_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError(
            "AZURE_AI_FOUNDRY_API_KEY and AZURE_AI_FOUNDRY_MODEL are required "
            "when AZURE_AI_FOUNDRY_ENDPOINT is configured"
        )

    client = OpenAI(api_key=api_key, base_url=endpoint)
    campaign = match.campaign
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a careful marketing copywriter. Respond only with a JSON "
                    "object containing subject, preheader, body, and image_prompt. "
                    "Write the email in natural Spanish and the image_prompt in English. "
                    "Do not invent terms, guarantees, or personal facts."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "first_name": first_name,
                        "campaign_name": campaign.name,
                        "offer": campaign.offer,
                        "description": campaign.description,
                        "interests": campaign.interests,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Azure AI Foundry returned an empty creative response")

    try:
        creative_fields = json.loads(content)
        return CampaignCreative.model_validate(
            {
                **creative_fields,
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
            }
        )
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError(
            "Azure AI Foundry returned a response that does not match the creative schema"
        ) from error


def _match_agent(state: WorkflowState) -> WorkflowState:
    dni = state.get("dni")
    if not dni:
        raise ValueError("A client DNI is required")
    client = _load_client(dni)
    recommendations = match_campaigns(client)
    return {"client": client, "recommendations": recommendations}


def _creative_agent(state: WorkflowState) -> WorkflowState:
    client = state.get("client")
    recommendations = state.get("recommendations")
    if client is None or recommendations is None:
        raise RuntimeError("Creative Agent requires a completed Match Agent result")

    first_name = client.full_name.split()[0]
    creatives = [
        _generate_creative(first_name, match) for match in recommendations
    ]
    return {"creatives": creatives}


def _build_workflow():
    graph = StateGraph(WorkflowState)
    graph.add_node("match_agent", _match_agent)
    graph.add_node("creative_agent", _creative_agent)
    graph.add_edge(START, "match_agent")
    graph.add_edge("match_agent", "creative_agent")
    graph.add_edge("creative_agent", END)
    return graph.compile()


_WORKFLOW = _build_workflow()


def run_campaign_workflow(dni: str) -> WorkflowResult:
    """Run both agents and return ranked campaigns with matching creatives."""
    result = _WORKFLOW.invoke({"dni": dni.strip()})
    client = result.get("client")
    recommendations = result.get("recommendations")
    creatives = result.get("creatives")
    if client is None or recommendations is None or creatives is None:
        raise RuntimeError("The campaign workflow completed without all expected outputs")

    return WorkflowResult(
        client_name=client.full_name,
        recommendations=recommendations,
        creatives=creatives,
    )
