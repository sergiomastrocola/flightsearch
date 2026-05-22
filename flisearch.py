"""
flisearch — Google Flights cheap fare finder
Built on top of the `fli` library (https://github.com/punitarani/fli).

Usage:
  python flisearch.py [options]

All options:
  Airports & destinations:
    --origins IATA [...]      Departure airport(s). Default: BGY MXP LIN
    --dest    IATA [...]      Specific destination(s). Overrides --region.
    --region  REGION [...]    World region(s) to scan. Default: europe
                              Choices: europe (eu), africa (af),
                              north_america (na), south_america (sa),
                              asia (as), australia_pacific (ap/oceania),
                              world / all
  Dates:
    --from  YYYY-MM-DD        Start of search period. Default: 2026-07-01
    --to    YYYY-MM-DD        End of search period.   Default: 2026-09-30

  Trip duration:
    --nights MIN MAX          Min/max nights (max 21). Default: 2-3 (weekend schemes).
                              Combined/roundtrip without --nights: uses --from/--to
                              as a single fixed departure/return date pair.
    --dep-days N [...]        Departure weekdays: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri
                              5=Sat 6=Sun. Default: 3 4 (Thu+Fri) for weekend mode.

  Time windows:
    --time-out HH-HH          Outbound departure window e.g. 18-23
    --time-ret HH-HH          Return departure window  e.g.  8-23

  Budget:
    --max EUR                 Maximum total budget in EUR. If omitted: no cap.
    --no-budget               Explicitly disable cap (same as omitting --max).

  Cabin & mode:
    --cabin CLASS             economy (default) | premium_economy | business | first
    --mode  MODE              combined (default) | roundtrip | oneway | bestprice
                              combined  = two separate one-ways; best for low-cost,
                                          allows different airports per leg.
                              roundtrip = native RT ticket; better for traditional
                                          carriers where A/R < 2x one-way.
                              oneway    = outbound only; --nights ignored.
                              bestprice = runs both combined AND roundtrip in parallel,
                                          returns the cheapest — same airline only
                                          (mixed-carrier itineraries discarded).

  Performance:
    --workers N               Parallel search threads. Default: 4.
                              Increase for speed; decrease if rate-limited.

  Output:
    --output FILE             CSV filename. Default: results.csv
                              Rows are written in real time as results arrive.
    --airport-names           Show full airport names (e.g. "Barcelona International
                              Airport (BCN)") instead of IATA codes. Default: off.

Examples:
  # Default: BGY+MXP+LIN → Europe, Jul-Sep 2026, no budget cap, economy, weekends
  python flisearch.py

  # Single destination, business class, roundtrip, no budget cap
  python flisearch.py --origins BGY --dest BCN --mode roundtrip --cabin business

  # BGY only, August, max €80, 5-7 nights, Friday departures
  python flisearch.py --origins BGY --from 2026-08-01 --to 2026-08-31 \
      --max 80 --nights 5 7 --dep-days 4

  # Long-haul to New York, up to 2 weeks, roundtrip pricing
  python flisearch.py --dest JFK --mode roundtrip --nights 7 14

  # One-way scan to multiple destinations, max €50, July
  python flisearch.py --dest BCN LIS MAD --mode oneway --max 50 \
      --from 2026-07-01 --to 2026-07-31

  # Scan Africa + Asia, no budget cap, 8 parallel workers
  python flisearch.py --region africa asia --workers 8

  # Worldwide scan, business class, 7-10 nights, roundtrip
  python flisearch.py --region world --cabin business --mode roundtrip --nights 7 10

  # Show full airport names in output
  python flisearch.py --dest BCN --airport-names

  # Fixed trip: depart 2026-06-01, return 2026-06-10, roundtrip
  python flisearch.py --origins MXP --dest HKG --from 2026-06-01 --to 2026-06-10 \
      --mode roundtrip --cabin economy
"""

import csv
import time
import argparse
import threading
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from fli.models import (
    Airport, PassengerInfo, SeatType, MaxStops, SortBy, TripType,
    FlightSearchFilters, FlightSegment, TimeRestrictions,
)
from fli.search import SearchFlights

# ══════════════════════════════════════════════════════════════
#  DEFAULTS — edit here or override via CLI flags
# ══════════════════════════════════════════════════════════════
DEFAULT_ORIGINS    = ["BGY", "MXP", "LIN"]
DEFAULT_NIGHTS_MIN = 2
DEFAULT_NIGHTS_MAX = 3
DEFAULT_DATE_FROM  = "2026-07-01"
DEFAULT_DATE_TO    = "2026-09-30"
DEFAULT_CABIN      = "economy"
DEFAULT_MODE       = "combined"
DEFAULT_WORKERS    = 4   # parallel HTTP threads; raise carefully to avoid rate limits

CABIN_MAP = {
    "economy":         SeatType.ECONOMY,
    "premium_economy": SeatType.PREMIUM_ECONOMY,
    "premium":         SeatType.PREMIUM_ECONOMY,
    "business":        SeatType.BUSINESS,
    "first":           SeatType.FIRST,
}
CABIN_LABEL = {
    "economy":         "Economy",
    "premium_economy": "Premium Economy",
    "premium":         "Premium Economy",
    "business":        "Business",
    "first":           "First",
}

# Default weekend trip schemes used when --nights is not specified:
# (nights_min, nights_max, departure_weekdays, outbound_time_window, return_time_window)
DEFAULT_TRIP_SCHEMES = [
    (2, 3, [3, 4], "18-23", "8-23"),   # Thu/Fri evening  → Sat/Sun/Mon
    (2, 3, [4],    "6-13",  "8-23"),   # Fri morning      → Sat/Sun
    (3, 3, [4],    "18-23", "8-23"),   # Fri evening      → Mon
]

