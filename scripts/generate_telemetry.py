#!/usr/bin/env python3
"""Generate the simulated production data for CineMeridian.

Writes two CSV files, ready to load into ClickHouse:

  ephemeris.csv      computed sun/moon (real astronomy) + tide (simulated),
                     one row per minute across the shoot and the pickup window
  env_telemetry.csv  1 Hz weather-station readings for each shoot day

Everything here is **simulated**. No real production was filmed, no real
weather service was queried, and the tide is a two-constituent stand-in. The
demo says so out loud; so does this file.

The environment model is deliberately physical rather than random: the sea
breeze builds through the afternoon and backs at dusk, humidity tracks the
falling temperature, illuminance follows the real solar elevation attenuated
by cloud. A pure random walk would produce data the agent could not reason
about, and would make the ClickHouse queries look smarter than they are.

Usage:
    python scripts/generate_telemetry.py --out data/
    python scripts/generate_telemetry.py --out data/ --telemetry-hz 1
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codes" / "backpy"))

from app import ephemeris as eph  # noqa: E402

UTC = timezone.utc

# ── The fictional production ─────────────────────────────────────────────────

PRODUCTION_ID = "prod_tideline"

#: A Pacific-facing beach at low northern latitude: the sun sets over the
#: water, shadows rake hard in the last hour, and the tide moves visibly
#: within a shooting day. All four are what the scene needs to be about.
LATITUDE = 8.75
LONGITUDE = -83.5
UTC_OFFSET_H = -6  # local wall-clock = UTC - 6

#: Afternoon-into-golden-hour, which is when the interesting physics happens.
SHOOT_START_LOCAL_H = 13
SHOOT_END_LOCAL_H = 18

#: Non-contiguous on purpose. The scene's coverage is split across a fortnight,
#: which is exactly the gap that defeats a human eye.
#:
#: The dates straddle the December solstice, and that is not decoration. Sun
#: geometry repeats when the declination repeats, and declination is symmetric
#: about a solstice — so a shoot in early December finds its match in early
#: January, five weeks later. Move the same shoot to early September and the
#: mirror date lands in April: seven months out, and the pickup question has no
#: usable answer at all. Shooting near a solstice is what makes a pickup window
#: exist. See agents/ATURAN-MAIN.md, entry of 1 Sep 2026.
SHOOT_DAYS = [
    date(2026, 12, 3),
    date(2026, 12, 4),
    date(2026, 12, 5),
    date(2026, 12, 14),
    date(2026, 12, 15),
]

#: The pickup window the agent searches when asked "when will this repeat?".
#: Far enough past the solstice to contain the mirror of every shoot day.
PICKUP_WINDOW_END = date(2027, 2, 15)

STATIONS = [
    # station_id, metres above sand, exposure factor (1.0 = fully exposed)
    ("stn_dune", 3.0, 1.0),
    ("stn_waterline", 0.5, 1.15),
    ("stn_treeline", 2.0, 0.55),
]

SEED = 20260908  # fixed: the demo must be reproducible


# ── Environment model ────────────────────────────────────────────────────────

def _sea_breeze(local_hours: float) -> tuple[float, float]:
    """Wind (direction in degrees, speed in m/s) for a coastal afternoon.

    Onshore breeze builds from late morning, peaks mid-afternoon, and backs
    toward offshore after sunset as the land cools faster than the water.
    """
    # Strength peaks around 15:00 local.
    drive = max(0.0, math.sin(math.pi * (local_hours - 9.0) / 11.0))
    speed = 1.2 + 6.0 * drive
    # Direction swings roughly 60 degrees across the afternoon as the breeze
    # veers with the Coriolis term.
    direction = (250.0 + 55.0 * drive) % 360.0
    return direction, speed


def _cloud_walk(rng: random.Random, previous: float) -> float:
    """Cloud cover as a slow random walk, bounded to a plausible band."""
    step = rng.gauss(0.0, 0.35)
    return max(0.0, min(78.0, previous * 0.995 + step))


def _temperature(local_hours: float, cloud_pct: float, rng: random.Random) -> float:
    """Air temperature: diurnal curve peaking mid-afternoon, damped by cloud."""
    diurnal = math.sin(math.pi * (local_hours - 6.0) / 14.0)
    base = 25.5 + 5.5 * max(0.0, diurnal)
    return base - 0.035 * cloud_pct + rng.gauss(0.0, 0.12)


def _dew_point(temp_c: float, humidity_pct: float) -> float:
    """Magnus approximation — the same relation a real station reports."""
    h = max(1.0, min(100.0, humidity_pct))
    a, b = 17.62, 243.12
    gamma = math.log(h / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


def _illuminance(sun_elevation_deg: float, cloud_pct: float) -> int:
    """Horizontal illuminance in lux from solar elevation and cloud."""
    if sun_elevation_deg <= -6.0:
        return 1
    if sun_elevation_deg <= 0.0:
        clear = 400.0 * math.exp(sun_elevation_deg)  # twilight falls off fast
    else:
        clear = 128000.0 * math.sin(math.radians(sun_elevation_deg)) ** 1.15
    return max(1, int(clear * (1.0 - 0.78 * cloud_pct / 100.0)))


def _measured_color_temp(computed_k: int, cloud_pct: float, rng: random.Random) -> int:
    """What a colour meter on the sand would read.

    Cloud scatters out the long wavelengths, so overcast light measures
    *cooler* than the clear-sky value the ephemeris computes. That offset is
    the honest reason a measured reading and the computed one differ, and the
    agent has to know it before it calls a mismatch.
    """
    shifted = computed_k + 22.0 * cloud_pct + rng.gauss(0.0, 25.0)
    return int(max(1800, min(20000, shifted)))


# ── Writers ──────────────────────────────────────────────────────────────────

def write_ephemeris(out_dir: Path) -> int:
    """One row per minute, from the first shoot day through the pickup window."""
    start = datetime.combine(min(SHOOT_DAYS), datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(PICKUP_WINDOW_END, datetime.max.time(), tzinfo=UTC)

    path = out_dir / "ephemeris.csv"
    rows = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = None
        for row in eph.ephemeris_series(
            PRODUCTION_ID, LATITUDE, LONGITUDE, start, end, step=timedelta(minutes=1)
        ):
            if writer is None:
                writer = csv.DictWriter(fh, fieldnames=list(row))
                writer.writeheader()
            row["ts"] = row["ts"].strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow(row)
            rows += 1
    print(f"  ephemeris.csv      {rows:>9,} rows  ({start:%Y-%m-%d} to {end:%Y-%m-%d})")
    return rows


def write_env_telemetry(out_dir: Path, hz: float) -> int:
    """Station readings across every shoot day, at the requested rate."""
    rng = random.Random(SEED)
    step = timedelta(seconds=1.0 / hz)

    path = out_dir / "env_telemetry.csv"
    rows = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "ts",
                "production_id",
                "station_id",
                "wind_dir_deg",
                "wind_speed_ms",
                "temp_c",
                "humidity_pct",
                "dew_point_c",
                "lux",
                "color_temp_k",
                "cloud_cover_pct",
            ]
        )

        for day in SHOOT_DAYS:
            # Each shoot day gets its own weather. Two of the five are hazier
            # than the rest, which is what makes cross-day matching non-trivial.
            cloud = rng.uniform(5.0, 55.0)
            humidity_base = rng.uniform(66.0, 82.0)

            start_utc = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
                hours=SHOOT_START_LOCAL_H - UTC_OFFSET_H
            )
            end_utc = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
                hours=SHOOT_END_LOCAL_H - UTC_OFFSET_H
            )

            ts = start_utc
            while ts < end_utc:
                cloud = _cloud_walk(rng, cloud)
                local_h = (ts + timedelta(hours=UTC_OFFSET_H)).hour + (
                    ts.minute + ts.second / 60.0
                ) / 60.0
                sun = eph.solar_position(LATITUDE, LONGITUDE, ts)
                wind_dir, wind_speed = _sea_breeze(local_h)
                temp = _temperature(local_h, cloud, rng)

                for station_id, _height_m, exposure in STATIONS:
                    # Sheltered stations read calmer and slightly damper.
                    s_speed = max(0.0, wind_speed * exposure + rng.gauss(0.0, 0.25))
                    s_dir = (wind_dir + rng.gauss(0.0, 4.0 / max(exposure, 0.3))) % 360.0
                    s_temp = temp + (1.0 - exposure) * 0.8 + rng.gauss(0.0, 0.08)
                    s_humidity = max(
                        30.0,
                        min(
                            99.0,
                            humidity_base
                            + (1.0 - exposure) * 4.0
                            - (s_temp - 26.0) * 1.6
                            + rng.gauss(0.0, 0.4),
                        ),
                    )
                    writer.writerow(
                        [
                            ts.strftime("%Y-%m-%d %H:%M:%S.000"),
                            PRODUCTION_ID,
                            station_id,
                            round(s_dir, 2),
                            round(s_speed, 2),
                            round(s_temp, 2),
                            round(s_humidity, 2),
                            round(_dew_point(s_temp, s_humidity), 2),
                            _illuminance(sun.elevation_deg, cloud),
                            _measured_color_temp(sun.color_temp_k, cloud, rng),
                            int(round(cloud)),
                        ]
                    )
                    rows += 1
                ts += step

    hours = (SHOOT_END_LOCAL_H - SHOOT_START_LOCAL_H) * len(SHOOT_DAYS)
    print(
        f"  env_telemetry.csv  {rows:>9,} rows  "
        f"({len(SHOOT_DAYS)} shoot days x {hours // len(SHOOT_DAYS)}h x {len(STATIONS)} stations @ {hz:g} Hz)"
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data", help="output directory (default: data/)")
    parser.add_argument(
        "--telemetry-hz",
        type=float,
        default=1.0,
        help="station sample rate; drop to 0.1 for a quick smoke test (default: 1)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"CineMeridian - simulated production data for {PRODUCTION_ID}")
    print(f"  location {LATITUDE}, {LONGITUDE} (UTC{UTC_OFFSET_H:+d})\n")
    write_ephemeris(out_dir)
    write_env_telemetry(out_dir, args.telemetry_hz)

    print(
        "\nLoad into ClickHouse (setup only - at runtime the agent goes through mcp-clickhouse):\n"
        f"  clickhouse client --query \"INSERT INTO cinemeridian.ephemeris FORMAT CSVWithNames\" < {out_dir}/ephemeris.csv\n"
        f"  clickhouse client --query \"INSERT INTO cinemeridian.env_telemetry FORMAT CSVWithNames\" < {out_dir}/env_telemetry.csv"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
