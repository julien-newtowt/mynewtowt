"""Consolidation locale des CSV satcoms TOWT (stdlib, sans base)."""

from __future__ import annotations

import json

from scripts.towt_gps_consolidate import consolidate, parse_satcoms_csv

SAMPLE = (
    "Date;Timestamp;Latitude;Latitude DMS;Longitude;Longitude DMS;SOG (knots);COG (degree);"
    "Active interface;Signal;Total distance (nm)\n"
    "2024-10-21T10:00:03;1729504803;49.25839;49° 15' 30\" N;-16.61361;016° 36' 49\" W;;;Starlink_1921681001;;0\n"
    "2024-10-21T09:55:09;1729504509;49.25934;49° 15' 34\" N;-16.63311;016° 37' 59\" W;9;94;Starlink_1921681001;;0.77\n"
    "2024-10-21T09:50:03;1729504203;;;-16.65248;016° 39' 09\" W;8;94;Starlink_1921681001;;1.53\n"
)


def test_parse_uses_epoch_and_rejects_missing_coordinates():
    points, rejected = parse_satcoms_csv(SAMPLE, vessel="anemos", source_file="f.csv")
    assert rejected == 1
    assert [p["recorded_at_utc"] for p in points] == [
        "2024-10-21T10:00:03Z",
        "2024-10-21T09:55:09Z",
    ]
    assert points[0]["sog_kn"] == "" and points[1]["sog_kn"] == "9"
    assert points[1]["interface"] == "Starlink_1921681001"


def test_consolidate_dedups_sorts_and_writes_manifest(tmp_path):
    src = tmp_path / "12 - Tracking"
    src.mkdir()
    (src / "20241021100502-anemos-satcoms.csv").write_text(SAMPLE, encoding="utf-8")
    # Fichier suivant : recouvre le point 10:00:03 avec un SOG renseigné.
    nxt = (
        SAMPLE.splitlines()[0]
        + "\n"
        + (
            "2024-10-21T11:00:02;1729508402;49.24;-16.40;;;7;90;Starlink_1921681001;;0\n"
            "2024-10-21T10:00:03;1729504803;49.25839;49° 15' 30\" N;-16.61361;016° 36' 49\" W;9;93;Starlink_1921681001;;0.8\n"
        ).replace(";49.24;-16.40;;;7;90", ";49.24;x;-16.40;y;7;90")
    )
    (src / "20241021110501-anemos-satcoms.csv").write_text(nxt, encoding="utf-8")
    (src / "20241021110501-artemis-satcoms.csv").write_text(SAMPLE, encoding="utf-8")
    (src / "notes.csv").write_text("ignored", encoding="utf-8")
    out = tmp_path / "out"
    manifest = consolidate(src, out)
    assert manifest["files_scanned"] == 3 and manifest["files_read"] == 3
    assert manifest["duplicates_dropped"] == 1
    names = set(manifest["outputs"])
    assert names == {"towt_gps_anemos_2024.csv", "towt_gps_artemis_2024.csv"}
    anemos = (out / "towt_gps_anemos_2024.csv").read_text(encoding="utf-8").splitlines()
    assert anemos[0].startswith("vessel,recorded_at_utc")
    stamps = [line.split(",")[1] for line in anemos[1:]]
    assert stamps == sorted(stamps) and len(stamps) == 3
    # Le doublon renseigné a remplacé la ligne « vide » de tête.
    assert any(
        line.startswith("anemos,2024-10-21T10:00:03Z") and ",9,93," in line for line in anemos
    )
    saved = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert saved["outputs"]["towt_gps_anemos_2024.csv"]["points"] == 3
    assert len(saved["outputs"]["towt_gps_anemos_2024.csv"]["sha256"]) == 64


def test_filters_by_vessel(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "20241021100502-anemos-satcoms.csv").write_text(SAMPLE, encoding="utf-8")
    (src / "20241021100502-artemis-satcoms.csv").write_text(SAMPLE, encoding="utf-8")
    manifest = consolidate(src, tmp_path / "out", vessel_filter="artemis")
    assert set(manifest["outputs"]) == {"towt_gps_artemis_2024.csv"}
