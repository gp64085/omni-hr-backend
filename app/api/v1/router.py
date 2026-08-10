from fastapi import APIRouter

from app.modules.audit.router import audit_logs_router
from app.modules.auth.router import protected_auth_router, public_auth_router
from app.modules.leaves.router import holidays_router, leaves_router
from app.modules.projects.router import projects_router
from app.modules.roles.router import permissions_router
from app.modules.roles.router import roles_router as roles_router
from app.modules.timesheets.router import timesheets_router
from app.modules.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(public_auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(
    protected_auth_router, prefix="/auth", tags=["Authentication"]
)
api_router.include_router(users_router, prefix="/users", tags=["Users & Profiles"])
api_router.include_router(roles_router, prefix="/roles", tags=["Roles & Permissions"])
api_router.include_router(
    permissions_router, prefix="/permissions", tags=["Roles & Permissions"]
)
api_router.include_router(leaves_router, prefix="/leaves", tags=["Leaves & Accruals"])
api_router.include_router(
    holidays_router, prefix="/holidays", tags=["Company Holidays"]
)
api_router.include_router(
    projects_router, prefix="/projects", tags=["Projects Management"]
)
api_router.include_router(
    timesheets_router, prefix="/timesheets", tags=["Daily Timesheets & Work Tracking"]
)
api_router.include_router(audit_logs_router, prefix="/audit-logs", tags=["Audit Trail"])