# Airline name corrections for known fli library bugs.
# Two types of bugs:
#   1. Alias bug: Python enum collapses duplicate values, e.g. W9="Wizz Air" (alias of W6)
#      so Airline['W9'].name → 'W6', losing the W9 identity.
#   2. Wrong data: e.g. W4 is mapped to "LC Péru" but W4 is actually Wizz Air Malta.
# The flight number is always reliable — we use it to show the corrected name.
AIRLINE_NAME_FIXES: dict[str, str] = {
    # Wrong data in fli source
    "LC Péru":               "Wizz Air Malta",
    "Peruvian Airlines":     "Peruvian Airlines ⚠️",   # P9 — may be correct, flag anyway
    "Viva Airlines Peru":    "Viva Air Peru ⚠️",        # VV — verify
    "Eastern Airlines, LLC": "Eastern Airlines ⚠️",     # 2D — verify
    "Lufthansa Cargo":       "Lufthansa",               # LH in fli maps to cargo brand
    "Alitalia":              "ITA Airways",             # Alitalia ceased 2021
    # Alias bugs (wrong name due to Python enum deduplication)
    "Thomas Cook Airlines":  "Thomas Cook Airlines",    # DK/MT — same, keep as-is
    "Norse Atlantic Airways":"Norse Atlantic Airways",  # N0/Z0 — keep
}

# Corrections keyed by IATA code (overrides enum .value lookup)
# Used when format_flight has the raw airline code from the leg
AIRLINE_CODE_CORRECTIONS: dict[str, str] = {
    "W4": "Wizz Air Malta",
    "W9": "Wizz Air UK",
    "LH": "Lufthansa",
    "AZ": "ITA Airways",
    "S0": "Somon Air",
    "Z0": "Norse Atlantic UK",
    "MT": "Thomas Cook Airlines Scandinavia",
}

# Curated IATA code → display name for --airlines and --exclude-airlines resolution
# Covers the most common carriers. Unknown codes fall back to the Airline enum value.
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
    "G3": "GOL", "AD": "Azul", "JJ": "LATAM Brasil",
    "SK": "SAS", "AY": "Finnair", "LO": "LOT Polish", "OK": "Czech Airlines",
    "OU": "Croatia Airlines", "JP": "Adria Airways",
    "SA": "South African Airways", "ET": "Ethiopian Airlines",
    "AT": "Royal Air Maroc", "MS": "EgyptAir",
    "QF": "Qantas", "NZ": "Air New Zealand",
}

# Alliance filter — uses fli's native Airline enum alliance values.
# Passing Airline.STAR_ALLIANCE (etc.) to FlightSearchFilters.airlines tells
# Google Flights to filter by alliance directly — no manual expansion needed.
from fli.models import Airline as _AirlineEnum
ALLIANCE_MAP: dict[str, "_AirlineEnum"] = {
    "star":     _AirlineEnum.STAR_ALLIANCE,
    "oneworld": _AirlineEnum.ONEWORLD,
    "skyteam":  _AirlineEnum.SKYTEAM,
}

