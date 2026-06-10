"""Ports service — haversine, CSV parser, filters.

Pure-function tests; DB-backed upsert/nearby are in tests/integration.
"""
from __future__ import annotations

from app.services.ports import (
    PortRow,
    _detect_delimiter,
    _filter_unlocode_seaports,
    haversine_km,
    haversine_nm,
    parse_csv,
)


def test_haversine_known_distance() -> None:
    # Le Havre (49.50, 0.13) → New York (40.71, -74.00) ≈ 5650 km / 3050 NM
    km = haversine_km(49.50, 0.13, 40.71, -74.00)
    assert 5500 < km < 5900, f"got {km}"
    nm = haversine_nm(49.50, 0.13, 40.71, -74.00)
    assert 2950 < nm < 3200, f"got {nm}"


def test_haversine_zero_distance() -> None:
    assert haversine_km(45.0, 5.0, 45.0, 5.0) == 0.0


def test_csv_parser_french_columns() -> None:
    csv_text = (
        "locode;nom;pays;latitude;longitude\n"
        "FRLEH;Le Havre;FR;49.4944;0.1079\n"
        "FRFEC;Fécamp;FR;49.7565;0.3712\n"
    )
    rows = parse_csv(csv_text, source="datagouv")
    assert len(rows) == 2
    assert rows[0].locode == "FRLEH"
    assert rows[0].name == "Le Havre"
    assert rows[0].country == "FR"
    assert rows[0].source == "datagouv"


def test_csv_parser_english_columns_comma() -> None:
    csv_text = (
        "LOCODE,Name,Country,Latitude,Longitude\n"
        "USNYC,New York,US,40.6759,-74.0173\n"
    )
    rows = parse_csv(csv_text, source="unlocode")
    assert len(rows) == 1
    assert rows[0].locode == "USNYC"
    assert rows[0].latitude == 40.6759


def test_csv_parser_skips_invalid_rows() -> None:
    csv_text = (
        "locode,name,country,latitude,longitude\n"
        "FRLEH,Le Havre,FR,49.5,0.13\n"
        "BADROW,,,,,\n"
        ",Empty Locode,FR,1,2\n"
        "ZZZZZ,No Lat,FR,,1.23\n"
    )
    rows = parse_csv(csv_text)
    assert len(rows) == 1  # only Le Havre is fully valid
    assert rows[0].locode == "FRLEH"


def test_csv_delimiter_detection() -> None:
    assert _detect_delimiter("a;b;c\n1;2;3") == ";"
    assert _detect_delimiter("a,b,c\n1,2,3") == ","
    assert _detect_delimiter("a\tb\tc\n1\t2\t3") == "\t"


def test_unlocode_seaport_filter() -> None:
    rows = [
        PortRow("FRLEH", "Le Havre", "FR", 49.5, 0.13, "unlocode", function_code="1-345---"),
        PortRow("FRCDG", "CDG Airport", "FR", 49.0, 2.5, "unlocode", function_code="---4----"),
        PortRow("USNYC", "New York", "US", 40.7, -74.0, "unlocode", function_code="123-----"),
        PortRow("ZZZZZ", "No func", "ZZ", 0.0, 0.0, "unlocode", function_code=None),
    ]
    filtered = _filter_unlocode_seaports(rows)
    locodes = [r.locode for r in filtered]
    assert "FRLEH" in locodes
    assert "USNYC" in locodes
    assert "FRCDG" not in locodes
    assert "ZZZZZ" not in locodes
