import uuid
from typing import Optional

from fastapi import Depends, Query, Request, status

from app.api.deps import (
    ProtectedAPIRouter,
    get_cache_service,
    get_project_service,
    require_permission,
)
from app.core.services.cache_service import CacheService, cache_response
from app.models.role import PermissionEnum
from app.models.user import User
from app.modules.projects.schemas import (
    ProjectCreatePayload,
    ProjectRead,
    ProjectUpdatePayload,
)
from app.modules.projects.service import ProjectService
from app.schemas.common import StandardResponse

projects_router = ProtectedAPIRouter()


@projects_router.post(
    "",
    response_model=StandardResponse[ProjectRead],
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def create_project(
    payload: ProjectCreatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.PROJECTS_WRITE)),
    project_service: ProjectService = Depends(get_project_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    created_project = await project_service.create_project(
        payload, user_id=current_user.id
    )
    await cache_service.invalidate_prefixes("projects_list", "project_detail")
    return StandardResponse.ok(data=created_project)


@projects_router.get(
    "",
    response_model=StandardResponse[list[ProjectRead]],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=300, key_prefix="projects_list")
async def list_projects(
    request: Request,
    department_id: Optional[uuid.UUID] = Query(None),
    current_user: User = Depends(require_permission(PermissionEnum.PROJECTS_READ)),
    project_service: ProjectService = Depends(get_project_service),
):
    projects = await project_service.list_projects(
        department_id=department_id, user_id=current_user.id
    )
    return StandardResponse.ok(data=projects)


@projects_router.get(
    "/{project_id}",
    response_model=StandardResponse[ProjectRead],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=300, key_prefix="project_detail")
async def get_project(
    request: Request,
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission(PermissionEnum.PROJECTS_READ)),
    project_service: ProjectService = Depends(get_project_service),
):
    project = await project_service.get_project_by_id(
        project_id, user_id=current_user.id
    )
    return StandardResponse.ok(data=project)


@projects_router.put(
    "/{project_id}",
    response_model=StandardResponse[ProjectRead],
    response_model_exclude_none=True,
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.PROJECTS_WRITE)),
    project_service: ProjectService = Depends(get_project_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    updated_project = await project_service.update_project(
        project_id, payload, user_id=current_user.id
    )
    await cache_service.invalidate_prefixes("projects_list", "project_detail")
    return StandardResponse.ok(data=updated_project)
