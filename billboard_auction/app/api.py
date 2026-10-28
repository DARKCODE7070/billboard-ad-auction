from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.orm import Session

from . import config, services
from .database import get_db
from .models import Auction, Bid, Billboard, HiddenAuction, Notification, Placement, User, as_utc, utcnow
from .security import create_token, hash_password, read_token, verify_password
from .services import ServiceError

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Auth plumbing
# ---------------------------------------------------------------------------


def optional_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> Optional[User]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    uid = read_token(authorization[7:].strip())
    return db.get(User, uid) if uid else None


def require_user(user: Optional[User] = Depends(optional_user)) -> User:
    if user is None:
        raise ServiceError("UNAUTHENTICATED", "Sign in to continue.", 401)
    return user


def require_owner(user: User = Depends(require_user)) -> User:
    if user.role != "owner":
        raise ServiceError("FORBIDDEN", "This is for billboard owners.", 403)
    return user


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
Price = Decimal


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    name: str = Field(min_length=1, max_length=120)
    company: Optional[str] = Field(default=None, max_length=160)
    password: str = Field(min_length=8, max_length=200)
    role: Literal["owner", "advertiser"]


class LoginIn(BaseModel):
    email: str
    password: str


class BillboardIn(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    city: str = Field(min_length=2, max_length=80)
    address: str = Field(min_length=3, max_length=255)
    format: str = Field(min_length=2, max_length=60)
    width_ft: int = Field(gt=0, le=500)
    height_ft: int = Field(gt=0, le=500)
    daily_impressions: int = Field(ge=0, le=100_000_000)
    illuminated: bool = False
    description: str = Field(default="", max_length=2000)


class AuctionIn(BaseModel):
    billboard_id: int
    floor_price: Price = Field(gt=0, max_digits=12, decimal_places=2)
    min_increment: Price = Field(gt=0, max_digits=12, decimal_places=2)
    slot_start: date
    slot_end: date
    end_at: datetime
    start_at: Optional[datetime] = None
    anti_snipe_seconds: int = Field(default=config.DEFAULT_ANTI_SNIPE_SECONDS, ge=0, le=900)
    pricing_rule: Literal["first_price"] = "first_price"


class BidIn(BaseModel):
    amount: Price = Field(gt=0, max_digits=12, decimal_places=2)
    max_amount: Optional[Price] = Field(default=None, gt=0, max_digits=12, decimal_places=2)


class PaymentIn(BaseModel):
    paid: bool = True
    ref: Optional[str] = Field(default=None, max_length=120)


class ReviewIn(BaseModel):
    approve: bool
    note: Optional[str] = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------
def f(x):
    return None if x is None else float(x)


def iso(dt):
    return None if dt is None else as_utc(dt).isoformat()


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "company": u.company, "role": u.role}


def billboard_out(bb: Billboard) -> dict:
    return {
        "id": bb.id,
        "title": bb.title,
        "city": bb.city,
        "address": bb.address,
        "format": bb.format,
        "width_ft": bb.width_ft,
        "height_ft": bb.height_ft,
        "daily_impressions": bb.daily_impressions,
        "illuminated": bb.illuminated,
        "description": bb.description,
        "owner_name": bb.owner.company or bb.owner.name,
    }


def viewer_positions(db: Session, viewer: Optional[User], auction_ids: list[int]) -> dict[int, dict]:
    if viewer is None or not auction_ids:
        return {}
    rows = db.execute(
        select(Bid.auction_id, Bid.amount, Bid.max_amount, Bid.seq)
        .where(Bid.bidder_id == viewer.id, Bid.auction_id.in_(auction_ids))
        .order_by(Bid.seq)
    ).all()
    out: dict[int, dict] = {}
    for aid, amount, mx, _ in rows:
        cur = out.setdefault(aid, {"last_amount": 0, "ceiling": Decimal(0), "bids": 0})
        cur["last_amount"] = amount
        cur["ceiling"] = max(cur["ceiling"], mx or amount)
        cur["bids"] += 1
    return out


