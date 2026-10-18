from sqlmodel import JSON, Column, Field, SQLModel

from .engine import Character


class Life(SQLModel, table=True):
    """One saved life. Stats, skills and history are stored as JSON."""
    id: int | None = Field(default=None, primary_key=True)
    name: str
    seed: int
    age: int = 0
    alive: bool = True
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    skills: dict = Field(default_factory=dict, sa_column=Column(JSON))
    history: list = Field(default_factory=list, sa_column=Column(JSON))
    cause_of_death: str | None = None

    def to_character(self) -> Character:
        return Character(
            name=self.name, age=self.age, alive=self.alive,
            stats=dict(self.stats), skills=dict(self.skills),
            history=list(self.history), cause_of_death=self.cause_of_death,
        )

    def update_from(self, c: Character) -> None:
        # Assign fresh objects so SQLAlchemy notices the JSON changed.
        self.age = c.age
        self.alive = c.alive
        self.stats = dict(c.stats)
        self.skills = dict(c.skills)
        self.history = list(c.history)
        self.cause_of_death = c.cause_of_death
