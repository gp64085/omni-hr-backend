import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import UUID, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.timesheet import project_departments

if TYPE_CHECKING:
    from app.models.timesheet import Project
    from app.models.user import User


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="department")
    projects: Mapped[list["Project"]] = relationship(
        "Project", secondary=project_departments, back_populates="departments"
    )


class Designation(Base):
    __tablename__ = "designations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="designation")
