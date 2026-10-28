import os
import threading
from datetime import timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import engine, services
from app.database import SessionLocal
from app.main import app
from app.models import Auction, Bid, Notification, Placement, utcnow
from app.services import ServiceError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_auction(db, owner, minutes=60, floor=100, inc=10, offset_days=3, snipe=120, now=None):
    now = now or utcnow()
    bb = services.create_billboard(db, owner, title="Test board", city="Pune", address="FC Road", format="Unipole",
                                   width_ft=40, height_ft=20, daily_impressions=1000, illuminated=True, description="")
    end = now + timedelta(minutes=minutes)
    return services.create_auction(
        db, owner, bb.id, D(floor), D(inc), slot_start=(end + timedelta(days=offset_days)).date(),
        slot_end=(end + timedelta(days=offset_days + 10)).date(), end_at=end, anti_snipe_seconds=snipe, now=now)


# ---------------------------------------------------------------- bidding
def test_bid_updates_denormalised_state_and_notifies_outbid_leader(db, owner, ads):
    a = make_auction(db, owner)
    services.place_bid(db, a.id, ads[0], D(100))
    out = services.place_bid(db, a.id, ads[1], D(110))
    db.refresh(a)
    assert out.is_leading and a.current_price == 110 and a.leader_id == ads[1].id and a.bid_count == 2
    note = db.execute(select(Notification).where(Notification.user_id == ads[0].id)).scalar_one()
    assert note.kind == "outbid"


def test_rejected_bids_leave_no_trace(db, owner, ads):
    a = make_auction(db, owner)
    services.place_bid(db, a.id, ads[0], D(100))
    with pytest.raises(ServiceError) as e:
        services.place_bid(db, a.id, ads[1], D(105))
    assert e.value.code == "BID_TOO_LOW" and e.value.status == 422
    assert db.query(Bid).count() == 1


def test_owner_and_wrong_role_cannot_bid(db, owner, ads):
    a = make_auction(db, owner)
    with pytest.raises(ServiceError) as e:
        services.place_bid(db, a.id, owner, D(100))
    assert e.value.code == "FORBIDDEN"
    from tests.conftest import make_user
    other_owner = make_user(db, "o2@test.dev", "owner")
    with pytest.raises(ServiceError) as e:
        services.place_bid(db, a.id, other_owner, D(100))
    assert e.value.status == 403


def test_cannot_bid_after_deadline(db, owner, ads):
    a = make_auction(db, owner, minutes=5)
    with pytest.raises(ServiceError) as e:
        services.place_bid(db, a.id, ads[0], D(100), now=utcnow() + timedelta(minutes=6))
    assert e.value.code == "AUCTION_CLOSED"


def test_late_bid_extends_deadline_early_bid_does_not(db, owner, ads):
    a = make_auction(db, owner, minutes=60)
    original = a.end_at
    assert not services.place_bid(db, a.id, ads[0], D(100)).extended
    late = utcnow() + timedelta(minutes=59)
    out = services.place_bid(db, a.id, ads[1], D(110), now=late)
    db.refresh(a)
    assert out.extended and a.extensions == 1 and a.end_at.replace(tzinfo=None) > original.replace(tzinfo=None)


def test_proxy_bid_end_to_end(db, owner, ads):
    a = make_auction(db, owner)
    services.place_bid(db, a.id, ads[0], D(100), D(1000))
    out = services.place_bid(db, a.id, ads[1], D(500))
    db.refresh(a)
    assert not out.is_leading and a.leader_id == ads[0].id and a.current_price == 510