def auction_out(a: Auction, viewer: Optional[User] = None, pos: Optional[dict] = None, now=None) -> dict:
    now = now or utcnow()
    end = as_utc(a.end_at)
    next_min = a.floor_price if a.bid_count == 0 else a.current_price + a.min_increment
    out = {
        "id": a.id,
        "status": a.status,
        "pricing_rule": a.pricing_rule,
        "floor_price": f(a.floor_price),
        "min_increment": f(a.min_increment),
        "current_price": f(a.current_price),
        "next_min_bid": f(next_min),
        "bid_count": a.bid_count,
        "slot_start": a.slot_start.isoformat(),
        "slot_end": a.slot_end.isoformat(),
        "start_at": iso(a.start_at),
        "end_at": iso(a.end_at),
        "seconds_left": max(0, int((end - now).total_seconds())) if a.status == "open" else 0,
        "anti_snipe_seconds": a.anti_snipe_seconds,
        "extensions": a.extensions,
        "final_price": f(a.final_price),
        "closed_at": iso(a.closed_at),
        "server_time": now.isoformat(),
        "billboard": billboard_out(a.billboard),
        "you": None,
    }
    if viewer is not None:
        pos = pos or {}
        out["you"] = {
            "has_bid": bool(pos),
            "is_leading": a.status == "open" and a.leader_id == viewer.id,
            "won": a.status == "closed" and a.winner_id == viewer.id,
            "my_ceiling": f(pos.get("ceiling")) if pos else None,
            "is_owner": a.billboard.owner_id == viewer.id,
        }
    return out


def placement_out(p: Placement, viewer: User) -> dict:
    a = p.auction
    return {
        "id": p.id,
        "status": p.status,
        "amount": f(p.amount),
        "creative_due_at": iso(p.creative_due_at),
        "creative_type": p.creative_type,
        "creative_name": p.creative_name,
        "has_creative": bool(p.creative_path),
        "review_note": p.review_note,
        "payment_status": p.payment_status,
        "payment_ref": p.payment_ref if p.advertiser_id == viewer.id or p.billboard.owner_id == viewer.id else None,
        "paid_at": iso(p.paid_at),
        "auction_id": a.id,
        "slot_start": a.slot_start.isoformat(),
        "slot_end": a.slot_end.isoformat(),
        "billboard": billboard_out(p.billboard),
        "advertiser_name": p.billboard and db_name(p),
        "updated_at": iso(p.updated_at),
        "as": "advertiser" if p.advertiser_id == viewer.id else "owner",
    }


def db_name(p: Placement) -> str:
    from sqlalchemy.orm import object_session

    adv = object_session(p).get(User, p.advertiser_id)
    return adv.company or adv.name


