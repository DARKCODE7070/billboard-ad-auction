"""Demo data:  python -m app.seed   (add --reset to wipe the database first)

Accounts (password for all: password123)
  owner       meridian@demo.test   skyline@demo.test
  advertiser  chaipoint@demo.test  novamotors@demo.test  lumen@demo.test
"""
import sys
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from . import config
from .database import Base, SessionLocal, engine, init_db
from .models import Billboard, User, utcnow
from .security import hash_password
from .services import ServiceError, close_due, create_auction, create_billboard, place_bid

PASSWORD = "password123"

USERS = [
    ("meridian@demo.test", "Asha Rao", "Meridian Outdoor", "owner"),
    ("skyline@demo.test", "Vikram Shah", "Skyline Media", "owner"),
    ("chaipoint@demo.test", "Neha Iyer", "Chai Point Co.", "advertiser"),
    ("novamotors@demo.test", "Rohan Mehta", "Nova Motors", "advertiser"),
    ("lumen@demo.test", "Tara Nair", "Lumen Mobile", "advertiser"),
]

# owner email, title, city, address, format, w, h, impressions, lit, description
BOARDS = [
    ("meridian@demo.test", "MG Road flyover unipole", "Bengaluru", "MG Road, near Trinity Circle", "Unipole", 40, 20, 185000, True,
     "Faces eastbound traffic as it comes off the flyover. Clear sightline for 400 metres."),
    ("meridian@demo.test", "Outer Ring Road tech corridor", "Bengaluru", "Marathahalli to Sarjapur stretch", "Gantry", 60, 15, 240000, True,
     "Spans all lanes above the busiest tech-park commute in the city."),
    ("meridian@demo.test", "Bandra Linking Road LED", "Mumbai", "Linking Road junction, Bandra West", "Digital LED", 30, 12, 310000, True,
     "10-second loop, six advertisers per cycle. This auction is for one exclusive slot."),
    ("meridian@demo.test", "Worli Sea Link approach", "Mumbai", "Dr. Annie Besant Road, Worli", "Unipole", 48, 24, 275000, True,
     "Large-format face with sea-link traffic stopped at the signal for 90 seconds."),
    ("skyline@demo.test", "Connaught Place canopy", "Delhi", "Outer Circle, Connaught Place", "Hoarding", 36, 18, 220000, True,
     "Central Delhi retail and office footfall, lit until midnight."),
    ("skyline@demo.test", "Dwarka Expressway gantry", "Delhi", "Dwarka Expressway, Sector 21 exit", "Gantry", 70, 16, 190000, False,
     "Long-distance approach view for commuters heading to Gurugram."),
    ("skyline@demo.test", "HITEC City metro bridge", "Hyderabad", "Madhapur, below HITEC City metro", "Digital LED", 28, 14, 205000, True,
     "Metro riders and road traffic both see this screen."),
    ("skyline@demo.test", "Anna Salai bus shelter row", "Chennai", "Anna Salai near Spencer Plaza", "Bus shelter", 6, 4, 64000, True,
     "Eye-level panels along a high-footfall shopping street."),
    ("meridian@demo.test", "Koregaon Park hoarding", "Pune", "North Main Road, Koregaon Park", "Hoarding", 32, 16, 88000, False,
     "Cafe and nightlife district with steady evening traffic."),
    ("skyline@demo.test", "Indiranagar 100 Feet Road", "Bengaluru", "100 Feet Road, Indiranagar", "Unipole", 24, 12, 97000, True,
     "Premium retail street, strong weekend footfall."),
]

# board index, floor, increment, minutes until close, slot offset days, bids (advertiser index, amount, max)
SCRIPT = [
    (0, 120000, 5000, 3, 4, [(0, 125000, None), (1, 130000, 150000), (2, 140000, None)]),
    (1, 180000, 10000, 45, 5, [(1, 190000, None), (0, 200000, 240000)]),
    (2, 300000, 15000, 180, 7, [(2, 300000, None), (1, 330000, 400000), (0, 345000, None)]),
    (3, 260000, 10000, 60 * 26, 9, [(0, 260000, None)]),
    (4, 150000, 10000, 60 * 5, 6, [(2, 160000, None), (0, 170000, 190000), (1, 180000, None), (2, 200000, None)]),
    (5, 90000, 5000, 60 * 30, 12, []),
    (6, 140000, 7500, 60 * 50, 10, [(1, 150000, 175000)]),
    (7, 55000, 2500, 25, 3, [(2, 55000, None), (0, 60000, None)]),
    (8, 40000, 2500, 60 * 72, 8, []),
    (9, 95000, 5000, 60 * 8, 5, [(1, 100000, None), (2, 105000, 125000), (0, 115000, None)]),
]


def run(reset: bool = False):
    if reset:
        Base.metadata.drop_all(engine)
    init_db()
    with SessionLocal() as db:
        if db.execute(select(User.id)).first():
            print("Database already has data. Use --reset to start over.")
            return
        users = {}
        for email, name, company, role in USERS:
            u = User(email=email, name=name, company=company, role=role, password_hash=hash_password(PASSWORD))
            db.add(u)
            users[email] = u
        db.commit()
        owners = {e: u for e, u in users.items() if u.role == "owner"}
        advertisers = [u for u in users.values() if u.role == "advertiser"]

        boards = []
        for email, title, city, addr, fmt, w, h, imp, lit, desc in BOARDS:
            boards.append(create_billboard(
                db, owners[email], title=title, city=city, address=addr, format=fmt,
                width_ft=w, height_ft=h, daily_impressions=imp, illuminated=lit, description=desc))

        now = utcnow()
        for bi, floor, inc, minutes, slot_off, bids in SCRIPT:
            end = now + timedelta(minutes=minutes)
            a = create_auction(
                db, owners[next(e for e, *_ in BOARDS[bi:bi + 1])], boards[bi].id,
                Decimal(floor), Decimal(inc),
                slot_start=(end + timedelta(days=slot_off)).date(),
                slot_end=(end + timedelta(days=slot_off + 29)).date(),
                end_at=end, now=now)
            for who, amount, mx in bids:
                try:
                    place_bid(db, a.id, advertisers[who], Decimal(amount), Decimal(mx) if mx else None)
                except ServiceError as e:
                    print("seed bid skipped:", e.message)
        print(f"Seeded {len(users)} users, {len(boards)} billboards, {len(SCRIPT)} auctions.")
        print(f"Sign in with any account above using password '{PASSWORD}'.")


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)
