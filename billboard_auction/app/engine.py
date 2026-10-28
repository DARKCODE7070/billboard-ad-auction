"""Pure auction logic. No database, no clock, no framework.

Everything here is deterministic so it can be unit tested and replayed:
the database stores the ledger of accepted bids, and the winner is always
derivable by folding those bids through `apply_bid` in order.

Model
-----
Each bidder has one *position*:
    base     the visible amount they last submitted
    ceiling  the most they are willing to pay (== base for a manual bid,
             higher for a proxy bid)
    seq      arrival order, used to break ties (earlier wins)

The leader is the position with the highest ceiling. Their standing price is
the smallest amount that still beats the runner-up (runner ceiling + one
increment), capped at their own ceiling and never below what they explicitly
bid. The winner pays that standing price: a first-price (highest bid) rule.

Other pricing rules can be added by registering a function in PRICING_RULES.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Iterable, Optional, Sequence

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


class BidRejected(Exception):
    """A bid broke an auction rule. `code` is stable for clients and tests."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class Position:
    bidder_id: int
    base: Decimal
    ceiling: Decimal
    seq: int


@dataclass(frozen=True)
class Standing:
    leader_id: Optional[int]
    price: Optional[Decimal]
    runner_up_id: Optional[int]
    positions: tuple[Position, ...]

    def next_min_bid(self, floor: Decimal, increment: Decimal) -> Decimal:
        return floor if self.price is None else self.price + increment


@dataclass(frozen=True)
class BidResult:
    standing: Standing
    previous_leader_id: Optional[int]
    leader_changed: bool
    # True when the bidder submitted a valid bid but a proxy already held
    # a higher ceiling, so they are not the leader afterwards.
    outbid_immediately: bool


@dataclass(frozen=True)
class Settlement:
    winner_id: Optional[int]
    price: Optional[Decimal]
    reason: str  # "sold" | "no_bids"


# --------------------------------------------------------------------------
# Pricing rules: Standing -> (winner_id, price)
# --------------------------------------------------------------------------
def first_price(standing: Standing, floor: Decimal, increment: Decimal):
    return standing.leader_id, standing.price


PRICING_RULES: dict[str, Callable] = {"first_price": first_price}


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
def resolve(positions: Iterable[Position], increment: Decimal) -> Standing:
    """Work out leader and standing price from the current positions."""
    ordered = sorted(positions, key=lambda p: (-p.ceiling, p.seq))
    if not ordered:
        return Standing(None, None, None, ())
    leader = ordered[0]
    if len(ordered) == 1:
        return Standing(leader.bidder_id, leader.base, None, tuple(ordered))
    runner = ordered[1]
    price = min(leader.ceiling, max(runner.ceiling + increment, leader.base))
    return Standing(leader.bidder_id, price, runner.bidder_id, tuple(ordered))


def apply_bid(
    floor: Decimal,
    increment: Decimal,
    standing: Standing,
    bidder_id: int,
    amount: Decimal,
    max_amount: Optional[Decimal],
    seq: int,
) -> BidResult:
    """Validate a bid against the current standing and return the new one."""
    floor, increment, amount = money(floor), money(increment), money(amount)
    ceiling = money(max_amount) if max_amount is not None else amount

    if amount <= 0:
        raise BidRejected("AMOUNT_INVALID", "Bid must be greater than zero.")
    if ceiling < amount:
        raise BidRejected(
            "CEILING_BELOW_BID",
            "Your maximum budget can't be lower than your bid.",
        )

    existing = next((p for p in standing.positions if p.bidder_id == bidder_id), None)
    is_leader = standing.leader_id == bidder_id

    if is_leader and existing is not None:
        if ceiling <= existing.ceiling:
            raise BidRejected(
                "ALREADY_LEADING",
                "You already hold the highest bid. Raise your maximum budget to change it.",
                current_ceiling=existing.ceiling,
            )
        new_pos = Position(bidder_id, max(existing.base, amount), ceiling, existing.seq)
    else:
        required = standing.next_min_bid(floor, increment)
        if amount < required:
            raise BidRejected(
                "BID_TOO_LOW",
                f"The lowest bid you can place is {required}.",
                min_bid=required,
            )
        new_pos = Position(bidder_id, amount, ceiling, seq)

    others = [p for p in standing.positions if p.bidder_id != bidder_id]
    new_standing = resolve(others + [new_pos], increment)
    return BidResult(
        standing=new_standing,
        previous_leader_id=standing.leader_id,
        leader_changed=new_standing.leader_id != standing.leader_id,
        outbid_immediately=new_standing.leader_id != bidder_id,
    )


EMPTY = Standing(None, None, None, ())


@dataclass(frozen=True)
class LedgerBid:
    bidder_id: int
    amount: Decimal
    max_amount: Optional[Decimal]
    seq: int


def replay(floor: Decimal, increment: Decimal, bids: Sequence[LedgerBid]) -> Standing:
    """Rebuild the standing from the stored ledger. Used to settle auctions."""
    standing = EMPTY
    for b in sorted(bids, key=lambda b: b.seq):
        standing = apply_bid(
            floor, increment, standing, b.bidder_id, b.amount, b.max_amount, b.seq
        ).standing
    return standing


def settle(
    floor: Decimal,
    increment: Decimal,
    bids: Sequence[LedgerBid],
    rule: str = "first_price",
) -> Settlement:
    if rule not in PRICING_RULES:
        raise ValueError(f"Unknown pricing rule: {rule}")
    standing = replay(floor, increment, bids)
    if standing.leader_id is None:
        return Settlement(None, None, "no_bids")
    winner, price = PRICING_RULES[rule](standing, money(floor), money(increment))
    return Settlement(winner, price, "sold")