# ---------------------------------------------------------------- closing
def test_close_awards_highest_bid_creates_one_placement_and_notifies(db, owner, ads):
    a = make_auction(db, owner, minutes=5)
    services.place_bid(db, a.id, ads[0], D(100), D(400))
    services.place_bid(db, a.id, ads[1], D(300))
    after = utcnow() + timedelta(minutes=6)
    assert services.close_auction(db, a.id, now=after) == "closed"
    assert services.close_auction(db, a.id, now=after) == "already"  # idempotent
    db.refresh(a)
    assert a.status == "closed" and a.winner_id == ads[0].id and a.final_price == 310
    p = db.execute(select(Placement)).scalar_one()
    assert p.advertiser_id == ads[0].id and p.amount == 310 and p.status == "awaiting_creative"
    kinds = {(n.user_id, n.kind) for n in db.execute(select(Notification)).scalars()}
    assert (ads[0].id, "won") in kinds and (owner.id, "sold") in kinds and (ads[1].id, "lost") in kinds


def test_close_before_deadline_does_nothing(db, owner, ads):
    a = make_auction(db, owner, minutes=30)
    services.place_bid(db, a.id, ads[0], D(100))
    assert services.close_auction(db, a.id) == "not_due"
    db.refresh(a)
    assert a.status == "open"


def test_auction_without_bids_ends_as_no_sale(db, owner):
    a = make_auction(db, owner, minutes=5)
    assert services.close_auction(db, a.id, now=utcnow() + timedelta(minutes=6)) == "no_sale"
    db.refresh(a)
    assert a.status == "no_sale" and db.query(Placement).count() == 0


def test_close_due_sweeps_only_overdue_auctions(db, owner, ads):
    soon = make_auction(db, owner, minutes=5)
    later = make_auction(db, owner, minutes=500, offset_days=40)
    services.place_bid(db, soon.id, ads[0], D(100))
    done = services.close_due(db, now=utcnow() + timedelta(minutes=10))
    assert done == [(soon.id, "closed")]
    db.refresh(later)
    assert later.status == "open"


def test_settlement_matches_ledger_replay(db, owner, ads):
    a = make_auction(db, owner, minutes=5)
    for who, amt, mx in [(0, 100, None), (1, 120, 300), (2, 200, None), (0, 250, 500)]:
        try:
            services.place_bid(db, a.id, ads[who], D(amt), D(mx) if mx else None)
        except ServiceError:
            pass
    ledger = [engine.LedgerBid(b.bidder_id, b.amount, b.max_amount, b.seq)
              for b in db.execute(select(Bid).order_by(Bid.seq)).scalars()]
    expected = engine.settle(a.floor_price, a.min_increment, ledger)
    services.close_auction(db, a.id, now=utcnow() + timedelta(minutes=6))
    db.refresh(a)
    assert (a.winner_id, a.final_price) == (expected.winner_id, expected.price)


def test_cancel_only_without_bids(db, owner, ads):
    a = make_auction(db, owner)
    services.cancel_auction(db, owner, a.id)
    b = make_auction(db, owner, offset_days=30)
    services.place_bid(db, b.id, ads[0], D(100))
    with pytest.raises(ServiceError) as e:
        services.cancel_auction(db, owner, b.id)
    assert e.value.code == "HAS_BIDS"


def test_remove_billboard_with_bids_cancels_and_notifies(db, owner, ads):
    from app.models import Notification
    a = make_auction(db, owner)
    services.place_bid(db, a.id, ads[0], D(100))
    r = services.remove_billboard(db, owner, a.billboard_id)
    db.refresh(a)
    assert r["auctions_cancelled"] == 1 and a.status == "cancelled"
    assert db.query(Notification).filter_by(user_id=ads[0].id, kind="cancelled").count() == 1
    with pytest.raises(ServiceError) as e:
        services.create_auction(db, owner, a.billboard_id, D(100), D(10), a.slot_start, a.slot_end,
                                utcnow() + timedelta(hours=2))
    assert e.value.code == "REMOVED"


def test_hidden_auction_is_per_advertiser(db, owner, ads):
    from app.models import HiddenAuction
    from sqlalchemy import select
    a = make_auction(db, owner)
    db.add(HiddenAuction(user_id=ads[0].id, auction_id=a.id))
    db.commit()
    mine = {r for r in db.execute(select(HiddenAuction.auction_id).where(HiddenAuction.user_id == ads[0].id)).scalars()}
    theirs = {r for r in db.execute(select(HiddenAuction.auction_id).where(HiddenAuction.user_id == ads[1].id)).scalars()}
    assert a.id in mine and a.id not in theirs


