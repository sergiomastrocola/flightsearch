"""
flightsearch — Google Flights cheap fare finder
Built on top of the `fli` library (https://github.com/punitarani/fli).

Usage:
  python flightsearch.py [options]

All options:
  Airports & destinations:
    --origins IATA [...]      Departure airport(s). Default: BGY MXP LIN
    --dest    IATA [...]      Specific destination(s). Overrides --region.
    --region  REGION [...]    World region(s) to scan. Default: europe
                              Choices: europe (eu), africa (af),
                              north_america (na), south_america (sa),
                              asia (as), australia_pacific (ap/oceania),
                              world / all
    --exclude CODE [...]      Exclude destinations by airport IATA (3 letters)
                              or country ISO code (2 letters). Mix freely.
                              e.g. --exclude ES FR BCN

  Dates:
    --from  YYYY-MM-DD        Start of search period. Default: 2026-07-01
    --to    YYYY-MM-DD        End of search period.   Default: 2026-09-30

  Trip duration:
    --nights MIN MAX          Min/max nights (max 21). Default: 2-3 (weekend).
                              combined/roundtrip without --nights: uses --from/--to
                              as a single fixed departure/return date pair.
    --dep-days N [...]        Departure weekdays: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri
                              5=Sat 6=Sun. Default: 3 4 (Thu+Fri) for weekend mode.

  Time windows:
    --time-out HH-HH          Outbound departure window e.g. 18-23
    --time-ret HH-HH          Return departure window  e.g.  8-23

  Budget:
    --max EUR                 Maximum total budget in EUR. If omitted: no cap.
    --min-price EUR           Minimum price filter (excludes suspiciously cheap
                              or erroneous results). Default: none.
    --no-budget               Explicitly disable cap (same as omitting --max).

  Passengers & bags:
    --adults N                Number of adult passengers. Default: 1
    --children N              Number of children (2-11). Default: 0
    --infants-lap N           Infants on lap. Default: 0
    --infants-seat N          Infants in seat. Default: 0
    --bags-checked N          Include N checked bag(s) in displayed price.
                              Makes low-cost vs traditional comparisons fair.
                              Default: 0 (price without bags)
    --bags-carryon            Include carry-on bag fee in displayed price.

  Flight options:
    --stops N                 Max stops: 0=non-stop, 1=one stop, 2=two or fewer,
                              any=no limit. Default: any
    --max-duration MINUTES    Max total flight duration in minutes.
                              e.g. --max-duration 180 for flights under 3h
    --max-layover MINUTES     Max layover duration in minutes.
    --layover-airports IATA   Preferred layover airport(s).
    --sort KEY                Sort results by: cheapest (default), best,
                              departure, arrival, duration, emissions

  Cabin & mode:
    --cabin CLASS             economy (default) | premium_economy | business | first
    --mode  MODE              combined (default) | roundtrip | oneway | bestprice
                              combined  = two separate one-ways; best for low-cost,
                                          allows different airports per leg.
                              roundtrip = native RT ticket; better for traditional
                                          carriers where A/R < 2x one-way.
                              oneway    = outbound only; --nights ignored.
                              bestprice = runs both combined AND roundtrip in parallel,
                                          returns the cheapest.

  Airline / alliance filters:
    --airlines IATA [...]     Only show results with these airlines.
    --exclude-airlines IATA   Exclude these airlines (client-side filter).
    --alliance NAME           star | oneworld | skyteam (native API filter).

  Performance:
    --workers N               Parallel search threads. Default: 4.

  Output:
    --output FILE             CSV filename. Default: results.csv
                              Rows written in real time as results arrive.
    --json FILE               Also save results as JSON. Default: none.
    --top N                   Show only the N cheapest results. Default: all.
    --airport-names           Show full airport names instead of IATA codes.
    --calendar                Calendar mode: for a fixed origin→dest pair, show
                              a price-per-day table across the date range.
                              Requires --dest with a single destination.

Examples:
  # Default: BGY+MXP+LIN → Europe, Jul-Sep 2026, no budget cap, economy, weekends
  python flightsearch.py

  # Non-stop flights only, with 1 checked bag included in price
  python flightsearch.py --dest BCN --stops 0 --bags-checked 1

  # 2 adults, business class, max 3h flight, sorted by duration
  python flightsearch.py --adults 2 --cabin business --max-duration 180 --sort duration

  # Calendar mode: cheapest day to fly BGY→BCN in July
  python flightsearch.py --origins BGY --dest BCN --from 2026-07-01 --to 2026-07-31 --calendar

  # Top 10 cheapest results only, saved as JSON
  python flightsearch.py --region europe --top 10 --json results.json

  # Exclude Spain and France, layover max 90 min
  python flightsearch.py --region europe --exclude ES FR --max-layover 90

  # Best price (combined vs roundtrip, cheapest wins)
  python flightsearch.py --origins BGY MXP LIN --region europe --mode bestprice --max 80
"""

import csv
import json
import time
import shelve
import hashlib
import argparse
import threading
import sys
from pathlib import Path
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import tomllib          # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib   # pip install tomli for 3.10
    except ImportError:
        tomllib = None
from fli.models import (
    Airport, PassengerInfo, SeatType, MaxStops, SortBy, TripType,
    FlightSearchFilters, FlightSegment, TimeRestrictions, BagsFilter,
    LayoverRestrictions, PriceLimit,
)
from fli.models.google_flights.base import Currency
from fli.search import SearchFlights

# ══════════════════════════════════════════════════════════════
#  DEFAULTS
# ══════════════════════════════════════════════════════════════
DEFAULT_ORIGINS    = ["BGY", "MXP", "LIN"]
DEFAULT_DATE_FROM  = "2026-07-01"
DEFAULT_DATE_TO    = "2026-09-30"
DEFAULT_CABIN      = "economy"
DEFAULT_MODE       = "combined"
DEFAULT_WORKERS    = 4
CACHE_FILE         = ".flightsearch_cache"
CACHE_TTL_HOURS    = 3

CABIN_MAP = {
    "economy":         SeatType.ECONOMY,
    "premium_economy": SeatType.PREMIUM_ECONOMY,
    "premium":         SeatType.PREMIUM_ECONOMY,
    "business":        SeatType.BUSINESS,
    "first":           SeatType.FIRST,
}
CABIN_LABEL = {
    "economy": "Economy", "premium_economy": "Premium Economy",
    "premium": "Premium Economy", "business": "Business", "first": "First",
}

STOPS_MAP = {
    "0": MaxStops.NON_STOP,
    "1": MaxStops.ONE_STOP_OR_FEWER,
    "2": MaxStops.TWO_OR_FEWER_STOPS,
    "any": MaxStops.ANY,
}

SORT_MAP = {
    "cheapest":   SortBy.CHEAPEST,
    "best":       SortBy.BEST,
    "departure":  SortBy.DEPARTURE_TIME,
    "arrival":    SortBy.ARRIVAL_TIME,
    "duration":   SortBy.DURATION,
    "emissions":  SortBy.EMISSIONS,
}

DEFAULT_TRIP_SCHEMES = [
    (2, 3, [3, 4], "18-23", "8-23"),
    (2, 3, [4],    "6-13",  "8-23"),
    (3, 3, [4],    "18-23", "8-23"),
]

AIRLINE_NAME_FIXES: dict[str, str] = {
    "LC Péru":               "Wizz Air Malta",
    "Peruvian Airlines":     "Peruvian Airlines ⚠️",
    "Viva Airlines Peru":    "Viva Air Peru ⚠️",
    "Eastern Airlines, LLC": "Eastern Airlines ⚠️",
    "Lufthansa Cargo":       "Lufthansa",
    "Alitalia":              "ITA Airways",
}

AIRLINE_CODE_CORRECTIONS: dict[str, str] = {
    "W4": "Wizz Air Malta", "W9": "Wizz Air UK",
    "LH": "Lufthansa", "AZ": "ITA Airways",
    "S0": "Somon Air", "Z0": "Norse Atlantic UK",
    "MT": "Thomas Cook Airlines Scandinavia",
}