# Airports grouped by world region.
# Used when --region is specified or as default (europe) when --dest is not given.
# Each list contains IATA codes of major airports with regular scheduled service.
REGIONS: dict[str, list[str]] = {
    "europe": [
        # Italy
        "BGY","MXP","LIN","FCO","NAP","PMO","CTA","CAG","BRI","BLQ","VCE","TRN","GOA","PSA","FLR","TSF",
        # Iberia
        "BCN","MAD","LIS","OPO","SVQ","VLC","AGP","PMI","IBZ","TFS","LPA","ACE","FNC","SCQ","BIO","VLL",
        # France
        "CDG","ORY","NCE","LYS","MRS","TLS","BOD","NTE","SXB",
        # Benelux
        "AMS","BRU","LGG","EIN","RTM","ANR",
        # UK & Ireland
        "LHR","LGW","STN","LTN","MAN","BHX","EDI","GLA","DUB","ORK","SNN","BFS","LPL","BRS","EXT","ABZ",
        # DACH
        "FRA","MUC","BER","DUS","HAM","STR","CGN","NUE","VIE","ZRH","GVA","BSL","SZG","INN","GRZ",
        # Nordics & Baltics
        "CPH","ARN","OSL","HEL","BGO","GOT","TRF","TLL","RIX","VNO","KEF",
        # Eastern Europe
        "WAW","KRK","GDN","WRO","POZ","BUD","PRG","BRQ","OTP","SOF","BEG","SKP","LJU","ZAG","DBV","SPU","TGD","TIV",
        # Greece & Cyprus
        "ATH","SKG","HER","CHQ","RHO","CFU","ZTH","KGS","MYT","LCA","PFO",
        # Turkey
        "IST","SAW","AYT","ADB","ESB","BJV",
        # Caucasus & western Russia
        "TBS","EVN","GYD","LED","SVO","DME","VKO",
        # Other
        "MLA","TIA","PRN",
    ],
    "africa": [
        # North Africa
        "CAI","HRG","SSH","LXR","CMN","RAK","AGA","TNG","TUN","SFA","MIR","ALG","ORN","CZL",
        # West Africa
        "ABV","LOS","ACC","DKR","ABJ","COO","OUA","BKO","NIM","LFW",
        # East Africa
        "NBO","MBA","ADD","DAR","ZNZ","KGL","EBB","JRO","ASM","HGA",
        # Southern Africa
        "JNB","CPT","DUR","HRE","LUN","LAD","MPM","WDH","GBE",
        # Indian Ocean islands
        "MRU","RUN","SEZ",
        # Central Africa
        "DLA","NSI","LBV","BZV","FIH","FBM",
    ],
    "north_america": [
        # USA
        "JFK","EWR","LGA","BOS","PHL","IAD","DCA","ATL","MIA","FLL","MCO","TPA","CLT",
        "ORD","MDW","DTW","MSP","STL","MCI","DFW","IAH","HOU","DEN","PHX","LAS",
        "LAX","SFO","SJC","OAK","SEA","PDX","SLC","ANC","HNL","OGG","SAN","SNA","BUR",
        # Canada
        "YYZ","YUL","YVR","YYC","YEG","YOW","YHZ","YWG",
        # Mexico
        "MEX","CUN","GDL","MTY","SJD","ZIH","PVR","MZT","OAX","VER",
        # Caribbean
        "HAV","SDQ","PUJ","SJU","STT","STX","BGI","ANU","SXM","SKB","POS","TAB",
        "NAS","FPO","MBJ","KIN","GCM","CUR","AUA","BON","PTP","FDF","SFG",
        # Central America
        "GUA","SAL","TGU","MGA","SJO","PTY","BZE",
    ],
    "south_america": [
        # Brazil
        "GRU","GIG","BSB","SSA","REC","FOR","BEL","MAO","CWB","POA","FLN","CGH","VCP",
        # Argentina
        "EZE","AEP","COR","MDZ","BRC","IGR","USH",
        # Chile
        "SCL","IPC","PMC","ANF","CCP","IQQ",
        # Colombia
        "BOG","MDE","CLO","CTG","BAQ",
        # Peru
        "LIM","CUZ","AQP","IQT","TRU",
        # Ecuador & Galapagos
        "UIO","GYE","GPS",
        # Venezuela
        "CCS","MAR",
        # Bolivia
        "VVI","LPB","CBB",
        # Paraguay & Uruguay
        "ASU","MVD",
        # Guyana & Suriname
        "GEO","PBM",
    ],
    "asia": [
        # Middle East
        "DXB","AUH","DOH","KWI","BAH","AMM","BEY","TLV","RUH","JED","MCT","MED",
        # South Asia
        "DEL","BOM","MAA","BLR","CCU","HYD","GOI","CMB","DAC","KTM","MLE","KHI","LHE","ISB",
        # Southeast Asia
        "BKK","DMK","HKT","CNX","KBV","USM","SGN","HAN","DAD","KUL","LGK","PEN",
        "SIN","CGK","DPS","SUB","MNL","CEB","BCD","RGN","VTE","BND",
        # East Asia
        "HKG","MFM","PEK","PVG","CAN","SZX","CTU","CKG","XIY","WUH","TSN",
        "ICN","GMP","PUS","NRT","HND","KIX","NGO","CTS","TPE","TSA","KHH",
        # Central Asia
        "ALA","NQZ","TAS","SKD","DYU","ASB","GYD",
        # Russia Far East
        "VVO","KHV",
    ],
    "australia_pacific": [
        # Australia
        "SYD","MEL","BNE","PER","ADL","CBR","OOL","CNS","DRW","TSV","HBA","MKY","LST","ASP",
        # New Zealand
        "AKL","CHC","WLG","ZQN","DUD","NSN",
        # Pacific islands
        "NAN","SUV","APW","PPT","FAA","RAR","INU","TRW","MHQ","MAJ","POM","HON",
        "GUM","SPN","PPG","TBU","VLI","HIR","FUN",
    ],
}

REGION_ALIASES = {
    "eu": "europe",
    "af": "africa",
    "na": "north_america",
    "northamerica": "north_america",
    "sa": "south_america",
    "southamerica": "south_america",
    "as": "asia",
    "ap": "australia_pacific",
    "pacific": "australia_pacific",
    "oceania": "australia_pacific",
    "world": None,  # special: all regions
    "all": None,
}

DEFAULT_REGION = "europe"

# ══════════════════════════════════════════════════════════════
#  Thread-safe helpers
# ══════════════════════════════════════════════════════════════

# Cache for one-way searches: (origin, dest, date, time_window, seat_type) → results
_cache: dict = {}
_cache_lock = threading.Lock()

# Print lock to avoid interleaved output from multiple threads
_print_lock = threading.Lock()

def tprint(*args, **kwargs):
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs)


def cached_search_one_way(origin, dest, travel_date, time_window, seat_type,
                          airlines=None, exclude_airlines=None):
    """
    One-way search with in-memory cache.
    airlines: Airline enum list to include (passed to Google Flights API).
    exclude_airlines: set of airline IATA name strings to filter out client-side.
    Cache key includes airlines so different filters don't collide.
    """
    airlines_key = tuple(sorted(a.name for a in airlines)) if airlines else ()
    key = (origin, dest, travel_date, time_window, seat_type, airlines_key)
    with _cache_lock:
        if key in _cache:
            results = _cache[key]
        else:
            results = _search_one_way_http(origin, dest, travel_date, time_window,
                                           seat_type, airlines=airlines)
            with _cache_lock:
                _cache[key] = results

    # Client-side exclusion filter
    if exclude_airlines and results:
        results = [
            f for f in results
            if not f.legs or
            (f.legs[0].airline.name not in exclude_airlines)
        ]
    return results


