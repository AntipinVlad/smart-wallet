# Every model is imported here. Alembic sees only imported models: a model missing from this list
# looks like a deleted table to autogenerate. tests/test_db/test_schema.py::test_models_match_migrations checks it.
# Base is re-exported: code that needs the metadata of all tables imports it from here
from app.database import Base
from app.models.operation import Operation
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.wallet import Wallet

__all__ = ["Base", "Operation", "RefreshToken", "User", "Wallet"]