AIRLINE_DISPLAY_NAMES: dict[str, str] = {
    "FR": "Ryanair", "U2": "easyJet", "VY": "Vueling", "V7": "Volotea",
    "W6": "Wizz Air", "W4": "Wizz Air Malta", "W9": "Wizz Air UK",
    "AF": "Air France", "KL": "KLM", "LH": "Lufthansa", "LX": "Swiss",
    "OS": "Austrian Airlines", "SN": "Brussels Airlines", "EW": "Eurowings",
    "BA": "British Airways", "IB": "Iberia", "AZ": "ITA Airways",
    "TK": "Turkish Airlines", "EK": "Emirates", "QR": "Qatar Airways",
    "EY": "Etihad", "SQ": "Singapore Airlines", "CX": "Cathay Pacific",
    "NH": "ANA", "JL": "Japan Airlines", "KE": "Korean Air",
    "OZ": "Asiana Airlines", "CA": "Air China", "MU": "China Eastern",
    "CZ": "China Southern", "AI": "Air India", "TG": "Thai Airways",
    "MH": "Malaysia Airlines", "GA": "Garuda Indonesia",
    "UA": "United Airlines", "AA": "American Airlines", "DL": "Delta Air Lines",
    "AC": "Air Canada", "AM": "Aeromexico", "LA": "LATAM Airlines",
    "G3": "GOL", "AD": "Azul", "SK": "SAS", "AY": "Finnair",
    "LO": "LOT Polish", "OK": "Czech Airlines", "OU": "Croatia Airlines",
    "SA": "South African Airways", "ET": "Ethiopian Airlines",
    "AT": "Royal Air Maroc", "MS": "EgyptAir", "QF": "Qantas",
    "NZ": "Air New Zealand",
}

from fli.models import Airline as _AirlineEnum
ALLIANCE_MAP: dict[str, "_AirlineEnum"] = {
    "star":     _AirlineEnum.STAR_ALLIANCE,
    "oneworld": _AirlineEnum.ONEWORLD,
    "skyteam":  _AirlineEnum.SKYTEAM,
}

REGIONS: dict[str, list[str]] = {
    "europe": [
        "BGY","MXP","LIN","FCO","NAP","PMO","CTA","CAG","BRI","BLQ","VCE","TRN","GOA","PSA","FLR","TSF",
        "BCN","MAD","LIS","OPO","SVQ","VLC","AGP","PMI","IBZ","TFS","LPA","ACE","FNC","SCQ","BIO","VLL",
        "CDG","ORY","NCE","LYS","MRS","TLS","BOD","NTE","SXB",
        "AMS","BRU","LGG","EIN","RTM","ANR",
        "LHR","LGW","STN","LTN","MAN","BHX","EDI","GLA","DUB","ORK","SNN","BFS","LPL","BRS","EXT","ABZ",
        "FRA","MUC","BER","DUS","HAM","STR","CGN","NUE","VIE","ZRH","GVA","BSL","SZG","INN","GRZ",
        "CPH","ARN","OSL","HEL","BGO","GOT","TRF","TLL","RIX","VNO","KEF",
        "WAW","KRK","GDN","WRO","POZ","BUD","PRG","BRQ","OTP","SOF","BEG","SKP","LJU","ZAG","DBV","SPU","TGD","TIV",
        "ATH","SKG","HER","CHQ","RHO","CFU","ZTH","KGS","MYT","LCA","PFO",
        "IST","SAW","AYT","ADB","ESB","BJV",
        "TBS","EVN","GYD","LED","SVO","DME","VKO",
        "MLA","TIA","PRN",
    ],
    "africa": [
        "CAI","HRG","SSH","LXR","CMN","RAK","AGA","TNG","TUN","SFA","MIR","ALG","ORN","CZL",
        "ABV","LOS","ACC","DKR","ABJ","COO","OUA","BKO","NIM","LFW",
        "NBO","MBA","ADD","DAR","ZNZ","KGL","EBB","JRO","ASM","HGA",
        "JNB","CPT","DUR","HRE","LUN","LAD","MPM","WDH","GBE",
        "MRU","RUN","SEZ","DLA","NSI","LBV","BZV","FIH","FBM",
    ],
    "north_america": [
        "JFK","EWR","LGA","BOS","PHL","IAD","DCA","ATL","MIA","FLL","MCO","TPA","CLT",
        "ORD","MDW","DTW","MSP","STL","MCI","DFW","IAH","HOU","DEN","PHX","LAS",
        "LAX","SFO","SJC","OAK","SEA","PDX","SLC","ANC","HNL","OGG","SAN","SNA","BUR",
        "YYZ","YUL","YVR","YYC","YEG","YOW","YHZ","YWG",
        "MEX","CUN","GDL","MTY","SJD","ZIH","PVR","MZT","OAX","VER",
        "HAV","SDQ","PUJ","SJU","STT","STX","BGI","ANU","SXM","SKB","POS","TAB",
        "NAS","FPO","MBJ","KIN","GCM","CUR","AUA","BON","PTP","FDF","SFG",
        "GUA","SAL","TGU","MGA","SJO","PTY","BZE",
    ],
    "south_america": [
        "GRU","GIG","BSB","SSA","REC","FOR","BEL","MAO","CWB","POA","FLN","CGH","VCP",
        "EZE","AEP","COR","MDZ","BRC","IGR","USH",
        "SCL","IPC","PMC","ANF","CCP","IQQ",
        "BOG","MDE","CLO","CTG","BAQ",
        "LIM","CUZ","AQP","IQT","TRU",
        "UIO","GYE","GPS","CCS","MAR",
        "VVI","LPB","CBB","ASU","MVD","GEO","PBM",
    ],
    "asia": [
        "DXB","AUH","DOH","KWI","BAH","AMM","BEY","TLV","RUH","JED","MCT","MED",
        "DEL","BOM","MAA","BLR","CCU","HYD","GOI","CMB","DAC","KTM","MLE","KHI","LHE","ISB",
        "BKK","DMK","HKT","CNX","KBV","USM","SGN","HAN","DAD","KUL","LGK","PEN",
        "SIN","CGK","DPS","SUB","MNL","CEB","BCD","RGN","VTE","BND",
        "HKG","MFM","PEK","PVG","CAN","SZX","CTU","CKG","XIY","WUH","TSN",
        "ICN","GMP","PUS","NRT","HND","KIX","NGO","CTS","TPE","TSA","KHH",
        "ALA","NQZ","TAS","SKD","DYU","ASB","GYD","VVO","KHV",
    ],
    "australia_pacific": [
        "SYD","MEL","BNE","PER","ADL","CBR","OOL","CNS","DRW","TSV","HBA","MKY","LST","ASP",
        "AKL","CHC","WLG","ZQN","DUD","NSN",
        "NAN","SUV","APW","PPT","FAA","RAR","INU","TRW","MHQ","MAJ","POM","HON",
        "GUM","SPN","PPG","TBU","VLI","HIR","FUN",
    ],
}

REGION_ALIASES = {
    "eu": "europe", "af": "africa", "na": "north_america",
    "northamerica": "north_america", "sa": "south_america",
    "southamerica": "south_america", "as": "asia",
    "ap": "australia_pacific", "pacific": "australia_pacific",
    "oceania": "australia_pacific", "world": None, "all": None,
}
DEFAULT_REGION = "europe"