def _search_one_way_http(origin, dest, travel_date, time_window, seat_type,
                         airlines=None):
    """Raw HTTP call for a single one-way flight.

    airlines: list of Airline enum members to filter by (include-only).
    Exclude-airline filtering is applied client-side after results arrive.
    """
    try:
        seg_kwargs = dict(
            departure_airport=[[origin, 0]],
            arrival_airport=[[dest, 0]],
            travel_date=travel_date.strftime("%Y-%m-%d"),
        )
        tr = parse_time_window(time_window)
        if tr:
            seg_kwargs["time_restrictions"] = tr
        filters = FlightSearchFilters(
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[FlightSegment(**seg_kwargs)],
            seat_type=seat_type,
            stops=MaxStops.ANY,
            sort_by=SortBy.CHEAPEST,
            trip_type=TripType.ONE_WAY,
            airlines=airlines if airlines else None,
        )
        return SearchFlights().search(filters) or []
    except Exception:
        return []


def _search_round_trip_http(origin, dest, dep_date, ret_date, out_window, ret_window, seat_type):
    """
    Native round-trip search.
    Strategy: one call with both legs declared. If the library returns bare
    FlightResult objects (no pairing yet), do one follow-up call for the
    cheapest return — at most 2 HTTP calls total.
    """
    try:
        filters_out = FlightSearchFilters(
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[
                make_segment(origin, dest, dep_date, out_window),
                make_segment(dest, origin, ret_date, ret_window),
            ],
            seat_type=seat_type,
            stops=MaxStops.ANY,
            sort_by=SortBy.CHEAPEST,
            trip_type=TripType.ROUND_TRIP,
            airlines=airlines if airlines else None,
        )
        outbound_results = SearchFlights().search(filters_out, top_n=1) or []

        pairs = []
        for item in outbound_results:
            if isinstance(item, tuple):
                pairs.append(item)
            else:
                filters_ret = FlightSearchFilters(
                    passenger_info=PassengerInfo(adults=1),
                    flight_segments=[
                        make_segment(origin, dest, dep_date, out_window, selected_flight=item),
                        make_segment(dest, origin, ret_date, ret_window),
                    ],
                    seat_type=seat_type,
                    stops=MaxStops.ANY,
                    sort_by=SortBy.CHEAPEST,
                    trip_type=TripType.ROUND_TRIP,
                )
                ret_results = SearchFlights().search(filters_ret, top_n=1) or []
                for ret_item in ret_results:
                    pairs.append((item, ret_item) if not isinstance(ret_item, tuple) else ret_item)
        return pairs
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
#  Model / formatting helpers
# ══════════════════════════════════════════════════════════════

def parse_time_window(s):
    """Convert 'HH-HH' string to TimeRestrictions, or None."""
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


def format_flight(flight, price_override=None):
    """Extract display fields from a FlightResult, applying known airline name fixes.

    price_override: use this price instead of flight.price (useful in combined mode
    where the per-leg price may be None and the total is computed externally).
    """
    if not flight or not flight.legs:
        return None
    leg = flight.legs[0]
    airline_raw = leg.airline.value if hasattr(leg.airline, "value") else str(leg.airline)
    price = price_override if price_override is not None else flight.price
    return {
        "price":         price,
        "airline":       AIRLINE_NAME_FIXES.get(airline_raw, airline_raw),
        "airline_raw":   airline_raw,
        "flight_number": leg.flight_number or "",
        "dep_time":      leg.departure_datetime.strftime("%H:%M") if leg.departure_datetime else "?",
        "arr_time":      leg.arrival_datetime.strftime("%H:%M") if leg.arrival_datetime else "?",
        "stops":         flight.stops,
        "has_warning":   airline_raw in AIRLINE_NAME_FIXES,
    }


def ap(code, use_names):
    """Return full airport name if --airport-names is on, otherwise the IATA code."""
    if not use_names:
        return code
    try:
        return f"{Airport[code].value} ({code})"
    except KeyError:
        return code


def print_result(r, mode, use_names=False):
    """Pretty-print a single result entry."""
    o = r["out"]
    rt = r["ret"]
    has_warn = o["has_warning"] or (rt["has_warning"] if rt else False)
    warn_tag = "  ⚠️  CHECK AIRLINE NAME" if has_warn else ""
    search_mode = r.get("search_mode", mode)
    if mode == "oneway":
        mode_tag = " [OW]"
    elif mode == "bestprice":
        tag_map = {
            "roundtrip":      " [BP/RT]",
            "combined":       " [BP/COMB]",
            "combined_mixed": " [BP/COMB-MIX]",  # mixed airlines
        }
        mode_tag = tag_map.get(search_mode, " [BP]")
    elif mode == "roundtrip":
        mode_tag = " [RT]"
    else:
        mode_tag = ""
    price_str = f"€{r['total']:.0f}" if r["total"] is not None else "€?"
    label = "PRICE" if mode == "oneway" else "TOTAL"
    origin_str = ap(r['origin'], use_names)
    dest_str   = ap(r['dest'],   use_names)
    ret_ap_str = ap(r['ret_ap'], use_names) if r['ret_ap'] else ""
    tprint(f"💶 {label} {price_str}  [{r['label']}]{mode_tag}{warn_tag}")
    tprint(f"   ✈  OUT    {r['dep_date'].strftime('%a %d/%m')}  "
           f"{origin_str} → {dest_str}  "
           f"{o['dep_time']} → {o['arr_time']}  {o['airline']} {o['flight_number']}  "
           f"€{o['price']:.0f}  ({o['stops']} stop{'s' if o['stops'] != 1 else ''})")
    if rt:
        tprint(f"   ↩  RET    {r['ret_date'].strftime('%a %d/%m')}  "
               f"{dest_str} → {ret_ap_str}  "
               f"{rt['dep_time']} → {rt['arr_time']}  {rt['airline']} {rt['flight_number']}  "
               f"€{rt['price']:.0f}  ({rt['stops']} stop{'s' if rt['stops'] != 1 else ''})")
    tprint()


