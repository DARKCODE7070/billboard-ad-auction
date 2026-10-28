"""Print the PostgreSQL DDL for the data model:  python export_schema.py > schema.sql"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import models  # noqa: F401
from app.database import Base

print("-- Generated from app/models.py by export_schema.py (PostgreSQL dialect).\n-- The app creates these tables itself on startup; this file is for review and migrations.\n")
for table in Base.metadata.sorted_tables:
    print(str(CreateTable(table).compile(dialect=postgresql.dialect())).strip() + ";\n")
    for idx in sorted(table.indexes, key=lambda i: i.name):
        print(str(CreateIndex(idx).compile(dialect=postgresql.dialect())).strip() + ";")
    print()