AIRPORT_COUNTRY: dict[str, str] = {
    "BGY":"IT","MXP":"IT","LIN":"IT","FCO":"IT","NAP":"IT","PMO":"IT",
    "CTA":"IT","CAG":"IT","BRI":"IT","BLQ":"IT","VCE":"IT","TRN":"IT",
    "GOA":"IT","PSA":"IT","FLR":"IT","TSF":"IT",
    "BCN":"ES","MAD":"ES","SVQ":"ES","VLC":"ES","AGP":"ES","PMI":"ES",
    "IBZ":"ES","TFS":"ES","LPA":"ES","ACE":"ES","SCQ":"ES","BIO":"ES","VLL":"ES",
    "LIS":"PT","OPO":"PT","FNC":"PT",
    "CDG":"FR","ORY":"FR","NCE":"FR","LYS":"FR","MRS":"FR","TLS":"FR","BOD":"FR","NTE":"FR","SXB":"FR",
    "BRU":"BE","LGG":"BE","ANR":"BE","AMS":"NL","EIN":"NL","RTM":"NL",
    "LHR":"GB","LGW":"GB","STN":"GB","LTN":"GB","MAN":"GB","BHX":"GB",
    "EDI":"GB","GLA":"GB","LPL":"GB","BRS":"GB","EXT":"GB","ABZ":"GB","BFS":"GB",
    "DUB":"IE","ORK":"IE","SNN":"IE",
    "FRA":"DE","MUC":"DE","BER":"DE","DUS":"DE","HAM":"DE","STR":"DE","CGN":"DE","NUE":"DE",
    "VIE":"AT","SZG":"AT","INN":"AT","GRZ":"AT","ZRH":"CH","GVA":"CH","BSL":"CH",
    "CPH":"DK","ARN":"SE","GOT":"SE","OSL":"NO","BGO":"NO","TRF":"NO","HEL":"FI","KEF":"IS",
    "TLL":"EE","RIX":"LV","VNO":"LT",
    "WAW":"PL","KRK":"PL","GDN":"PL","WRO":"PL","POZ":"PL",
    "BUD":"HU","PRG":"CZ","BRQ":"CZ","OTP":"RO","SOF":"BG","BEG":"RS","SKP":"MK",
    "LJU":"SI","ZAG":"HR","DBV":"HR","SPU":"HR","TIV":"HR","TGD":"ME",
    "MLA":"MT","TIA":"AL","PRN":"XK",
    "ATH":"GR","SKG":"GR","HER":"GR","CHQ":"GR","RHO":"GR","CFU":"GR","ZTH":"GR","KGS":"GR","MYT":"GR",
    "LCA":"CY","PFO":"CY","IST":"TR","SAW":"TR","AYT":"TR","ADB":"TR","ESB":"TR","BJV":"TR",
    "TBS":"GE","EVN":"AM","GYD":"AZ",
    "LED":"RU","SVO":"RU","DME":"RU","VKO":"RU","VVO":"RU","KHV":"RU",
    "CMN":"MA","RAK":"MA","AGA":"MA","TNG":"MA","TUN":"TN","SFA":"TN","MIR":"TN",
    "ALG":"DZ","ORN":"DZ","CZL":"DZ","CAI":"EG","HRG":"EG","SSH":"EG","LXR":"EG",
    "ABV":"NG","LOS":"NG","ACC":"GH","DKR":"SN","ABJ":"CI","COO":"BJ",
    "OUA":"BF","BKO":"ML","NIM":"NE","LFW":"TG",
    "NBO":"KE","MBA":"KE","ADD":"ET","DAR":"TZ","ZNZ":"TZ","KGL":"RW",
    "EBB":"UG","JRO":"TZ","ASM":"ER","HGA":"SO",
    "JNB":"ZA","CPT":"ZA","DUR":"ZA","HRE":"ZW","LUN":"ZM","LAD":"AO",
    "MPM":"MZ","WDH":"NA","GBE":"BW","MRU":"MU","RUN":"RE","SEZ":"SC",
    "DLA":"CM","NSI":"CM","LBV":"GA","BZV":"CG","FIH":"CD","FBM":"CD",
    "JFK":"US","EWR":"US","LGA":"US","BOS":"US","PHL":"US","IAD":"US","DCA":"US",
    "ATL":"US","MIA":"US","FLL":"US","MCO":"US","TPA":"US","CLT":"US","ORD":"US",
    "MDW":"US","DTW":"US","MSP":"US","STL":"US","MCI":"US","DFW":"US","IAH":"US",
    "HOU":"US","DEN":"US","PHX":"US","LAS":"US","LAX":"US","SFO":"US","SJC":"US",
    "OAK":"US","SEA":"US","PDX":"US","SLC":"US","ANC":"US","HNL":"US","OGG":"US",
    "SAN":"US","SNA":"US","BUR":"US","SJD":"US","HON":"US","GUM":"US","SPN":"US","PPG":"US",
    "YYZ":"CA","YUL":"CA","YVR":"CA","YYC":"CA","YEG":"CA","YOW":"CA","YHZ":"CA","YWG":"CA",
    "MEX":"MX","CUN":"MX","GDL":"MX","MTY":"MX","SJD":"MX","ZIH":"MX","PVR":"MX","MZT":"MX","OAX":"MX","VER":"MX",
    "HAV":"CU","SDQ":"DO","PUJ":"DO","SJU":"PR","STT":"VI","STX":"VI",
    "BGI":"BB","ANU":"AG","SXM":"SX","SKB":"KN","POS":"TT","TAB":"TT",
    "NAS":"BS","FPO":"BS","MBJ":"JM","KIN":"JM","GCM":"KY","CUR":"CW","AUA":"AW",
    "BON":"BQ","PTP":"GP","FDF":"MQ","SFG":"MF",
    "GUA":"GT","SAL":"SV","TGU":"HN","MGA":"NI","SJO":"CR","PTY":"PA","BZE":"BZ",
    "GRU":"BR","GIG":"BR","BSB":"BR","SSA":"BR","REC":"BR","FOR":"BR","BEL":"BR",
    "MAO":"BR","CWB":"BR","POA":"BR","FLN":"BR","CGH":"BR","VCP":"BR",
    "EZE":"AR","AEP":"AR","COR":"AR","MDZ":"AR","BRC":"AR","IGR":"AR","USH":"AR",
    "SCL":"CL","IPC":"CL","PMC":"CL","ANF":"CL","CCP":"CL","IQQ":"CL",
    "BOG":"CO","MDE":"CO","CLO":"CO","CTG":"CO","BAQ":"CO",
    "LIM":"PE","CUZ":"PE","AQP":"PE","IQT":"PE","TRU":"PE",
    "UIO":"EC","GYE":"EC","GPS":"EC","CCS":"VE","MAR":"VE",
    "VVI":"BO","LPB":"BO","CBB":"BO","ASU":"PY","MVD":"UY","GEO":"GY","PBM":"SR",
    "DXB":"AE","AUH":"AE","DOH":"QA","KWI":"KW","BAH":"BH","AMM":"JO",
    "BEY":"LB","TLV":"IL","RUH":"SA","JED":"SA","MED":"SA","MCT":"OM",
    "DEL":"IN","BOM":"IN","MAA":"IN","BLR":"IN","CCU":"IN","HYD":"IN","GOI":"IN",
    "CMB":"LK","DAC":"BD","KTM":"NP","MLE":"MV","KHI":"PK","LHE":"PK","ISB":"PK",
    "BKK":"TH","DMK":"TH","HKT":"TH","CNX":"TH","KBV":"TH","USM":"TH",
    "SGN":"VN","HAN":"VN","DAD":"VN","KUL":"MY","LGK":"MY","PEN":"MY",
    "SIN":"SG","CGK":"ID","DPS":"ID","SUB":"ID","MNL":"PH","CEB":"PH","BCD":"PH",
    "RGN":"MM","VTE":"LA","BND":"BN","HKG":"HK","MFM":"MO",
    "PEK":"CN","PVG":"CN","CAN":"CN","SZX":"CN","CTU":"CN","CKG":"CN","XIY":"CN","WUH":"CN","TSN":"CN",
    "ICN":"KR","GMP":"KR","PUS":"KR",
    "NRT":"JP","HND":"JP","KIX":"JP","NGO":"JP","CTS":"JP",
    "TPE":"TW","TSA":"TW","KHH":"TW",
    "ALA":"KZ","NQZ":"KZ","TAS":"UZ","SKD":"UZ","DYU":"TJ","ASB":"TM",
    "SYD":"AU","MEL":"AU","BNE":"AU","PER":"AU","ADL":"AU","CBR":"AU","OOL":"AU",
    "CNS":"AU","DRW":"AU","TSV":"AU","HBA":"AU","MKY":"AU","LST":"AU","ASP":"AU",
    "AKL":"NZ","CHC":"NZ","WLG":"NZ","ZQN":"NZ","DUD":"NZ","NSN":"NZ",
    "NAN":"FJ","SUV":"FJ","APW":"WS","PPT":"PF","FAA":"PF","RAR":"CK","INU":"NR",
    "TRW":"KI","MAJ":"MH","POM":"PG","TBU":"TO","VLI":"VU","HIR":"SB","FUN":"TV",
}

# ══════════════════════════════════════════════════════════════
#  Thread-safe helpers
# ══════════════════════════════════════════════════════════════
_cache_lock  = threading.Lock()
_print_lock  = threading.Lock()
_disk_cache: dict = {}   # in-memory layer; shelve used for persistence
_disk_cache_db = None    # opened on demand

