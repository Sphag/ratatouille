"""Owner-bound manual menu routes, opt-in locally until Telegram authentication."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI

from ratatouille.access import Guard, OwnerResolver, owner_dependency
from ratatouille.catalog import NutritionFields, StoredNutrition
from ratatouille.fulfilment import CookingRequest, Fulfilment, FulfilmentView, ShoppingRequest
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
from ratatouille.replacements import (
    ApplyReplacement,
    ReplacementLocation,
    ReplacementPreview,
    ReplacementRequest,
    Replacements,
)


def install_menu_api(
    application: FastAPI, menus: Menus, owner: str | OwnerResolver, guard: Guard = local_request
) -> None:
    resolve_owner = owner_dependency(owner)
    router = APIRouter(prefix="/api/menu", dependencies=[Depends(guard)])
    proposals = Proposals(menus)
    replacements = Replacements(menus)
    fulfilment = Fulfilment(menus.store)

    @router.get("/revisions/{identifier}/fulfilment")
    def fulfilment_view(identifier: str, owner: str = Depends(resolve_owner)) -> FulfilmentView:
        return fulfilment.get(owner, identifier)

    @router.put("/revisions/{identifier}/shopping-check")
    def shopping_check(
        identifier: str, data: ShoppingRequest, owner: str = Depends(resolve_owner)
    ) -> FulfilmentView:
        return fulfilment.mark(owner, identifier, data)

    @router.put("/revisions/{identifier}/cooking-check")
    def cooking_check(
        identifier: str, data: CookingRequest, owner: str = Depends(resolve_owner)
    ) -> FulfilmentView:
        return fulfilment.mark(owner, identifier, data)

    @router.post("/revisions/{identifier}/replacement-options")
    def replacement_options(
        identifier: str, data: ReplacementLocation, owner: str = Depends(resolve_owner)
    ) -> list[MenuView]:
        return replacements.suggestions(owner, identifier, data)

    @router.post("/revisions/{identifier}/replacement")
    def replacement(
        identifier: str, data: ReplacementRequest, owner: str = Depends(resolve_owner)
    ) -> ReplacementPreview:
        return replacements.preview(owner, identifier, data)

    @router.put("/revisions/{identifier}/replacement")
    def apply_replacement(
        identifier: str, data: ApplyReplacement, owner: str = Depends(resolve_owner)
    ) -> MenuView:
        return replacements.apply(owner, identifier, data)

    @router.post("/revisions/{identifier}/proposal")
    def proposal(identifier: str, data: Expected, owner: str = Depends(resolve_owner)) -> Proposal:
        return proposals.preview(owner, identifier, data)

    @router.put("/revisions/{identifier}/proposal")
    def apply_proposal(
        identifier: str, data: ApplyProposal, owner: str = Depends(resolve_owner)
    ) -> MenuView:
        return proposals.apply(owner, identifier, data)

    @router.get("/defaults")
    def defaults(owner: str = Depends(resolve_owner)) -> dict[str, str]:
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        monday = today + timedelta(days=(-today.weekday()) % 7)
        return {"start_date": monday.isoformat()}

    @router.get("/goals")
    def goals(owner: str = Depends(resolve_owner)) -> StoredNutrition:
        return stored_nutrition(menus.store.get_goals(owner))

    @router.put("/goals")
    def save_goals(data: NutritionFields, owner: str = Depends(resolve_owner)) -> StoredNutrition:
        menus.store.save_goals(owner, data.domain())
        return goals(owner)

    @router.get("/plans")
    def plans(owner: str = Depends(resolve_owner)) -> list[PlanSummary]:
        return menus.list_plans(owner)

    @router.post("/plans", status_code=201)
    def create(data: CreateMenu, owner: str = Depends(resolve_owner)) -> MenuView:
        return menus.create(owner, data)

    @router.post("/plans/{plan_id}/draft", status_code=201)
    def edit(plan_id: str, owner: str = Depends(resolve_owner)) -> MenuView:
        return view(menus.store.create_draft(owner, plan_id=plan_id))

    @router.get("/revisions/{identifier}")
    def get(identifier: str, owner: str = Depends(resolve_owner)) -> MenuView:
        return view(menus.store.get_revision(owner, identifier))

    @router.put("/revisions/{identifier}/settings")
    def configure(
        identifier: str, data: ConfigureMenu, owner: str = Depends(resolve_owner)
    ) -> MenuView:
        return menus.configure(owner, identifier, data)

    @router.put("/revisions/{identifier}/entries")
    def positions(
        identifier: str, data: SetPositions, owner: str = Depends(resolve_owner)
    ) -> MenuView:
        return menus.set_positions(owner, identifier, data)

    @router.post("/revisions/{identifier}/confirm")
    def confirm(identifier: str, data: Expected, owner: str = Depends(resolve_owner)) -> MenuView:
        return menus.confirm(owner, identifier, data.expected_number)

    @router.post("/revisions/{identifier}/cancel")
    def cancel(identifier: str, data: Expected, owner: str = Depends(resolve_owner)) -> MenuView:
        return view(menus.store.cancel(owner, identifier, expected_number=data.expected_number))

    application.include_router(router)
