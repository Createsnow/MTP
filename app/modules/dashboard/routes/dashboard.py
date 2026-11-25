from fastapi import APIRouter

from app.api.deps import UserDep
from app.modules.dashboard.services import dashboard

router = APIRouter(tags=["audit"])


@router.get("/dashboard")
def dashboard_load(cu: UserDep) -> dict:
    return dashboard.load(cu)
