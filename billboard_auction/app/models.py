from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; everything we store is UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


Money = Numeric(12, 2)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role in ('owner','advertiser')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    company: Mapped[Optional[str]] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Billboard(Base):
    __tablename__ = "billboards"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80), index=True)
    address: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(60))  # e.g. "Unipole", "Digital LED"
    width_ft: Mapped[int] = mapped_column(Integer)
    height_ft: Mapped[int] = mapped_column(Integer)
    daily_impressions: Mapped[int] = mapped_column(Integer, default=0)
    illuminated: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship()


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint("floor_price > 0", name="ck_auction_floor"),
        CheckConstraint("min_increment > 0", name="ck_auction_increment"),
        CheckConstraint("status in ('open','closed','no_sale','cancelled')", name="ck_auction_status"),
        Index("ix_auctions_status_end", "status", "end_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    billboard_id: Mapped[int] = mapped_column(ForeignKey("billboards.id"), index=True)
    pricing_rule: Mapped[str] = mapped_column(String(32), default="first_price")
    floor_price: Mapped[Decimal] = mapped_column(Money)
    min_increment: Mapped[Decimal] = mapped_column(Money)
    slot_start: Mapped[date] = mapped_column(Date)  # first day the ad runs
    slot_end: Mapped[date] = mapped_column(Date)  # last day the ad runs
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    anti_snipe_seconds: Mapped[int] = mapped_column(Integer, default=120)
    extensions: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")

    # Denormalised from the bid ledger so listings stay cheap to read.
    current_price: Mapped[Optional[Decimal]] = mapped_column(Money)
    leader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    bid_count: Mapped[int] = mapped_column(Integer, default=0)

    winner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    final_price: Mapped[Optional[Decimal]] = mapped_column(Money)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    billboard: Mapped[Billboard] = relationship()


class Bid(Base):
    """Append-only ledger. The winner is always derivable by replaying it."""

    __tablename__ = "bids"
    __table_args__ = (
        UniqueConstraint("auction_id", "seq", name="uq_bid_auction_seq"),
        CheckConstraint("amount > 0", name="ck_bid_amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey("auctions.id"), index=True)
    bidder_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 1,2,3... per auction
    amount: Mapped[Decimal] = mapped_column(Money)
    max_amount: Mapped[Optional[Decimal]] = mapped_column(Money)  # proxy ceiling
    price_after: Mapped[Decimal] = mapped_column(Money)
    leader_after_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Placement(Base):
    """The contract created when an auction is won: who runs which creative."""

    __tablename__ = "placements"
    __table_args__ = (
        CheckConstraint(
            "status in ('awaiting_creative','in_review','approved','rejected')",
            name="ck_placement_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey("auctions.id"), unique=True)
    billboard_id: Mapped[int] = mapped_column(ForeignKey("billboards.id"), index=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(24), default="awaiting_creative")
    creative_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creative_path: Mapped[Optional[str]] = mapped_column(String(255))
    creative_type: Mapped[Optional[str]] = mapped_column(String(80))
    creative_name: Mapped[Optional[str]] = mapped_column(String(255))
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    payment_status: Mapped[str] = mapped_column(String(12), default="unpaid", server_default="unpaid")
    payment_ref: Mapped[Optional[str]] = mapped_column(String(120))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    auction: Mapped[Auction] = relationship()
    billboard: Mapped[Billboard] = relationship()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    auction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("auctions.id"))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HiddenAuction(Base):
    """An advertiser's personal 'hide this listing' choice. Only affects their own marketplace view."""

    __tablename__ = "hidden_auctions"
    __table_args__ = (UniqueConstraint("user_id", "auction_id", name="uq_hidden_user_auction"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey("auctions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
