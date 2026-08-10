import uuid
from typing import Optional

from fastapi import HTTPException, status

from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.timesheet import Project
from app.modules.audit.repository import AuditLogRepository
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import (
    ProjectCreatePayload,
    ProjectRead,
    ProjectUpdatePayload,
)


class ProjectService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        audit_repository: AuditLogRepository,
    ):
        self._project_repo = project_repository
        self._audit_repo = audit_repository

    async def create_project(self, payload: ProjectCreatePayload) -> ProjectRead:
        existing_project = await self._project_repo.get_by_code(payload.code)
        if existing_project:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Project with code '{payload.code}' already exists.",
            )

        existing_name = await self._project_repo.get_by_name(payload.name)
        if existing_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Project with name '{payload.name}' already exists.",
            )

        departments = []
        if payload.department_ids:
            departments = list(
                await self._project_repo.get_departments_by_ids(payload.department_ids)
            )
            if len(departments) != len(set(payload.department_ids)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One or more specified department IDs were not found.",
                )

        new_project = Project(
            name=payload.name,
            code=payload.code,
            is_active=payload.is_active,
            departments=departments,
        )
        created_project = await self._project_repo.create(new_project)

        audit_entry = AuditLog(
            action=AuditAction.PROJECT_CREATE.value,
            module=AuditModule.TIMESHEETS.value,
            entity=AuditEntity.PROJECT.value,
            entity_id=created_project.id,
            extra_metadata={"name": created_project.name, "code": created_project.code},
        )
        await self._audit_repo.create_log(audit_entry)

        return ProjectRead.model_validate(created_project)

    async def list_projects(
        self, department_id: Optional[uuid.UUID] = None
    ) -> list[ProjectRead]:
        projects = await self._project_repo.list_active_projects(department_id)
        return [ProjectRead.model_validate(p) for p in projects]

    async def get_project_by_id(self, project_id: uuid.UUID) -> ProjectRead:
        project = await self._project_repo.get_by_id(project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project with ID '{project_id}' not found.",
            )
        return ProjectRead.model_validate(project)

    async def update_project(
        self, project_id: uuid.UUID, payload: ProjectUpdatePayload
    ) -> ProjectRead:
        project = await self._project_repo.get_by_id(project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project with ID '{project_id}' not found.",
            )

        update_fields = payload.model_dump(exclude_unset=True)
        if "department_ids" in update_fields:
            dept_ids = update_fields.pop("department_ids")
            if dept_ids is not None:
                departments = list(
                    await self._project_repo.get_departments_by_ids(dept_ids)
                )
                if len(departments) != len(set(dept_ids)):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="One or more specified department IDs were not found.",
                    )
                project.departments = departments

        if "code" in update_fields and update_fields["code"] != project.code:
            existing = await self._project_repo.get_by_code(update_fields["code"])
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Project with code '{update_fields['code']}' already exists.",
                )

        if "name" in update_fields and update_fields["name"] != project.name:
            existing = await self._project_repo.get_by_name(update_fields["name"])
            if existing and existing.id != project_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Project with name '{update_fields['name']}' already exists.",
                )

        updated_project = await self._project_repo.update(project, update_fields)
        return ProjectRead.model_validate(updated_project)