def notification_out(n: Notification) -> dict:
    return {
        "id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
        "auction_id": n.auction_id, "read": n.read, "created_at": iso(n.created_at),
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@router.post("/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.execute(select(User.id).where(User.email == email)).first():
        raise ServiceError("EMAIL_TAKEN", "An account with that email already exists.", 409)
    user = User(email=email, name=body.name.strip(), company=(body.company or "").strip() or None,
                role=body.role, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return {"token": create_token(user.id), "user": user_out(user)}


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == body.email.strip().lower())).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise ServiceError("BAD_CREDENTIALS", "That email and password don't match.", 401)
    return {"token": create_token(user.id), "user": user_out(user)}


@router.get("/me")
def me(user: User = Depends(require_user)):
    return user_out(user)


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------
@router.get("/auctions")
def list_auctions(
    status: Literal["open", "closed", "all"] = "open",
    q: Optional[str] = Query(None, max_length=80),
    city: Optional[str] = None,
    format: Optional[str] = None,
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    ending_within_hours: Optional[float] = Query(None, gt=0),
    sort: Literal["ending", "price_low", "price_high", "newest", "popular"] = "ending",
    limit: int = Query(24, ge=1, le=60),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(optional_user),
):
    price_expr = func.coalesce(Auction.current_price, Auction.floor_price)
    stmt = select(Auction).join(Billboard, Billboard.id == Auction.billboard_id)
    if viewer is not None:  # drop listings this person chose to hide
        stmt = stmt.where(Auction.id.not_in(select(HiddenAuction.auction_id).where(HiddenAuction.user_id == viewer.id)))
    if status == "open":
        stmt = stmt.where(Auction.status == "open")
    elif status == "closed":
        stmt = stmt.where(Auction.status == "closed")
    else:
        stmt = stmt.where(Auction.status.in_(("open", "closed")))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Billboard.title).like(like), func.lower(Billboard.address).like(like),
                              func.lower(Billboard.city).like(like)))
    if city:
        stmt = stmt.where(Billboard.city == city)
    if format:
        stmt = stmt.where(Billboard.format == format)
    if min_price is not None:
        stmt = stmt.where(price_expr >= min_price)
    if max_price is not None:
        stmt = stmt.where(price_expr <= max_price)
    if ending_within_hours:
        from datetime import timedelta

        stmt = stmt.where(Auction.end_at <= utcnow() + timedelta(hours=ending_within_hours))
    order = {
        "ending": Auction.end_at.asc() if status == "open" else Auction.closed_at.desc(),
        "price_low": price_expr.asc(),
        "price_high": price_expr.desc(),
        "newest": Auction.created_at.desc(),
        "popular": desc(Auction.bid_count),
    }[sort]
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(db.execute(stmt.order_by(order, Auction.id).limit(limit).offset(offset)).scalars())
    pos = viewer_positions(db, viewer, [a.id for a in rows])
    now = utcnow()
    return {"total": total, "items": [auction_out(a, viewer, pos.get(a.id), now) for a in rows]}


@router.get("/facets")
def facets(db: Session = Depends(get_db)):
    cities = db.execute(
        select(Billboard.city, func.count(Auction.id)).join(Auction, Auction.billboard_id == Billboard.id)
        .where(Auction.status == "open").group_by(Billboard.city).order_by(Billboard.city)
    ).all()
    formats = db.execute(
        select(Billboard.format, func.count(Auction.id)).join(Auction, Auction.billboard_id == Billboard.id)
        .where(Auction.status == "open").group_by(Billboard.format).order_by(Billboard.format)
    ).all()
    return {"cities": [{"name": c, "count": n} for c, n in cities],
            "formats": [{"name": c, "count": n} for c, n in formats]}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    open_n = db.execute(select(func.count()).select_from(Auction).where(Auction.status == "open")).scalar_one()
    sold = db.execute(select(func.count(), func.coalesce(func.sum(Auction.final_price), 0))
                      .where(Auction.status == "closed")).one()
    bidders = db.execute(select(func.count(func.distinct(Bid.bidder_id)))).scalar_one()
    return {"open_auctions": open_n, "sold_count": sold[0], "sold_value": float(sold[1]), "bidders": bidders}


@router.get("/auctions/{auction_id}")
def get_auction(auction_id: int, db: Session = Depends(get_db), viewer: Optional[User] = Depends(optional_user)):
    a = db.get(Auction, auction_id)
    if a is None:
        raise ServiceError("NOT_FOUND", "Auction not found.", 404)
    pos = viewer_positions(db, viewer, [a.id])
    return auction_out(a, viewer, pos.get(a.id))


