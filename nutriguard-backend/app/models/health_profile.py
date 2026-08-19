import uuid

from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class UserHealthProfileModel(Base):
    """Mirrors com.example.data.model.UserHealthProfile (one row per user)."""

    __tablename__ = "user_health_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    has_diabetes: Mapped[bool] = mapped_column("has_diabetes", Boolean, default=False)
    has_hypertension: Mapped[bool] = mapped_column("has_hypertension", Boolean, default=False)
    has_kidney_disease: Mapped[bool] = mapped_column("has_kidney_disease", Boolean, default=False)
    has_gout: Mapped[bool] = mapped_column("has_gout", Boolean, default=False)
    is_pregnant: Mapped[bool] = mapped_column("is_pregnant", Boolean, default=False)
    for_children: Mapped[bool] = mapped_column("for_children", Boolean, default=False)
    has_high_cholesterol: Mapped[bool] = mapped_column("has_high_cholesterol", Boolean, default=False)

    avoid_gluten: Mapped[bool] = mapped_column("avoid_gluten", Boolean, default=False)
    avoid_lactose: Mapped[bool] = mapped_column("avoid_lactose", Boolean, default=False)
    avoid_peanuts: Mapped[bool] = mapped_column("avoid_peanuts", Boolean, default=False)
    avoid_soy: Mapped[bool] = mapped_column("avoid_soy", Boolean, default=False)
    avoid_tree_nuts: Mapped[bool] = mapped_column("avoid_tree_nuts", Boolean, default=False)
    require_vegan: Mapped[bool] = mapped_column("require_vegan", Boolean, default=False)
    require_vegetarian: Mapped[bool] = mapped_column("require_vegetarian", Boolean, default=False)
    require_halal: Mapped[bool] = mapped_column("require_halal", Boolean, default=False)
    require_kosher: Mapped[bool] = mapped_column("require_kosher", Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="health_profile")  # noqa: F821
