"""Parseur de formulaire noon report TOWT — piloté par les libellés, deux générations."""

from __future__ import annotations

from scripts.towt_noon_extract import parse_form


def _grid(text: str) -> list[list]:
    return [line.split("\t") for line in text.strip("\n").splitlines()]


V3 = """TOWT Reporting Form
\t
Vessel name:\tANEMOS\t\tVoyage number :\t1YMB4\t\t\tVersion 3
Type of report:\tNoon report
Date:\t10/09/2024
Time:\t12:00\tUTC-4
Latitude:\t30 °\t6 '\tN
Longitude:\t74 °\t29 '\tW
Previous port:\tNew York\t\tVessel condition:\tBallast
Next Port:\tSanta Marta
Time from last report\t24\t\tSpeed from last report\t8,3 kt
Distance from last report\t200
Time from SOSP\t85,3\t\tSpeed from SOSP\t8,3 kt
Distance from SOSP\t704
Distance to go\t1149
Announced ETA\t16/09/2024\t08:00\tUTC-5
\tRunning hours\tDO Consumption
Port Main Engine\t\t
FWD Generator\t21\t0,257
Total consumption:\t\t0
ROB DO \t
Weather \tTWS\tAWA\tAWS\tSea state\tSea direction\tShip speed
16:00\t18 kt\t135 °\t15 kt\t3\t120\t8,0 kt
20:00\t14 kt\t135 °\t14 kt\t3\t120\t8,0 kt
00:00\t18 kt\t105 °\t14 kt\t3\t120\t8,0 kt
04:00\t12 kt\t120 °\t8 kt\t3\t120\t8,0 kt
08:00\t14 kt\t110 °\t10 kt\t3\t120\t8,0 kt
12:00\t16 kt\t90 °\t13 kt\t3\t120\t8,0 kt
Sails in use \tJ0\tFWD J1\tFWD MS\tAFT J1\tAFT MS\tSail Boost\tMain Engine
16:00\tOFF\tOFF\tON\tON\tON\t\t
20:00\tOFF\tON\tON\tON\tON\t\t
00:00\tOFF\tON\tON\tON\tON\t40 kW\t
04:00\tOFF\tON\tON\tON\tON\t\t55%
08:00\tOFF\tON\tON\tON\tON\t\t55%
12:00\tOFF\tON\tON\tON\tON\t\t
\tTemperature \tRel. Humidity \tTemperature \tRel. Humidity
\tMidnight\t\tMidday
Sea water\t27°C\t\t27°C
Air\t25°C\t76%\t28°C\t68%
Lower Aft hold\t26°C\t67%\t28°C\t68%
Comments
No data regarding engines consumption today.
"""

REV21 = """TOWT Reporting Form
\t
Vessel name:\tANEMOS\t\tVoyage number :\t1HYF5\t\tLegend
\t\t\tSubject to EU-MRV\tYES
Type of report:\tNoon report
\t\t\tVessel is \tSailing
Date:\t18/08/2025
Time:\t12:00\tUTC-1
Latitude:\t46 °\t56 '\tN
Longitude:\t31 °\t17 '\tW
Previous port:\tNewark\t\tVessel condition:\tBallast\tDraft Fwd (m)\t3,0 m
Next Port:\tFécamp\t\tCargo quantity (MT)\t0,000 MT\tDraft Aft (m)\t4,1 m
Time from last report (h)\t24,0 hours\t\tSpeed from last report\t9,9 kt
Distance from last report (NM)\t238,0 NM
Time from departure from berth (h)\t258,5 hours\t\tSpeed from SOSP\t8,5 kt
Distance from departure from berth (NM)\t2209,0 NM
Distance to go (NM)\t1273,0 NM
Announced ETA\t25/08/2025\t12:00\tUTC+2
\tRunning hours (h)\tDO Consumption (t)\t\t\t\t\t\t\tRunning hours D\tRunning hours D-1 \tConso D (L)\tConso D-1 (L)\tHours Dep\tConso Dep
Port Main Engine\t0 hours\t0,000 MT\t\t\t\t\t\t\tPort Main Engine\t1945\t1945\t110187\t110187\t1863\t107073
FWD Generator\t21 hours\t0,257 MT\t\t\t\t\t\t\tFWD Generator\t2659\t2638\t47501\t47193\t2419\t43672
Total consumption:\t\t0,302 MT\t15,1 L/h
\tFrom departure\t8,054 MT\t37,3 L/h\t\t\t\t\t\tGO Density (t/m3)\t0,835
Bunkering (MT)
ROB DO (MT)\t64,7 MT
Weather \tTWS (kt)\tAWA (°)\tAWS (kt)\tSea state \tSea direction (heading, °)\tShip speed (kt)
16:00\t25 kt\t110 °\t18 kt\t2\t140\t9,5 kt
20:00\t13 kt\t90 °\t9 kt\t2\t140\t7,8 kt
00:00\t20 kt\t110 °\t16 kt\t3\t140\t10,0 kt
04:00\t27 kt\t90 °\t20 kt\t2\t25\t10,0 kt
08:00\t26 kt\t120 °\t21 kt\t3\t120\t10,0 kt
12:00\t26 kt\t120 °\t21 kt\t3\t120\t10,0 kt
Sails and Engines in use \tJ0\tFWD J1\tFWD MS\tAFT J1\tAFT MS\tSail Boost\tME PS load\tME SB load
16:00\tON\tOFF\tON\tON\tON\t00 kW\t00 %\t00 %
20:00\tON\tOFF\tON\tON\tON\t00 kW\t00 %\t00 %
00:00\tON\tOFF\tON\tON\tON\t00 kW\t00 %\t00 %
04:00\tOFF\tON\tON\tON\tOFF\t00 kW\t00 %\t00 %
08:00\tOFF\tON\tON\tON\tOFF\t00 kW\t00 %\t00 %
12:00\tOFF\tON\tON\tON\tON\t00 kW\t00 %\t00 %
\tTemperature \tRel. Humidity \tTemperature \tRel. Humidity
\tMidnight\t\tMidday
Sea water\t\t\t18°C
Cellar\t\t\t21°C\t61 %
Comments
Parc de batteries non disponible.
CFOTE_05 Noon Report\tRev 2.1\t18/06/2025
"""


