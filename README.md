# flisearch

> Find cheap flights via Google Flights — built on [`fli`](https://github.com/punitarani/fli)

**flisearch** is a Python command-line tool that searches Google Flights for the cheapest fares across configurable origins, destinations, date ranges, cabin classes, and trip types. It can scan ~80 European destinations automatically, or target any specific airport in the world.

---

## 🇬🇧 English

### Requirements

- Python 3.10+
- macOS / Linux / Windows

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/sergiomastocola/flightsearch.git
cd flightsearch

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install flights
```

### Quick Start

```bash
# Default search: BGY + MXP + LIN origins, Jul–Sep 2026, max €60, economy, 2–3 night weekends
python flisearch.py

# Help — full list of options
python flisearch.py --help
```

### All Options

| Flag | Description | Default |
|---|---|---|
| `--origins IATA [...]` | Departure airport(s) | `BGY MXP LIN` |
| `--dest IATA [...]` | Specific destination(s). If omitted, scans ~80 European airports | — |
| `--from YYYY-MM-DD` | Start of search period | `2026-07-01` |
| `--to YYYY-MM-DD` | End of search period | `2026-09-30` |
| `--nights MIN MAX` | Min/max nights away (max 21) | `2 3` |
| `--dep-days N [...]` | Departure weekdays: 0=Mon … 6=Sun | `3 4` (Thu+Fri) |
| `--time-out HH-HH` | Outbound departure time window | none |
| `--time-ret HH-HH` | Return departure time window | none |
| `--max EUR` | Maximum total budget in EUR | `60` |
| `--no-budget` | No budget cap — return everything | — |
| `--cabin CLASS` | `economy` \| `premium_economy` \| `business` \| `first` | `economy` |
| `--mode MODE` | `combined` \| `roundtrip` \| `oneway` (see below) | `combined` |
| `--output FILE` | CSV output filename | `results.csv` |

### Search Modes

| Mode | When to use |
|---|---|
| `combined` | Low-cost carriers (Ryanair, easyJet, Wizz Air). Searches outbound and return independently, then combines the cheapest pair. Supports different airports for outbound and return. |
| `roundtrip` | Traditional carriers (Lufthansa, Air France, Turkish Airlines…). Fetches the native round-trip price from Google Flights, which is often cheaper than two one-ways. |
| `oneway` | One-way only. `--nights` is ignored. Every day in the period is searched. |

### Examples

```bash
# Weekend trip to Barcelona, no budget cap, business class
python flisearch.py --dest BCN --cabin business --no-budget

# BGY only, August, max €80, 5–7 nights, Friday departures
python flisearch.py --origins BGY --from 2026-08-01 --to 2026-08-31 \
    --max 80 --nights 5 7 --dep-days 4

# Long-haul economy to New York, up to 2 weeks, roundtrip pricing
python flisearch.py --dest JFK --mode roundtrip --nights 7 14 --no-budget

# One-way scan to multiple destinations, max €50, July
python flisearch.py --dest BCN LIS MAD --mode oneway --max 50 \
    --from 2026-07-01 --to 2026-07-31

# Business class to Dubai, Friday evening departures, 4–6 nights
python flisearch.py --dest DXB --cabin business --mode roundtrip \
    --nights 4 6 --dep-days 4 --time-out 18-23 --no-budget
```

### Output

Results are printed to the terminal as they are found and saved to a CSV file:

```
💶 TOTAL €54  [3n Fri→Mon]
   ✈  OUT    Fri 03/07  BGY → BCN  18:40 → 20:45  Ryanair FR1234  €29  (0 stops)
   ↩  RET    Mon 06/07  BCN → MXP  07:15 → 09:20  Vueling VY6012  €25  (0 stops)
```

CSV columns: `Total (EUR)`, `Mode`, `Cabin`, `Label`, `Dep Date`, `Ret Date`, `Origin`, `Destination`, `Return Airport`, `Airline Out`, `Flight Out`, `Dep Out`, `Arr Out`, `Price Out (EUR)`, `Airline Ret`, `Flight Ret`, `Dep Ret`, `Arr Ret`, `Price Ret (EUR)`, `Warning`.

### ⚠️ Known Issue — Airline Names

The `fli` library has a known bug where some IATA carrier codes are mapped to incorrect airline names (e.g. code `W4` displays as "LC Péru" instead of the real European carrier). The **flight number is always reliable** — search it directly on Google Flights to identify the actual airline. Affected results are flagged with `⚠️  CHECK AIRLINE NAME`.

### Disclaimer

This tool queries Google Flights indirectly via the `fli` library. Prices are indicative and may differ from what you see when booking. Always verify on Google Flights or the airline's official website before purchasing.

---

## 🇮🇹 Italiano

### Requisiti

- Python 3.10+
- macOS / Linux / Windows

### Installazione

```bash
# 1. Clona il repository
git clone https://github.com/YOUR_USERNAME/flisearch.git
cd flisearch

# 2. Crea un ambiente virtuale
python3 -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows

# 3. Installa le dipendenze
pip install flights
```

### Avvio rapido

```bash
# Ricerca default: origini BGY + MXP + LIN, lug–set 2026, max €60, economy, weekend 2–3 notti
python flisearch.py

# Aiuto — lista completa delle opzioni
python flisearch.py --help
```

### Tutte le opzioni

| Parametro | Descrizione | Default |
|---|---|---|
| `--origins IATA [...]` | Aeroporti di partenza | `BGY MXP LIN` |
| `--dest IATA [...]` | Destinazione/i specifica/e. Se omesso scansiona ~80 aeroporti europei | — |
| `--from YYYY-MM-DD` | Inizio periodo di ricerca | `2026-07-01` |
| `--to YYYY-MM-DD` | Fine periodo di ricerca | `2026-09-30` |
| `--nights MIN MAX` | Notti min/max (massimo 21) | `2 3` |
| `--dep-days N [...]` | Giorni di partenza: 0=lun … 6=dom | `3 4` (gio+ven) |
| `--time-out HH-HH` | Fascia oraria partenza andata | nessuna |
| `--time-ret HH-HH` | Fascia oraria partenza ritorno | nessuna |
| `--max EUR` | Budget massimo totale A/R in euro | `60` |
| `--no-budget` | Nessun limite di budget | — |
| `--cabin CLASSE` | `economy` \| `premium_economy` \| `business` \| `first` | `economy` |
| `--mode MODALITA` | `combined` \| `roundtrip` \| `oneway` (vedi sotto) | `combined` |
| `--output FILE` | Nome file CSV output | `results.csv` |

### Modalità di ricerca

| Modalità | Quando usarla |
|---|---|
| `combined` | Compagnie low cost (Ryanair, easyJet, Wizz Air). Cerca andata e ritorno come singoli biglietti separati, poi combina la coppia più economica. Supporta aeroporti diversi per andata e ritorno. |
| `roundtrip` | Compagnie tradizionali (Lufthansa, Air France, Turkish Airlines…). Recupera il prezzo A/R nativo da Google Flights, spesso più conveniente di due singoli biglietti. |
| `oneway` | Solo andata. `--nights` viene ignorato. Viene eseguita una ricerca per ogni giorno del periodo. |

### Esempi

```bash
# Weekend a Barcellona, nessun limite di budget, business class
python flisearch.py --dest BCN --cabin business --no-budget

# Solo BGY, agosto, max €80, 5–7 notti, partenza venerdì
python flisearch.py --origins BGY --from 2026-08-01 --to 2026-08-31 \
    --max 80 --nights 5 7 --dep-days 4

# Lungo raggio economy verso New York, fino a 2 settimane, prezzo roundtrip
python flisearch.py --dest JFK --mode roundtrip --nights 7 14 --no-budget

# Solo andata verso più destinazioni, max €50, luglio
python flisearch.py --dest BCN LIS MAD --mode oneway --max 50 \
    --from 2026-07-01 --to 2026-07-31

# Business verso Dubai, partenza venerdì sera, 4–6 notti
python flisearch.py --dest DXB --cabin business --mode roundtrip \
    --nights 4 6 --dep-days 4 --time-out 18-23 --no-budget
```

### Output

I risultati vengono stampati in tempo reale e salvati in un file CSV:

```
💶 TOTALE €54  [3n Ven→Lun]
   ✈  OUT    Fri 03/07  BGY → BCN  18:40 → 20:45  Ryanair FR1234  €29  (0 stop)
   ↩  RET    Mon 06/07  BCN → MXP  07:15 → 09:20  Vueling VY6012  €25  (0 stop)
```

Colonne CSV: `Total (EUR)`, `Mode`, `Cabin`, `Label`, `Dep Date`, `Ret Date`, `Origin`, `Destination`, `Return Airport`, `Airline Out`, `Flight Out`, `Dep Out`, `Arr Out`, `Price Out (EUR)`, `Airline Ret`, `Flight Ret`, `Dep Ret`, `Arr Ret`, `Price Ret (EUR)`, `Warning`.

### ⚠️ Problema noto — Nomi delle compagnie

La libreria `fli` ha un bug noto per cui alcuni codici IATA vettore vengono mappati a nomi di compagnia errati (es. il codice `W4` viene mostrato come "LC Péru" invece della compagnia europea reale). Il **numero di volo è sempre affidabile** — cercalo direttamente su Google Flights per identificare il vettore corretto. I risultati affetti sono segnalati con `⚠️  CHECK AIRLINE NAME`.

### Disclaimer

Questo strumento interroga Google Flights indirettamente tramite la libreria `fli`. I prezzi sono indicativi e possono differire da quelli visualizzati al momento della prenotazione. Verifica sempre su Google Flights o sul sito ufficiale della compagnia prima di acquistare.

---

## License

MIT
