import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Table, Text, Time, func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, ExcludeConstraint, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKey

# Approval lifecycle shared by properties and units.
ST_DRAFT = "draft"
ST_SUBMITTED = "submitted"
ST_CHANGES = "changes_requested"
ST_REJECTED = "rejected"
ST_APPROVED = "approved"
ST_PAUSED = "paused"            # host switched it off (was approved)
ST_UNPUBLISHED = "unpublished"  # admin switched it off (was approved)
ST_ARCHIVED = "archived"

EDITABLE_STATUSES = (ST_DRAFT, ST_CHANGES, ST_REJECTED)
LIVE_LIFECYCLE = (ST_APPROVED, ST_PAUSED, ST_UNPUBLISHED)  # has been approved at some point

unit_amenities = Table(
    "unit_amenities",
    Base.metadata,
    Column("unit_id", UUID(as_uuid=True), ForeignKey("units.id", ondelete="CASCADE"), primary_key=True),
    Column("amenity_id", UUID(as_uuid=True), ForeignKey("amenities.id", ondelete="CASCADE"), primary_key=True),
)


class PropertyType(UUIDPrimaryKey, Base):
    __tablename__ = "property_types"

    name: Mapped[str] = mapped_column(String(80), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Amenity(UUIDPrimaryKey, Base):
    __tablename__ = "amenities"

    name: Mapped[str] = mapped_column(String(80), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Property(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "properties"

    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("host_profiles.id"), index=True)
    type_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("property_types.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[str] = mapped_column(Text, default="")
    address_line1: Mapped[str] = mapped_column(String(200), default="")
    address_line2: Mapped[str] = mapped_column(String(200), default="")
    suburb: Mapped[str] = mapped_column(String(100), default="")
    state: Mapped[str] = mapped_column(String(50), default="")
    postcode: Mapped[str] = mapped_column(String(20), default="")
    country: Mapped[str] = mapped_column(String(2), default="AU")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    timezone: Mapped[str] = mapped_column(String(64), default="Australia/Sydney")
    checkin_instructions: Mapped[str] = mapped_column(Text, default="")  # shown to the guest only after confirmation
    cancellation_template_key: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default=ST_DRAFT, index=True)
    review_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    units: Mapped[list["Unit"]] = relationship(back_populates="property", cascade="all, delete-orphan", lazy="selectin")


class Unit(UUIDPrimaryKey, TimestampMixin, Base):
    """The bookable thing (a whole house, a room type...). Price and availability belong here."""

    __tablename__ = "units"

    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    price_per_24h: Mapped[int] = mapped_column(Integer, default=0)  # minor units (cents)
    currency: Mapped[str] = mapped_column(String(3), default="AUD")
    cleaning_fee: Mapped[int] = mapped_column(Integer, default=0)  # flat per booking, minor units
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    quantity: Mapped[int] = mapped_column(Integer, default=1)  # identical rooms of this type
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    beds: Mapped[int | None] = mapped_column(Integer)
    max_stay_hours: Mapped[int | None] = mapped_column(Integer)  # per-unit override of the global maximum
    checkin_from: Mapped[time] = mapped_column(Time, default=time(8, 0))
    checkin_to: Mapped[time] = mapped_column(Time, default=time(22, 0))
    status: Mapped[str] = mapped_column(String(20), default=ST_DRAFT, index=True)
    review_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped[Property] = relationship(back_populates="units")
    rooms: Mapped[list["Room"]] = relationship(back_populates="unit", cascade="all, delete-orphan", lazy="selectin",
                                                order_by="Room.created_at")
    amenities: Mapped[list[Amenity]] = relationship(secondary=unit_amenities, lazy="selectin")


class Room(UUIDPrimaryKey, Base):
    """One physical room of a unit. Bookings are assigned to a room, so the database can enforce no overlaps."""

    __tablename__ = "rooms"

    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    unit: Mapped[Unit] = relationship(back_populates="rooms")


class Media(UUIDPrimaryKey, Base):
    __tablename__ = "media"

    owner_type: Mapped[str] = mapped_column(String(20), index=True)  # property | unit
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    path: Mapped[str] = mapped_column(String(500))
    thumb_path: Mapped[str] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # approved = visible to the public; pending_add / pending_delete wait for admin approval of a photo change.
    status: Mapped[str] = mapped_column(String(20), default="approved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ListingRevision(UUIDPrimaryKey, Base):
    """A pending edit to an already-approved property/unit. The approved version stays live until it is accepted."""

    __tablename__ = "listing_revisions"

    entity_type: Mapped[str] = mapped_column(String(20), index=True)  # property | unit
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    photos_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | approved | rejected
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class RoomOccupancy(UUIDPrimaryKey, Base):
    """Every reason a room is unavailable (host block now; holds, bookings and iCal blocks in later phases).

    The exclusion constraint makes overlapping rows impossible at the database level, so double booking
    cannot happen even if two requests race."""

    __tablename__ = "room_occupancy"
    __table_args__ = (
        ExcludeConstraint(("room_id", "="), ("period", "&&"), using="gist", name="room_occupancy_no_overlap"),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    period = mapped_column(TSTZRANGE, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # host_block | hold | booking | external_block
    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
