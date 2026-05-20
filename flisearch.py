"""
flisearch — Google Flights cheap fare finder
Built on top of the `fli` library (https://github.com/punitarani/fli).

Usage:
  python flisearch.py [options]

Search modes (--mode):
  combined   (default) Searches outbound and return as separate one-way tickets,
             then combines them. Best for low-cost carriers and mixing airlines.
  roundtrip  Searches a single round-trip ticket (real combined price from
             Google Flights). Better for traditional carriers where the RT fare
             is cheaper than two one-ways.
  oneway     One-way only. --nights is ignored; searches every day in the period.

Examples:
  # Default: BGY+MXP+LIN origins, Jul-Sep 2026, max 60 EUR, economy, 2-3 night weekends
  python flisearch.py

  # Single destination, no budget cap, business class, roundtrip
  python flisearch.py --origins BGY --dest BCN --mode roundtrip --no-budget --cabin business

  # BGY only, August, max 80 EUR, 5-7 nights, Friday departures
  python flisearch.py --origins BGY --from 2026-08-01 --to 2026-08-31 --max 80 --nights 5 7 --dep-days 4

  # Long-haul, economy, up to 2 weeks, no budget cap
  python flisearch.py --dest JFK --mode roundtrip --nights 7 14 --no-budget

  # One-way only, max 50 EUR
  python flisearch.py --dest BCN --mode oneway --max 50 --from 2026-07-01 --to 2026-07-31

  # Use more parallel workers for faster scanning (careful: may trigger rate limits)
  python flisearch.py --workers 8
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

# Known airline name mapping bugs in the fli library.
# The IATA code is correct; the display name is wrong (e.g. W4 maps to "LC Péru"
# instead of the actual European carrier). Flight numbers are reliable.
AIRLINE_NAME_FIXES = {
    "LC Péru":               "⚠️ W4 (verify on Google Flights)",
    "Peruvian Airlines":     "⚠️ P9 (verify on Google Flights)",
    "Viva Airlines Peru":    "⚠️ VV (verify on Google Flights)",
    "Eastern Airlines, LLC": "⚠️ 2D (verify on Google Flights)",
}

# ~80 European and Mediterranean destinations served by low-cost carriers
# from BGY / LIN / MXP. You can override this with --dest.
DEFAULT_DESTINATIONS = [
    Airport.BCN, Airport.MAD, Airport.LIS, Airport.OPO,
    Airport.CDG, Airport.ORY, Airport.AMS, Airport.BRU,
    Airport.VIE, Airport.PRG, Airport.WAW, Airport.GDN,
    Airport.KRK, Airport.BUD, Airport.BEG, Airport.SOF,
    Airport.SKG, Airport.ATH, Airport.OTP, Airport.TBS,
    Airport.EVN, Airport.FCO, Airport.NAP, Airport.PMO,
    Airport.CTA, Airport.CAG, Airport.BRI, Airport.BLQ,
    Airport.VCE, Airport.TRN, Airport.GOA, Airport.PSA,
    Airport.FLR, Airport.TSF, Airport.LJU, Airport.ZAG,
    Airport.DBV, Airport.SPU, Airport.TGD, Airport.SKP,
    Airport.LCA, Airport.ESB, Airport.IST, Airport.SAW,
    Airport.LED, Airport.RIX, Airport.TLL, Airport.VNO,
    Airport.HEL, Airport.ARN, Airport.CPH, Airport.OSL,
    Airport.DUB, Airport.EDI, Airport.STN, Airport.LTN,
    Airport.MAN, Airport.BHX, Airport.GLA,
    Airport.HAM, Airport.DUS, Airport.CGN, Airport.FRA,
    Airport.MUC, Airport.NUE, Airport.STR, Airport.BER,
    Airport.LPL, Airport.BRS, Airport.EXT,
    Airport.SVQ, Airport.VLC, Airport.AGP, Airport.PMI,
    Airport.IBZ, Airport.TFS, Airport.LPA, Airport.ACE,
    Airport.FNC, Airport.RAK, Airport.CMN,
    Airport.TUN, Airport.ALG,
    Airport.CHQ, Airport.HER, Airport.RHO,
    Airport.CFU, Airport.ZTH, Airport.KGS, Airport.MYT,
]

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


def cached_search_one_way(origin, dest, travel_date, time_window, seat_type):
    """
    One-way search with in-memory cache.
    Identical (origin, dest, date, window, cabin) calls within the same run
    are served from cache — avoids redundant HTTP requests when the same
    outbound date appears across multiple night-length combinations.
    """
    key = (origin, dest, travel_date, time_window, seat_type)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    result = _search_one_way_http(origin, dest, travel_date, time_window, seat_type)

    with _cache_lock:
        _cache[key] = result
    return result


def _search_one_way_http(origin, dest, travel_date, time_window, seat_type):
    """Raw HTTP call for a single one-way flight."""
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


def format_flight(flight):
    """Extract display fields from a FlightResult, applying known airline name fixes."""
    if not flight or not flight.legs:
        return None
    leg = flight.legs[0]
    airline_raw = leg.airline.value if hasattr(leg.airline, "value") else str(leg.airline)
    return {
        "price":         flight.price,
        "airline":       AIRLINE_NAME_FIXES.get(airline_raw, airline_raw),
        "airline_raw":   airline_raw,
        "flight_number": leg.flight_number or "",
        "dep_time":      leg.departure_datetime.strftime("%H:%M") if leg.departure_datetime else "?",
        "arr_time":      leg.arrival_datetime.strftime("%H:%M") if leg.arrival_datetime else "?",
        "stops":         flight.stops,
        "has_warning":   airline_raw in AIRLINE_NAME_FIXES,
    }


def print_result(r, mode):
    """Pretty-print a single result entry."""
    o = r["out"]
    rt = r["ret"]
    has_warn = o["has_warning"] or (rt["has_warning"] if rt else False)
    warn_tag = "  ⚠️  CHECK AIRLINE NAME" if has_warn else ""
    mode_tag = " [RT]" if mode == "roundtrip" else (" [OW]" if mode == "oneway" else "")
    price_str = f"€{r['total']:.0f}" if r["total"] is not None else "€?"
    label = "PRICE" if mode == "oneway" else "TOTAL"
    tprint(f"💶 {label} {price_str}  [{r['label']}]{mode_tag}{warn_tag}")
    tprint(f"   ✈  OUT    {r['dep_date'].strftime('%a %d/%m')}  "
           f"{r['origin']} → {r['dest']}  "
           f"{o['dep_time']} → {o['arr_time']}  {o['airline']} {o['flight_number']}  "
           f"€{o['price']:.0f}  ({o['stops']} stop{'s' if o['stops'] != 1 else ''})")
    if rt:
        tprint(f"   ↩  RET    {r['ret_date'].strftime('%a %d/%m')}  "
               f"{r['dest']} → {r['ret_ap']}  "
               f"{rt['dep_time']} → {rt['arr_time']}  {rt['airline']} {rt['flight_number']}  "
               f"€{rt['price']:.0f}  ({rt['stops']} stop{'s' if rt['stops'] != 1 else ''})")
    tprint()


# ══════════════════════════════════════════════════════════════
#  Per-task search functions (one unit of work for the thread pool)
# ══════════════════════════════════════════════════════════════

def task_oneway(origin, dest, dep_date, out_w, label, seat_type, max_eur):
    flights = cached_search_one_way(origin, dest, dep_date, out_w, seat_type)
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


def task_roundtrip(origin, dest, dep_date, ret_date, out_w, ret_w, label, seat_type, max_eur):
    pairs = _search_round_trip_http(origin, dest, dep_date, ret_date, out_w, ret_w, seat_type)
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
                  seat_type, max_eur, all_origins):
    """
    Search outbound + best return across all origin airports in parallel.
    The outbound is fetched first; if it already exceeds budget the return
    calls are skipped entirely.
    """
    out_flights = cached_search_one_way(origin, dest, dep_date, out_w, seat_type)
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
            inner.submit(cached_search_one_way, dest, ret_ap, ret_date, ret_w, seat_type): ret_ap
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

    total = best_out.price + best_ret.price
    if max_eur and total > max_eur:
        return None

    o = format_flight(best_out)
    r = format_flight(best_ret)
    if not o or not r:
        return None
    return {
        "label": label, "dep_date": dep_date, "ret_date": ret_date,
        "origin": origin.name, "dest": dest.name, "ret_ap": best_ret_ap.name,
        "out": o, "ret": r, "total": total,
    }


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
                   help="Specific destination(s). If omitted, scans ~80 European airports.")
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
                   choices=["combined", "roundtrip", "oneway"],
                   help=f"Search mode (default: {DEFAULT_MODE})")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"Parallel search threads (default: {DEFAULT_WORKERS}). "
                        "Increase for speed, decrease if you hit rate limits.")
    p.add_argument("--output", default="results.csv", metavar="FILE",
                   help="CSV output filename (default: results.csv)")
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
    mode        = args.mode
    workers     = max(1, args.workers)

    # Destinations
    if args.dest:
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
        destinations = DEFAULT_DESTINATIONS

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
    dest_str    = (", ".join(d.name for d in destinations)
                   if args.dest else f"{len(destinations)} European destinations")
    mode_labels = {
        "combined":  "Two separate one-ways (combined)",
        "roundtrip": "Native round-trip ticket",
        "oneway":    "One-way only",
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
    print(f"⚠️   Always verify prices on Google Flights before booking.\n")

    results_found = []
    completed = 0
    lock = threading.Lock()

    def run_task(task):
        origin, dest, dep_date, ret_date, out_w, ret_w, label = task
        if mode == "oneway":
            return task_oneway(origin, dest, dep_date, out_w, label, seat_type, max_eur)
        elif mode == "roundtrip":
            return task_roundtrip(origin, dest, dep_date, ret_date, out_w, ret_w,
                                  label, seat_type, max_eur)
        else:
            return task_combined(origin, dest, dep_date, ret_date, out_w, ret_w,
                                 label, seat_type, max_eur, origins)

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

    # Final summary
    print(f"\n{'═' * 70}")
    budget_tag = f"under €{max_eur:.0f}" if max_eur else "found"
    print(f"🎉  {len(results_found)} FLIGHTS {budget_tag.upper()} — "
          f"{cabin_label.upper()} — {mode.upper()}")
    print(f"{'═' * 70}\n")

    results_found.sort(key=lambda x: x["total"] or 0)
    warn_count = 0

    for r in results_found:
        print_result(r, mode)
        if r["out"]["has_warning"] or (r["ret"]["has_warning"] if r["ret"] else False):
            warn_count += 1

    if warn_count:
        print(f"⚠️   {warn_count} result(s) have incorrect airline names (known fli library bug).")
        print(f"    The flight number is reliable — search it on Google Flights to find the "
              f"real carrier.\n")

    if results_found:
        out_file = args.output
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "Total (EUR)", "Mode", "Cabin", "Label",
                "Dep Date", "Ret Date",
                "Origin", "Destination", "Return Airport",
                "Airline Out", "Flight Out", "Dep Out", "Arr Out", "Price Out (EUR)",
                "Airline Ret", "Flight Ret", "Dep Ret", "Arr Ret", "Price Ret (EUR)",
                "Warning",
            ])
            for r in results_found:
                o = r["out"]
                rt = r["ret"]
                w.writerow([
                    f"{r['total']:.0f}", mode, cabin_label, r["label"],
                    r["dep_date"].strftime("%Y-%m-%d"),
                    r["ret_date"].strftime("%Y-%m-%d") if r["ret_date"] else "",
                    r["origin"], r["dest"], r["ret_ap"] or "",
                    o["airline_raw"], o["flight_number"], o["dep_time"], o["arr_time"],
                    f"{o['price']:.0f}",
                    rt["airline_raw"] if rt else "",
                    rt["flight_number"] if rt else "",
                    rt["dep_time"] if rt else "",
                    rt["arr_time"] if rt else "",
                    f"{rt['price']:.0f}" if rt else "",
                    "CHECK" if (o["has_warning"] or (rt["has_warning"] if rt else False)) else "",
                ])
        print(f"📄  Results saved to {out_file}")


if __name__ == "__main__":
    main()
