import random
from decimal import Decimal as D

import pytest

from app import engine
from app.engine import BidRejected, LedgerBid

FLOOR, INC = D("100"), D("10")


def bid(standing, who, amount, mx=None, seq=None):
    seq = seq if seq is not None else len(standing.positions) + 1
    return engine.apply_bid(FLOOR, INC, standing, who, D(amount), D(mx) if mx else None, seq)


def test_first_bid_must_meet_floor():
    with pytest.raises(BidRejected) as e:
        bid(engine.EMPTY, 1, 99)
    assert e.value.code == "BID_TOO_LOW"
    r = bid(engine.EMPTY, 1, 100)
    assert r.standing.leader_id == 1 and r.standing.price == 100


def test_next_bid_must_beat_price_by_increment():
    s = bid(engine.EMPTY, 1, 100).standing
    with pytest.raises(BidRejected) as e:
        bid(s, 2, 105)
    assert e.value.code == "BID_TOO_LOW" and e.value.details["min_bid"] == D("110.00")
    r = bid(s, 2, 110)
    assert r.standing.leader_id == 2 and r.standing.price == 110 and r.leader_changed


def test_manual_bid_is_first_price():
    s = bid(engine.EMPTY, 1, 100).standing
    s = bid(s, 2, 500, seq=2).standing
    assert engine.settle(FLOOR, INC, [LedgerBid(1, D(100), None, 1), LedgerBid(2, D(500), None, 2)]).price == 500


def test_proxy_raises_only_as_far_as_needed():
    s = bid(engine.EMPTY, 1, 100, mx=1000).standing
    assert s.price == 100  # a lone proxy bidder pays only their opening bid
    r = bid(s, 2, 500, seq=2)
    assert r.standing.leader_id == 1 and r.standing.price == 510
    assert r.outbid_immediately and not r.leader_changed


def test_challenger_above_proxy_ceiling_takes_lead_and_pays_own_bid():
    s = bid(engine.EMPTY, 1, 100, mx=1000).standing
    r = bid(s, 2, 1200, seq=2)
    assert r.standing.leader_id == 2 and r.standing.price == 1200 and r.leader_changed


def test_equal_ceilings_favour_the_earlier_bidder_at_their_ceiling():
    s = bid(engine.EMPTY, 1, 100, mx=500).standing
    r = bid(s, 2, 200, mx=500, seq=2)
    assert r.standing.leader_id == 1 and r.standing.price == 500


def test_two_proxies_settle_one_increment_above_the_loser():
    s = bid(engine.EMPTY, 1, 100, mx=800).standing
    s = bid(s, 2, 150, mx=600, seq=2).standing
    assert s.leader_id == 1 and s.price == 610
    s = bid(s, 3, 700, mx=700, seq=3).standing
    assert s.leader_id == 1 and s.price == 710


def test_leader_must_raise_ceiling_to_rebid():
    s = bid(engine.EMPTY, 1, 100, mx=500).standing
    with pytest.raises(BidRejected) as e:
        bid(s, 1, 300, mx=400)
    assert e.value.code == "ALREADY_LEADING"
    r = bid(s, 1, 100, mx=900)
    assert r.standing.leader_id == 1 and r.standing.price == 100


def test_ceiling_cannot_be_below_bid_and_amounts_must_be_positive():
    with pytest.raises(BidRejected) as e:
        bid(engine.EMPTY, 1, 200, mx=150)
    assert e.value.code == "CEILING_BELOW_BID"
    with pytest.raises(BidRejected):
        bid(engine.EMPTY, 1, 0)


def test_settle_with_no_bids_is_no_sale():
    s = engine.settle(FLOOR, INC, [])
    assert s.winner_id is None and s.reason == "no_bids"


def test_unknown_pricing_rule_is_refused():
    with pytest.raises(ValueError):
        engine.settle(FLOOR, INC, [], rule="mystery")


def test_money_rounds_half_up_to_cents():
    assert engine.money("10.005") == D("10.01")


@pytest.mark.parametrize("seed", range(40))
def test_invariants_hold_for_random_bidding(seed):
    rng = random.Random(seed)
    standing, ledger, last_price = engine.EMPTY, [], D(0)
    for seq in range(1, 40):
        who = rng.randint(1, 5)
        base = (standing.price or FLOOR) + INC * rng.randint(0, 4)
        mx = base + INC * rng.randint(0, 30) if rng.random() < 0.5 else None
        try:
            r = engine.apply_bid(FLOOR, INC, standing, who, base, mx, seq)
        except BidRejected:
            continue
        standing = r.standing
        ledger.append(LedgerBid(who, base, mx, seq))
        leader = max(standing.positions, key=lambda p: (p.ceiling, -p.seq))
        assert standing.leader_id == leader.bidder_id           # highest ceiling leads
        assert FLOOR <= standing.price <= leader.ceiling        # never below floor, never above ceiling
        assert standing.price >= last_price                     # price only goes up
        last_price = standing.price
    # The stored ledger always replays to the same result.
    replayed = engine.replay(FLOOR, INC, ledger)
    assert (replayed.leader_id, replayed.price) == (standing.leader_id, standing.price)
    assert engine.settle(FLOOR, INC, ledger).price == standing.price
