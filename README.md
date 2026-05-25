# flightsearch

> Find cheap flights via Google Flights — built on [`fli`](https://github.com/punitarani/fli)

**flightsearch** is a Python command-line tool that searches Google Flights for the cheapest fares across configurable origins, destinations, date ranges, cabin classes, passenger counts, bags, and trip types. It can scan ~440 airports across 6 world regions automatically, or target any specific airport.

---

## 🇬🇧 English

### Requirements

- Python 3.10+
- macOS / Linux / Windows

### Installation

**macOS / Linux:**
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
# Default: BGY + MXP + LIN → Europe, Jul–Sep 2026, no budget cap, economy, 2–3 night weekends
python flightsearch.py

# Full option list
python flightsearch.py --help
```

---

### All Options

#### Airports & Destinations

| Flag | Description | Default |
|---|---|---|
| `--origins IATA [...]` | Departure airport(s) as IATA codes | `BGY MXP LIN` |
| `--dest IATA [...]` | Specific destination(s). Takes precedence over `--region` | — |
| `--region REGION [...]` | World region(s) to scan (see table below). Multiple allowed | `europe` |
| `--exclude CODE [...]` | Exclude destinations by airport IATA (3 letters) or country ISO code (2 letters). Mix freely e.g. `--exclude ES FR BCN` | — |

#### Dates & Duration

| Flag | Description | Default |
|---|---|---|
| `--from YYYY-MM-DD` | Start of search period | `2026-07-01` |
| `--to YYYY-MM-DD` | End of search period | `2026-09-30` |
| `--nights MIN MAX` | Min/max nights away (max 21). If omitted in `combined`/`roundtrip` mode without `--dep-days`: uses `--from` as departure and `--to` as return (single fixed pair) | `2 3` |
| `--dep-days N [...]` | Departure weekdays: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun | `3 4` (Thu+Fri) |
| `--time-out HH-HH` | Outbound departure time window e.g. `18-23` | none |
| `--time-ret HH-HH` | Return departure time window e.g. `8-23` | none |

#### Budget

| Flag | Description | Default |
|---|---|---|
| `--max EUR` | Maximum total budget in EUR. If omitted: no cap | — |
| `--no-budget` | Explicitly disable budget cap (same as omitting `--max`) | — |
| `--min-price EUR` | Minimum price — filters out suspiciously cheap or erroneous results | — |

#### Passengers & Bags

| Flag | Description | Default |
|---|---|---|
| `--adults N` | Number of adult passengers | `1` |
| `--children N` | Number of children (age 2–11) | `0` |
| `--infants-lap N` | Infants travelling on lap | `0` |
| `--infants-seat N` | Infants with their own seat | `0` |
| `--bags-checked N` | Include N checked bag(s) in the displayed price. Makes low-cost vs traditional comparisons fair | `0` |
| `--bags-carryon` | Include carry-on bag fee in the displayed price | off |

#### Flight Options

| Flag | Description | Default |
|---|---|---|
| `--stops N` | Max stops: `0`=non-stop `1`=one stop `2`=two or fewer `any`=no limit | `any` |
| `--max-duration MINUTES` | Max total flight duration in minutes e.g. `--max-duration 180` | none |
| `--max-layover MINUTES` | Max layover duration in minutes | none |
| `--layover-airports IATA [...]` | Preferred layover airports | none |
| `--sort KEY` | Sort by: `cheapest` \| `best` \| `departure` \| `arrival` \| `duration` \| `emissions` | `cheapest` |

#### Cabin & Mode

| Flag | Description | Default |
|---|---|---|
| `--cabin CLASS` | `economy` \| `premium_economy` \| `business` \| `first` | `economy` |
| `--mode MODE` | `combined` \| `roundtrip` \| `oneway` \| `bestprice` (see below) | `combined` |

#### Airline Filters

| Flag | Description | Default |
|---|---|---|
| `--airlines IATA [...]` | Only show results operated by these airlines. Mutually exclusive with `--exclude-airlines` and `--alliance` | — |
| `--exclude-airlines IATA [...]` | Exclude results with these airlines (client-side). Mutually exclusive with `--airlines` and `--alliance` | — |
| `--alliance NAME` | Filter to a single alliance: `star` \| `oneworld` \| `skyteam`. Uses Google Flights' native filter. Mutually exclusive with `--airlines` and `--exclude-airlines` | — |

#### Performance & Output

| Flag | Description | Default |
|---|---|---|
| `--workers N` | Parallel search threads. Increase for speed; reduce if rate-limited | `4` |
| `--output FILE` | CSV output filename. Rows written in real time as results arrive | `results.csv` |
| `--json FILE` | Also save results as JSON | none |
| `--top N` | Show only the N cheapest results in the final summary | all |
| `--airport-names` | Show full airport names instead of IATA codes e.g. `Barcelona International Airport (BCN)` | off |
| `--calendar` | Calendar mode: show a price-per-day table for a fixed origin→dest pair. Requires `--dest` with a single destination | off |
| `--no-cache` | Disable disk cache — always fetch fresh results | off |
| `--config FILE` | Path to a TOML config file. If not specified, loads `flightsearch.toml` automatically if it exists in the current directory. CLI flags always override config values | auto |

---

### Search Modes

| Mode | When to use |
|---|---|
| `combined` | Low-cost carriers (Ryanair, easyJet, Wizz Air). Searches outbound and return independently, then combines the cheapest pair. Supports different return airports. |
| `roundtrip` | Traditional carriers (Lufthansa, Air France, Turkish…). Fetches the native RT price from Google Flights — often cheaper than two one-ways. |
| `oneway` | One-way only. `--nights` is ignored. Every day in the period is searched. |
| `bestprice` | Runs both `combined` and `roundtrip` in parallel and returns the cheapest. Result tag: `[BP/RT]` (roundtrip won), `[BP/COMB]` (combined, same airline), `[BP/COMB-MIX]` (combined, mixed airlines). |

---

### Alliances (`--alliance`)

Alliance filtering is passed natively to the Google Flights API via the `fli` library.

| Value | Members (examples) |
|---|---|
| `star` | Lufthansa (LH), United (UA), Singapore Airlines (SQ), Turkish Airlines (TK), Air Canada (AC), ANA (NH), Asiana (OZ), TAP (TP), LOT (LO), Swiss (LX), Austrian (OS), Brussels Airlines (SN), SAS (SK), Finnair (AY), Air China (CA), Air India (AI) and more |
| `oneworld` | British Airways (BA), American Airlines (AA), Qatar Airways (QR), Japan Airlines (JL), Qantas (QF), Iberia (IB), Cathay Pacific (CX), LATAM (LA), Royal Jordanian (RJ), Royal Air Maroc (AT) and more |
| `skyteam` | Air France (AF), KLM (KL), Delta (DL), Korean Air (KE), China Eastern (MU), China Southern (CZ), Aeromexico (AM), ITA Airways (AZ), Garuda (GA), Vietnam Airlines (VN), Saudia (SV) and more |

---

### Regions (`--region`)

| Value | Alias(es) | Airports |
|---|---|---|
| `europe` | `eu` | ~134 airports across Italy, Iberia, France, Benelux, UK & Ireland, DACH, Nordics, Eastern Europe, Greece, Turkey, Caucasus |
| `africa` | `af` | ~52 airports across North, West, East, Central and Southern Africa + Indian Ocean islands |
| `north_america` | `na`, `northamerica` | ~88 airports in USA, Canada, Mexico, Caribbean and Central America |
| `south_america` | `sa`, `southamerica` | ~48 airports in Brazil, Argentina, Chile, Colombia, Peru, Ecuador and more |
| `asia` | `as` | ~79 airports in Middle East, South Asia, Southeast Asia, East Asia, Central Asia |
| `australia_pacific` | `ap`, `pacific`, `oceania` | ~39 airports in Australia, New Zealand and Pacific islands |
| `world` | `all` | All of the above (~440 airports) |

---

### Output

Results print to the terminal in real time and are written to CSV as they arrive:

```
💶 TOTAL €54  [3n Fri→Mon]
   ✈  OUT    Fri 03/07  BGY → BCN  18:40 → 20:45  Ryanair FR1234  €29  1h05m  (0 stops)
   ↩  RET    Mon 06/07  BCN → MXP  07:15 → 09:20  Vueling VY6012  €25  1h05m  (0 stops)
```

With `--airport-names`:
```
💶 TOTAL €54  [3n Fri→Mon]
   ✈  OUT    Fri 03/07  Bergamo / Orio Al Serio Airport (BGY) → Barcelona International Airport (BCN)  18:40 → 20:45  Ryanair FR1234  €29  1h05m  (0 stops)
   ↩  RET    Mon 06/07  Barcelona International Airport (BCN) → Malpensa International Airport (MXP)  07:15 → 09:20  Vueling VY6012  €25  1h05m  (0 stops)
```

CSV columns: `Total (EUR)`, `Mode`, `Cabin`, `Passengers`, `Bags`, `Label`, `Dep Date`, `Ret Date`, `Origin`, `Destination`, `Return Airport`, `Airline Out`, `Flight Out`, `Dep Out`, `Arr Out`, `Price Out (EUR)`, `Duration Out`, `Airline Ret`, `Flight Ret`, `Dep Ret`, `Arr Ret`, `Price Ret (EUR)`, `Duration Ret`, `Stops Out`, `Stops Ret`, `Warning`.

---


### Configuration File

Instead of typing all options on the command line, you can put them in a TOML file:

```bash
# Use an explicit config file
python flightsearch.py --config my_search.toml

# Or just name it flightsearch.toml and it loads automatically
python flightsearch.py
```

**Priority:** CLI flags > config file > built-in defaults.
This means you can set base options in the config and override specific ones on the fly:

```bash
# Config sets economy + Europe, CLI overrides to business + Asia
python flightsearch.py --config base.toml --cabin business --region asia
```

A template config file (`flightsearch.toml`) is included in the repo with all available options commented out. Copy it, rename it, and uncomment the options you need.

**Example config for cheap Milan weekend trips:**
```toml
origins     = ["BGY", "MXP", "LIN"]
region      = ["europe"]
from        = "2026-07-01"
to          = "2026-09-30"
nights      = [2, 3]
dep_days    = [3, 4]
time_out    = "18-23"
time_ret    = "8-23"
cabin       = "economy"
mode        = "combined"
max         = 60
workers     = 6
output      = "weekends.csv"
```

**Example config for a fixed long-haul trip:**
```toml
origins      = ["MXP"]
dest         = ["JFK"]
from         = "2026-10-01"
to           = "2026-10-10"
cabin        = "business"
mode         = "roundtrip"
adults       = 2
bags_checked = 1
alliance     = "star"
sort         = "duration"
json         = "jfk_results.json"
```

### Practical Examples

#### All economy flights from Milan to Europe, Friday evening, back Sunday, next 2 months, max €60

```bash
python flightsearch.py \
  --origins BGY MXP LIN \
  --region europe \
  --from 2026-06-01 --to 2026-07-31 \
  --dep-days 4 --time-out 18-23 --time-ret 8-23 \
  --nights 2 2 --cabin economy --mode combined --max 60
```

#### Non-stop flights only, price including 1 checked bag, 2 adults

```bash
python flightsearch.py \
  --origins MXP \
  --region europe \
  --adults 2 --bags-checked 1 \
  --stops 0 \
  --from 2026-07-01 --to 2026-09-30
```

#### Europe scan excluding Italy and Spain

```bash
python flightsearch.py \
  --origins BGY MXP LIN \
  --region europe \
  --exclude IT ES \
  --from 2026-07-01 --to 2026-09-30 \
  --dep-days 4 --time-out 18-23 --time-ret 8-23 \
  --nights 2 3 --max 80
```

#### Best price: economy from Milan to Europe, same or mixed airline, max €80

```bash
python flightsearch.py \
  --origins BGY MXP LIN \
  --region europe \
  --from 2026-07-01 --to 2026-09-30 \
  --mode bestprice --cabin economy --max 80
```

#### Business class with Star Alliance, Asia, 1 week, sorted by duration

```bash
python flightsearch.py \
  --origins MXP \
  --region asia \
  --from 2026-09-01 --to 2026-09-30 \
  --nights 7 7 --cabin business \
  --mode roundtrip --alliance star \
  --sort duration
```

#### Calendar mode: cheapest day to fly BGY→BCN in July

```bash
python flightsearch.py \
  --origins BGY --dest BCN \
  --from 2026-07-01 --to 2026-07-31 \
  --calendar
```

#### Fixed dates: Milan → Hong Kong, 1–10 June, economy roundtrip

```bash
python flightsearch.py \
  --origins MXP --dest HKG \
  --from 2026-06-01 --to 2026-06-10 \
  --cabin economy --mode roundtrip
```

#### Top 10 cheapest results, save as JSON too

```bash
python flightsearch.py \
  --region europe --max 100 \
  --top 10 --json results.json
```

#### Long weekend (Fri evening → Mon), no budget cap, show full airport names

```bash
python flightsearch.py \
  --origins BGY MXP LIN --region europe \
  --from 2026-07-01 --to 2026-09-30 \
  --dep-days 4 --time-out 18-23 \
  --nights 3 3 --mode combined \
  --airport-names
```

#### Worldwide scan, business class, 7–10 nights, roundtrip, no budget cap

```bash
python flightsearch.py \
  --region world --cabin business \
  --mode roundtrip --nights 7 10 \
  --workers 8
```

#### Only Ryanair and easyJet

```bash
python flightsearch.py --airlines FR U2
```

#### Exclude Ryanair

```bash
python flightsearch.py --region europe --exclude-airlines FR
```

---

### ⚠️ Known Issue — Airline Names

The `fli` library has a known bug where some IATA carrier codes are mapped to incorrect names (e.g. `W4` displayed as "LC Péru" instead of "Wizz Air Malta"). flightsearch applies a correction map for all known cases. The **flight number is always reliable** — search it on Google Flights to confirm the carrier. Affected results are flagged with `⚠️ CHECK AIRLINE NAME`.

### Caching

Results are cached on disk for 3 hours in `.flightsearch_cache.*` files. Re-running the same search within that window is instant. Use `--no-cache` to always fetch fresh results.

### Disclaimer

This tool queries Google Flights indirectly via the `fli` library. Prices are indicative and may differ at booking time. Always verify on Google Flights or the airline's official website before purchasing.

---

## 🇮🇹 Italiano

### Requisiti

- Python 3.10+
- macOS / Linux / Windows

### Installazione

**macOS / Linux:**
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

> ⚠️ **Importante:** attiva l'ambiente virtuale ogni volta che apri un nuovo terminale:
> ```bash
> source venv/bin/activate      # macOS / Linux
> venv\Scripts\activate         # Windows
> ```

### Avvio rapido

```bash
python flightsearch.py
python flightsearch.py --help
```

---

### Tutte le opzioni

#### Aeroporti e destinazioni

| Parametro | Descrizione | Default |
|---|---|---|
| `--origins IATA [...]` | Aeroporti di partenza | `BGY MXP LIN` |
| `--dest IATA [...]` | Destinazione/i specifica/e. Ha priorità su `--region` | — |
| `--region REGION [...]` | Regione/i da scansionare (vedi tabella). Più regioni consentite | `europe` |
| `--exclude CODE [...]` | Escludi per codice IATA aeroporto (3 lettere) o paese ISO (2 lettere). Es. `--exclude ES FR BCN` | — |

#### Date e durata

| Parametro | Descrizione | Default |
|---|---|---|
| `--from YYYY-MM-DD` | Inizio periodo | `2026-07-01` |
| `--to YYYY-MM-DD` | Fine periodo | `2026-09-30` |
| `--nights MIN MAX` | Notti min/max (max 21). Se omesso in `combined`/`roundtrip` senza `--dep-days`: usa `--from` come andata e `--to` come ritorno | `2 3` |
| `--dep-days N [...]` | Giorni di partenza: 0=lun … 6=dom | `3 4` (gio+ven) |
| `--time-out HH-HH` | Fascia oraria partenza andata es. `18-23` | nessuna |
| `--time-ret HH-HH` | Fascia oraria partenza ritorno es. `8-23` | nessuna |

#### Budget

| Parametro | Descrizione | Default |
|---|---|---|
| `--max EUR` | Budget massimo totale in euro. Se omesso: nessun limite | — |
| `--no-budget` | Disabilita esplicitamente il limite (equivalente a omettere `--max`) | — |
| `--min-price EUR` | Prezzo minimo — filtra risultati sospettosamente economici o erronei | — |

#### Passeggeri e bagagli

| Parametro | Descrizione | Default |
|---|---|---|
| `--adults N` | Numero di adulti | `1` |
| `--children N` | Numero di bambini (2–11 anni) | `0` |
| `--infants-lap N` | Neonati in braccio | `0` |
| `--infants-seat N` | Neonati con posto proprio | `0` |
| `--bags-checked N` | Include N bagaglio/i registrato/i nel prezzo visualizzato. Rende equo il confronto low-cost vs tradizionali | `0` |
| `--bags-carryon` | Include il costo del bagaglio a mano nel prezzo | off |

#### Opzioni volo

| Parametro | Descrizione | Default |
|---|---|---|
| `--stops N` | Scali max: `0`=diretto `1`=max 1 scalo `2`=max 2 scali `any`=nessun limite | `any` |
| `--max-duration MINUTI` | Durata massima totale del volo in minuti es. `--max-duration 180` | nessuna |
| `--max-layover MINUTI` | Durata massima dello scalo in minuti | nessuna |
| `--layover-airports IATA [...]` | Aeroporti di scalo preferiti | nessuno |
| `--sort CHIAVE` | Ordina per: `cheapest` \| `best` \| `departure` \| `arrival` \| `duration` \| `emissions` | `cheapest` |

#### Cabina e modalità

| Parametro | Descrizione | Default |
|---|---|---|
| `--cabin CLASSE` | `economy` \| `premium_economy` \| `business` \| `first` | `economy` |
| `--mode MODALITA` | `combined` \| `roundtrip` \| `oneway` \| `bestprice` (vedi sotto) | `combined` |

#### Filtri compagnie

| Parametro | Descrizione | Default |
|---|---|---|
| `--airlines IATA [...]` | Solo risultati con queste compagnie. Non combinabile con `--exclude-airlines` e `--alliance` | — |
| `--exclude-airlines IATA [...]` | Escludi queste compagnie (filtro client-side). Non combinabile con `--airlines` e `--alliance` | — |
| `--alliance NOME` | Filtra per alleanza: `star` \| `oneworld` \| `skyteam`. Usa il filtro nativo di Google Flights | — |

#### Performance e output

| Parametro | Descrizione | Default |
|---|---|---|
| `--workers N` | Thread di ricerca paralleli. Aumenta per velocità; riduci in caso di rate limit | `4` |
| `--output FILE` | Nome file CSV. Le righe vengono scritte in tempo reale | `results.csv` |
| `--json FILE` | Salva i risultati anche in formato JSON | nessuno |
| `--top N` | Mostra solo i N risultati più economici nel riepilogo finale | tutti |
| `--airport-names` | Mostra i nomi estesi degli aeroporti invece dei codici IATA | off |
| `--calendar` | Modalità calendario: tabella prezzi per giorno per una tratta fissa. Richiede `--dest` con una sola destinazione | off |
| `--no-cache` | Disabilita la cache su disco — recupera sempre dati freschi | off |
| `--config FILE` | Percorso a un file di configurazione TOML. Se non specificato, carica `flightsearch.toml` automaticamente se esiste nella directory corrente. I flag CLI hanno sempre priorità sui valori del config | auto |

---

### Modalità di ricerca

| Modalità | Quando usarla |
|---|---|
| `combined` | Compagnie low cost (Ryanair, easyJet, Wizz Air). Cerca andata e ritorno separatamente e combina la coppia più economica. Supporta aeroporti di ritorno diversi. |
| `roundtrip` | Compagnie tradizionali (Lufthansa, Air France, Turkish…). Recupera il prezzo A/R nativo da Google Flights, spesso più conveniente di due singoli. |
| `oneway` | Solo andata. `--nights` ignorato. Cerca ogni giorno del periodo. |
| `bestprice` | Esegue `combined` e `roundtrip` in parallelo e restituisce il più economico. Tag: `[BP/RT]`, `[BP/COMB]`, `[BP/COMB-MIX]`. |

---

### Alleanze (`--alliance`)

Il filtro alleanza viene passato nativamente all'API di Google Flights tramite `fli`.

| Valore | Membri (esempi) |
|---|---|
| `star` | Lufthansa (LH), United (UA), Singapore Airlines (SQ), Turkish Airlines (TK), Air Canada (AC), ANA (NH), Asiana (OZ), TAP (TP), LOT (LO), Swiss (LX), Austrian (OS), Brussels Airlines (SN), SAS (SK), Finnair (AY), Air China (CA), Air India (AI) e altri |
| `oneworld` | British Airways (BA), American Airlines (AA), Qatar Airways (QR), Japan Airlines (JL), Qantas (QF), Iberia (IB), Cathay Pacific (CX), LATAM (LA), Royal Jordanian (RJ), Royal Air Maroc (AT) e altri |
| `skyteam` | Air France (AF), KLM (KL), Delta (DL), Korean Air (KE), China Eastern (MU), China Southern (CZ), Aeromexico (AM), ITA Airways (AZ), Garuda (GA), Vietnam Airlines (VN), Saudia (SV) e altri |

---

### Regioni (`--region`)

| Valore | Alias | Aeroporti |
|---|---|---|
| `europe` | `eu` | ~134 aeroporti in Italia, Iberia, Francia, Benelux, UK & Irlanda, DACH, Nordici, Europa orientale, Grecia, Turchia, Caucaso |
| `africa` | `af` | ~52 aeroporti in Africa settentrionale, occidentale, orientale, centrale e meridionale + isole dell'Oceano Indiano |
| `north_america` | `na`, `northamerica` | ~88 aeroporti in USA, Canada, Messico, Caraibi e America centrale |
| `south_america` | `sa`, `southamerica` | ~48 aeroporti in Brasile, Argentina, Cile, Colombia, Perù, Ecuador e altri |
| `asia` | `as` | ~79 aeroporti in Medio Oriente, Asia meridionale, Asia sudorientale, Asia orientale, Asia centrale |
| `australia_pacific` | `ap`, `pacific`, `oceania` | ~39 aeroporti in Australia, Nuova Zelanda e isole del Pacifico |
| `world` | `all` | Tutte le regioni (~440 aeroporti) |

---

### Output

```
💶 TOTALE €54  [3n Ven→Lun]
   ✈  OUT    Fri 03/07  BGY → BCN  18:40 → 20:45  Ryanair FR1234  €29  1h05m  (0 stop)
   ↩  RET    Mon 06/07  BCN → MXP  07:15 → 09:20  Vueling VY6012  €25  1h05m  (0 stop)
```

Con `--airport-names`:
```
   ✈  OUT    Fri 03/07  Bergamo / Orio Al Serio Airport (BGY) → Barcelona International Airport (BCN) ...
```

---


### File di configurazione

Invece di scrivere tutte le opzioni sulla riga di comando, puoi metterle in un file TOML:

```bash
# Usa un file di configurazione esplicito
python flightsearch.py --config mia_ricerca.toml

# Oppure nominalo flightsearch.toml e viene caricato automaticamente
python flightsearch.py
```

**Priorità:** flag CLI > file di configurazione > default integrati.
Puoi impostare le opzioni base nel config e sovrascriverne alcune al volo:

```bash
# Config imposta economy + Europa, CLI sovrascrive a business + Asia
python flightsearch.py --config base.toml --cabin business --region asia
```

Il file template (`flightsearch.toml`) è incluso nel repo con tutte le opzioni disponibili commentate. Copialo, rinominalo e decommenta le opzioni che ti servono.

**Esempio config per weekend economici da Milano:**
```toml
origins     = ["BGY", "MXP", "LIN"]
region      = ["europe"]
from        = "2026-07-01"
to          = "2026-09-30"
nights      = [2, 3]
dep_days    = [3, 4]
time_out    = "18-23"
time_ret    = "8-23"
cabin       = "economy"
mode        = "combined"
max         = 60
workers     = 6
output      = "weekend.csv"
```

**Esempio config per un viaggio intercontinentale:**
```toml
origins      = ["MXP"]
dest         = ["JFK"]
from         = "2026-10-01"
to           = "2026-10-10"
cabin        = "business"
mode         = "roundtrip"
adults       = 2
bags_checked = 1
alliance     = "star"
sort         = "duration"
json         = "jfk_risultati.json"
```

### Esempi pratici

#### Tutti i voli economy dagli scali milanesi verso Europa, venerdì sera, rientro domenica, prossimi 2 mesi, max €60

```bash
python flightsearch.py \
  --origins BGY MXP LIN --region europe \
  --from 2026-06-01 --to 2026-07-31 \
  --dep-days 4 --time-out 18-23 --time-ret 8-23 \
  --nights 2 2 --cabin economy --mode combined --max 60
```

#### Solo voli diretti, prezzo con 1 bagaglio registrato, 2 adulti

```bash
python flightsearch.py \
  --origins MXP --region europe \
  --adults 2 --bags-checked 1 --stops 0 \
  --from 2026-07-01 --to 2026-09-30
```

#### Scansione Europa escludendo Italia e Spagna

```bash
python flightsearch.py \
  --origins BGY MXP LIN --region europe --exclude IT ES \
  --from 2026-07-01 --to 2026-09-30 \
  --dep-days 4 --time-out 18-23 --time-ret 8-23 \
  --nights 2 3 --max 80
```

#### Miglior prezzo: economy da Milano verso Europa, max €80

```bash
python flightsearch.py \
  --origins BGY MXP LIN --region europe \
  --from 2026-07-01 --to 2026-09-30 \
  --mode bestprice --cabin economy --max 80
```

#### Business class Star Alliance, Asia, 1 settimana, ordinato per durata

```bash
python flightsearch.py \
  --origins MXP --region asia \
  --from 2026-09-01 --to 2026-09-30 \
  --nights 7 7 --cabin business \
  --mode roundtrip --alliance star --sort duration
```

#### Modalità calendario: giorno più economico BGY→BCN a luglio

```bash
python flightsearch.py \
  --origins BGY --dest BCN \
  --from 2026-07-01 --to 2026-07-31 \
  --calendar
```

#### Date fisse: Milano → Hong Kong, 1–10 giugno, economy roundtrip

```bash
python flightsearch.py \
  --origins MXP --dest HKG \
  --from 2026-06-01 --to 2026-06-10 \
  --cabin economy --mode roundtrip
```

#### Top 10 più economici, salvati anche in JSON

```bash
python flightsearch.py \
  --region europe --max 100 \
  --top 10 --json results.json
```

#### Solo Ryanair e easyJet

```bash
python flightsearch.py --airlines FR U2
```

#### Escludi Ryanair

```bash
python flightsearch.py --region europe --exclude-airlines FR
```

---

### ⚠️ Problema noto — Nomi delle compagnie

La libreria `fli` ha un bug noto per cui alcuni codici IATA vengono mappati a nomi errati (es. `W4` mostrato come "LC Péru" invece di "Wizz Air Malta"). flightsearch applica una mappa di correzione per tutti i casi noti. Il **numero di volo è sempre affidabile**. I risultati affetti sono segnalati con `⚠️ CHECK AIRLINE NAME`.

### Cache

I risultati vengono salvati su disco per 3 ore nei file `.flightsearch_cache.*`. Rieseguire la stessa ricerca entro quel periodo è istantaneo. Usa `--no-cache` per ottenere sempre dati freschi.

### Disclaimer

Questo strumento interroga Google Flights indirettamente tramite la libreria `fli`. I prezzi sono indicativi. Verifica sempre su Google Flights o sul sito ufficiale della compagnia prima di acquistare.

---

## License

MIT