def test_parse_version3_form():
    rec = parse_form(_grid(V3), source_file="ANEMOS - Noon - 2024-09-10.xlsx")
    assert rec["vessel"] == "ANEMOS" and rec["voyage_code"] == "1YMB4"
    assert rec["report_type"] == "Noon report"
    assert rec["tz_offset_h"] == -4 and rec["datetime_utc"] == "2024-09-10T16:00:00+00:00"
    assert rec["latitude"] == 30.1 and rec["longitude"] == -74.48333
    assert rec["previous_port"] == "New York" and rec["next_port"] == "Santa Marta"
    assert rec["speed_since_last_report_kn"] == 8.3
    assert rec["distance_since_departure_nm"] == 704 and rec["distance_to_go_nm"] == 1149
    assert rec["engines"]["FWD Generator"] == {"running_hours": 21.0, "consumption_t": 0.257}
    assert rec["total_consumption_t"] == 0.0
    assert len(rec["weather"]) == 6 and rec["weather"][0]["tws_kn"] == 18.0
    assert rec["sails_engines"][2]["j0"] is False and rec["sails_engines"][2]["aft_ms"] is True
    assert rec["sails_engines"][2]["sail_boost"] == 40.0
    assert rec["holds"]["Air"] == {
        "temp_midnight_c": 25.0,
        "rh_midnight_pct": 76.0,
        "temp_midday_c": 28.0,
        "rh_midday_pct": 68.0,
    }
    assert rec["comments"].startswith("No data")
    assert rec["form_version"] == "Version 3"


def test_parse_rev21_form_with_counters():
    rec = parse_form(_grid(REV21), source_file="ANEMOS - Noon - 2025-08-18.xlsx")
    assert (
        rec["voyage_code"] == "1HYF5" and rec["eu_mrv"] == "YES" and rec["vessel_is"] == "Sailing"
    )
    assert rec["datetime_utc"] == "2025-08-18T13:00:00+00:00"
    assert (
        rec["cargo_quantity_t"] == 0.0 and rec["draft_fwd_m"] == 3.0 and rec["draft_aft_m"] == 4.1
    )
    assert rec["hours_since_departure"] == 258.5 and rec["distance_since_departure_nm"] == 2209.0
    gen = rec["engines"]["FWD Generator"]
    assert gen["running_hours"] == 21.0 and gen["consumption_t"] == 0.257
    assert gen["counter_hours_d"] == 2659 and gen["counter_litres_d1"] == 47193
    assert gen["counter_litres_departure"] == 43672
    assert rec["total_consumption_t"] == 0.302 and rec["total_consumption_l_per_h"] == 15.1
    assert rec["go_density_t_m3"] == 0.835 and rec["rob_do_t"] == 64.7
    assert rec["sails_engines"][3]["me_ps_load"] == 0.0
    assert rec["holds"]["Cellar"]["rh_midday_pct"] == 61.0
    assert rec["form_version"].startswith("CFOTE_05 Noon Report Rev 2.1")
