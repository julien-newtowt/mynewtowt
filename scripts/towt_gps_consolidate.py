"""Reprise d'historique TOWT — consolidation LOCALE des relevés GPS satcom.

À exécuter sur un poste où la bibliothèque SharePoint « Service Technique /
12 - Tracking » est synchronisée (OneDrive), par exemple :
    C:\\Users\\<user>\\TOWT\\NewTOWT - Service Technique - Documents\\12 - Tracking

Ce dossier contient un CSV par navire et par heure depuis le 2024-10-21
(``AAAAMMJJhhmmss-<navire>-satcoms.csv``, séparateur ``;``, ~12 points au pas
de 5 min, ordre antéchronologique, première ligne souvent sans SOG/COG) :
plusieurs dizaines de milliers de fichiers. Ce script les lit tous (bibliothèque
standard uniquement, aucune dépendance) et produit :

- un CSV consolidé par navire et par année ``towt_gps_<navire>_<année>.csv``
  (UTF-8, ``,``, trié par horodatage, dédoublonné sur (navire, horodatage)) ;
- ``manifest.json`` : fichiers scannés / lus / rejetés, points par navire-année,
  premier et dernier horodatage, trous > 6 h, SHA-256 de chaque sortie.

Horodatage : la colonne ``Timestamp`` (epoch Unix, UTC) fait foi ; ``Date`` n'est
utilisée qu'en repli. Les lignes sans latitude/longitude sont rejetées et comptées.

Le résultat se charge ensuite côté serveur avec ``scripts/import_towt_positions.py``.

Mémoire : tous les points d'un passage sont tenus en RAM (≈ 1 Ko par point) —
compter ~0,5 Go pour deux navires sur deux ans. Sur un poste modeste, traiter
par année (``--year 2024``, puis 2025, 2026) : les sorties sont indépendantes.

Usage :
    python scripts/towt_gps_consolidate.py --source "<dossier 12 - Tracking>" --out ./gps_towt
    python scripts/towt_gps_consolidate.py --source ... --out ... --vessel anemos --year 2025
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

FILE_RE = re.compile(r"^(\d{14})-([a-z]+)-satcoms\.csv$", re.IGNORECASE)
OUT_FIELDS = (
    "vessel",
    "recorded_at_utc",
    "latitude",
    "longitude",
    "sog_kn",
    "cog_deg",
    "interface",
    "source_file",
)
GAP_HOURS = 6.0


def _float(value: str | None) -> float | None:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _ts(row: dict) -> datetime | None:
    raw = (row.get("Timestamp") or "").strip()
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=UTC)
    d = (row.get("Date") or "").strip()
    if not d:
        return None
    try:
        dt = datetime.fromisoformat(d)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_satcoms_csv(text: str, *, vessel: str, source_file: str) -> tuple[list[dict], int]:
    """Lit un fichier satcoms ; renvoie (points, lignes rejetées)."""
    reader = csv.DictReader(text.splitlines(), delimiter=";")
    points: list[dict] = []
    rejected = 0
    for row in reader:
        ts = _ts(row)
        lat = _float(row.get("Latitude"))
        lon = _float(row.get("Longitude"))
        if (
            ts is None
            or lat is None
            or lon is None
            or not (-90 <= lat <= 90 and -180 <= lon <= 180)
        ):
            rejected += 1
            continue
        points.append(
            {
                "vessel": vessel,
                "recorded_at_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "latitude": f"{lat:.6f}",
                "longitude": f"{lon:.6f}",
                "sog_kn": _fmt(_float(row.get("SOG (knots)"))),
                "cog_deg": _fmt(_float(row.get("COG (degree)"))),
                "interface": (row.get("Active interface") or "").strip()[:40],
                "source_file": source_file,
            }
        )
    return points, rejected


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:g}"


def consolidate(
    source: Path, out: Path, *, vessel_filter: str | None = None, year_filter: int | None = None
) -> dict:
    all_csv = sorted(source.rglob("*.csv"))
    files = [p for p in all_csv if FILE_RE.match(p.name)]
    ignored = [p.name for p in all_csv if not FILE_RE.match(p.name)]
    manifest: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "files_scanned": len(files),
        # Un nommage inattendu (copie « (1) », navire avec chiffre…) n'est pas
        # silencieux : compté, et échantillonné pour être corrigé à la main.
        "files_ignored_name": len(ignored),
        "files_ignored_sample": ignored[:20],
        "files_read": 0,
        "files_unreadable": [],
        "rows_rejected": 0,
        "duplicates_dropped": 0,
        "outputs": {},
    }
    # (vessel, year) -> {ts_iso: point}
    buckets: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for i, path in enumerate(files, 1):
        m = FILE_RE.match(path.name)
        assert m is not None
        vessel = m.group(2).lower()
        if vessel_filter and vessel != vessel_filter.lower():
            continue
        if year_filter and not path.name.startswith(str(year_filter)):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            manifest["files_unreadable"].append(f"{path.name}: {exc}")
            continue
        points, rejected = parse_satcoms_csv(text, vessel=vessel, source_file=path.name)
        manifest["files_read"] += 1
        manifest["rows_rejected"] += rejected
        for pt in points:
            year = int(pt["recorded_at_utc"][:4])
            bucket = buckets[(vessel, year)]
            key = pt["recorded_at_utc"]
            prev = bucket.get(key)
            if prev is None:
                bucket[key] = pt
            else:
                manifest["duplicates_dropped"] += 1
                # Préférer la version renseignée (SOG/COG) à la ligne « vide » de tête.
                if not prev["sog_kn"] and pt["sog_kn"]:
                    bucket[key] = pt
        if i % 2000 == 0:
            print(f"  … {i}/{len(files)} fichiers", file=sys.stderr)

    out.mkdir(parents=True, exist_ok=True)
    for (vessel, year), bucket in sorted(buckets.items()):
        rows = [bucket[k] for k in sorted(bucket)]
        name = f"towt_gps_{vessel}_{year}.csv"
        target = out / name
        with target.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
            w.writeheader()
            w.writerows(rows)
        gaps = _gaps(rows)
        manifest["outputs"][name] = {
            "vessel": vessel,
            "year": year,
            "points": len(rows),
            "first": rows[0]["recorded_at_utc"],
            "last": rows[-1]["recorded_at_utc"],
            "gaps_over_6h": len(gaps),
            "longest_gap_hours": max((g[2] for g in gaps), default=0.0),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def _gaps(rows: list[dict]) -> list[tuple[str, str, float]]:
    gaps: list[tuple[str, str, float]] = []
    prev: datetime | None = None
    for r in rows:
        cur = datetime.strptime(r["recorded_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if prev is not None:
            hours = (cur - prev).total_seconds() / 3600
            if hours > GAP_HOURS:
                gaps.append((prev.isoformat(), cur.isoformat(), round(hours, 1)))
        prev = cur
    return gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, required=True, help="dossier « 12 - Tracking »")
    parser.add_argument("--out", type=Path, required=True, help="dossier de sortie")
    parser.add_argument("--vessel", help="anemos | artemis (défaut : tous)")
    parser.add_argument("--year", type=int, help="ne traiter qu'une année de fichiers")
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        print(f"✖ dossier introuvable : {args.source}", file=sys.stderr)
        return 2
    manifest = consolidate(args.source, args.out, vessel_filter=args.vessel, year_filter=args.year)
    print(
        f"✔ {manifest['files_read']}/{manifest['files_scanned']} fichiers lus, "
        f"{manifest['rows_rejected']} ligne(s) rejetée(s), "
        f"{manifest['duplicates_dropped']} doublon(s) écarté(s)"
    )
    for name, info in manifest["outputs"].items():
        print(
            f"  {name}: {info['points']} points, {info['first']} → {info['last']}, "
            f"{info['gaps_over_6h']} trou(s) > 6 h"
        )
    if manifest["files_ignored_name"]:
        print(
            f"⚠ {manifest['files_ignored_name']} CSV au nom inattendu ignoré(s) — "
            "voir files_ignored_sample dans manifest.json"
        )
    if manifest["files_unreadable"]:
        print(f"⚠ {len(manifest['files_unreadable'])} fichier(s) illisible(s) — voir manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
