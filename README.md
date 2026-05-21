# flightsearch

> Find cheap flights via Google Flights — built on [`fli`](https://github.com/punitarani/fli)

**flightsearch** is a Python command-line tool that searches Google Flights for the cheapest fares across configurable origins, destinations, date ranges, cabin classes, and trip types. It can scan ~80 European destinations automatically, or target any specific airport in the world.

---

## 🇬🇧 English

### Requirements

- Python 3.10+
- macOS / Linux / Windows

### Installation

**macOS / Linux — one command:**
```bash
git clone https://github.com/sergiomastrocola/flightsearch.git
cd flightsearch
bash setup.sh
```

**Windows:**
```bat
git clone https://github.com/sergiomastrocola/flightsearch.git
cd flightsearch
setup.bat
```

The setup script creates a `venv/` virtual environment and installs all dependencies automatically.

> ⚠️ **Important:** activate the virtual environment every time you open a new terminal:
> ```bash
> source venv/bin/activate      # macOS / Linux
> venv\Scripts\activate         # Windows
> ```

### Quick Start

```bash
# Default: BGY + MXP + LIN origins, Jul–Sep 2026, no budget cap, economy, 2–3 night weekends
python flightsearch.py

# Full option list
python flightsearch.py --help
```

### All Options

| Flag | Description | Default |
|---|---|---|
| `--origins IATA [...]` | Departure airport(s) | `BGY MXP LIN` |
| `--dest IATA [...]` | Specific destination(s) as IATA codes e.g. `--dest BCN LIS`. Takes precedence over `--region` | — |
| `--region REGION [...]` | World region(s) to scan (see table below). Default: `europe`. Multiple allowed e.g. `--region europe africa` | `europe` |
| `--from YYYY-MM-DD` | Start of search period | `2026-07-01` |
| `--to YYYY-MM-DD` | End of search period | `2026-09-30` |
| `--nights MIN MAX` | Min/max nights away (max 21). If omitted in `combined`/`roundtrip` mode: `--from` is used as departure date and `--to` as return date (single fixed pair). | `2 3` |
| `--dep-days N [...]` | Departure weekdays: 0=Mon … 6=Sun | `3 4` (Thu+Fri) |
| `--time-out HH-HH` | Outbound departure time window | none |
| `--time-ret HH-HH` | Return departure time window | none |
| `--max EUR` | Maximum total budget in EUR e.g. `--max 200`. If omitted, no cap is applied | — |
| `--no-budget` | Explicitly disable budget cap (equivalent to omitting `--max`) | — |
| `--cabin CLASS` | `economy` \| `premium_economy` \| `business` \| `first` | `economy` |
| `--mode MODE` | `combined` \| `roundtrip` \| `oneway` (see below) | `combined` |
| `--workers N` | Parallel search threads. Increase for speed, reduce if rate-limited | `4` |
| `--output FILE` | CSV output filename | `results.csv` |


### Regions (`--region`)

| Value | Alias(es) | Coverage |
|---|---|---|
| `europe` | `eu` | ~134 airports across Italy, Iberia, France, Benelux, UK & Ireland, DACH, Nordics, Eastern Europe, Greece, Turkey, Caucasus |
| `africa` | `af` | ~52 airports across North, West, East, Central and Southern Africa + Indian Ocean islands |
| `north_america` | `na`, `northamerica` | ~88 airports in USA, Canada, Mexico, Caribbean and Central America |
| `south_america` | `sa`, `southamerica` | ~48 airports in Brazil, Argentina, Chile, Colombia, Peru, Ecuador and more |
| `asia` | `as` | ~79 airports in Middle East, South Asia, Southeast Asia, East Asia, Central Asia |
| `australia_pacific` | `ap`, `pacific`, `oceania` | ~39 airports in Australia, New Zealand and Pacific islands |
| `world` | `all` | All of the above (~440 airports) |

### Search Modes

| Mode | When to use |
|---|---|
| `combined` | Low-cost carriers (Ryanair, easyJet, Wizz Air). Searches outbound and return independently, then combines the cheapest pair. Supports different airports for outbound and return. |
| `roundtrip` | Traditional carriers (Lufthansa, Air France, Turkish Airlines…). Fetches the native round-trip price from Google Flights, often cheaper than two one-ways. |
| `oneway` | One-way only. `--nights` is ignored. Every day in the period is searched. |

### Examples

```bash
# Weekend in Barcelona, no budget cap, business class
python flightsearch.py --dest BCN --cabin business

# BGY only, August, max €80, 5–7 nights, Friday departures
python flightsearch.py --origins BGY --from 2026-08-01 --to 2026-08-31 \
    --max 80 --nights 5 7 --dep-days 4

# Long-haul economy to New York, up to 2 weeks, roundtrip pricing
python flightsearch.py --dest JFK --mode roundtrip --nights 7 14

# One-way to multiple destinations, max €50, July
python flightsearch.py --dest BCN LIS MAD --mode oneway --max 50 \
    --from 2026-07-01 --to 2026-07-31

# Set a budget cap of €400 round-trip
python flightsearch.py --dest TBS --mode roundtrip --nights 5 7 --max 400

# Business to Dubai, Friday evening departures, 4–6 nights
python flightsearch.py --dest DXB --cabin business --mode roundtrip \
    --nights 4 6 --dep-days 4 --time-out 18-23

# Scan Africa and Middle East (asia includes Middle East)
python flightsearch.py --region africa asia --mode roundtrip --no-budget

# Worldwide scan (all regions, ~440 destinations)
python flightsearch.py --region world --mode roundtrip --nights 7 14

# Faster scan with more parallel threads
python flightsearch.py --workers 8

# Slower but safer if Google Flights rate-limits you
python flightsearch.py --workers 2
```

### Output

Results print to the terminal in real time and are saved to a CSV file:

```
💶 TOTAL €54  [3n Fri→Mon]
   ✈  OUT    Fri 03/07  BGY → BCN  18:40 → 20:45  Ryanair FR1234  €29  (0 stops)
   ↩  RET    Mon 06/07  BCN → MXP  07:15 → 09:20  Vueling VY6012  €25  (0 stops)
```

CSV columns: `Total (EUR)`, `Mode`, `Cabin`, `Label`, `Dep Date`, `Ret Date`, `Origin`, `Destination`, `Return Airport`, `Airline Out`, `Flight Out`, `Dep Out`, `Arr Out`, `Price Out (EUR)`, `Airline Ret`, `Flight Ret`, `Dep Ret`, `Arr Ret`, `Price Ret (EUR)`, `Warning`.

### ⚠️ Known Issue — Airline Names

The `fli` library has a known bug where some IATA carrier codes are mapped to incorrect airline names (e.g. code `W4` displays as "LC Péru" instead of the actual European carrier). The **flight number is always reliable** — search it on Google Flights to find the real airline. Affected results are flagged with `⚠️  CHECK AIRLINE NAME`.

### Disclaimer

This tool queries Google Flights indirectly via the `fli` library. Prices are indicative and may differ from those shown at booking time. Always verify on Google Flights or the airline's official website before purchasing.

---

## 🇮🇹 Italiano

### Requisiti

- Python 3.10+
- macOS / Linux / Windows

### Installazione

**macOS / Linux — un solo comando:**
```bash
git clone https://github.com/sergiomastrocola/flightsearch.git
cd flightsearch
bash setup.sh
```

**Windows:**
```bat
git clone https://github.com/sergiomastrocola/flightsearch.git
cd flightsearch
setup.bat
```

Lo script crea automaticamente un ambiente virtuale `venv/` e installa tutte le dipendenze.

> ⚠️ **Importante:** ogni volta che apri un nuovo terminale devi attivare l'ambiente virtuale:
> ```bash
> source venv/bin/activate      # macOS / Linux
> venv\Scripts\activate         # Windows
> ```

### Avvio rapido

```bash
# Default: origini BGY + MXP + LIN, lug–set 2026, nessun limite di budget, economy, weekend 2–3 notti
python flightsearch.py

# Lista completa delle opzioni
python flightsearch.py --help
```

### Tutte le opzioni

| Parametro | Descrizione | Default |
|---|---|---|
| `--origins IATA [...]` | Aeroporti di partenza | `BGY MXP LIN` |
| `--dest IATA [...]` | Destinazione/i specifica/e come codici IATA es. `--dest BCN LIS`. Ha priorità su `--region` | — |
| `--region REGION [...]` | Regione/i geografica/e da scansionare (vedi tabella sotto). Default: `europe`. Più regioni consentite es. `--region europe africa` | `europe` |
| `--from YYYY-MM-DD` | Inizio periodo di ricerca | `2026-07-01` |
| `--to YYYY-MM-DD` | Fine periodo di ricerca | `2026-09-30` |
| `--nights MIN MAX` | Notti min/max (massimo 21). Se omesso in modalità `combined`/`roundtrip`: `--from` viene usato come data di andata e `--to` come data di ritorno (coppia fissa unica) | `2 3` |
| `--dep-days N [...]` | Giorni di partenza: 0=lun … 6=dom | `3 4` (gio+ven) |
| `--time-out HH-HH` | Fascia oraria partenza andata | nessuna |
| `--time-ret HH-HH` | Fascia oraria partenza ritorno | nessuna |
| `--max EUR` | Budget massimo totale A/R in euro es. `--max 200`. Se omesso, nessun limite viene applicato | — |
| `--no-budget` | Disabilita esplicitamente il limite di budget (equivalente a omettere `--max`) | — |
| `--cabin CLASSE` | `economy` \| `premium_economy` \| `business` \| `first` | `economy` |
| `--mode MODALITA` | `combined` \| `roundtrip` \| `oneway` (vedi sotto) | `combined` |
| `--workers N` | Thread di ricerca paralleli. Aumenta per velocità, riduci in caso di rate limit | `4` |
| `--output FILE` | Nome file CSV output | `results.csv` |


### Regioni (`--region`)

| Valore | Alias | Copertura |
|---|---|---|
| `europe` | `eu` | ~134 aeroporti in Italia, Iberia, Francia, Benelux, UK & Irlanda, DACH, Nordici, Europa orientale, Grecia, Turchia, Caucaso |
| `africa` | `af` | ~52 aeroporti in Africa settentrionale, occidentale, orientale, centrale e meridionale + isole dell'Oceano Indiano |
| `north_america` | `na`, `northamerica` | ~88 aeroporti in USA, Canada, Messico, Caraibi e America centrale |
| `south_america` | `sa`, `southamerica` | ~48 aeroporti in Brasile, Argentina, Cile, Colombia, Perù, Ecuador e altri |
| `asia` | `as` | ~79 aeroporti in Medio Oriente, Asia meridionale, Asia sudorientale, Asia orientale, Asia centrale |
| `australia_pacific` | `ap`, `pacific`, `oceania` | ~39 aeroporti in Australia, Nuova Zelanda e isole del Pacifico |
| `world` | `all` | Tutte le regioni (~440 aeroporti) |

### Modalità di ricerca

| Modalità | Quando usarla |
|---|---|
| `combined` | Compagnie low cost (Ryanair, easyJet, Wizz Air). Cerca andata e ritorno come biglietti separati, poi combina la coppia più economica. Supporta aeroporti diversi per andata e ritorno. |
| `roundtrip` | Compagnie tradizionali (Lufthansa, Air France, Turkish Airlines…). Recupera il prezzo A/R nativo da Google Flights, spesso più conveniente di due singoli biglietti. |
| `oneway` | Solo andata. `--nights` viene ignorato. Viene eseguita una ricerca per ogni giorno del periodo. |

### Esempi

```bash
# Weekend a Barcellona, nessun limite di budget, business class
python flightsearch.py --dest BCN --cabin business

# Solo BGY, agosto, max €80, 5–7 notti, partenza venerdì
python flightsearch.py --origins BGY --from 2026-08-01 --to 2026-08-31 \
    --max 80 --nights 5 7 --dep-days 4

# Lungo raggio economy verso New York, fino a 2 settimane, prezzo roundtrip
python flightsearch.py --dest JFK --mode roundtrip --nights 7 14

# Solo andata verso più destinazioni, max €50, luglio
python flightsearch.py --dest BCN LIS MAD --mode oneway --max 50 \
    --from 2026-07-01 --to 2026-07-31

# Imposta un budget massimo di €400 andata/ritorno
python flightsearch.py --dest TBS --mode roundtrip --nights 5 7 --max 400

# Business verso Dubai, partenza venerdì sera, 4–6 notti
python flightsearch.py --dest DXB --cabin business --mode roundtrip \
    --nights 4 6 --dep-days 4 --time-out 18-23

# Scan Africa and Middle East (asia includes Middle East)
python flightsearch.py --region africa asia --mode roundtrip --no-budget

# Worldwide scan (all regions, ~440 destinations)
python flightsearch.py --region world --mode roundtrip --nights 7 14

# Faster scan with more parallel threads
python flightsearch.py --workers 8

# Slower but safer if Google Flights rate-limits you
python flightsearch.py --workers 2
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

La libreria `fli` ha un bug noto per cui alcuni codici IATA vengono mappati a nomi di compagnia errati (es. il codice `W4` viene mostrato come "LC Péru" invece della compagnia europea reale). Il **numero di volo è sempre affidabile** — cercalo su Google Flights per identificare il vettore corretto. I risultati affetti sono segnalati con `⚠️  CHECK AIRLINE NAME`.

### Disclaimer

Questo strumento interroga Google Flights indirettamente tramite la libreria `fli`. I prezzi sono indicativi e possono differire da quelli visualizzati al momento della prenotazione. Verifica sempre su Google Flights o sul sito ufficiale della compagnia prima di acquistare.

---

## License

MIT