# ══════════════════════════════════════════════════════════════
#  Per-task search functions (one unit of work for the thread pool)
# ══════════════════════════════════════════════════════════════

def task_oneway(origin, dest, dep_date, out_w, label, seat_type, max_eur,
                airlines=None, exclude_airlines=None):
    flights = cached_search_one_way(origin, dest, dep_date, out_w, seat_type,
                                    airlines=airlines, exclude_airlines=exclude_airlines)
    if not flights:
        return None
    best = flights[0]
    if best.price is None or (max_eur and best.price > max_eur):
        return None
    o = format_flight(best)
    if not o:
        return None
    return {
        "label": label, "dep_date": dep_date, "ret_date": None,
        "origin": origin.name, "dest": dest.name, "ret_ap": None,
        "out": o, "ret": None, "total": best.price,
    }


def task_roundtrip(origin, dest, dep_date, ret_date, out_w, ret_w, label, seat_type, max_eur,
                   airlines=None, exclude_airlines=None):
    pairs = _search_round_trip_http(origin, dest, dep_date, ret_date, out_w, ret_w, seat_type)
    # Apply client-side exclude filter to roundtrip pairs
    if exclude_airlines and pairs:
        pairs = [
            (o, r) for o, r in pairs
            if (not o.legs or o.legs[0].airline.name not in exclude_airlines)
            and (not r.legs or r.legs[0].airline.name not in exclude_airlines)
        ]
    for out_flight, ret_flight in pairs[:1]:
        o = format_flight(out_flight)
        r = format_flight(ret_flight)
        if not o or not r:
            continue
        total = o["price"] or 0
        if max_eur and total > max_eur:
            return None
        return {
            "label": label, "dep_date": dep_date, "ret_date": ret_date,
            "origin": origin.name, "dest": dest.name, "ret_ap": origin.name,
            "out": o, "ret": r, "total": total,
        }
    return None


