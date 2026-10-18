import random
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import engine as sim
from .db import get_session, init_db
from .models import Life


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Fantasy Life Sim", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LifeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    seed: int | None = None


class AdvanceRequest(BaseModel):
    choice: str | None = None


def _get_life(session: Session, life_id: int) -> Life:
    life = session.get(Life, life_id)
    if life is None:
        raise HTTPException(404, "Life not found")
    return life


@app.get("/skills")
def get_skills():
    """{skill_id: category}, straight from skills.json."""
    return sim.load_skills()


@app.post("/lives", response_model=Life, status_code=201)
def create_life(body: LifeCreate, session: Session = Depends(get_session)):
    seed = body.seed if body.seed is not None else random.randrange(2**31)
    c = sim.new_character(body.name.strip(), random.Random(seed))
    life = Life(name=c.name, seed=seed, stats=c.stats)
    session.add(life)
    session.commit()
    session.refresh(life)
    return life


@app.get("/lives", response_model=list[Life])
def list_lives(session: Session = Depends(get_session)):
    return session.exec(select(Life).order_by(Life.id.desc())).all()


@app.get("/lives/{life_id}", response_model=Life)
def get_life(life_id: int, session: Session = Depends(get_session)):
    return _get_life(session, life_id)


@app.get("/lives/{life_id}/choices")
def get_choices(life_id: int, session: Session = Depends(get_session)):
    life = _get_life(session, life_id)
    return sim.offer_choices(life.to_character(), life.seed)


@app.post("/lives/{life_id}/advance", response_model=Life)
def advance(life_id: int, body: AdvanceRequest, session: Session = Depends(get_session)):
    life = _get_life(session, life_id)
    c = life.to_character()
    if not c.alive:
        raise HTTPException(409, "This character has died")

    offered = {ch["id"] for ch in sim.offer_choices(c, life.seed)}
    if body.choice is not None and body.choice not in offered:
        raise HTTPException(400, f"Choice not available this year: {body.choice}")

    sim.advance_year(c, sim.year_rng(life.seed, c.age), body.choice)
    life.update_from(c)
    session.add(life)
    session.commit()
    session.refresh(life)
    return life


@app.get("/lives/{life_id}/summary")
def summary(life_id: int, session: Session = Depends(get_session)):
    life = _get_life(session, life_id)
    if life.alive:
        raise HTTPException(409, "This life is still in progress")
    return sim.summarize(life.to_character())


@app.delete("/lives/{life_id}", status_code=204)
def delete_life(life_id: int, session: Session = Depends(get_session)):
    session.delete(_get_life(session, life_id))
    session.commit()