def test_overlapping_display_periods_are_refused(db, owner):
    a = make_auction(db, owner)
    with pytest.raises(ServiceError) as e:
        services.create_auction(db, owner, a.billboard_id, D(100), D(10), a.slot_start, a.slot_end,
                                utcnow() + timedelta(hours=2))
    assert e.value.code in ("SLOT_TAKEN", "SLOT_BEFORE_DEADLINE")


# ---------------------------------------------------------------- creative hand-off
def test_creative_upload_review_cycle(db, owner, ads):
    a = make_auction(db, owner, minutes=5)
    services.place_bid(db, a.id, ads[0], D(100))
    services.close_auction(db, a.id, now=utcnow() + timedelta(minutes=6))
    p = db.execute(select(Placement)).scalar_one()

    with pytest.raises(ServiceError) as e:
        services.submit_creative(db, ads[0], p.id, "evil.png", b"MZ\x90\x00 not an image")
    assert e.value.code == "BAD_TYPE"
    with pytest.raises(ServiceError) as e:
        services.submit_creative(db, ads[1], p.id, "x.png", PNG)
    assert e.value.status == 403

    services.submit_creative(db, ads[0], p.id, "ad.png", PNG)
    db.refresh(p)
    assert p.status == "in_review" and p.creative_type == "image/png"

    with pytest.raises(ServiceError) as e:
        services.review_placement(db, owner, p.id, approve=False, note="")
    assert e.value.code == "NOTE_REQUIRED"
    services.review_placement(db, owner, p.id, approve=False, note="Logo is cut off")
    db.refresh(p)
    assert p.status == "rejected" and p.review_note == "Logo is cut off"

    services.submit_creative(db, ads[0], p.id, "ad2.png", PNG)
    with pytest.raises(ServiceError) as e:
        services.review_placement(db, owner, p.id, approve=True, note=None)
    assert e.value.code == "PAYMENT_PENDING"
    services.set_payment(db, owner, p.id, True, "UTR123")
    db.refresh(p)
    assert p.payment_status == "paid" and p.payment_ref == "UTR123" and p.paid_at is not None
    services.review_placement(db, owner, p.id, approve=True, note=None)
    db.refresh(p)
    assert p.status == "approved"
    with pytest.raises(ServiceError):
        services.submit_creative(db, ads[0], p.id, "late.png", PNG)


