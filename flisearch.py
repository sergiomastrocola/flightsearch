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
"""

import csv
import time
import argparse
from datetime import date, timedelta, datetime
from fli.models import (
    Airport, PassengerInfo, SeatType, MaxStops, SortBy, TripType,
    FlightSearchFilters, FlightSegment, TimeRestrictions,
)
from fli.search import SearchFlights

# ══════════════════════════════════════════════════════════════
#  DEFAULTS — edit here or override via CLI flags
# ══════════════════════════════════════════════════════════════
DEFAULT_ORIGINS   = ["BGY", "MXP", "LIN"]
DEFAULT_DATE_FROM = "2026-07-01"
DEFAULT_DATE_TO   = "2026-09-30"
DEFAULT_MAX_EUR   = 60
DEFAULT_NIGHTS_MIN = 2
DEFAULT_NIGHTS_MAX = 3
DEFAULT_CABIN     = "economy"
DEFAULT_MODE      = "combined"

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


def parse_args():
    p = argparse.ArgumentParser(
        description="flisearch — Find cheap flights via Google Flights",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Origins / destinations
    p.add_argument(
        "--origins", nargs="+", default=DEFAULT_ORIGINS, metavar="IATA",
        help=f"Departure airport(s) as IATA codes (default: {' '.join(DEFAULT_ORIGINS)})",
    )
    p.add_argument(
        "--dest", nargs="+", default=None, metavar="IATA",
        help="Specific destination(s) e.g. --dest BCN LIS. "
             "If omitted, searches ~80 European destinations.",
    )

    # Date range
    p.add_argument(
        "--from", dest="date_from", default=DEFAULT_DATE_FROM, metavar="YYYY-MM-DD",
        help=f"Start of search period (default: {DEFAULT_DATE_FROM})",
    )
    p.add_argument(
        "--to", dest="date_to", default=DEFAULT_DATE_TO, metavar="YYYY-MM-DD",
        help=f"End of search period (default: {DEFAULT_DATE_TO})",
    )

    # Trip duration
    p.add_argument(
        "--nights", nargs=2, type=int, default=None, metavar=("MIN", "MAX"),
        help="Min and max nights away e.g. --nights 5 14. Max supported: 21. "
             "Default: 2-3 (weekend schemes).",
    )
    p.add_argument(
        "--dep-days", nargs="+", type=int, default=None, metavar="N",
        help="Departure weekdays: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun. "
             "Default depends on mode (3 4 for combined/roundtrip, all days for oneway).",
    )

    # Time windows
    p.add_argument(
        "--time-out", default=None, metavar="HH-HH",
        help="Outbound departure time window e.g. 18-23. Default: no filter.",
    )
    p.add_argument(
        "--time-ret", default=None, metavar="HH-HH",
        help="Return departure time window e.g. 8-23. Default: no filter.",
    )

    # Budget
    budget_group = p.add_mutually_exclusive_group()
    budget_group.add_argument(
        "--max", type=float, default=DEFAULT_MAX_EUR,
        help=f"Maximum total budget in EUR (default: {DEFAULT_MAX_EUR})",
    )
    budget_group.add_argument(
        "--no-budget", action="store_true",
        help="No budget cap — return all results regardless of price.",
    )

    # Cabin class
    p.add_argument(
        "--cabin", default=DEFAULT_CABIN, choices=list(CABIN_MAP.keys()),
        help=f"Cabin class (default: {DEFAULT_CABIN}). "
             "Choices: economy | premium_economy | business | first",
    )

    # Search mode
    p.add_argument(
        "--mode", default=DEFAULT_MODE,
        choices=["combined", "roundtrip", "oneway"],
        help="'combined' = two separate one-way tickets (good for low-cost / mixed airlines). "
             "'roundtrip' = single RT ticket (better for traditional carriers). "
             "'oneway' = outbound only, no return (--nights ignored). "
             f"Default: {DEFAULT_MODE}",
    )

    # Output
    p.add_argument(
        "--output", default="results.csv", metavar="FILE",
        help="CSV output filename (default: results.csv)",
    )

    return p.parse_args()


def parse_time_window(s):
    """Convert 'HH-HH' string to TimeRestrictions object, or None if not provided."""
    if not s:
        return None
    parts = s.split("-")
    if len(parts) != 2:
        return None
    return TimeRestrictions(earliest_departure=int(parts[0]), latest_departure=int(parts[1]))


def build_date_pairs(date_from, date_to, nights_min, nights_max,
                     dep_days, time_out, time_ret):
    """
    Generate all (dep_date, ret_date, time_out, time_ret, label) tuples
    within the given date range.
    """
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


def make_segment(origin, dest, travel_date, time_window_str, selected_flight=None):
    """Build a FlightSegment with optional time restriction and pre-selected flight."""
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


def search_one_way(origin, dest, travel_date, time_window, seat_type):
    """Search a single one-way flight. Used by both 'oneway' and 'combined' modes."""
    try:
        filters = FlightSearchFilters(
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[make_segment(origin, dest, travel_date, time_window)],
            seat_type=seat_type,
            stops=MaxStops.ANY,
            sort_by=SortBy.CHEAPEST,
            trip_type=TripType.ONE_WAY,
        )
        return SearchFlights().search(filters) or []
    except Exception:
        return []


def search_round_trip(origin, dest, dep_date, ret_date, out_window, ret_window, seat_type):
    """
    Search a native round-trip ticket (TripType.ROUND_TRIP).
    Returns a list of (outbound_flight, return_flight) tuples with real combined pricing
    as shown on Google Flights.
    """
    try:
        # Step 1: fetch outbound options
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
        outbound_results = SearchFlights().search(filters_out, top_n=5) or []

        # The fli library may return already-paired tuples or individual FlightResult objects
        pairs = []
        for item in outbound_results:
            if isinstance(item, tuple):
                # Already a (out, ret) pair — add directly
                pairs.append(item)
            else:
                # Outbound flight only — fetch matching return legs
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
                ret_results = SearchFlights().search(filters_ret, top_n=3) or []
                for ret_item in ret_results:
                    if isinstance(ret_item, tuple):
                        pairs.append(ret_item)
                    else:
                        pairs.append((item, ret_item))
                time.sleep(0.5)
        return pairs
    except Exception:
        return []


def format_flight(flight):
    """Extract display fields from a FlightResult, applying known airline name fixes."""
    if not flight or not flight.legs:
        return None
    leg = flight.legs[0]
    airline_raw = leg.airline.value if hasattr(leg.airline, "value") else str(leg.airline)
    return {
        "price":          flight.price,
        "airline":        AIRLINE_NAME_FIXES.get(airline_raw, airline_raw),
        "airline_raw":    airline_raw,
        "flight_number":  leg.flight_number or "",
        "dep_time":       leg.departure_datetime.strftime("%H:%M") if leg.departure_datetime else "?",
        "arr_time":       leg.arrival_datetime.strftime("%H:%M") if leg.arrival_datetime else "?",
        "stops":          flight.stops,
        "has_warning":    airline_raw in AIRLINE_NAME_FIXES,
    }


def print_result(r, mode):
    """Pretty-print a single result entry to stdout."""
    o = r["out"]
    rt = r["ret"]
    has_warn = o["has_warning"] or (rt["has_warning"] if rt else False)
    warn_tag = "  ⚠️  CHECK AIRLINE NAME" if has_warn else ""
    mode_tag = " [RT]" if mode == "roundtrip" else (" [OW]" if mode == "oneway" else "")
    price_str = f"€{r['total']:.0f}" if r["total"] is not None else "€?"

    label = "PRICE" if mode == "oneway" else "TOTAL"
    print(f"💶 {label} {price_str}  [{r['label']}]{mode_tag}{warn_tag}")
    print(f"   ✈  OUT    {r['dep_date'].strftime('%a %d/%m')}  "
          f"{r['origin']} → {r['dest']}  "
          f"{o['dep_time']} → {o['arr_time']}  {o['airline']} {o['flight_number']}  "
          f"€{o['price']:.0f}  ({o['stops']} stop{'s' if o['stops'] != 1 else ''})")
    if rt:
        print(f"   ↩  RET    {r['ret_date'].strftime('%a %d/%m')}  "
              f"{r['dest']} → {r['ret_ap']}  "
              f"{rt['dep_time']} → {rt['arr_time']}  {rt['airline']} {rt['flight_number']}  "
              f"€{rt['price']:.0f}  ({rt['stops']} stop{'s' if rt['stops'] != 1 else ''})")
    print()


def main():
    args = parse_args()

    # ── Resolve origin airports ────────────────────────────────
    origins = []
    for code in [c.upper() for c in args.origins]:
        try:
            origins.append(Airport[code])
        except KeyError:
            print(f"⚠️  Unknown origin airport '{code}' — skipped.")
    if not origins:
        print("❌ No valid origin airports. Exiting.")
        return

    # ── Parse date range ───────────────────────────────────────
    try:
        date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date()
        date_to   = datetime.strptime(args.date_to,   "%Y-%m-%d").date()
    except ValueError as e:
        print(f"❌ Invalid date format: {e}")
        return
    if date_from > date_to:
        print("❌ --from date must be before --to date.")
        return

    # ── Budget ─────────────────────────────────────────────────
    max_eur = None if args.no_budget else args.max

    # ── Cabin / mode ───────────────────────────────────────────
    seat_type   = CABIN_MAP[args.cabin]
    cabin_label = CABIN_LABEL[args.cabin]
    mode        = args.mode

    # ── Destinations ───────────────────────────────────────────
    if args.dest:
        destinations = []
        for code in [c.upper() for c in args.dest]:
            try:
                destinations.append(Airport[code])
            except KeyError:
                print(f"⚠️  Unknown destination airport '{code}' — skipped.")
        if not destinations:
            print("❌ No valid destination airports. Exiting.")
            return
    else:
        destinations = DEFAULT_DESTINATIONS

    # ── Build date pairs ───────────────────────────────────────
    if mode == "oneway":
        # One-way: one entry per calendar day in range (filtered by dep-days)
        DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        dep_days = args.dep_days if args.dep_days else list(range(7))
        all_pairs = []
        d = date_from
        while d <= date_to:
            if d.weekday() in dep_days:
                all_pairs.append((d, d, args.time_out, None, DAYS[d.weekday()]))
            d += timedelta(days=1)

    elif args.nights:
        # Custom night range
        nights_min, nights_max = args.nights[0], min(args.nights[1], 21)
        dep_days = args.dep_days if args.dep_days else list(range(7))
        all_pairs = build_date_pairs(date_from, date_to, nights_min, nights_max,
                                     dep_days, args.time_out, args.time_ret)

    elif args.dep_days:
        # Custom departure days, default night range
        all_pairs = build_date_pairs(date_from, date_to,
                                     DEFAULT_NIGHTS_MIN, DEFAULT_NIGHTS_MAX,
                                     args.dep_days, args.time_out, args.time_ret)

    elif mode in ("roundtrip", "combined") and not args.nights and not args.dep_days:
        # No --nights specified with roundtrip/combined: treat --from as departure
        # date and --to as return date — single fixed pair.
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

    # ── Summary header ─────────────────────────────────────────
    budget_str  = f"max €{max_eur:.0f}" if max_eur else "no limit"
    dest_str    = (", ".join(d.name for d in destinations)
                   if args.dest else f"{len(destinations)} European destinations")
    mode_labels = {
        "combined":  "Two separate one-ways (combined)",
        "roundtrip": "Native round-trip ticket",
        "oneway":    "One-way only",
    }
    trip_label  = "one-way" if mode == "oneway" else "round-trip"

    print(f"\n🔍  flisearch — {trip_label} search")
    print(f"✈   Origins:      {', '.join(o.name for o in origins)}")
    print(f"🌍  Destinations: {dest_str}")
    print(f"📅  Period:       {date_from} → {date_to}")
    print(f"🌙  {'Dates' if mode == 'oneway' else 'Date pairs'}:  {len(all_pairs)}")
    print(f"💺  Cabin:        {cabin_label}")
    print(f"💶  Budget:       {budget_str}")
    print(f"🔄  Mode:         {mode_labels[mode]}")
    print(f"⚠️   Always verify prices on Google Flights before booking.\n")

    results_found = []
    count = 0

    # ── Main search loop ───────────────────────────────────────
    for origin in origins:
        for dest in destinations:
            if dest == origin:
                continue

            for dep_date, ret_date, out_w, ret_w, label in all_pairs:

                # ── ONE-WAY ───────────────────────────────────
                if mode == "oneway":
                    flights = search_one_way(origin, dest, dep_date, out_w, seat_type)
                    if not flights:
                        time.sleep(0.3)
                        continue
                    best = flights[0]
                    if best.price is None:
                        time.sleep(0.2)
                        continue
                    if max_eur and best.price > max_eur:
                        time.sleep(0.2)
                        continue
                    o = format_flight(best)
                    if o:
                        entry = {
                            "label": label, "dep_date": dep_date, "ret_date": None,
                            "origin": origin.name, "dest": dest.name, "ret_ap": None,
                            "out": o, "ret": None, "total": best.price,
                        }
                        results_found.append(entry)
                        count += 1
                        warn = " ⚠️" if o["has_warning"] else ""
                        print(f"✅ [{count}] {dep_date} {origin.name} → {dest.name} "
                              f"€{best.price:.0f}{warn}")
                    time.sleep(0.4)

                # ── NATIVE ROUND-TRIP ─────────────────────────
                elif mode == "roundtrip":
                    rt_pairs = search_round_trip(
                        origin, dest, dep_date, ret_date, out_w, ret_w, seat_type)
                    for out_flight, ret_flight in rt_pairs[:1]:
                        o = format_flight(out_flight)
                        r = format_flight(ret_flight)
                        if not o or not r:
                            continue
                        # In a native round-trip the full combined price is
                        # always on the outbound leg. The return leg price is 0 or
                        # a duplicate — never sum them.
                        total = o["price"] or 0
                        if max_eur and total > max_eur:
                            continue
                        entry = {
                            "label": label, "dep_date": dep_date, "ret_date": ret_date,
                            "origin": origin.name, "dest": dest.name,
                            "ret_ap": origin.name,
                            "out": o, "ret": r, "total": total,
                        }
                        results_found.append(entry)
                        count += 1
                        warn = " ⚠️" if (o["has_warning"] or r["has_warning"]) else ""
                        print(f"✅ [{count}] [RT] {dep_date} {origin.name} ⇄ {dest.name} "
                              f"€{total:.0f}{warn}")
                    time.sleep(0.8)

                # ── COMBINED (two one-ways) ────────────────────
                else:
                    out_flights = search_one_way(origin, dest, dep_date, out_w, seat_type)
                    if not out_flights:
                        time.sleep(0.3)
                        continue
                    best_out = out_flights[0]
                    if best_out.price is None:
                        time.sleep(0.2)
                        continue
                    if max_eur and best_out.price >= max_eur:
                        time.sleep(0.2)
                        continue

                    # Search return across all origin airports (allows mixed airports)
                    best_ret = None
                    best_ret_ap = origin
                    for ret_ap in origins:
                        rf = search_one_way(dest, ret_ap, ret_date, ret_w, seat_type)
                        if rf and rf[0].price is not None:
                            if best_ret is None or rf[0].price < best_ret.price:
                                best_ret = rf[0]
                                best_ret_ap = ret_ap
                        time.sleep(0.2)

                    if best_ret is None or best_ret.price is None:
                        continue

                    total = best_out.price + best_ret.price
                    if max_eur and total > max_eur:
                        continue

                    o = format_flight(best_out)
                    r = format_flight(best_ret)
                    if o and r:
                        entry = {
                            "label": label, "dep_date": dep_date, "ret_date": ret_date,
                            "origin": origin.name, "dest": dest.name,
                            "ret_ap": best_ret_ap.name,
                            "out": o, "ret": r, "total": total,
                        }
                        results_found.append(entry)
                        count += 1
                        warn = " ⚠️" if (o["has_warning"] or r["has_warning"]) else ""
                        print(f"✅ [{count}] {dep_date} {origin.name} → {dest.name} "
                              f"€{best_out.price:.0f} + {ret_date} "
                              f"{dest.name} → {best_ret_ap.name} "
                              f"€{best_ret.price:.0f} = €{total:.0f}{warn}")
                    time.sleep(0.5)

    # ── Final summary ──────────────────────────────────────────
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

    # ── CSV export ─────────────────────────────────────────────
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
