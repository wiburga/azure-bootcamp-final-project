"""HTTP API for the campaign recommendation workflow."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agents import ClientNotFoundError, WorkflowResult, run_campaign_workflow

app = FastAPI(
    title="Personalized Campaign Multi-Agent API",
    description="Matches fictional customers to campaigns and generates creative assets.",
    version="1.0.0",
)


class RecommendationRequest(BaseModel):
    dni: str = Field(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z-]+$")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/campaigns/recommendations", response_model=WorkflowResult)
def create_recommendations(request: RecommendationRequest) -> WorkflowResult:
    try:
        return run_campaign_workflow(request.dni)
    except ClientNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
