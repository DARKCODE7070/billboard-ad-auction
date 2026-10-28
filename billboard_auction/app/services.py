"""Business operations. Every function here owns its transaction boundaries.

Concurrency model
-----------------
`place_bid` and `close_auction` both take a row lock on the auction
(SELECT ... FOR UPDATE on PostgreSQL), so bids on one auction are strictly
serialised while different auctions proceed in parallel. As a backstop the
bid ledger has UNIQUE(auction_id, seq); if two writers ever slip past the
lock (e.g. SQLite in tests) one of them gets an IntegrityError and retries.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, engine
from .models import (
    Auction,
    Bid,
    Billboard,
    Notification,
    Placement,
    User,
    as_utc,
    utcnow,
)
from .scheduling import schedule_close

log = logging.getLogger("billboard.services")


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


# --------------------------------------------------------------------------
# Notifications (in-app row + an outbox log line standing in for email)
# --------------------------------------------------------------------------
def notify(db: Session, user_id: int, kind: str, title: str, body: str = "", auction_id: Optional[int] = None):
    db.add(Notification(user_id=user_id, kind=kind, title=title, body=body, auction_id=auction_id))
    log.info("notify user=%s kind=%s title=%s", user_id, kind, title)


def inr(value) -> str:
    n = int(Decimal(value))
    s = f"{n:,}"
    # Indian digit grouping: 12,34,567
    digits = str(n)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"₹{s}"


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------
def create_billboard(db: Session, owner: User, **fields) -> Billboard:
    if owner.role != "owner":
        raise ServiceError("FORBIDDEN", "Only billboard owners can list billboards.", 403)
    bb = Billboard(owner_id=owner.id, **fields)
    db.add(bb)
    db.commit()
    return bb


def create_auction(
    db: Session,
    owner: User,
    billboard_id: int,
    floor_price: Decimal,
    min_increment: Decimal,
    slot_start: date,
    slot_end: date,
    end_at: datetime,
    start_at: Optional[datetime] = None,
    anti_snipe_seconds: int = config.DEFAULT_ANTI_SNIPE_SECONDS,
    pricing_rule: str = "first_price",
    now: Optional[datetime] = None,
) -> Auction:
    now = now or utcnow()
    bb = db.get(Billboard, billboard_id)
    if bb is None:
        raise ServiceError("NOT_FOUND", "Billboard not found.", 404)
    if bb.owner_id != owner.id:
        raise ServiceError("FORBIDDEN", "You can only auction your own billboards.", 403)
    if bb.removed_at is not None:
        raise ServiceError("REMOVED", "This billboard has been removed.", 409)
    if pricing_rule not in engine.PRICING_RULES:
        raise ServiceError("RULE_UNKNOWN", f"Unknown pricing rule '{pricing_rule}'.", 422)

    start_at = as_utc(start_at) or now
    end_at = as_utc(end_at)
    if end_at <= now:
        raise ServiceError("DEADLINE_PAST", "The bidding deadline must be in the future.", 422)
    if end_at - start_at < timedelta(minutes=1):
        raise ServiceError("WINDOW_TOO_SHORT", "Bidding must stay open for at least one minute.", 422)
    if slot_end < slot_start:
        raise ServiceError("SLOT_INVALID", "The display period must end on or after its start date.", 422)
    if slot_start < end_at.date():
        raise ServiceError("SLOT_BEFORE_DEADLINE", "The display period must start on or after the bidding deadline.", 422)

    clash = db.execute(
        select(Auction.id).where(
            Auction.billboard_id == billboard_id,
            Auction.status.in_(("open", "closed")),
            Auction.slot_start <= slot_end,
            Auction.slot_end >= slot_start,
        )
    ).first()
    if clash:
        raise ServiceError("SLOT_TAKEN", "This billboard already has an auction covering those dates.", 409)

    auction = Auction(
        billboard_id=billboard_id,
        pricing_rule=pricing_rule,
        floor_price=engine.money(floor_price),
        min_increment=engine.money(min_increment),
        slot_start=slot_start,
        slot_end=slot_end,
        start_at=start_at,
        end_at=end_at,
        anti_snipe_seconds=anti_snipe_seconds,
        status="open",
    )
    db.add(auction)
    db.commit()
    schedule_close(auction.id, auction.end_at)
    return auction


def cancel_auction(db: Session, owner: User, auction_id: int) -> Auction:
    auction = db.execute(select(Auction).where(Auction.id == auction_id).with_for_update()).scalar_one_or_none()
    if auction is None:
        raise ServiceError("NOT_FOUND", "Auction not found.", 404)
    if auction.billboard.owner_id != owner.id:
        raise ServiceError("FORBIDDEN", "You can only cancel your own auctions.", 403)
    if auction.status != "open":
        raise ServiceError("NOT_OPEN", "Only open auctions can be cancelled.", 409)
    if auction.bid_count > 0:
        raise ServiceError("HAS_BIDS", "An auction with bids can't be cancelled.", 409)
    auction.status = "cancelled"
    auction.closed_at = utcnow()
    db.commit()
    return auction


# --------------------------------------------------------------------------
# Bidding
# --------------------------------------------------------------------------
@dataclass
class BidOutcome:
    auction: Auction
    bid: Bid
    is_leading: bool
    extended: bool
    min_next_bid: Decimal


def place_bid(
    db: Session,
    auction_id: int,
    bidder: User,
    amount: Decimal,
    max_amount: Optional[Decimal] = None,
    now: Optional[datetime] = None,
) -> BidOutcome:
    for _ in range(4):
        try:
            return _place_bid_once(db, auction_id, bidder, amount, max_amount, now)
        except IntegrityError:
            db.rollback()  # lost a race on (auction_id, seq): try again
    raise ServiceError("BUSY", "Lots of bidding right now. Try again.", 503)


def _ledger(db: Session, auction_id: int) -> list[Bid]:
    return list(db.execute(select(Bid).where(Bid.auction_id == auction_id).order_by(Bid.seq)).scalars())


def _to_ledger(bids: list[Bid]) -> list[engine.LedgerBid]:
    return [engine.LedgerBid(b.bidder_id, b.amount, b.max_amount, b.seq) for b in bids]


def _place_bid_once(db, auction_id, bidder, amount, max_amount, now) -> BidOutcome:
    now = now or utcnow()
    auction = db.execute(select(Auction).where(Auction.id == auction_id).with_for_update()).scalar_one_or_none()
    if auction is None:
        raise ServiceError("NOT_FOUND", "Auction not found.", 404)
    if bidder.role != "advertiser":
        raise ServiceError("FORBIDDEN", "Only advertiser accounts can place bids.", 403)
    if auction.billboard.owner_id == bidder.id:
        raise ServiceError("OWN_LISTING", "You can't bid on your own billboard.", 403)
    if auction.status != "open" or now >= as_utc(auction.end_at):
        raise ServiceError("AUCTION_CLOSED", "Bidding on this auction has closed.", 409)
    if now < as_utc(auction.start_at):
        raise ServiceError("NOT_STARTED", "Bidding hasn't opened yet.", 409)

    bids = _ledger(db, auction_id)
    standing = engine.replay(auction.floor_price, auction.min_increment, _to_ledger(bids))
    seq = len(bids) + 1
    try:
        result = engine.apply_bid(
            auction.floor_price, auction.min_increment, standing, bidder.id, amount, max_amount, seq
        )
    except engine.BidRejected as e:
        raise ServiceError(e.code, e.message, 422, **{k: str(v) for k, v in e.details.items()})

    new = result.standing
    bid = Bid(
        auction_id=auction.id,
        bidder_id=bidder.id,
        seq=seq,
        amount=engine.money(amount),
        max_amount=engine.money(max_amount) if max_amount is not None else None,
        price_after=new.price,
        leader_after_id=new.leader_id,
        created_at=now,
    )
    db.add(bid)
    auction.current_price = new.price
    auction.leader_id = new.leader_id
    auction.bid_count = seq

    extended = False
    end = as_utc(auction.end_at)
    if (end - now).total_seconds() <= auction.anti_snipe_seconds:
        new_end = now + timedelta(seconds=auction.anti_snipe_seconds)
        if new_end > end:
            auction.end_at = new_end
            auction.extensions += 1
            extended = True

    prev = result.previous_leader_id
    if result.leader_changed and prev is not None and prev != bidder.id:
        title = auction.billboard.title
        notify(
            db, prev, "outbid", f"You've been outbid on {title}",
            f"The price is now {inr(new.price)}. Bid again before the deadline.", auction.id,
        )
    db.commit()
    return BidOutcome(
        auction=auction,
        bid=bid,
        is_leading=new.leader_id == bidder.id,
        extended=extended,
        min_next_bid=new.next_min_bid(auction.floor_price, auction.min_increment),
    )


# --------------------------------------------------------------------------
# Closing
# --------------------------------------------------------------------------
def close_auction(db: Session, auction_id: int, now: Optional[datetime] = None) -> str:
    """Settle one auction. Safe to call repeatedly and from many workers.

    Returns: 'closed' | 'no_sale' | 'not_due' | 'already' | 'missing'
    """
    now = now or utcnow()
    auction = db.execute(select(Auction).where(Auction.id == auction_id).with_for_update()).scalar_one_or_none()
    if auction is None:
        db.rollback()
        return "missing"
    if auction.status != "open":
        db.rollback()
        return "already"
    if now < as_utc(auction.end_at):
        db.rollback()
        return "not_due"

    bids = _ledger(db, auction_id)
    settlement = engine.settle(auction.floor_price, auction.min_increment, _to_ledger(bids), auction.pricing_rule)
    bb = auction.billboard
    auction.closed_at = now

    if settlement.winner_id is None:
        auction.status = "no_sale"
        notify(db, bb.owner_id, "no_sale", f"No bids on {bb.title}",
               "The auction ended without bids. You can list the slot again.", auction.id)
        db.commit()
        return "no_sale"

    auction.status = "closed"
    auction.winner_id = settlement.winner_id
    auction.final_price = settlement.price
    auction.current_price = settlement.price
    auction.leader_id = settlement.winner_id
    due = now + timedelta(days=config.CREATIVE_DEADLINE_DAYS)
    db.add(Placement(
        auction_id=auction.id,
        billboard_id=bb.id,
        advertiser_id=settlement.winner_id,
        amount=settlement.price,
        status="awaiting_creative",
        creative_due_at=due,
    ))
    notify(db, settlement.winner_id, "won", f"You won {bb.title}",
           f"Your price is {inr(settlement.price)}. Upload your creative within "
           f"{config.CREATIVE_DEADLINE_DAYS} days to confirm the booking.", auction.id)
    notify(db, bb.owner_id, "sold", f"{bb.title} sold for {inr(settlement.price)}",
           "The winning advertiser has been asked to upload their creative.", auction.id)
    for loser in sorted({b.bidder_id for b in bids} - {settlement.winner_id}):
        notify(db, loser, "lost", f"Auction ended: {bb.title}",
               f"It sold for {inr(settlement.price)}. Other slots are open for bidding.", auction.id)
    db.commit()
    return "closed"


def close_due(db: Session, now: Optional[datetime] = None) -> list[tuple[int, str]]:
    now = now or utcnow()
    ids = list(db.execute(select(Auction.id).where(Auction.status == "open", Auction.end_at <= now)).scalars())
    return [(i, close_auction(db, i, now)) for i in ids]


# --------------------------------------------------------------------------
# Creative hand-off
# --------------------------------------------------------------------------
ALLOWED_CREATIVES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
}


def _sniff(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def get_placement_for(db: Session, user: User, placement_id: int) -> Placement:
    p = db.get(Placement, placement_id)
    if p is None:
        raise ServiceError("NOT_FOUND", "Booking not found.", 404)
    if user.id not in (p.advertiser_id, p.billboard.owner_id):
        raise ServiceError("FORBIDDEN", "You don't have access to this booking.", 403)
    return p


def submit_creative(db: Session, user: User, placement_id: int, filename: str, data: bytes) -> Placement:
    p = db.execute(select(Placement).where(Placement.id == placement_id).with_for_update()).scalar_one_or_none()
    if p is None:
        raise ServiceError("NOT_FOUND", "Booking not found.", 404)
    if p.advertiser_id != user.id:
        raise ServiceError("FORBIDDEN", "Only the winning advertiser can upload a creative.", 403)
    if p.status == "approved":
        raise ServiceError("LOCKED", "This creative is already approved.", 409)
    if not data:
        raise ServiceError("EMPTY_FILE", "That file is empty.", 422)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ServiceError("TOO_LARGE", f"Files can be up to {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", 413)
    kind = _sniff(data[:16])
    if kind is None or kind not in ALLOWED_CREATIVES:
        raise ServiceError("BAD_TYPE", "Upload a PNG, JPEG, WebP or MP4 file.", 415)

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{p.id}-{uuid.uuid4().hex}{ALLOWED_CREATIVES[kind]}"
    (config.UPLOAD_DIR / stored).write_bytes(data)
    if p.creative_path:
        (config.UPLOAD_DIR / p.creative_path).unlink(missing_ok=True)

    p.creative_path, p.creative_type = stored, kind
    p.creative_name = (filename or "creative")[:255]
    p.status, p.review_note = "in_review", None
    notify(db, p.billboard.owner_id, "creative", f"Creative ready to review for {p.billboard.title}",
           "Approve it or send it back with a note.", p.auction_id)
    db.commit()
    return p


def review_placement(db: Session, owner: User, placement_id: int, approve: bool, note: Optional[str]) -> Placement:
    p = db.execute(select(Placement).where(Placement.id == placement_id).with_for_update()).scalar_one_or_none()
    if p is None:
        raise ServiceError("NOT_FOUND", "Booking not found.", 404)
    if p.billboard.owner_id != owner.id:
        raise ServiceError("FORBIDDEN", "Only the billboard owner can review creatives.", 403)
    if p.status != "in_review":
        raise ServiceError("NOT_IN_REVIEW", "There's no creative waiting for review.", 409)
    if approve and p.payment_status != "paid":
        raise ServiceError("PAYMENT_PENDING", "Mark this booking as paid before approving the creative.", 409)
    if not approve and not (note or "").strip():
        raise ServiceError("NOTE_REQUIRED", "Tell the advertiser what to change.", 422)
    p.status = "approved" if approve else "rejected"
    p.review_note = (note or "").strip() or None
    if approve:
        notify(db, p.advertiser_id, "approved", f"Creative approved for {p.billboard.title}",
               "Your ad is booked for the display period.", p.auction_id)
    else:
        notify(db, p.advertiser_id, "rejected", f"Creative needs changes for {p.billboard.title}",
               p.review_note or "", p.auction_id)
    db.commit()
    return p


def set_payment(db: Session, owner: User, placement_id: int, paid: bool, ref: Optional[str]) -> Placement:
    p = db.execute(select(Placement).where(Placement.id == placement_id).with_for_update()).scalar_one_or_none()
    if p is None:
        raise ServiceError("NOT_FOUND", "Booking not found.", 404)
    if p.billboard.owner_id != owner.id:
        raise ServiceError("FORBIDDEN", "Only the billboard owner can record payments.", 403)
    if p.status == "approved" and not paid:
        raise ServiceError("ALREADY_APPROVED", "This creative is already approved.", 409)
    p.payment_status = "paid" if paid else "unpaid"
    p.payment_ref = (ref or "").strip() or None if paid else None
    p.paid_at = utcnow() if paid else None
    if paid:
        notify(db, p.advertiser_id, "payment", f"Payment received for {p.billboard.title}",
               f"Reference: {p.payment_ref}" if p.payment_ref else "", p.auction_id)
    db.commit()
    return p


def remove_billboard(db: Session, owner: User, billboard_id: int) -> dict:
    """Remove a billboard. Open auctions on it are cancelled even if they have bids;
    every bidder is notified. Past bookings are left untouched."""
    bb = db.execute(select(Billboard).where(Billboard.id == billboard_id).with_for_update()).scalar_one_or_none()
    if bb is None or bb.removed_at is not None:
        raise ServiceError("NOT_FOUND", "Billboard not found.", 404)
    if bb.owner_id != owner.id:
        raise ServiceError("FORBIDDEN", "You can only remove your own billboards.", 403)
    open_auctions = list(db.execute(
        select(Auction).where(Auction.billboard_id == bb.id, Auction.status == "open").with_for_update()).scalars())
    cancelled = 0
    for a in open_auctions:
        a.status = "cancelled"
        a.closed_at = utcnow()
        cancelled += 1
        bidders = db.execute(select(Bid.bidder_id).where(Bid.auction_id == a.id).distinct()).scalars().all()
        for uid in bidders:
            notify(db, uid, "cancelled", f"Auction cancelled: {bb.title}",
                   "The owner removed this billboard, so your bid is void and nothing is owed.", a.id)
    bb.removed_at = utcnow()
    db.commit()
    return {"removed": bb.id, "auctions_cancelled": cancelled}