# ---------------------------------------------------------------- HTTP API
def register(c, role, email):
    r = c.post("/api/auth/register", json={"email": email, "name": "T " + role, "password": "password123", "role": role})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_full_http_journey():
    with TestClient(app) as c:
        o = register(c, "owner", "own@t.dev")
        a1 = register(c, "advertiser", "a1@t.dev")
        a2 = register(c, "advertiser", "a2@t.dev")

        assert c.post("/api/auth/login", json={"email": "own@t.dev", "password": "nope"}).json()["code"] == "BAD_CREDENTIALS"
        assert c.get("/api/me").status_code == 401
        assert c.post("/api/billboards", headers=a1, json={}).status_code in (403, 422)

        bb = c.post("/api/billboards", headers=o, json={
            "title": "Ring Road gantry", "city": "Pune", "address": "Baner Road", "format": "Gantry",
            "width_ft": 60, "height_ft": 15, "daily_impressions": 90000, "illuminated": True}).json()
        end = (utcnow() + timedelta(hours=2)).isoformat()
        day = (utcnow() + timedelta(days=5)).date()
        r = c.post("/api/auctions", headers=o, json={
            "billboard_id": bb["id"], "floor_price": 5000, "min_increment": 500, "end_at": end,
            "slot_start": day.isoformat(), "slot_end": (day + timedelta(days=20)).isoformat()})
        assert r.status_code == 200, r.text
        aid = r.json()["id"]

        listing = c.get("/api/auctions", params={"city": "Pune"}).json()
        assert listing["total"] == 1 and listing["items"][0]["next_min_bid"] == 5000

        low = c.post(f"/api/auctions/{aid}/bids", headers=a1, json={"amount": 4000})
        assert low.status_code == 422 and low.json()["code"] == "BID_TOO_LOW"
        ok = c.post(f"/api/auctions/{aid}/bids", headers=a1, json={"amount": 5000, "max_amount": 9000}).json()
        assert ok["is_leading"] and ok["auction"]["you"]["is_leading"]
        lost = c.post(f"/api/auctions/{aid}/bids", headers=a2, json={"amount": 6000}).json()
        assert not lost["is_leading"] and lost["auction"]["current_price"] == 6500

        hist = c.get(f"/api/auctions/{aid}/bids", headers=a1).json()
        assert [b["bidder"] for b in hist["items"]] == ["Bidder 2", "Bidder 1"] and hist["items"][1]["mine"]
        assert "max_amount" not in hist["items"][0]  # ceilings stay private

        # Fast-forward: close it the way the sweeper would.
        with SessionLocal() as db:
            services.close_due(db, now=utcnow() + timedelta(hours=3))
        closed = c.get(f"/api/auctions/{aid}", headers=a1).json()
        assert closed["status"] == "closed" and closed["final_price"] == 6500 and closed["you"]["won"]

        pl = c.get("/api/me/placements", headers=a1).json()["items"][0]
        assert pl["status"] == "awaiting_creative"
        bad = c.post(f"/api/placements/{pl['id']}/creative", headers=a1, files={"file": ("x.png", b"hello", "image/png")})
        assert bad.status_code == 415
        up = c.post(f"/api/placements/{pl['id']}/creative", headers=a1, files={"file": ("ad.png", PNG, "image/png")})
        assert up.status_code == 200 and up.json()["status"] == "in_review"
        assert c.get(f"/api/placements/{pl['id']}/creative", headers=a2).status_code == 403
        assert c.get(f"/api/placements/{pl['id']}/creative", headers=o).status_code == 200
        assert c.post(f"/api/placements/{pl['id']}/review", headers=o, json={"approve": True}).status_code == 409
        pay = c.post(f"/api/placements/{pl['id']}/payment", headers=o, json={"paid": True, "ref": "UTR1"})
        assert pay.status_code == 200 and pay.json()["payment_status"] == "paid"
        rv = c.post(f"/api/placements/{pl['id']}/review", headers=o, json={"approve": True})
        assert rv.json()["status"] == "approved"

        notes = c.get("/api/notifications", headers=a1).json()
        assert {n["kind"] for n in notes["items"]} >= {"won", "approved"}
        assert c.get("/api/stats").json()["sold_value"] == 6500


# ---------------------------------------------------------------- concurrency (PostgreSQL only)
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
                    reason="row locking needs PostgreSQL: set TEST_DATABASE_URL")
def test_concurrent_bids_are_serialised(db, owner):
    from tests.conftest import make_user
    a = make_auction(db, owner, minutes=60, floor=100, inc=1)
    bidders = [make_user(db, f"c{i}@t.dev") for i in range(12)]
    errors = []

    def worker(user, amount):
        with SessionLocal() as s:
            try:
                services.place_bid(s, a.id, s.get(type(user), user.id), D(amount))
            except ServiceError:
                pass  # losing a race to a higher bid is fine
            except Exception as e:  # anything else is a bug
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(u, 100 + i * 5)) for i, u in enumerate(bidders)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    with SessionLocal() as s:
        seqs = [b.seq for b in s.execute(select(Bid).where(Bid.auction_id == a.id).order_by(Bid.seq)).scalars()]
        assert seqs == list(range(1, len(seqs) + 1))  # no gaps, no duplicates
        auc = s.get(Auction, a.id)
        ledger = [engine.LedgerBid(b.bidder_id, b.amount, b.max_amount, b.seq)
                  for b in s.execute(select(Bid).where(Bid.auction_id == a.id)).scalars()]
        assert engine.replay(auc.floor_price, auc.min_increment, ledger).price == auc.current_price