@router.get("/auctions/{auction_id}/bids")
def auction_bids(auction_id: int, db: Session = Depends(get_db), viewer: Optional[User] = Depends(optional_user)):
    a = db.get(Auction, auction_id)
    if a is None:
        raise ServiceError("NOT_FOUND", "Auction not found.", 404)
    bids = list(db.execute(select(Bid).where(Bid.auction_id == auction_id).order_by(Bid.seq)).scalars())
    labels: dict[int, str] = {}
    for b in bids:
        labels.setdefault(b.bidder_id, f"Bidder {len(labels) + 1}")
    items = [{
        "seq": b.seq,
        "bidder": labels[b.bidder_id],
        "mine": viewer is not None and b.bidder_id == viewer.id,
        "amount": f(b.amount),
        "price_after": f(b.price_after),
        "leader": labels[b.leader_after_id],
        "created_at": iso(b.created_at),
    } for b in bids]
    items.reverse()
    return {"items": items, "bidders": len(labels)}


@router.post("/auctions/{auction_id}/bids")
def place_bid(auction_id: int, body: BidIn, db: Session = Depends(get_db), user: User = Depends(require_user)):
    out = services.place_bid(db, auction_id, user, body.amount, body.max_amount)
    pos = viewer_positions(db, user, [auction_id])
    return {
        "is_leading": out.is_leading,
        "extended": out.extended,
        "message": ("You're in the lead." if out.is_leading else
                    "Bid placed, but another bidder's maximum is higher. Raise your bid to take the lead."),
        "auction": auction_out(out.auction, user, pos.get(auction_id)),
    }


