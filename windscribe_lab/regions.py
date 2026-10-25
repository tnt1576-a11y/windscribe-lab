"""Continent groups for server selection, using country codes rather than names.

UN geographic convention: Russia is Europe; Turkey, Cyprus and Georgia are Asia.
Unknown catalog codes remain selectable in Other.
"""

CONTINENTS = {
    "Africa": "DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG ZM ZW EH RE YT SH IO TF",
    "Asia": "AF AM AZ BH BD BT BN KH CN CY GE HK IN ID IR IQ IL JP JO KZ KW KG LA LB MO MY MV MN MM NP KP OM PK PS PH QA SA SG KR LK SY TW TJ TH TL TR TM AE UZ VN YE",
    "Europe": "AL AD AT BY BE BA BG HR CZ DK EE FO FI FR DE GI GR GG VA HU IS IE IM IT JE LV LI LT LU MT MD MC ME NL MK NO PL PT RO RU SM RS SK SI ES SJ SE CH UA GB AX",
    "North America": "AI AG AW BS BB BZ BM BQ CA KY CR CU CW DM DO SV GL GD GP GT HT HN JM MQ MX MS NI PA PR BL KN LC MF PM VC SX TT TC US VG VI",
    "South America": "AR BO BR CL CO EC FK GF GY PY PE SR UY VE GS",
    "Oceania": "AS AU CX CC CK FJ PF GU HM KI MH FM NR NC NZ NU NF MP PW PG PN WS SB TK TO TV UM VU WF",
    "Antarctica": "AQ BV",
}
_BY_CODE = {code: continent for continent, codes in CONTINENTS.items() for code in codes.split()}


def continent_for(code: str) -> str:
    return _BY_CODE.get(code.upper(), "Other")