def tprint(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)

def _cache_key(origin, dest, travel_date, time_window, seat_type, airlines_key,
               stops, max_duration, bags, passengers_key, sort_by, max_layover):
    raw = f"{origin}|{dest}|{travel_date}|{time_window}|{seat_type}|{airlines_key}|{stops}|{max_duration}|{bags}|{passengers_key}|{sort_by}|{max_layover}"
    return hashlib.md5(raw.encode()).hexdigest()

def _load_disk_cache(cache_file):
    global _disk_cache_db, _disk_cache
    try:
        _disk_cache_db = shelve.open(cache_file, flag='c', writeback=False)
        now = datetime.now().timestamp()
        expired = [k for k, (ts, _) in _disk_cache_db.items() if now - ts > CACHE_TTL_HOURS * 3600]
        for k in expired:
            del _disk_cache_db[k]
        _disk_cache = {k: v for k, (_, v) in _disk_cache_db.items()}
    except Exception:
        _disk_cache = {}

def _save_to_disk(key, value):
    global _disk_cache_db
    try:
        if _disk_cache_db is not None:
            _disk_cache_db[key] = (datetime.now().timestamp(), value)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════
#  Model helpers
# ══════════════════════════════════════════════════════════════
def parse_time_window(s):
    if not s:
        return None
    parts = s.split("-")
    if len(parts) != 2:
        return None
    return TimeRestrictions(earliest_departure=int(parts[0]), latest_departure=int(parts[1]))

def make_segment(origin, dest, travel_date, time_window_str, selected_flight=None):
    seg = dict(
        departure_airport=[[origin, 0]],
        arrival_airport=[[dest, 0]],
        travel_date=travel_date.strftime("%Y-%m-%d"),
    )
    tr = parse_time_window(time_window_str)
    if tr:
        seg["time_restrictions"] = tr
    if selected_flight is not None:
        seg["selected_flight"] = selected_flight
    return FlightSegment(**seg)

def build_filters(origin, dest, travel_date, time_window, seat_type,
                  trip_type, airlines, stops, max_duration,
                  bags, passengers, sort_by, max_layover,
                  extra_segment=None, selected_flight=None):
    segs = [make_segment(origin, dest, travel_date, time_window, selected_flight)]
    if extra_segment:
        segs.append(extra_segment)
    lr = LayoverRestrictions(max_duration=max_layover) if max_layover else None
    bags_f = BagsFilter(checked_bags=bags[0], carry_on=bags[1]) if bags else None
    return FlightSearchFilters(
        passenger_info=passengers,
        flight_segments=segs,
        seat_type=seat_type,
        stops=stops,
        sort_by=sort_by,
        trip_type=trip_type,
        airlines=airlines if airlines else None,
        max_duration=max_duration,
        layover_restrictions=lr,
        bags=bags_f,
    )

def ap(code, use_names):
    if not use_names or not code:
        return code
    try:
        return f"{Airport[code].value} ({code})"
    except KeyError:
        return code

def format_flight(flight, price_override=None):
    if not flight or not flight.legs:
        return None
    leg = flight.legs[0]
    airline_raw = leg.airline.name  # use .name (IATA code) not .value (display name)
    display_name = AIRLINE_CODE_CORRECTIONS.get(airline_raw,
                   AIRLINE_NAME_FIXES.get(leg.airline.value, leg.airline.value))
    price = price_override if price_override is not None else flight.price
    return {
        "price":         price,
        "airline":       display_name,
        "airline_raw":   airline_raw,
        "flight_number": leg.flight_number or "",
        "dep_time":      leg.departure_datetime.strftime("%H:%M") if leg.departure_datetime else "?",
        "arr_time":      leg.arrival_datetime.strftime("%H:%M") if leg.arrival_datetime else "?",
        "stops":         flight.stops,
        "duration_min":  flight.duration,
        "has_warning":   "⚠️" in display_name,
    }

def fmt_duration(minutes):
    if not minutes:
        return ""
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m"

def print_result(r, mode, use_names=False):
    o = r["out"]
    rt = r["ret"]
    has_warn = o["has_warning"] or (rt["has_warning"] if rt else False)
    warn_tag = "  ⚠️  CHECK AIRLINE NAME" if has_warn else ""
    search_mode = r.get("search_mode", mode)
    if mode == "oneway":
        mode_tag = " [OW]"
    elif mode == "bestprice":
        tag_map = {"roundtrip": " [BP/RT]", "combined": " [BP/COMB]", "combined_mixed": " [BP/COMB-MIX]"}
        mode_tag = tag_map.get(search_mode, " [BP]")
    elif mode == "roundtrip":
        mode_tag = " [RT]"
    else:
        mode_tag = ""
    price_str = f"€{r['total']:.0f}" if r["total"] is not None else "€?"
    label = "PRICE" if mode == "oneway" else "TOTAL"
    origin_str = ap(r["origin"], use_names)
    dest_str   = ap(r["dest"],   use_names)
    ret_ap_str = ap(r["ret_ap"], use_names) if r.get("ret_ap") else ""
    dur_out = fmt_duration(o.get("duration_min"))
    tprint(f"💶 {label} {price_str}  [{r['label']}]{mode_tag}{warn_tag}")
    tprint(f"   ✈  OUT    {r['dep_date'].strftime('%a %d/%m')}  "
           f"{origin_str} → {dest_str}  "
           f"{o['dep_time']} → {o['arr_time']}  {o['airline']} {o['flight_number']}  "
           f"€{o['price']:.0f}  {dur_out}  ({o['stops']} stop{'s' if o['stops'] != 1 else ''})")
    if rt:
        dur_ret = fmt_duration(rt.get("duration_min"))
        tprint(f"   ↩  RET    {r['ret_date'].strftime('%a %d/%m')}  "
               f"{dest_str} → {ret_ap_str}  "
               f"{rt['dep_time']} → {rt['arr_time']}  {rt['airline']} {rt['flight_number']}  "
               f"€{rt['price']:.0f}  {dur_ret}  ({rt['stops']} stop{'s' if rt['stops'] != 1 else ''})")
    tprint()

# ══════════════════════════════════════════════════════════════
#  HTTP search functions (with persistent cache)
# ══════════════════════════════════════════════════════════════
def cached_search_one_way(origin, dest, travel_date, time_window, seat_type,
                          airlines=None, exclude_airlines=None,
                          stops=MaxStops.ANY, max_duration=None,
                          bags=None, passengers=None,
                          sort_by=SortBy.CHEAPEST, max_layover=None):
    if passengers is None:
        passengers = PassengerInfo(adults=1)
    airlines_key = tuple(sorted(a.name for a in airlines)) if airlines else ()
    bags_key = str(bags) if bags else ""
    pax_key = f"{passengers.adults},{passengers.children},{passengers.infants_in_seat},{passengers.infants_on_lap}"
    key = _cache_key(origin.name, dest.name, str(travel_date), str(time_window),
                     seat_type.name, str(airlines_key), stops.name,
                     str(max_duration), bags_key, pax_key, sort_by.name, str(max_layover))

    with _cache_lock:
        if key in _disk_cache:
            results = _disk_cache[key]
        else:
            results = None

    if results is None:
        try:
            f = build_filters(origin, dest, travel_date, time_window, seat_type,
                              TripType.ONE_WAY, airlines, stops, max_duration,
                              bags, passengers, sort_by, max_layover)
            results = SearchFlights().search(f) or []
        except Exception:
            results = []
        with _cache_lock:
            _disk_cache[key] = results
        _save_to_disk(key, results)

    if exclude_airlines and results:
        results = [f for f in results
                   if not f.legs or f.legs[0].airline.name not in exclude_airlines]
    return results

