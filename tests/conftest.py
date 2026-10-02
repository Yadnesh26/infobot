from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def offline_prefs(monkeypatch):
    """Language preferences and reply buttons without the database or WhatsApp.

    Defaults to a returning user with no chosen language (already prompted, so no
    chooser is sent). Tests change `state.language` / `state.prompted` and read
    `state.saved` (language choices written) and `state.buttons` (button messages sent).
    """
    state = SimpleNamespace(language=None, prompted=True, saved=[], prompted_marks=[], buttons=[])

    async def get(_hash):
        return {"language": state.language, "prompted": state.prompted}

    async def set_language(_hash, language):
        state.saved.append(language)
        state.language = language
        return True

    async def mark_prompted(_hash):
        state.prompted_marks.append(True)
        state.prompted = True
        return True

    async def send_reply_buttons(to, body, buttons):
        state.buttons.append({"to": to, "body": body, "buttons": buttons})
        return {"messages": [{"id": "wamid.BUTTONS"}]}

    monkeypatch.setattr("app.main.db_prefs.get", get)
    monkeypatch.setattr("app.main.db_prefs.set_language", set_language)
    monkeypatch.setattr("app.main.db_prefs.mark_prompted", mark_prompted)
    monkeypatch.setattr("app.main.send_reply_buttons", send_reply_buttons)
    return state


@pytest.fixture(autouse=True)
def gemini_models(monkeypatch):
    """Tests name their own models so they never depend on what a developer's .env holds
    (CI has no .env at all)."""
    monkeypatch.setattr("app.providers.gemini.settings.GEMINI_MODEL", "primary-model")
    monkeypatch.setattr("app.providers.gemini.settings.GEMINI_MODEL_FALLBACK", "fallback-model")


@pytest.fixture(autouse=True)
def fresh_http_pools():
    """Each test runs in its own event loop and may swap in a fake httpx client, so no
    connection pool may survive from one test to the next."""
    from app import http

    http.reset()
    yield
    http.reset()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Unit tests never reach the real internet. A test that forgets to stub a service fails
    loudly here, the same on a developer's machine (which has a real .env) and in CI (which has
    none). Tests that need a fake client install their own over this one."""

    class _Blocked:
        def __init__(self, *args, **kwargs):
            pass

        async def _refuse(self, method, url):
            raise AssertionError(f"a unit test made a real network call: {method} {url}")

        async def get(self, url, *args, **kwargs):
            await self._refuse("GET", url)

        async def post(self, url, *args, **kwargs):
            await self._refuse("POST", url)

        async def patch(self, url, *args, **kwargs):
            await self._refuse("PATCH", url)

        async def aclose(self):
            pass

    monkeypatch.setattr("httpx.AsyncClient", _Blocked)
