import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth as auth_api
from app.api import memories as memories_api
from app.api import sessions as sessions_api
from app.db import engine, get_session


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # A turn still running at shutdown is cancelled; its owner message stays saved
    for task in list(app.state.turns):
        task.cancel()
    await asyncio.gather(*app.state.turns, return_exceptions=True)
    await engine.dispose()


app = FastAPI(title="Personal Agent", lifespan=lifespan)
app.state.agent = None  # built on first use, see api.deps.get_agent
app.state.turns = set()  # running turns, kept so they are not garbage collected
app.include_router(auth_api.router)
app.include_router(sessions_api.router)
app.include_router(memories_api.router)


@app.get("/health")
async def health(session: Annotated[AsyncSession, Depends(get_session)]):
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