def search_round_trip(origin, dest, dep_date, ret_date, out_window, ret_window, seat_type,
                      airlines=None, exclude_airlines=None,
                      stops=MaxStops.ANY, max_duration=None,
                      bags=None, passengers=None,
                      sort_by=SortBy.CHEAPEST, max_layover=None):
    if passengers is None:
        passengers = PassengerInfo(adults=1)
    try:
        ret_seg = make_segment(dest, origin, ret_date, ret_window)
        f = build_filters(origin, dest, dep_date, out_window, seat_type,
                          TripType.ROUND_TRIP, airlines, stops, max_duration,
                          bags, passengers, sort_by, max_layover,
                          extra_segment=ret_seg)
        outbound_results = SearchFlights().search(f, top_n=1) or []
        pairs = []
        for item in outbound_results:
            if isinstance(item, tuple):
                pairs.append(item)
            else:
                ret_seg2 = make_segment(dest, origin, ret_date, ret_window)
                f2 = build_filters(origin, dest, dep_date, out_window, seat_type,
                                   TripType.ROUND_TRIP, airlines, stops, max_duration,
                                   bags, passengers, sort_by, max_layover,
                                   extra_segment=ret_seg2, selected_flight=item)
                ret_results = SearchFlights().search(f2, top_n=1) or []
                for ret_item in ret_results:
                    pairs.append((item, ret_item) if not isinstance(ret_item, tuple) else ret_item)
        if exclude_airlines and pairs:
            pairs = [(o, r) for o, r in pairs
                     if (not o.legs or o.legs[0].airline.name not in exclude_airlines)
                     and (not r.legs or r.legs[0].airline.name not in exclude_airlines)]
        return pairs
    except Exception:
        return []

# ══════════════════════════════════════════════════════════════
#  Task functions
# ══════════════════════════════════════════════════════════════
def _search_kwargs(args_ns):
    """Return common search kwargs from parsed args."""
    return dict(
        stops=args_ns._stops,
        max_duration=args_ns._max_duration,
        bags=args_ns._bags,
        passengers=args_ns._passengers,
        sort_by=args_ns._sort_by,
        max_layover=args_ns._max_layover,
    )

def task_oneway(origin, dest, dep_date, out_w, label, seat_type, max_eur, min_price,
                airlines, exclude_airlines, skw):
    flights = cached_search_one_way(origin, dest, dep_date, out_w, seat_type,
                                    airlines=airlines, exclude_airlines=exclude_airlines, **skw)
    if not flights:
        return None
    best = flights[0]
    if best.price is None:
        return None
    if max_eur and best.price > max_eur:
        return None
    if min_price and best.price < min_price:
        return None
    o = format_flight(best)
    if not o:
        return None
    return {"label": label, "dep_date": dep_date, "ret_date": None,
            "origin": origin.name, "dest": dest.name, "ret_ap": None,
            "out": o, "ret": None, "total": best.price}

def task_roundtrip(origin, dest, dep_date, ret_date, out_w, ret_w, label,
                   seat_type, max_eur, min_price, airlines, exclude_airlines, skw):
    pairs = search_round_trip(origin, dest, dep_date, ret_date, out_w, ret_w, seat_type,
                              airlines=airlines, exclude_airlines=exclude_airlines, **skw)
    for out_flight, ret_flight in pairs[:1]:
        o = format_flight(out_flight)
        r = format_flight(ret_flight)
        if not o or not r:
            continue
        total = o["price"] or 0
        if max_eur and total > max_eur:
            return None
        if min_price and total < min_price:
            return None
        return {"label": label, "dep_date": dep_date, "ret_date": ret_date,
                "origin": origin.name, "dest": dest.name, "ret_ap": origin.name,
                "out": o, "ret": r, "total": total, "search_mode": "roundtrip"}
    return None

def task_combined(origin, dest, dep_date, ret_date, out_w, ret_w, label,
                  seat_type, max_eur, min_price, all_origins, airlines, exclude_airlines, skw):
    out_flights = cached_search_one_way(origin, dest, dep_date, out_w, seat_type,
                                        airlines=airlines, exclude_airlines=exclude_airlines, **skw)
    if not out_flights:
        return None
    best_out = out_flights[0]
    if best_out.price is None or (max_eur and best_out.price >= max_eur):
        return None

    best_ret = None
    best_ret_ap = origin
    with ThreadPoolExecutor(max_workers=len(all_origins)) as inner:
        futs = {
            inner.submit(cached_search_one_way, dest, ret_ap, ret_date, ret_w, seat_type,
                         airlines, exclude_airlines, **skw): ret_ap
            for ret_ap in all_origins
        }
        for fut in as_completed(futs):
            ret_ap = futs[fut]
            rf = fut.result()
            if rf and rf[0].price is not None:
                if best_ret is None or rf[0].price < best_ret.price:
                    best_ret = rf[0]
                    best_ret_ap = ret_ap

    if best_ret is None or best_ret.price is None:
        return None

    price_out = best_out.price or 0
    price_ret = best_ret.price or 0
    total = price_out + price_ret
    if max_eur and total > max_eur:
        return None
    if min_price and total < min_price:
        return None

    o = format_flight(best_out, price_override=price_out)
    r = format_flight(best_ret, price_override=price_ret)
    if not o or not r:
        return None
    out_code = o.get("airline_raw", "")
    ret_code = r.get("airline_raw", "")
    search_mode = "combined" if out_code == ret_code else "combined_mixed"
    return {"label": label, "dep_date": dep_date, "ret_date": ret_date,
            "origin": origin.name, "dest": dest.name, "ret_ap": best_ret_ap.name,
            "out": o, "ret": r, "total": total, "search_mode": search_mode}

def task_bestprice(origin, dest, dep_date, ret_date, out_w, ret_w, label,
                   seat_type, max_eur, min_price, all_origins, airlines, exclude_airlines, skw):
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_rt   = pool.submit(task_roundtrip, origin, dest, dep_date, ret_date,
                               out_w, ret_w, label, seat_type, max_eur, min_price,
                               airlines, exclude_airlines, skw)
        fut_comb = pool.submit(task_combined, origin, dest, dep_date, ret_date,
                               out_w, ret_w, label, seat_type, max_eur, min_price,
                               all_origins, airlines, exclude_airlines, skw)
        rt_entry   = fut_rt.result()
        comb_entry = fut_comb.result()

    candidates = [e for e in [rt_entry, comb_entry] if e is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda e: e["total"] or float("inf"))

# ══════════════════════════════════════════════════════════════
#  Calendar mode
# ══════════════════════════════════════════════════════════════
def run_calendar(origin, dest, date_from, date_to, seat_type, max_eur, min_price,
                 airlines, exclude_airlines, skw, workers, time_out, time_ret,
                 mode, all_origins):
    """Scan every day in the range and print a price calendar table."""
    days = []
    d = date_from
    while d <= date_to:
        days.append(d)
        d += timedelta(days=1)

    tprint(f"\n📅  Price calendar: {origin.name} → {dest.name}  ({date_from} → {date_to})\n")
    results_by_day: dict = {}
    lock = threading.Lock()

    def fetch_day(dep_date):
        label = dep_date.strftime("%a %d/%m")
        if mode in ("combined", "bestprice"):
            e = task_combined(origin, dest, dep_date, dep_date, time_out, time_ret,
                              label, seat_type, None, min_price, all_origins,
                              airlines, exclude_airlines, skw)
        else:
            e = task_oneway(origin, dest, dep_date, time_out, label, seat_type,
                            None, min_price, airlines, exclude_airlines, skw)
        return dep_date, e

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for dep_date, entry in executor.map(fetch_day, days):
            with lock:
                results_by_day[dep_date] = entry

    # Print calendar table
    valid = [(d, e) for d, e in sorted(results_by_day.items()) if e]
    if not valid:
        tprint("No results found.")
        return

    best_price = min(e["total"] for _, e in valid)
    tprint(f"{'Date':<14} {'Price':>8}  {'Airline':<22} {'Flight':<10} {'Dep':>6} {'Arr':>6}  {'Dur':>6}")
    tprint("─" * 76)
    for dep_date, e in valid:
        o = e["out"]
        tag = " ◀ cheapest" if abs(e["total"] - best_price) < 0.5 else ""
        if max_eur and e["total"] > max_eur:
            continue
        tprint(f"{dep_date.strftime('%a %d/%m/%Y'):<14} "
               f"€{e['total']:>6.0f}  "
               f"{o['airline']:<22} "
               f"{o['flight_number']:<10} "
               f"{o['dep_time']:>6} {o['arr_time']:>6}  "
               f"{fmt_duration(o.get('duration_min')):>6}"
               f"{tag}")
    tprint()

# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════
def build_date_pairs(date_from, date_to, nights_min, nights_max,
                     dep_days, time_out, time_ret):
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    pairs = []
    d = date_from
    while d <= date_to:
        if d.weekday() in dep_days:
            for nights in range(nights_min, nights_max + 1):
                ret = d + timedelta(days=nights)
                label = f"{nights}n {DAYS[d.weekday()]}→{DAYS[ret.weekday()]}"
                pairs.append((d, ret, time_out, time_ret, label))
        d += timedelta(days=1)
    return pairs

DEFAULT_CONFIG_FILE = "flightsearch.toml"

def load_config(path: str) -> dict:
    """Load a TOML config file and return its contents as a flat dict."""
    if tomllib is None:
        print("⚠️  TOML support not available. Install tomli: pip install tomli")
        return {}
    p = Path(path)
    if not p.exists():
        print(f"❌ Config file not found: {path}")
        sys.exit(1)
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"❌ Error reading config file: {e}")
        sys.exit(1)

def merge_config_into_args(config: dict, args: argparse.Namespace) -> argparse.Namespace:
    """
    Merge config file values into parsed args.
    CLI arguments always take precedence over config file values.
    Only sets a value from config if the current arg value is still the default (None or default).
    """
    # Map of config key → (argparse dest, is_list, default_value)
    CONFIG_MAP = {
        "origins":            ("origins",         True,  DEFAULT_ORIGINS),
        "dest":               ("dest",            True,  None),
        "region":             ("region",          True,  None),
        "exclude":            ("exclude",         True,  None),
        "from":               ("date_from",       False, DEFAULT_DATE_FROM),
        "to":                 ("date_to",         False, DEFAULT_DATE_TO),
        "nights":             ("nights",          True,  None),
        "dep_days":           ("dep_days",        True,  None),
        "time_out":           ("time_out",        False, None),
        "time_ret":           ("time_ret",        False, None),
        "max":                ("max",             False, None),
        "no_budget":          ("no_budget",       False, False),
        "min_price":          ("min_price",       False, None),
        "adults":             ("adults",          False, 1),
        "children":           ("children",        False, 0),
        "infants_lap":        ("infants_lap",     False, 0),
        "infants_seat":       ("infants_seat",    False, 0),
        "bags_checked":       ("bags_checked",    False, 0),
        "bags_carryon":       ("bags_carryon",    False, False),
        "stops":              ("stops",           False, "any"),
        "max_duration":       ("max_duration",    False, None),
        "max_layover":        ("max_layover",     False, None),
        "layover_airports":   ("layover_airports",True,  None),
        "sort":               ("sort",            False, "cheapest"),
        "cabin":              ("cabin",           False, DEFAULT_CABIN),
        "mode":               ("mode",            False, DEFAULT_MODE),
        "workers":            ("workers",         False, DEFAULT_WORKERS),
        "output":             ("output",          False, "results.csv"),
        "json":               ("json",            False, None),
        "top":                ("top",             False, None),
        "airport_names":      ("airport_names",   False, False),
        "calendar":           ("calendar",        False, False),
        "no_cache":           ("no_cache",        False, False),
        "airlines":           ("airlines",        True,  None),
        "exclude_airlines":   ("exclude_airlines",True,  None),
        "alliance":           ("alliance",        False, None),
    }

    for cfg_key, (dest, is_list, default) in CONFIG_MAP.items():
        if cfg_key not in config:
            continue
        cfg_val = config[cfg_key]
        current = getattr(args, dest, None)
        # Only override if the current value is still the default
        if current == default:
            if is_list and not isinstance(cfg_val, list):
                cfg_val = [cfg_val]
            if is_list and isinstance(cfg_val, list):
                cfg_val = [str(v) for v in cfg_val]
            setattr(args, dest, cfg_val)
    return args


