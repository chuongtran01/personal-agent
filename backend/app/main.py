from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth as auth_api
from app.db import engine, get_session


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Personal Agent", lifespan=lifespan)
app.include_router(auth_api.router)


@app.get("/health")
async def health(session: Annotated[AsyncSession, Depends(get_session)]):
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