# ---------------------------------------------------------------------------
# Owner tools
# ---------------------------------------------------------------------------
@router.post("/billboards")
def create_billboard(body: BillboardIn, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    bb = services.create_billboard(db, owner, **body.model_dump())
    return billboard_out(bb)


@router.delete("/billboards/{billboard_id}")
def remove_billboard(billboard_id: int, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    return services.remove_billboard(db, owner, billboard_id)


@router.get("/owner/billboards")
def owner_billboards(db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    rows = db.execute(select(Billboard).where(Billboard.owner_id == owner.id, Billboard.removed_at.is_(None)).order_by(Billboard.id.desc())).scalars()
    return {"items": [billboard_out(b) for b in rows]}


@router.post("/auctions")
def create_auction(body: AuctionIn, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    a = services.create_auction(db, owner, **body.model_dump())
    return auction_out(a, owner)


@router.post("/auctions/{auction_id}/cancel")
def cancel_auction(auction_id: int, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    return auction_out(services.cancel_auction(db, owner, auction_id), owner)


@router.get("/owner/auctions")
def owner_auctions(db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    rows = list(db.execute(
        select(Auction).join(Billboard, Billboard.id == Auction.billboard_id)
        .where(Billboard.owner_id == owner.id).order_by(
            case((Auction.status == "open", 0), else_=1), Auction.end_at.desc())
    ).scalars())
    now = utcnow()
    return {"items": [auction_out(a, owner, None, now) for a in rows]}


# ---------------------------------------------------------------------------
# Advertiser dashboard
# ---------------------------------------------------------------------------
@router.post("/auctions/{auction_id}/hide")
def hide_auction(auction_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if user.role != "advertiser":
        raise ServiceError("FORBIDDEN", "Only advertisers can hide listings.", 403)
    if db.get(Auction, auction_id) is None:
        raise ServiceError("NOT_FOUND", "Auction not found.", 404)
    exists = db.execute(select(HiddenAuction.id).where(HiddenAuction.user_id == user.id,
                                                       HiddenAuction.auction_id == auction_id)).first()
    if not exists:
        db.add(HiddenAuction(user_id=user.id, auction_id=auction_id))
        db.commit()
    return {"hidden": True, "auction_id": auction_id}


@router.delete("/auctions/{auction_id}/hide")
def unhide_auction(auction_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.execute(select(HiddenAuction).where(HiddenAuction.user_id == user.id,
                                                 HiddenAuction.auction_id == auction_id)).scalar_one_or_none()
    if row:
        db.delete(row)
        db.commit()
    return {"hidden": False, "auction_id": auction_id}


@router.get("/me/hidden")
def my_hidden(db: Session = Depends(get_db), user: User = Depends(require_user)):
    ids = list(db.execute(select(HiddenAuction.auction_id).where(HiddenAuction.user_id == user.id)
                          .order_by(HiddenAuction.id.desc())).scalars())
    rows = {a.id: a for a in db.execute(select(Auction).where(Auction.id.in_(ids))).scalars()} if ids else {}
    now = utcnow()
    return {"items": [auction_out(rows[i], user, None, now) for i in ids if i in rows]}


@router.get("/me/bids")
def my_bids(db: Session = Depends(get_db), user: User = Depends(require_user)):
    ids = list(db.execute(select(Bid.auction_id).where(Bid.bidder_id == user.id).distinct()).scalars())
    if not ids:
        return {"items": []}
    rows = list(db.execute(select(Auction).where(Auction.id.in_(ids)).order_by(Auction.end_at.desc())).scalars())
    pos = viewer_positions(db, user, ids)
    now = utcnow()
    items = []
    for a in rows:
        item = auction_out(a, user, pos.get(a.id), now)
        if a.status == "open":
            item["my_state"] = "leading" if a.leader_id == user.id else "outbid"
        else:
            item["my_state"] = "won" if a.winner_id == user.id else "lost"
        items.append(item)
    items.sort(key=lambda i: (i["status"] != "open", i["seconds_left"] if i["status"] == "open" else 0))
    return {"items": items}


@router.get("/me/placements")
def my_placements(db: Session = Depends(get_db), user: User = Depends(require_user)):
    stmt = select(Placement).order_by(Placement.updated_at.desc())
    if user.role == "advertiser":
        stmt = stmt.where(Placement.advertiser_id == user.id)
    else:
        stmt = stmt.join(Billboard, Billboard.id == Placement.billboard_id).where(Billboard.owner_id == user.id)
    return {"items": [placement_out(p, user) for p in db.execute(stmt).scalars()]}


@router.post("/placements/{placement_id}/creative")
async def upload_creative(placement_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                          user: User = Depends(require_user)):
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    p = services.submit_creative(db, user, placement_id, file.filename or "creative", data)
    return placement_out(p, user)


@router.get("/placements/{placement_id}/creative")
def get_creative(placement_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    p = services.get_placement_for(db, user, placement_id)
    if not p.creative_path:
        raise ServiceError("NOT_FOUND", "No creative uploaded yet.", 404)
    path = config.UPLOAD_DIR / p.creative_path
    if not path.exists():
        raise ServiceError("NOT_FOUND", "The creative file is missing.", 404)
    return FileResponse(path, media_type=p.creative_type, filename=p.creative_name)


@router.post("/placements/{placement_id}/payment")
def placement_payment(placement_id: int, body: PaymentIn, db: Session = Depends(get_db),
                      owner: User = Depends(require_owner)):
    p = services.set_payment(db, owner, placement_id, body.paid, body.ref)
    return placement_out(p, owner)


@router.post("/placements/{placement_id}/review")
def review_placement(placement_id: int, body: ReviewIn, db: Session = Depends(get_db),
                     owner: User = Depends(require_owner)):
    p = services.review_placement(db, owner, placement_id, body.approve, body.note)
    return placement_out(p, owner)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@router.get("/notifications")
def notifications(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = list(db.execute(select(Notification).where(Notification.user_id == user.id)
                           .order_by(Notification.id.desc()).limit(30)).scalars())
    unread = db.execute(select(func.count()).select_from(Notification)
                        .where(Notification.user_id == user.id, Notification.read.is_(False))).scalar_one()
    return {"unread": unread, "items": [notification_out(n) for n in rows]}


@router.post("/notifications/read")
def mark_read(db: Session = Depends(get_db), user: User = Depends(require_user)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.read.is_(False)).update({"read": True})
    db.commit()
    return {"ok": True}
