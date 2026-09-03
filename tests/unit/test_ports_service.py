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
    csv_text = "LOCODE,Name,Country,Latitude,Longitude\nUSNYC,New York,US,40.6759,-74.0173\n"
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


# ─────────────────────── UN/LOCODE — parsing et rapport ──────────────────────

# Extrait réel du miroir géolocalisé (colonnes de cristan/improved-un-locodes).
_UNLOCODE_SAMPLE = (
    "Change,Country,Location,Name,NameWoDiacritics,Subdivision,Status,Function,"
    "Date,IATA,Coordinates,Remarks,CoordinatesDecimal,Distance,Source\n"
    # Port avec coordonnées décimales corrigées (OSM) : cas nominal.
    ',PH,MNL,Manila,Manila,,AI,1--45---,,,,,"14.5904492,120.9803621",,OSM\n'
    # Port sans décimale → repli sur le DDMM, hémisphères S et E.
    ",RE,LPT,Le Port,Le Port,,AF,1-3-5---,,,2056S 05519E,,,,UN/LOCODE\n"
    # DDMM hémisphères N et W (longitude à trois chiffres).
    ",US,LAX,Los Angeles,Los Angeles,CA,AI,12345---,,,3344N 11816W,,,,UN/LOCODE\n"
    # Entrée retirée de la prochaine édition (statut XX) → écartée.
    ",RE,PDG,Pointe des Galets,Pointe des Galets,,XX,--3-----,,,2055S 05517E,,"
    '"-20.91667,55.28333",,OSM\n'
    # Aucune coordonnée exploitable → écartée, et comptée comme telle.
    ",AD,FMO,La Farga de Moles,La Farga de Moles,,RL,--3----B,,,,,,,\n"
    # Doublon de locode (variante orthographique) → première occurrence gagne.
    ',PH,MNL,Manila (variante),Manila,,AI,1--45---,,,,,"14.60,120.98",,OSM\n'
    # Sans nom → écartée.
    ",FR,XXX,,,,RL,1-------,,,4930N 00010E,,,,UN/LOCODE\n"
)


def test_unlocode_csv_prefers_decimal_then_falls_back_to_packed() -> None:
    from app.services.ports import UnlocodeReport, parse_unlocode_csv

    report = UnlocodeReport()
    rows = {r.locode: r for r in parse_unlocode_csv(_UNLOCODE_SAMPLE, report=report)}

    # Décimale prioritaire : précision au 6ᵉ chiffre, source distinguée.
    assert rows["PHMNL"].latitude == 14.590449
    assert rows["PHMNL"].longitude == 120.980362
    assert rows["PHMNL"].source == "unlocode-improved"
    # Repli DDMM : 2056S 05519E → -20.9333 / 55.3167 (degré + minute/60).
    assert rows["RELPT"].latitude == -20.9333
    assert rows["RELPT"].longitude == 55.3167
    assert rows["RELPT"].source == "unlocode"
    # DDMM N/W avec longitude à trois chiffres.
    assert rows["USLAX"].latitude == 33.7333
    assert rows["USLAX"].longitude == -118.2667
    # Locode reconstitué Country + Location, subdivision reprise.
    assert rows["USLAX"].country == "US" and rows["USLAX"].subdivision == "CA"

    assert report.kept == 3
    assert report.from_decimal == 1 and report.from_packed == 2
    assert report.skipped_retired == 1  # REPDG (statut XX)
    assert report.skipped_no_coordinates == 1  # ADFMO
    assert report.skipped_no_name == 1  # FRXXX
    assert report.duplicates == 1  # 2ᵉ PHMNL
    assert "3 retenues" in report.summary()


def test_unlocode_csv_skips_retired_entries() -> None:
    """Statut XX = code en cours de retrait chez UNECE : on ne l'ajoute pas.

    Cas réel : REPDG (Pointe des Galets) est en XX et sans fonction port,
    alors que le port de La Réunion est RELPT (« Le Port »).
    """
    from app.services.ports import parse_unlocode_csv

    locodes = {r.locode for r in parse_unlocode_csv(_UNLOCODE_SAMPLE)}
    assert "REPDG" not in locodes
    assert "RELPT" in locodes


def test_unlocode_packed_coordinates_rejects_garbage() -> None:
    from app.services.ports import parse_unlocode_packed_coordinates as parse

    assert parse("4015N 12453W") == (40.25, -124.8833)
    assert parse("") is None
    assert parse(None) is None
    assert parse("4015N") is None  # une seule composante
    assert parse("4015X 12453W") is None  # hémisphère invalide
    assert parse("40aN 12453W") is None  # non numérique
    assert parse("9915N 12453W") is None  # latitude hors plage


def test_unlocode_decimal_coordinates_rejects_garbage() -> None:
    from app.services.ports import parse_unlocode_decimal_coordinates as parse

    assert parse("42.50000,1.51667") == (42.5, 1.51667)
    assert parse(" -20.9,55.3 ") == (-20.9, 55.3)
    assert parse("") is None
    assert parse("42.5") is None  # une seule composante
    assert parse("nord,est") is None
    assert parse("120.0,55.0") is None  # latitude hors plage
    assert parse("42.5,200.0") is None  # longitude hors plage


def test_unlocode_seaport_filter_keeps_function_position_one() -> None:
    from app.services.ports import parse_unlocode_csv

    rows = parse_unlocode_csv(_UNLOCODE_SAMPLE)
    sea = {r.locode for r in _filter_unlocode_seaports(rows)}
    assert sea == {"PHMNL", "RELPT", "USLAX"}


# ─────────────────── Hiérarchie des sources à l'upsert ───────────────────


def test_source_precedence_protects_curated_data() -> None:
    """Un import automatique ne dégrade jamais une donnée curée.

    Sans cette règle, un rafraîchissement UN/LOCODE réécrivait Fécamp
    (49,7594 / 0,3742, catalogue embarqué) avec la valeur arrondie à la
    minute (49,75 / 0,38333) — ~1 km d'écart.
    """
    from app.services.ports import may_overwrite

    # Correction humaine : intouchable par l'automatique.
    assert may_overwrite("manual", "unlocode-improved") is False
    assert may_overwrite("manual", "world_ports") is False
    assert may_overwrite("manual", "manual") is True
    # Catalogue embarqué : protégé de l'automatique, cédant à l'humain.
    assert may_overwrite("world_ports", "unlocode") is False
    assert may_overwrite("world_ports", "manual") is True
    assert may_overwrite("world_ports", "world_ports") is True
    # Coordonnées corrigées : non dégradées par le miroir brut, mais
    # rafraîchissables par elles-mêmes.
    assert may_overwrite("unlocode-improved", "unlocode") is False
    assert may_overwrite("unlocode-improved", "unlocode-improved") is True
    assert may_overwrite("unlocode", "unlocode-improved") is True
    # Sources automatiques à égalité : la dernière importée gagne (historique).
    assert may_overwrite("unlocode", "datagouv:default") is True
    assert may_overwrite("datagouv:default", "unlocode") is True
    # Port inconnu / source vide : traité comme automatique.
    assert may_overwrite(None, "unlocode") is True