def parse_args():
    p = argparse.ArgumentParser(
        description="flightsearch — Find cheap flights via Google Flights",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--origins", nargs="+", default=DEFAULT_ORIGINS, metavar="IATA")
    p.add_argument("--dest", nargs="+", default=None, metavar="IATA")
    p.add_argument("--region", nargs="+", default=None, metavar="REGION")
    p.add_argument("--exclude", nargs="+", default=None, metavar="CODE",
                   help="Exclude by airport IATA (3 letters) or country ISO (2 letters)")
    p.add_argument("--from", dest="date_from", default=DEFAULT_DATE_FROM, metavar="YYYY-MM-DD")
    p.add_argument("--to",   dest="date_to",   default=DEFAULT_DATE_TO,   metavar="YYYY-MM-DD")
    p.add_argument("--nights", nargs=2, type=int, default=None, metavar=("MIN", "MAX"))
    p.add_argument("--dep-days", nargs="+", type=int, default=None, metavar="N")
    p.add_argument("--time-out", default=None, metavar="HH-HH")
    p.add_argument("--time-ret", default=None, metavar="HH-HH")
    budget_group = p.add_mutually_exclusive_group()
    budget_group.add_argument("--max", type=float, default=None)
    budget_group.add_argument("--no-budget", action="store_true")
    p.add_argument("--min-price", type=float, default=None,
                   help="Minimum price — filters out suspiciously cheap results")
    p.add_argument("--adults",       type=int, default=1)
    p.add_argument("--children",     type=int, default=0)
    p.add_argument("--infants-lap",  type=int, default=0, dest="infants_lap")
    p.add_argument("--infants-seat", type=int, default=0, dest="infants_seat")
    p.add_argument("--bags-checked", type=int, default=0, dest="bags_checked",
                   help="Include N checked bag(s) in displayed price")
    p.add_argument("--bags-carryon", action="store_true", dest="bags_carryon",
                   help="Include carry-on bag fee in displayed price")
    p.add_argument("--stops", default="any", choices=["0", "1", "2", "any"],
                   help="Max stops: 0=non-stop 1=one stop 2=two or fewer any=no limit")
    p.add_argument("--max-duration", type=int, default=None, dest="max_duration",
                   metavar="MINUTES", help="Max total flight duration in minutes")
    p.add_argument("--max-layover", type=int, default=None, dest="max_layover",
                   metavar="MINUTES", help="Max layover duration in minutes")
    p.add_argument("--layover-airports", nargs="+", default=None, dest="layover_airports",
                   metavar="IATA", help="Preferred layover airports")
    p.add_argument("--sort", default="cheapest",
                   choices=["cheapest", "best", "departure", "arrival", "duration", "emissions"])
    p.add_argument("--cabin", default=DEFAULT_CABIN, choices=list(CABIN_MAP.keys()))
    p.add_argument("--mode", default=DEFAULT_MODE,
                   choices=["combined", "roundtrip", "oneway", "bestprice"])
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--output", default="results.csv", metavar="FILE")
    p.add_argument("--json", default=None, metavar="FILE",
                   help="Also save results as JSON")
    p.add_argument("--top", type=int, default=None, metavar="N",
                   help="Show only the N cheapest results")
    p.add_argument("--airport-names", action="store_true", default=False)
    p.add_argument("--calendar", action="store_true", default=False,
                   help="Calendar mode: price per day for a fixed origin→dest pair. "
                        "Requires --dest with a single destination.")
    p.add_argument("--no-cache", action="store_true", default=False,
                   help="Disable disk cache (always fetch fresh results)")
    p.add_argument("--airlines", nargs="+", default=None, metavar="IATA")
    p.add_argument("--exclude-airlines", nargs="+", default=None, metavar="IATA",
                   dest="exclude_airlines")
    p.add_argument("--alliance", default=None, choices=["star", "oneworld", "skyteam"])
    p.add_argument("--config", default=None, metavar="FILE",
                   help=f"Path to a TOML config file (default: {DEFAULT_CONFIG_FILE} if it exists). "
                        "CLI flags always override config file values.")
    return p.parse_args()

def main():
    args = parse_args()

    # ── Config file ───────────────────────────────────────────
    # Load config file: explicit --config > default flightsearch.toml > nothing
    config_path = args.config
    if config_path is None and Path(DEFAULT_CONFIG_FILE).exists():
        config_path = DEFAULT_CONFIG_FILE
    if config_path:
        config = load_config(config_path)
        args = merge_config_into_args(config, args)
        print(f"📋  Config loaded from: {config_path}")

    # ── Cache ─────────────────────────────────────────────────
    if not args.no_cache:
        _load_disk_cache(CACHE_FILE)

    # ── Origins ───────────────────────────────────────────────
    origins = []
    for code in [c.upper() for c in args.origins]:
        try:
            origins.append(Airport[code])
        except KeyError:
            print(f"⚠️  Unknown origin '{code}' — skipped.")
    if not origins:
        print("❌ No valid origin airports.")
        return

    # ── Dates ─────────────────────────────────────────────────
    try:
        date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date()
        date_to   = datetime.strptime(args.date_to,   "%Y-%m-%d").date()
    except ValueError as e:
        print(f"❌ Invalid date: {e}")
        return
    if date_from > date_to:
        print("❌ --from must be before --to.")
        return

    # ── Budget ────────────────────────────────────────────────
    max_eur   = None if args.no_budget else args.max
    min_price = args.min_price

    # ── Passengers & bags ─────────────────────────────────────
    passengers = PassengerInfo(
        adults=args.adults, children=args.children,
        infants_in_seat=args.infants_seat, infants_on_lap=args.infants_lap,
    )
    bags = (args.bags_checked, args.bags_carryon) if (args.bags_checked or args.bags_carryon) else None

    # ── Flight options ────────────────────────────────────────
    stops       = STOPS_MAP.get(args.stops, MaxStops.ANY)
    sort_by     = SORT_MAP.get(args.sort, SortBy.CHEAPEST)
    max_dur     = args.max_duration
    max_layover = args.max_layover

    layover_airports = None
    if args.layover_airports:
        layover_airports = []
        for c in [x.upper() for x in args.layover_airports]:
            try:
                layover_airports.append(Airport[c])
            except KeyError:
                print(f"⚠️  Unknown layover airport '{c}' — skipped.")

    # Attach resolved params to args for _search_kwargs
    args._stops      = stops
    args._max_duration = max_dur
    args._bags       = bags
    args._passengers = passengers
    args._sort_by    = sort_by
    args._max_layover = max_layover
    skw = _search_kwargs(args)

    # ── Cabin / mode ──────────────────────────────────────────
    seat_type   = CABIN_MAP[args.cabin]
    cabin_label = CABIN_LABEL[args.cabin]
    mode        = args.mode
    workers     = max(1, args.workers)
    airport_names = args.airport_names

    # ── Exclude filter ────────────────────────────────────────
    exclude_airports: set[str] = set()
    exclude_countries: set[str] = set()
    if args.exclude:
        for code in [c.upper() for c in args.exclude]:
            if len(code) == 2:
                exclude_countries.add(code)
            elif len(code) == 3:
                exclude_airports.add(code)
            else:
                print(f"⚠️  --exclude: '{code}' invalid — skipped.")
        if exclude_airports or exclude_countries:
            print(f"🚫  Excluding: {', '.join(sorted(exclude_airports | exclude_countries))}")

    def is_excluded(iata: str) -> bool:
        if iata in exclude_airports:
            return True
        country = AIRPORT_COUNTRY.get(iata)
        return country in exclude_countries if country else False

    # ── Destinations ──────────────────────────────────────────
    if args.dest:
        destinations = []
        for code in [c.upper() for c in args.dest]:
            if is_excluded(code):
                print(f"ℹ️  '{code}' excluded.")
                continue
            try:
                destinations.append(Airport[code])
            except KeyError:
                print(f"⚠️  Unknown destination '{code}' — skipped.")
        if not destinations:
            print("❌ No valid destinations.")
            return
    else:
        selected_regions = args.region if args.region else [DEFAULT_REGION]
        codes_set: list[str] = []
        seen_codes: set[str] = set()
        region_names_used: list[str] = []
        for r in selected_regions:
            r_key = REGION_ALIASES.get(r.lower(), r.lower())
            if r_key is None:
                for reg_name, reg_codes in REGIONS.items():
                    for c in reg_codes:
                        if c not in seen_codes:
                            seen_codes.add(c)
                            codes_set.append(c)
                region_names_used = list(REGIONS.keys())
                break
            elif r_key in REGIONS:
                for c in REGIONS[r_key]:
                    if c not in seen_codes:
                        seen_codes.add(c)
                        codes_set.append(c)
                region_names_used.append(r_key)
            else:
                print(f"⚠️  Unknown region '{r}' — skipped.")
        destinations = [Airport[c] for c in codes_set if c and not is_excluded(c)]
        excluded_count = len(codes_set) - len(destinations)
        if excluded_count:
            print(f"ℹ️  {excluded_count} destination(s) excluded.")

    # ── Airline / alliance ────────────────────────────────────
    from fli.models import Airline as AirlineEnum
    _valid_airline_codes = {a.name for a in AirlineEnum}

    def resolve_airline_codes(codes):
        result = []
        for c in [x.upper() for x in (codes or [])]:
            if c in _valid_airline_codes:
                result.append(AirlineEnum[c])
            else:
                print(f"⚠️  Unknown airline '{c}' — skipped.")
        return result or None

    if args.airlines and args.exclude_airlines:
        print("❌ --airlines and --exclude-airlines cannot be used together.")
        return
    if args.alliance and args.exclude_airlines:
        print("❌ --alliance and --exclude-airlines cannot be used together.")
        return
    if args.alliance and args.airlines:
        print("❌ --alliance and --airlines cannot be used together.")
        return

    filter_airlines = None
    if args.alliance:
        alliance_enum = ALLIANCE_MAP.get(args.alliance)
        if alliance_enum:
            filter_airlines = [alliance_enum]
            labels = {"star": "Star Alliance", "oneworld": "oneworld", "skyteam": "SkyTeam"}
            print(f"🤝  Alliance: {labels.get(args.alliance)}")
    elif args.airlines:
        filter_airlines = resolve_airline_codes(args.airlines)
        if filter_airlines:
            names = [AIRLINE_DISPLAY_NAMES.get(c.upper(), c.upper()) for c in args.airlines]
            print(f"✈   Airline filter: {', '.join(names)}")

    exclude_airlines = None
    if args.exclude_airlines:
        ex_enum = resolve_airline_codes(args.exclude_airlines)
        exclude_airlines = {a.name for a in (ex_enum or [])}
        if exclude_airlines:
            names = [AIRLINE_DISPLAY_NAMES.get(c.upper(), c.upper()) for c in args.exclude_airlines]
            print(f"🚫  Excluding airlines: {', '.join(names)}")

    # ── Calendar mode ─────────────────────────────────────────
    if args.calendar:
        if not args.dest or len(destinations) != 1:
            print("❌ --calendar requires --dest with exactly one destination.")
            return
        run_calendar(origins[0], destinations[0], date_from, date_to,
                     seat_type, max_eur, min_price,
                     filter_airlines, exclude_airlines, skw,
                     workers, args.time_out, args.time_ret, mode, origins)
        return

    # ── Date pairs ────────────────────────────────────────────
    if mode == "oneway":
        DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        dep_days = args.dep_days if args.dep_days else list(range(7))
        all_pairs = []
        d = date_from
        while d <= date_to:
            if d.weekday() in dep_days:
                all_pairs.append((d, d, args.time_out, None, DAYS[d.weekday()]))
            d += timedelta(days=1)
    elif args.nights:
        nights_min, nights_max = args.nights[0], min(args.nights[1], 21)
        dep_days = args.dep_days if args.dep_days else list(range(7))
        all_pairs = build_date_pairs(date_from, date_to, nights_min, nights_max,
                                     dep_days, args.time_out, args.time_ret)
    elif args.dep_days:
        all_pairs = build_date_pairs(date_from, date_to, 2, 3,
                                     args.dep_days, args.time_out, args.time_ret)
    elif mode in ("roundtrip", "combined", "bestprice") and not args.nights and not args.dep_days:
        nights = (date_to - date_from).days
        label = f"{nights}n {date_from.strftime('%a')}→{date_to.strftime('%a')}"
        all_pairs = [(date_from, date_to, args.time_out, args.time_ret, label)]
    else:
        all_pairs = []
        seen = set()
        for (nmin, nmax, dep_days, oo, ort) in DEFAULT_TRIP_SCHEMES:
            for pair in build_date_pairs(date_from, date_to, nmin, nmax, dep_days, oo, ort):
                key = (pair[0], pair[1])
                if key not in seen:
                    seen.add(key)
                    all_pairs.append(pair)

    tasks = [
        (origin, dest, dep_date, ret_date, out_w, ret_w, label)
        for origin in origins
        for dest in destinations if dest != origin
        for dep_date, ret_date, out_w, ret_w, label in all_pairs
    ]

    # ── Summary ───────────────────────────────────────────────
    budget_str = f"max €{max_eur:.0f}" if max_eur else "no limit"
    if min_price:
        budget_str += f" (min €{min_price:.0f})"
    dest_str = (", ".join(d.name for d in destinations)
                if args.dest else f"{len(destinations)} destinations")
    bags_str = ""
    if bags:
        parts = []
        if bags[0]: parts.append(f"{bags[0]} checked bag{'s' if bags[0]>1 else ''}")
        if bags[1]: parts.append("carry-on")
        bags_str = " + " + " & ".join(parts) + " included"
    pax_parts = [f"{args.adults} adult{'s' if args.adults>1 else ''}"]
    if args.children: pax_parts.append(f"{args.children} child{'ren' if args.children>1 else ''}")
    if args.infants_lap: pax_parts.append(f"{args.infants_lap} infant(s) lap")
    if args.infants_seat: pax_parts.append(f"{args.infants_seat} infant(s) seat")

    mode_labels = {
        "combined":  "Two separate one-ways",
        "roundtrip": "Native round-trip",
        "oneway":    "One-way only",
        "bestprice": "Best price (combined + roundtrip)",
    }

    print(f"\n🔍  flightsearch")
    print(f"✈   Origins:      {', '.join(o.name for o in origins)}")
    print(f"🌍  Destinations: {dest_str}")
    print(f"📅  Period:       {date_from} → {date_to}")
    print(f"🌙  Date pairs:   {len(all_pairs)}")
    print(f"💺  Cabin:        {cabin_label}{bags_str}")
    print(f"👥  Passengers:   {', '.join(pax_parts)}")
    print(f"🛑  Stops:        {args.stops}")
    if max_dur:  print(f"⏱   Max duration: {fmt_duration(max_dur)}")
    if max_layover: print(f"⏳  Max layover:  {fmt_duration(max_layover)}")
    print(f"🔃  Sort:         {args.sort}")
    print(f"💶  Budget:       {budget_str}")
    print(f"🔄  Mode:         {mode_labels[mode]}")
    print(f"⚡  Workers:      {workers}  |  Tasks: {len(tasks)}")
    if filter_airlines and not args.alliance:
        names = [AIRLINE_DISPLAY_NAMES.get(a.name, a.value) for a in filter_airlines]
        print(f"✈   Airlines:     {', '.join(names)}")
    if exclude_airlines:
        names = [AIRLINE_DISPLAY_NAMES.get(c, c) for c in exclude_airlines]
        print(f"🚫  Excl airlines: {', '.join(names)}")
    print(f"⚠️   Always verify prices on Google Flights before booking.\n")

    results_found = []
    completed = 0
    count = 0
    lock = threading.Lock()

    # Open CSV immediately
    out_file = args.output
    csv_file = open(out_file, "w", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        "Total (EUR)", "Mode", "Cabin", "Passengers", "Bags", "Label",
        "Dep Date", "Ret Date", "Origin", "Destination", "Return Airport",
        "Airline Out", "Flight Out", "Dep Out", "Arr Out", "Price Out (EUR)", "Duration Out",
        "Airline Ret", "Flight Ret", "Dep Ret", "Arr Ret", "Price Ret (EUR)", "Duration Ret",
        "Stops Out", "Stops Ret", "Warning",
    ])
    csv_file.flush()

    def run_task(task):
        origin, dest, dep_date, ret_date, out_w, ret_w, label = task
        if mode == "oneway":
            return task_oneway(origin, dest, dep_date, out_w, label, seat_type,
                               max_eur, min_price, filter_airlines, exclude_airlines, skw)
        elif mode == "roundtrip":
            return task_roundtrip(origin, dest, dep_date, ret_date, out_w, ret_w,
                                  label, seat_type, max_eur, min_price,
                                  filter_airlines, exclude_airlines, skw)
        elif mode == "bestprice":
            return task_bestprice(origin, dest, dep_date, ret_date, out_w, ret_w,
                                  label, seat_type, max_eur, min_price, origins,
                                  filter_airlines, exclude_airlines, skw)
        else:
            return task_combined(origin, dest, dep_date, ret_date, out_w, ret_w,
                                 label, seat_type, max_eur, min_price, origins,
                                 filter_airlines, exclude_airlines, skw)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_task, task): task for task in tasks}
        for fut in as_completed(futures):
            with lock:
                completed += 1
                pct = completed * 100 // max(len(tasks), 1)

            entry = fut.result()
            if entry:
                with lock:
                    results_found.append(entry)
                    count = len(results_found)
                    _o = entry["out"]; _rt = entry["ret"]
                    pax_str = f"{passengers.adults}+{passengers.children}" if passengers.children else str(passengers.adults)
                    bags_csv = f"{bags[0]}checked,{'carryon' if bags and bags[1] else ''}" if bags else ""
                    csv_writer.writerow([
                        f"{entry['total']:.0f}", mode, cabin_label, pax_str, bags_csv, entry["label"],
                        entry["dep_date"].strftime("%Y-%m-%d"),
                        entry["ret_date"].strftime("%Y-%m-%d") if entry["ret_date"] else "",
                        entry["origin"], entry["dest"], entry.get("ret_ap") or "",
                        _o["airline_raw"], _o["flight_number"], _o["dep_time"], _o["arr_time"],
                        f"{_o['price']:.0f}" if _o["price"] is not None else "",
                        fmt_duration(_o.get("duration_min")),
                        _rt["airline_raw"] if _rt else "",
                        _rt["flight_number"] if _rt else "",
                        _rt["dep_time"] if _rt else "",
                        _rt["arr_time"] if _rt else "",
                        f"{_rt['price']:.0f}" if (_rt and _rt["price"] is not None) else "",
                        fmt_duration(_rt.get("duration_min")) if _rt else "",
                        str(_o.get("stops", "")),
                        str(_rt.get("stops", "")) if _rt else "",
                        "CHECK" if (_o["has_warning"] or (_rt["has_warning"] if _rt else False)) else "",
                    ])
                    csv_file.flush()
                warn = " ⚠️" if (_o["has_warning"] or (_rt["has_warning"] if _rt else False)) else ""
                sm = entry.get("search_mode", mode)
                tprint(f"✅ [{count}] {entry['dep_date']} {entry['origin']}→{entry['dest']} "
                       f"€{entry['total']:.0f}  [{sm}]{warn}  ({pct}%)")
            else:
                if completed % max(1, len(tasks) // 20) == 0:
                    tprint(f"   … {completed}/{len(tasks)} ({pct}%)", flush=True)

    csv_file.close()
    if _disk_cache_db:
        try:
            _disk_cache_db.close()
        except Exception:
            pass

    # ── Final output ──────────────────────────────────────────
    print(f"\n{'═' * 70}")
    budget_tag = f"under €{max_eur:.0f}" if max_eur else "found"
    print(f"🎉  {len(results_found)} FLIGHTS {budget_tag.upper()} — {cabin_label.upper()} — {mode.upper()}")
    print(f"{'═' * 70}\n")

    results_found.sort(key=lambda x: x["total"] or 0)

    if args.top:
        results_found = results_found[:args.top]
        print(f"Showing top {args.top} results.\n")

    warn_count = 0
    for r in results_found:
        print_result(r, mode, use_names=airport_names)
        if r["out"]["has_warning"] or (r["ret"]["has_warning"] if r["ret"] else False):
            warn_count += 1

    if warn_count:
        print(f"⚠️   {warn_count} result(s) with unverified airline names. "
              f"Use the flight number on Google Flights to confirm.\n")

    print(f"📄  Results saved to {out_file}")

    # JSON export
    if args.json:
        def entry_to_dict(e):
            d = {k: str(v) if isinstance(v, date) else v for k, v in e.items()}
            return d
        with open(args.json, "w", encoding="utf-8") as jf:
            json.dump([entry_to_dict(e) for e in results_found], jf, indent=2, default=str)
        print(f"📋  JSON saved to {args.json}")

if __name__ == "__main__":
    main()
