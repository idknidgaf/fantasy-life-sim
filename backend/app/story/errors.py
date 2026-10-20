"""Exceptions of the story engine. The API layer maps each to an HTTP status."""


class StoryError(Exception):
    """Base class for all story engine errors."""


class StoryContentError(StoryError):
    """Event content is invalid. Carries every problem found, like app.content.ContentError."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} story content problem(s):\n" + "\n".join(f"  - {e}" for e in errors))


class StoryNotFound(StoryError):
    """The event or instance does not exist for this life (HTTP 404)."""


class StoryInvalidChoice(StoryError):
    """The submitted choice id does not exist on the event instance (HTTP 400)."""


class StoryNotAllowed(StoryError):
    """The request is well formed but not possible right now (HTTP 409)."""


class StoryEffectError(StoryError):
    """An effect could not be applied. The whole resolution is rolled back (HTTP 422)."""
