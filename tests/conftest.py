import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def no_network_prompt_guard(monkeypatch):
    """The injection classifier is a network call; unit tests score everything
    as benign unless a test overrides this."""

    async def benign(_text):
        return 0.0

    monkeypatch.setattr("app.pipeline.guard._guard_score", benign)
