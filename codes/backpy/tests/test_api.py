"""The HTTP surface, exercised without a model behind it.

The physics has its own suite. What this file is for is the wiring, which is
where this project has actually been bitten: a route that was never registered,
a payload whose shape the browser could not read, an upstream failure arriving
as a bare 500. None of those are visible from a unit test of the maths, and all
of them are invisible until something tries to speak HTTP to the app.

So the vision call is replaced with a fixed set of measurements and everything
else runs for real: multipart parsing, the form fields, the ephemeris, the
verdict, and the JSON that comes back out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import ephemeris as eph
from app.main import app

LAT = 8.75
LON = -83.5

#: Enough of a JPEG that the route's "is this empty" check is satisfied. The
#: bytes are never decoded, because nothing here looks at pixels.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def shadow_for(moment: datetime, *, bias: float = 1.0, heading: float = 90.0):
    """The measurements a perfect vision pass would return for `moment`."""
    sun = eph.solar_position(LAT, LON, moment)
    return [
        {
            "entity": "primary_shadow",
            "attribute": "length_ratio",
            "numeric_value": sun.shadow_len_ratio * bias,
            "confidence": 0.9,
        },
        {
            "entity": "primary_shadow",
            "attribute": "direction_deg",
            "numeric_value": (sun.azimuth_deg + 180.0 - heading) % 360.0,
            "confidence": 0.9,
        },
    ]


@pytest.fixture()
def client():
    return TestClient(app)


def post_compare(client, *, out_at, in_at, out_obs, in_obs, monkeypatch):
    """Drive /api/compare with the vision pass replaced by fixed answers.

    Each frame gets its own set, handed out in call order, so the two sides of
    the cut can disagree the way two real frames would.
    """
    answers = iter([out_obs, in_obs])

    async def fake_observe(payload, role, settings):
        return next(answers)

    monkeypatch.setattr("app.main._observe", fake_observe)

    return client.post(
        "/api/compare",
        files={
            "outgoing": ("outgoing.jpg", FAKE_JPEG, "image/jpeg"),
            "incoming": ("incoming.jpg", FAKE_JPEG, "image/jpeg"),
        },
        data={
            "outgoing_recorded_at": out_at.isoformat().replace("+00:00", "Z"),
            "incoming_recorded_at": in_at.isoformat().replace("+00:00", "Z"),
            "latitude": str(LAT),
            "longitude": str(LON),
            "outgoing_at_seconds": "0",
            "incoming_at_seconds": "0",
        },
    )


class TestCompareRoute:
    def test_an_honest_cut_comes_back_matched(self, client, monkeypatch):
        out_at = datetime(2026, 12, 14, 21, 30, tzinfo=timezone.utc)
        in_at = out_at + timedelta(minutes=4)

        response = post_compare(
            client,
            out_at=out_at,
            in_at=in_at,
            out_obs=shadow_for(out_at),
            in_obs=shadow_for(in_at),
            monkeypatch=monkeypatch,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["verdict"]["verdict"] == "matched"
        assert len(body["frames"]) == 2
        assert [frame["role"] for frame in body["frames"]] == ["outgoing", "incoming"]

    def test_a_cut_across_three_hours_comes_back_suspect(self, client, monkeypatch):
        """Labelled four minutes apart, lit three hours apart."""
        out_at = datetime(2026, 12, 14, 19, 0, tzinfo=timezone.utc)
        claimed = out_at + timedelta(minutes=4)
        actually = datetime(2026, 12, 14, 22, 0, tzinfo=timezone.utc)

        response = post_compare(
            client,
            out_at=out_at,
            in_at=claimed,
            out_obs=shadow_for(out_at),
            in_obs=shadow_for(actually),
            monkeypatch=monkeypatch,
        )

        assert response.status_code == 200
        verdict = response.json()["verdict"]
        assert verdict["verdict"] == "suspect"
        assert verdict["observed_length_ratio"] > verdict["expected_length_ratio"]

    def test_an_upstream_failure_is_not_blamed_on_the_clip(self, client, monkeypatch):
        """Expired credentials must not reach a person as a rejected video.

        The break is introduced one layer below the handler, in the vision call
        itself, so the handler's own guard is what is under test rather than a
        stand-in for it. This exact failure happened during development: the
        credentials went stale and the browser was told, in effect, that the
        footage was bad.
        """

        def fails(payload, mime_type=None, settings=None):
            raise RuntimeError("Reauthentication is needed.")

        monkeypatch.setattr("app.tools.vision.observe_frame", fails)
        # The handler waits before its last attempt, on the theory that a total
        # failure is usually a rate limit. Correct in production, and twenty
        # seconds of nothing in a test suite.
        monkeypatch.setattr("app.main.RETRY_PAUSE_S", 0.0)

        response = client.post(
            "/api/compare",
            files={
                "outgoing": ("a.jpg", FAKE_JPEG, "image/jpeg"),
                "incoming": ("b.jpg", FAKE_JPEG, "image/jpeg"),
            },
            data={
                "outgoing_recorded_at": "2026-12-14T21:30:00Z",
                "incoming_recorded_at": "2026-12-14T21:34:00Z",
                "latitude": str(LAT),
                "longitude": str(LON),
            },
        )

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "not your clip" in detail
        assert "outgoing" in detail

    def test_a_nonsense_position_is_refused(self, client):
        response = client.post(
            "/api/compare",
            files={
                "outgoing": ("a.jpg", FAKE_JPEG, "image/jpeg"),
                "incoming": ("b.jpg", FAKE_JPEG, "image/jpeg"),
            },
            data={
                "outgoing_recorded_at": "2026-12-14T21:30:00Z",
                "incoming_recorded_at": "2026-12-14T21:34:00Z",
                "latitude": "980",
                "longitude": "0",
            },
        )
        assert response.status_code == 400
        assert "out of range" in response.json()["detail"]

    def test_an_unreadable_timestamp_names_which_clip(self, client):
        response = client.post(
            "/api/compare",
            files={
                "outgoing": ("a.jpg", FAKE_JPEG, "image/jpeg"),
                "incoming": ("b.jpg", FAKE_JPEG, "image/jpeg"),
            },
            data={
                "outgoing_recorded_at": "last tuesday",
                "incoming_recorded_at": "2026-12-14T21:34:00Z",
                "latitude": str(LAT),
                "longitude": str(LON),
            },
        )
        assert response.status_code == 400
        assert "outgoing" in response.json()["detail"]


class TestInspectRoute:
    def test_head_and_tail_are_read_at_their_own_moments(self, client, monkeypatch):
        """The tail frame is later than the head, and must be judged as such."""
        start = datetime(2026, 12, 14, 21, 30, tzinfo=timezone.utc)
        answers = iter([shadow_for(start), shadow_for(start + timedelta(seconds=90))])

        async def fake_observe(payload, role, settings):
            return next(answers)

        monkeypatch.setattr("app.main._observe", fake_observe)

        response = client.post(
            "/api/inspect",
            files={
                "head": ("head.jpg", FAKE_JPEG, "image/jpeg"),
                "tail": ("tail.jpg", FAKE_JPEG, "image/jpeg"),
            },
            data={
                "recorded_at": "2026-12-14T21:30:00Z",
                "latitude": str(LAT),
                "longitude": str(LON),
                "head_at_seconds": "0",
                "tail_at_seconds": "90",
            },
        )

        assert response.status_code == 200
        head, tail = response.json()["frames"]
        assert head["moment"] != tail["moment"]
        assert tail["inferred"]["sun_elevation_deg"] < head["inferred"]["sun_elevation_deg"]
