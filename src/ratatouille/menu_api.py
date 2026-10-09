"""Owner-bound manual menu routes, opt-in locally until Telegram authentication."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI

from ratatouille.catalog import NutritionFields, StoredNutrition
from ratatouille.menus import (
    ConfigureMenu,
    CreateMenu,
    Expected,
    Menus,
    MenuView,
    PlanSummary,
    SetPositions,
    stored_nutrition,
    view,
)
from ratatouille.proposals import ApplyProposal, Proposal, Proposals
from ratatouille.recipe_api import local_request


def install_menu_api(application: FastAPI, menus: Menus, owner: str) -> None:
    router = APIRouter(prefix="/api/menu", dependencies=[Depends(local_request)])
    proposals = Proposals(menus)

    @router.post("/revisions/{identifier}/proposal")
    def proposal(identifier: str, data: Expected) -> Proposal:
        return proposals.preview(owner, identifier, data)

    @router.put("/revisions/{identifier}/proposal")
    def apply_proposal(identifier: str, data: ApplyProposal) -> MenuView:
        return proposals.apply(owner, identifier, data)

    @router.get("/defaults")
    def defaults() -> dict[str, str]:
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        monday = today + timedelta(days=(-today.weekday()) % 7)
        return {"start_date": monday.isoformat()}

    @router.get("/goals")
    def goals() -> StoredNutrition:
        return stored_nutrition(menus.store.get_goals(owner))

    @router.put("/goals")
    def save_goals(data: NutritionFields) -> StoredNutrition:
        menus.store.save_goals(owner, data.domain())
        return goals()

    @router.get("/plans")
    def plans() -> list[PlanSummary]:
        return menus.list_plans(owner)

    @router.post("/plans", status_code=201)
    def create(data: CreateMenu) -> MenuView:
        return menus.create(owner, data)

    @router.post("/plans/{plan_id}/draft", status_code=201)
    def edit(plan_id: str) -> MenuView:
        return view(menus.store.create_draft(owner, plan_id=plan_id))

    @router.get("/revisions/{identifier}")
    def get(identifier: str) -> MenuView:
        return view(menus.store.get_revision(owner, identifier))

    @router.put("/revisions/{identifier}/settings")
    def configure(identifier: str, data: ConfigureMenu) -> MenuView:
        return menus.configure(owner, identifier, data)

    @router.put("/revisions/{identifier}/entries")
    def positions(identifier: str, data: SetPositions) -> MenuView:
        return menus.set_positions(owner, identifier, data)

    @router.post("/revisions/{identifier}/confirm")
    def confirm(identifier: str, data: Expected) -> MenuView:
        return menus.confirm(owner, identifier, data.expected_number)

    @router.post("/revisions/{identifier}/cancel")
    def cancel(identifier: str, data: Expected) -> MenuView:
        return view(menus.store.cancel(owner, identifier, expected_number=data.expected_number))

    application.include_router(router)
