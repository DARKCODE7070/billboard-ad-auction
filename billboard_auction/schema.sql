-- Generated from app/models.py by export_schema.py (PostgreSQL dialect).
-- The app creates these tables itself on startup; this file is for review and migrations.

CREATE TABLE users (
	id SERIAL NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	company VARCHAR(160), 
	role VARCHAR(16) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_users_role CHECK (role in ('owner','advertiser'))
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE billboards (
	id SERIAL NOT NULL, 
	owner_id INTEGER NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	city VARCHAR(80) NOT NULL, 
	address VARCHAR(255) NOT NULL, 
	format VARCHAR(60) NOT NULL, 
	width_ft INTEGER NOT NULL, 
	height_ft INTEGER NOT NULL, 
	daily_impressions INTEGER NOT NULL, 
	illuminated BOOLEAN NOT NULL, 
	description TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
);

CREATE INDEX ix_billboards_city ON billboards (city);
CREATE INDEX ix_billboards_owner_id ON billboards (owner_id);

CREATE TABLE auctions (
	id SERIAL NOT NULL, 
	billboard_id INTEGER NOT NULL, 
	pricing_rule VARCHAR(32) NOT NULL, 
	floor_price NUMERIC(12, 2) NOT NULL, 
	min_increment NUMERIC(12, 2) NOT NULL, 
	slot_start DATE NOT NULL, 
	slot_end DATE NOT NULL, 
	start_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	end_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	anti_snipe_seconds INTEGER NOT NULL, 
	extensions INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	current_price NUMERIC(12, 2), 
	leader_id INTEGER, 
	bid_count INTEGER NOT NULL, 
	winner_id INTEGER, 
	final_price NUMERIC(12, 2), 
	closed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_auction_floor CHECK (floor_price > 0), 
	CONSTRAINT ck_auction_increment CHECK (min_increment > 0), 
	CONSTRAINT ck_auction_status CHECK (status in ('open','closed','no_sale','cancelled')), 
	FOREIGN KEY(billboard_id) REFERENCES billboards (id), 
	FOREIGN KEY(leader_id) REFERENCES users (id), 
	FOREIGN KEY(winner_id) REFERENCES users (id)
);

CREATE INDEX ix_auctions_billboard_id ON auctions (billboard_id);
CREATE INDEX ix_auctions_status_end ON auctions (status, end_at);

CREATE TABLE bids (
	id SERIAL NOT NULL, 
	auction_id INTEGER NOT NULL, 
	bidder_id INTEGER NOT NULL, 
	seq INTEGER NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	max_amount NUMERIC(12, 2), 
	price_after NUMERIC(12, 2) NOT NULL, 
	leader_after_id INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_bid_auction_seq UNIQUE (auction_id, seq), 
	CONSTRAINT ck_bid_amount CHECK (amount > 0), 
	FOREIGN KEY(auction_id) REFERENCES auctions (id), 
	FOREIGN KEY(bidder_id) REFERENCES users (id), 
	FOREIGN KEY(leader_after_id) REFERENCES users (id)
);

CREATE INDEX ix_bids_auction_id ON bids (auction_id);
CREATE INDEX ix_bids_bidder_id ON bids (bidder_id);

CREATE TABLE notifications (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	body TEXT NOT NULL, 
	auction_id INTEGER, 
	read BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(auction_id) REFERENCES auctions (id)
);

CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE TABLE placements (
	id SERIAL NOT NULL, 
	auction_id INTEGER NOT NULL, 
	billboard_id INTEGER NOT NULL, 
	advertiser_id INTEGER NOT NULL, 
	amount NUMERIC(12, 2) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	creative_due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	creative_path VARCHAR(255), 
	creative_type VARCHAR(80), 
	creative_name VARCHAR(255), 
	review_note TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_placement_status CHECK (status in ('awaiting_creative','in_review','approved','rejected')), 
	UNIQUE (auction_id), 
	FOREIGN KEY(auction_id) REFERENCES auctions (id), 
	FOREIGN KEY(billboard_id) REFERENCES billboards (id), 
	FOREIGN KEY(advertiser_id) REFERENCES users (id)
);

CREATE INDEX ix_placements_advertiser_id ON placements (advertiser_id);
CREATE INDEX ix_placements_billboard_id ON placements (billboard_id);

