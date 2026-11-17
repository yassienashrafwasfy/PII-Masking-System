"""Shared fixtures for the arena tests."""

from __future__ import annotations

import pytest
from fake_storage import FakeS3
from fastapi.testclient import TestClient

from leaderboard_api import library, main, objectstore, ranking, runs
from leaderboard_api.config import Settings


class RegistrySlot:
    """The run registry the app serves, swappable mid-test.

    "The API restarted" is a case several tests need to reproduce -- the
    registry is process state and the manifest is the record -- and `restart`
    is how they do it without rebinding an attribute on another module.
    """

    def __init__(self) -> None:
        self.current = runs.RunRegistry()

    def restart(self) -> runs.RunRegistry:
        """Forget every run, the way starting a fresh process would."""
        self.current = runs.RunRegistry()
        return self.current


@pytest.fixture(autouse=True)
def registry():
    """Give every test its own run registry.

    The registry deliberately outlives a request, so without this one test's
    uploads are still listed by the next. Overriding the dependency scopes the
    substitution to the app and removes it again on the way out.
    """
    slot = RegistrySlot()
    main.app.dependency_overrides[main.get_registry] = lambda: slot.current
    yield slot
    main.app.dependency_overrides.pop(main.get_registry, None)


@pytest.fixture(autouse=True)
def _clear_board_cache():
    """Stop one test's board or file index from being served to the next.

    `ranking.load_board_cached` keys its TTL cache on the `Settings` value, and
    tests deliberately reuse the same environment, so without this a test that
    reads an empty bucket would poison every later test with its cached `None`.
    Cleared on the way out as well as in, so a failure mid-test cannot leak.

    `library` has the same problem twice over: a TTL cache over the whole index
    *and* a `(key, etag)` memo that deliberately outlives it, and the fake
    store restarts its etag counter for every test -- so a stale memo would
    hand one test the run another test built.
    """
    ranking.reset_board_cache()
    library.reset_cache()
    yield
    ranking.reset_board_cache()
    library.reset_cache()


@pytest.fixture
def fake(monkeypatch):
    """Serve every storage call from memory instead of the network.

    Patching `objectstore.client_for` rather than boto3 keeps the seam at this
    package's own boundary: a test that reaches storage a new way still goes
    through here. `monkeypatch` undoes it, so nothing leaks into the next test.
    """
    store = FakeS3()
    monkeypatch.setattr(objectstore, "client_for", lambda _settings: store)
    return store


@pytest.fixture
def settings():
    """Credentials that satisfy `storage_configured`, and nothing else.

    Deliberately carries no bucket or prefix: which buckets exist is what each
    module is about, so a module overrides this fixture and adds its own with
    `replace()` rather than inheriting a set it does not use.
    """
    return Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
    )


@pytest.fixture
def client():
    """The API under test, entered so startup runs and closed on the way out."""
    with TestClient(main.app) as test_client:
        yield test_client