def task_combined(origin, dest, dep_date, ret_date, out_w, ret_w, label,
                  seat_type, max_eur, all_origins,
                  airlines=None, exclude_airlines=None):
    """
    Search outbound + best return across all origin airports in parallel.
    The outbound is fetched first; if it already exceeds budget the return
    calls are skipped entirely.
    """
    out_flights = cached_search_one_way(origin, dest, dep_date, out_w, seat_type,
                                          airlines=airlines, exclude_airlines=exclude_airlines)
    if not out_flights:
        return None
    best_out = out_flights[0]
    if best_out.price is None or (max_eur and best_out.price >= max_eur):
        return None

    # Fetch returns for all origin airports in parallel (small inner pool)
    best_ret = None
    best_ret_ap = origin
    ret_results = {}

    with ThreadPoolExecutor(max_workers=len(all_origins)) as inner:
        futs = {
            inner.submit(cached_search_one_way, dest, ret_ap, ret_date, ret_w, seat_type,
                         airlines, exclude_airlines): ret_ap
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

    # Use explicit prices: some carriers return None on flight.price for individual legs
    price_out = best_out.price or 0
    price_ret = best_ret.price or 0
    total = price_out + price_ret
    if max_eur and total > max_eur:
        return None

    o = format_flight(best_out, price_override=price_out)
    r = format_flight(best_ret, price_override=price_ret)
    if not o or not r:
        return None
    return {
        "label": label, "dep_date": dep_date, "ret_date": ret_date,
        "origin": origin.name, "dest": dest.name, "ret_ap": best_ret_ap.name,
        "out": o, "ret": r, "total": total,
    }


def task_bestprice(origin, dest, dep_date, ret_date, out_w, ret_w, label,
                   seat_type, max_eur, all_origins,
                   airlines=None, exclude_airlines=None):
    """
    Run both combined and roundtrip searches in parallel, then return the
    cheapest result.

    Priority logic:
    1. Both results available → pick the cheapest.
    2. Same-airline combined cheaper than roundtrip → prefer it (tagged [BP/COMB]).
    3. Mixed-airline combined is kept if it's cheaper and no same-airline option exists.
    4. Only one result available → return it.
    5. No results → None.

    The "search_mode" key in the entry records which search won (roundtrip/combined)
    and whether the combined result uses mixed airlines ("combined_mixed").
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_rt   = pool.submit(task_roundtrip, origin, dest, dep_date, ret_date,
                               out_w, ret_w, label, seat_type, max_eur,
                               airlines, exclude_airlines)
        fut_comb = pool.submit(task_combined, origin, dest, dep_date, ret_date,
                               out_w, ret_w, label, seat_type, max_eur, all_origins,
                               airlines, exclude_airlines)
        rt_entry   = fut_rt.result()
        comb_entry = fut_comb.result()

    if comb_entry:
        out_code = comb_entry["out"].get("airline_raw", "")
        ret_code = comb_entry["ret"].get("airline_raw", "") if comb_entry["ret"] else ""
        comb_entry["search_mode"] = "combined" if out_code == ret_code else "combined_mixed"

    if rt_entry:
        rt_entry["search_mode"] = "roundtrip"

    candidates = [e for e in [rt_entry, comb_entry] if e is not None]
    if not candidates:
        return None

    best = min(candidates, key=lambda e: e["total"] or float("inf"))
    return best


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


def parse_args():
    p = argparse.ArgumentParser(
        description="flisearch — Find cheap flights via Google Flights",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--origins", nargs="+", default=DEFAULT_ORIGINS, metavar="IATA",
                   help=f"Departure airport(s) (default: {' '.join(DEFAULT_ORIGINS)})")
    p.add_argument("--dest", nargs="+", default=None, metavar="IATA",
                   help="Specific destination(s) as IATA codes e.g. --dest BCN LIS. "
                        "Takes precedence over --region.")
    p.add_argument("--region", nargs="+", default=None,
                   metavar="REGION",
                   help="World region(s) to scan. Choices: "
                        "europe (default), africa, north_america, south_america, asia, australia_pacific, world. "
                        "Shortcuts: eu, af, na, sa, as, ap, oceania, all. "
                        "Multiple regions allowed e.g. --region europe africa. "
                        "Ignored when --dest is used.")
    p.add_argument("--from", dest="date_from", default=DEFAULT_DATE_FROM, metavar="YYYY-MM-DD",
                   help=f"Start of search period (default: {DEFAULT_DATE_FROM})")
    p.add_argument("--to", dest="date_to", default=DEFAULT_DATE_TO, metavar="YYYY-MM-DD",
                   help=f"End of search period (default: {DEFAULT_DATE_TO})")
    p.add_argument("--nights", nargs=2, type=int, default=None, metavar=("MIN", "MAX"),
                   help="Min/max nights away (max 21). Default for combined/roundtrip without "
                        "--nights: use --from as departure and --to as return (single pair).")
    p.add_argument("--dep-days", nargs="+", type=int, default=None, metavar="N",
                   help="Departure weekdays: 0=Mon … 6=Sun.")
    p.add_argument("--time-out", default=None, metavar="HH-HH",
                   help="Outbound departure time window e.g. 18-23.")
    p.add_argument("--time-ret", default=None, metavar="HH-HH",
                   help="Return departure time window e.g. 8-23.")
    budget_group = p.add_mutually_exclusive_group()
    budget_group.add_argument("--max", type=float, default=None,
                               help="Maximum total budget EUR (e.g. --max 200). "
                                    "If neither --max nor --no-budget is given, no budget cap is applied.")
    budget_group.add_argument("--no-budget", action="store_true",
                               help="Explicitly disable budget cap (default behaviour when --max is not set).")
    p.add_argument("--cabin", default=DEFAULT_CABIN, choices=list(CABIN_MAP.keys()),
                   help=f"Cabin class (default: {DEFAULT_CABIN})")
    p.add_argument("--mode", default=DEFAULT_MODE,
                   choices=["combined", "roundtrip", "oneway", "bestprice"],
                   help=f"Search mode (default: {DEFAULT_MODE}). "
                        "bestprice = runs both combined and roundtrip, returns the "
                        "cheapest — same airline only for outbound and return.")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"Parallel search threads (default: {DEFAULT_WORKERS}). "
                        "Increase for speed, decrease if you hit rate limits.")
    p.add_argument("--output", default="results.csv", metavar="FILE",
                   help="CSV output filename (default: results.csv)")
    p.add_argument("--airport-names", action="store_true", default=False,
                   help="Show full airport names instead of IATA codes in results "
                        "(e.g. 'Barcelona International Airport' instead of 'BCN'). "
                        "Default: off (IATA codes only).")

    # Airline / alliance filters
    p.add_argument("--airlines", nargs="+", default=None, metavar="IATA",
                   help="Only show results with these airlines (IATA codes) e.g. --airlines FR U2 VY. "
                        "Mutually exclusive with --exclude-airlines.")
    p.add_argument("--exclude-airlines", nargs="+", default=None, metavar="IATA",
                   help="Exclude results with these airlines (IATA codes) e.g. --exclude-airlines FR. "
                        "Mutually exclusive with --airlines.")
    p.add_argument("--alliance", default=None,
                   choices=["star", "oneworld", "skyteam"],
                   help="Filter to a specific alliance: star (Star Alliance: LH UA SQ TK…), "
                        "oneworld (BA AA QR JL…), skyteam (AF KL DL…). "
                        "Expands to individual airline codes. Mutually exclusive with --exclude-airlines.")
    return p.parse_args()


# ══════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════

def main():
    args = parse_args()

    # Origins
    origins = []
    for code in [c.upper() for c in args.origins]:
        try:
            origins.append(Airport[code])
        except KeyError:
            print(f"⚠️  Unknown origin airport '{code}' — skipped.")
    if not origins:
        print("❌ No valid origin airports. Exiting.")
        return

    # Dates
    try:
        date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date()
        date_to   = datetime.strptime(args.date_to,   "%Y-%m-%d").date()
    except ValueError as e:
        print(f"❌ Invalid date format: {e}")
        return
    if date_from > date_to:
        print("❌ --from date must be before --to date.")
        return

    # If --max is not provided, no budget cap is applied (same as --no-budget)
    max_eur = args.max  # None if not set
    seat_type   = CABIN_MAP[args.cabin]
    cabin_label = CABIN_LABEL[args.cabin]
    mode         = args.mode
    workers      = max(1, args.workers)
    airport_names = args.airport_names

    # ── Airline / alliance filter resolution ──────────────────────────────
    from fli.models import Airline as AirlineEnum
    _valid_airline_codes = {a.name for a in AirlineEnum}  # includes STAR_ALLIANCE etc.

    def resolve_airline_codes(codes):
        """Convert IATA code strings to Airline enum members, warn on unknowns."""
        result = []
        for c in [x.upper() for x in (codes or [])]:
            if c in _valid_airline_codes:
                result.append(AirlineEnum[c])
            else:
                print(f"⚠️  Unknown airline code '{c}' — skipped.")
        return result or None

    # Validate mutual exclusivity
    if args.airlines and args.exclude_airlines:
        print("❌ --airlines and --exclude-airlines cannot be used together.")
        return
    if args.alliance and args.exclude_airlines:
        print("❌ --alliance and --exclude-airlines cannot be used together.")
        return
    if args.alliance and args.airlines:
        print("❌ --alliance and --airlines cannot be used together.")
        return

    # Resolve include list
    filter_airlines = None
    if args.alliance:
        # Use fli's native alliance enum value — passed directly to Google Flights API
        alliance_enum = ALLIANCE_MAP.get(args.alliance)
        if alliance_enum:
            filter_airlines = [alliance_enum]
            alliance_labels = {
                "star":     "Star Alliance (LH, UA, SQ, TK, AC, NH…)",
                "oneworld": "oneworld (BA, AA, QR, JL, QF, IB…)",
                "skyteam":  "SkyTeam (AF, KL, DL, KE, MU, CZ…)",
            }
            print(f"🤝  Alliance: {alliance_labels.get(args.alliance, args.alliance)}")
    elif args.airlines:
        filter_airlines = resolve_airline_codes(args.airlines)
        if filter_airlines:
            names = [AIRLINE_DISPLAY_NAMES.get(c.upper(), c.upper()) for c in args.airlines]
            print(f"✈   Airline filter: {', '.join(names)}")

    # Resolve exclude list (client-side only — Google Flights has no native exclude)
    exclude_airlines = None
    if args.exclude_airlines:
        exclude_airlines_enum = resolve_airline_codes(args.exclude_airlines)
        exclude_airlines = {a.name for a in (exclude_airlines_enum or [])}
        if exclude_airlines:
            names = [AIRLINE_DISPLAY_NAMES.get(c.upper(), c.upper()) for c in args.exclude_airlines]
            print(f"🚫  Excluding airlines: {', '.join(names)}")

    # Destinations
    if args.dest:
        # Explicit IATA codes take precedence over --region
        destinations = []
        for code in [c.upper() for c in args.dest]:
            try:
                destinations.append(Airport[code])
            except KeyError:
                print(f"⚠️  Unknown destination '{code}' — skipped.")
        if not destinations:
            print("❌ No valid destination airports. Exiting.")
            return
    else:
        # Build destination list from --region (default: europe)
        selected_regions = args.region if args.region else [DEFAULT_REGION]
        codes_set: list[str] = []
        seen_codes: set[str] = set()
        region_names_used: list[str] = []
        for r in selected_regions:
            r_key = REGION_ALIASES.get(r.lower(), r.lower())
            if r_key is None:
                # "world" / "all" — include every region
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
                valid_keys = list(REGIONS.keys()) + list(REGION_ALIASES.keys())
                print(f"⚠️  Unknown region '{r}'. Valid: {', '.join(sorted(set(valid_keys)))}")
        if not codes_set:
            print("❌ No valid destinations after region resolution. Exiting.")
            return
        destinations = [Airport[c] for c in codes_set if c != "" ]

    # Date pairs
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
        all_pairs = build_date_pairs(date_from, date_to,
                                     DEFAULT_NIGHTS_MIN, DEFAULT_NIGHTS_MAX,
                                     args.dep_days, args.time_out, args.time_ret)

    elif mode in ("roundtrip", "combined") and not args.nights and not args.dep_days:
        # No --nights: treat --from as departure, --to as return (single pair)
        nights = (date_to - date_from).days
        label = f"{nights}n {date_from.strftime('%a')}→{date_to.strftime('%a')}"
        all_pairs = [(date_from, date_to, args.time_out, args.time_ret, label)]

    else:
        # Default weekend schemes
        all_pairs = []
        seen = set()
        for (nmin, nmax, dep_days, oo, ort) in DEFAULT_TRIP_SCHEMES:
            for pair in build_date_pairs(date_from, date_to, nmin, nmax, dep_days, oo, ort):
                key = (pair[0], pair[1])
                if key not in seen:
                    seen.add(key)
                    all_pairs.append(pair)

    # Build task list
    tasks = [
        (origin, dest, dep_date, ret_date, out_w, ret_w, label)
        for origin in origins
        for dest in destinations if dest != origin
        for dep_date, ret_date, out_w, ret_w, label in all_pairs
    ]

    total_tasks = len(tasks)
    budget_str  = f"max €{max_eur:.0f}" if max_eur else "no limit"
    if args.dest:
        dest_str = ", ".join(d.name for d in destinations)
    elif args.region and any(r.lower() in ("world","all") for r in args.region):
        dest_str = f"{len(destinations)} destinations worldwide"
    elif args.region:
        dest_str = f"{len(destinations)} destinations in: {', '.join(region_names_used)}"
    else:
        dest_str = f"{len(destinations)} destinations in: {DEFAULT_REGION}"
    mode_labels = {
        "combined":  "Two separate one-ways (combined)",
        "roundtrip": "Native round-trip ticket",
        "oneway":    "One-way only",
        "bestprice": "Best price (combined + roundtrip, same airline, cheapest wins)",
    }

    print(f"\n🔍  flisearch — {'one-way' if mode == 'oneway' else 'round-trip'} search")
    print(f"✈   Origins:      {', '.join(o.name for o in origins)}")
    print(f"🌍  Destinations: {dest_str}")
    print(f"📅  Period:       {date_from} → {date_to}")
    print(f"🌙  {'Dates' if mode == 'oneway' else 'Date pairs'}:  {len(all_pairs)}")
    print(f"💺  Cabin:        {cabin_label}")
    print(f"💶  Budget:       {budget_str}")
    print(f"🔄  Mode:         {mode_labels[mode]}")
    print(f"⚡  Workers:      {workers} parallel threads")
    print(f"🔢  Total tasks:  {total_tasks}")
    if filter_airlines and not args.alliance:
        al_names = [AIRLINE_DISPLAY_NAMES.get(a.name, a.value) for a in filter_airlines]
        print(f"✈   Airlines:     {', '.join(al_names)}")
    if exclude_airlines:
        ex_names = [AIRLINE_DISPLAY_NAMES.get(c, c) for c in exclude_airlines]
        print(f"🚫  Excluding:     {', '.join(ex_names)}")
    print(f"⚠️   Always verify prices on Google Flights before booking.\n")

    results_found = []
    completed = 0
    lock = threading.Lock()

    # Open CSV immediately so rows are written as results arrive
    out_file = args.output
    csv_file = open(out_file, "w", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        "Total (EUR)", "Mode", "Cabin", "Label",
        "Dep Date", "Ret Date",
        "Origin", "Destination", "Return Airport",
        "Airline Out", "Flight Out", "Dep Out", "Arr Out", "Price Out (EUR)",
        "Airline Ret", "Flight Ret", "Dep Ret", "Arr Ret", "Price Ret (EUR)",
        "Warning",
    ])
    csv_file.flush()

    def run_task(task):
        origin, dest, dep_date, ret_date, out_w, ret_w, label = task
        if mode == "oneway":
            return task_oneway(origin, dest, dep_date, out_w, label, seat_type, max_eur,
                               airlines=filter_airlines, exclude_airlines=exclude_airlines)
        elif mode == "roundtrip":
            return task_roundtrip(origin, dest, dep_date, ret_date, out_w, ret_w,
                                  label, seat_type, max_eur,
                                  airlines=filter_airlines, exclude_airlines=exclude_airlines)
        elif mode == "bestprice":
            return task_bestprice(origin, dest, dep_date, ret_date, out_w, ret_w,
                                  label, seat_type, max_eur, origins,
                                  airlines=filter_airlines, exclude_airlines=exclude_airlines)
        else:
            return task_combined(origin, dest, dep_date, ret_date, out_w, ret_w,
                                 label, seat_type, max_eur, origins,
                                 airlines=filter_airlines, exclude_airlines=exclude_airlines)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_task, task): task for task in tasks}
        for fut in as_completed(futures):
            with lock:
                completed += 1
                pct = completed * 100 // total_tasks

            entry = fut.result()
            if entry:
                with lock:
                    results_found.append(entry)
                    count = len(results_found)
                    # Write CSV row immediately
                    _o = entry["out"]; _rt = entry["ret"]
                    csv_writer.writerow([
                        f"{entry['total']:.0f}", mode, cabin_label, entry["label"],
                        entry["dep_date"].strftime("%Y-%m-%d"),
                        entry["ret_date"].strftime("%Y-%m-%d") if entry["ret_date"] else "",
                        entry["origin"], entry["dest"], entry["ret_ap"] or "",
                        _o["airline_raw"], _o["flight_number"], _o["dep_time"], _o["arr_time"],
                        f"{_o['price']:.0f}" if _o["price"] is not None else "",
                        _rt["airline_raw"] if _rt else "",
                        _rt["flight_number"] if _rt else "",
                        _rt["dep_time"] if _rt else "",
                        _rt["arr_time"] if _rt else "",
                        f"{_rt['price']:.0f}" if (_rt and _rt["price"] is not None) else "",
                        "CHECK" if (_o["has_warning"] or (_rt["has_warning"] if _rt else False)) else "",
                    ])
                    csv_file.flush()
                o = entry["out"]
                r = entry["ret"]
                warn = " ⚠️" if (o["has_warning"] or (r["has_warning"] if r else False)) else ""
                if mode == "roundtrip":
                    tprint(f"✅ [{count}] [RT] {entry['dep_date']} "
                           f"{entry['origin']} ⇄ {entry['dest']} "
                           f"€{entry['total']:.0f}{warn}  [{pct}%]")
                elif mode == "oneway":
                    tprint(f"✅ [{count}] {entry['dep_date']} "
                           f"{entry['origin']} → {entry['dest']} "
                           f"€{entry['total']:.0f}{warn}  [{pct}%]")
                else:
                    tprint(f"✅ [{count}] {entry['dep_date']} "
                           f"{entry['origin']} → {entry['dest']} "
                           f"€{o['price']:.0f} + {entry['ret_date']} "
                           f"{entry['dest']} → {entry['ret_ap']} "
                           f"€{r['price']:.0f} = €{entry['total']:.0f}{warn}  [{pct}%]")
            else:
                # Progress ticker every 5%
                if completed % max(1, total_tasks // 20) == 0:
                    tprint(f"   … {completed}/{total_tasks} tasks ({pct}%)", flush=True)

    csv_file.close()

    # Final summary
    print(f"\n{'═' * 70}")
    budget_tag = f"under €{max_eur:.0f}" if max_eur else "found"
    print(f"🎉  {len(results_found)} FLIGHTS {budget_tag.upper()} — "
          f"{cabin_label.upper()} — {mode.upper()}")
    print(f"{'═' * 70}\n")

    results_found.sort(key=lambda x: x["total"] or 0)
    warn_count = 0

    for r in results_found:
        print_result(r, mode, use_names=airport_names)
        if r["out"]["has_warning"] or (r["ret"]["has_warning"] if r["ret"] else False):
            warn_count += 1

    if warn_count:
        print(f"⚠️   {warn_count} result(s) have incorrect airline names (known fli library bug).")
        print(f"    The flight number is reliable — search it on Google Flights to find the "
              f"real carrier.\n")

    print(f"📄  Results saved to {out_file}")


if __name__ == "__main__":
    main()
