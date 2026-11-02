from fastapi import FastAPI

from app.api.v1.wallets import router as wallet_router
from app.api.v1.operations import router as operation_router
from app.api.v1.users import router as users_router
from app.database import engine, Base

app = FastAPI()

app.include_router(users_router, prefix="/api/v1", tags=["users"])
app.include_router(wallet_router, prefix="/api/v1", tags=["wallet"])
app.include_router(operation_router, prefix="/api/v1", tags=["operation"])

Base.metadata.create_all(bind=engine)
