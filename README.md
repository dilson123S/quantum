# QuantTradingBot

A professional, institutional-grade algorithmic trading bot connected to **MetaTrader 5 (MT5)**, powered by a multi-timeframe **5-Confirmation Scoring Engine** and an advanced quantitative risk management framework.

---

## 🎓 Entregables Académicos Oficiales (Hito 3 · Entrega Final)
* **Autores:**
  * Nilson David Rivera Gutiérrez (Código: `T00078156`)
  * Mauro Jose Prasca Bohorquez (Código: `T00065353`)
* 📄 **Informe Técnico en Formato IEEE (PDF):** [`quantum_hito3.pdf`](quantum_hito3.pdf) *(Copia en docs: [`docs/quantum_hito3.pdf`](docs/quantum_hito3.pdf))*
* 📄 **Bitácora de Co-Pilotaje con IA (PDF Anexo):** [`quantum_bitacora_ia.pdf`](quantum_bitacora_ia.pdf) *(Copia en docs: [`docs/quantum_bitacora_ia.pdf`](docs/quantum_bitacora_ia.pdf))*
* 📓 **Bitácora humano-IA (Markdown):** [`docs/bitacora_ia.md`](docs/bitacora_ia.md)
* 📓 **Bitácora Integral Extendida (Markdown):** [`BITACORA_IA.md`](BITACORA_IA.md) *(Copia en docs: [`docs/BITACORA_IA.md`](docs/BITACORA_IA.md))*
* 📄 **Informe Hito 2 (Formato APA 7ma):** [`quantum_hito2.pdf`](quantum_hito2.pdf)
* 📓 **Notebook de Exploración:** [`notebooks/exploracion_quantum.ipynb`](notebooks/exploracion_quantum.ipynb)

---

## Architecture Overview

```
+-----------------------------------------------------------------------------------+
|                                QuantTradingBot                                    |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                                Market Data Layer                                  |
|  - MT5 Connection / Tick & Bar Feed                                               |
|  - Multi-Timeframe Data Synchronization (Higher: H4 | Setup: H1 | Entry: M15)     |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                        5-Confirmation Scoring Engine (0-100)                      |
|  +---------------------------+---------------------------+---------------------+  |
|  | 1. Market Structure (30p) | 2. Trend Alignment (25p)  | 3. Momentum (15p)   |  |
|  |    - Swings / BOS / CHoCH |    - EMA 20/50/200 + ADX  |    - RSI + MACD     |  |
|  +---------------------------+---------------------------+---------------------+  |
|  | 4. Volume Confirmation(15)| 5. Volatility Filter (15p)| Total Score Threshold|  |
|  |    - OBV / Tick/Real Vol  |    - ATR Ratio & Multipl. | (Min: 75, Gates Met)|  |
|  +---------------------------+---------------------------+---------------------+  |
+-----------------------------------------------------------------------------------+
                                         |
                                    [Score >= 75]
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                             Risk Management Layer                                 |
|  - Dynamic Position Sizing (Account % / Fixed Money / Score-Adjusted)             |
|  - Daily Max Loss & Max Drawdown Circuit Breakers                                 |
|  - Consecutive Loss Risk Reducer (3 losses -> Halt)                              |
|  - Correlation Limits & Currency Exposure Controls                                |
|  - Spread & Trading Session / News Filters                                        |
+-----------------------------------------------------------------------------------+
                                         |
                                 [Risk Checks Pass]
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                           Execution & Order Management                            |
|  - MT5 Order Routing (Breakout / Market Entries)                                  |
|  - Structured SL / TP Placement (ATR-based)                                        |
|  - Active Position Management (Break-Even @ 1R, Partial TP @ 1R, Hybrid Trailing) |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                         Persistence & Monitoring Layer                            |
|  - SQLite / SQLAlchemy Trade & Signal Logging                                     |
|  - Telegram Real-Time Alerts & Heartbeat Notifications                            |
+-----------------------------------------------------------------------------------+
```

---

## The 5-Confirmation Scoring System

Every potential trade signal is evaluated across 5 distinct analytical pillars, yielding a composite score between **0 and 100**. A trade is only triggered when the total score reaches the configured threshold (default **75**) AND mandatory component minimums are satisfied.

| # | Confirmation Pillar | Weight | Description & Key Metrics | Mandatory Min |
|---|---------------------|:------:|---------------------------|:-------------:|
| **1** | **Market Structure** | **30 pts** | Swing highs/lows identification, Break of Structure (BOS), and Change of Character (CHoCH) on Setup/Entry timeframes. | **18 pts** |
| **2** | **Trend Alignment** | **25 pts** | Multi-timeframe trend alignment using EMA ribbon (20/50/200) and ADX directional strength (>20-25). | **15 pts** |
| **3** | **Momentum** | **15 pts** | RSI directional filters (Bull > 50, Bear < 50) and MACD histogram momentum confirmation. | - |
| **4** | **Volume Confirmation** | **15 pts** | Volume spike detection relative to moving average (tick/real volume) and On-Balance Volume (OBV) trend confirmation. | - |
| **5** | **Volatility Validation** | **15 pts** | ATR volatility expansion checks, ensuring current ATR falls within acceptable bounds (0.8x - 2.5x of baseline). | - |
| **TOTAL** | | **100 pts** | **Minimum Score to Trigger Trade: 75 / 100** | |

### Score Classification

| Score Range | Classification | Action | Risk Multiplier |
|:-----------:|:--------------:|:------:|:---------------:|
| **95 - 100** | `EXCEPTIONAL` | Full Execution | 100% Risk (`1.0x`) |
| **85 - 94** | `EXCELLENT` | Full Execution | 100% Risk (`1.0x`) |
| **75 - 84** | `GOOD` | Scaled Execution | 75% Risk (`0.75x`) |
| **60 - 74** | `WEAK` | Filtered Out / Discarded | No Trade (`0.0x`) |
| **0 - 59** | `INVALID` | Discarded | No Trade (`0.0x`) |

---

## Quantitative Risk Management

The risk layer enforces strict institutional risk protocols before any order is submitted:

- **Position Sizing:** Calculates exact lot sizes based on account balance/equity, stop loss distance (in price/points), tick value, and score-adjusted risk tier.
- **Daily Loss Circuit Breaker:** Halts trading if daily loss reaches the maximum allowance (default: `2.0%`).
- **Maximum Drawdown Protection:** Locks bot if overall peak-to-trough drawdown hits the hard limit (default: `10.0%`).
- **Consecutive Loss Reducer:** Dynamically adjusts risk after losses (0-1 loss: 100% risk, 2 losses: 75% risk, 3 losses: complete trading pause).
- **Correlation Filter:** Prevents overexposure across correlated currency groups (e.g., EUR basket, USD basket) with a maximum correlation coefficient threshold (`0.80`).
- **Trade Management:**
  - **Break-Even:** Automatically shifts Stop Loss to entry once price reaches `1.0R`.
  - **Partial Take-Profit:** Closes `50%` of volume at `1.0R`.
  - **Hybrid Trailing Stop:** Protects running profits beyond `1.0R` using structural swings and ATR trailing.

### Execution Semantics

- Indicators, scores, structure and trailing management use only completed MT5 candles; the forming candle is excluded.
- `entry_mode: breakout` creates an MT5 `BUY_STOP`/`SELL_STOP` beyond a confirmed structural swing (plus a broker-safe buffer), never a market chase. Each order has a broker-side expiry of `breakout_expiry_bars` and is cancelled earlier if its protective thesis is invalidated before entry.
- The signal id is deterministic per closed bar. MT5 comments, the SQLite journal and startup reconciliation prevent a retry/restart from duplicating a pending order or position.
- ML is locked in shadow mode. It may observe and log predictions, but cannot block a trade until `ml.allow_active_filter: true` is explicitly reviewed after validation and a cost-aware backtest.

---

## Requirements

- **Operating System:** Windows 10/11 or Windows Server (required for MetaTrader 5 desktop client)
- **Python:** Python 3.10 or higher
- **Broker Terminal:** MetaTrader 5 Terminal installed with algorithmic trading enabled

---

## Installation & Setup

### 1. Clone & Set Up Python Environment

```powershell
git clone https://github.com/dilson123S/quantum.git
cd quantum
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the sample `.env.example` file to `.env` and fill in your MetaTrader 5 credentials:

```powershell
copy .env.example .env
```

Edit `.env`:
```ini
# MetaTrader 5 Credentials
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Demo
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# Telegram Notifications (V2)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Database
DB_PATH=data/trading_bot.db

# Required only for dashboard control actions (pause, close, retrain)
DASHBOARD_API_TOKEN=
```

### 3. Configure MT5 Terminal

1. Open MetaTrader 5.
2. Go to **Tools -> Options -> Expert Advisors**.
3. Enable **"Allow algorithmic trading"**.
4. (Optional) Check **"Allow WebRequest for listed URL"** if using webhook/API services.

---

## Configuration Overview

All configuration parameters are externalized in human-readable YAML files under `config/`:

| Configuration File | Purpose |
|--------------------|---------|
| [`config/settings.yaml`](file:///c:/Users/Asus/Documents/quantum/config/settings.yaml) | Master bot settings: active symbols, timeframes (`H4`/`H1`/`M15`), scoring weights, indicator parameters, trade execution parameters, and market filters. |
| [`config/symbols.yaml`](file:///c:/Users/Asus/Documents/quantum/config/symbols.yaml) | Per-symbol overrides: tick vs. real volume modes, max spread thresholds in points, and trading session windows. |
| [`config/risk.yaml`](file:///c:/Users/Asus/Documents/quantum/config/risk.yaml) | Risk parameters: sizing mode, daily loss/drawdown limits, correlation groups, consecutive loss reductions, and score-based risk scaling. |

## 🌐 Web Dashboard Terminal

QuantTradingBot includes an institutional-grade, real-time Web Dashboard (Dark Theme / Glassmorphism) with WebSocket streaming (1-2s):

- **Command Center**: Live Balance, Equity, Drawdown %, Daily P&L, and Equity Curve Chart.
- **5 Confirmations Radar**: Live visual progress bars for Structure (/30), Trend (/25), Momentum (/15), Volume (/15), and Volatility (/15) for EURUSD, GBPUSD, USDJPY, XAUUSD.
- **Active Positions Monitor**: Real-time open trades table with live floating P&L, Break-Even badges, and manual close controls.
- **Emergency Kill Switch**: Close all open positions immediately from the UI.
- **History & Signals Audit**: Closed trades history and audit log of all detected signals with filter rejection reasons.

### Launching the Web Dashboard

The dashboard binds to `127.0.0.1` by default. Read-only monitoring is local;
all controls require `DASHBOARD_API_TOKEN` in `.env`. Generate a token with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. Do not expose
the dashboard to a network unless it is placed behind authenticated HTTPS.

To launch the dashboard server:

```powershell
python dashboard_app.py --port 8000
```

Then open your browser at **`http://localhost:8000`**.

---

## 🚦 Operational Modes

The bot can be operated in 4 distinct operational modes configured via `settings.yaml` (`bot.mode`):

- **`backtest`**: Historical simulation and backtesting against historical tick/bar data with performance analytics.
- **`paper`**: Real-time forward testing using live market feed without sending orders to MT5 broker.
- **`demo`**: Live order routing to MT5 Demo accounts for staging validation.
- **`live`**: Production live trading with real capital (requires `bot.allow_live_trading: true` safety switch).

The default mode is **`paper`**. `demo` will only route orders to an MT5 demo
account. `live` requires both `bot.allow_live_trading: true` and a connected
MT5 real account; the bot refuses to start or route an order when either check
fails.

## Validación sin sobreajuste

El backtester replica la entrada estructural mediante órdenes stop pendientes,
costes configurados y una regla conservadora de velas ambiguas. Las ejecuciones
con `--oos-start` generan un manifiesto con hashes de configuración y datos;
la guía completa está en [VALIDATION_PROTOCOL.md](docs/VALIDATION_PROTOCOL.md).

---

## Project Structure

```
quantum/
├── config/                    # Master bot configuration (YAML)
├── core/                      # Enums, models, exceptions
├── data/
│   ├── experiments/           # Resultados de backtest y holdout
│   └── ...                    # Datasets, descargadores, limpieza
├── docs/
│   ├── bitacora_ia.md         # Bitácora humano-IA (formato rúbrica)
│   └── BITACORA_IA.md         # Bitácora extendida
├── execution/                 # MT5 order routing & trade management
├── filters/                   # Trading session & regime filters
├── indicators/                # Technical indicators (structure, trend, momentum, volume, volatility)
├── learning/                  # Adaptive learning modules
├── ml/                        # ML pipeline (XGBoost, PyTorch, Qwen SLM)
├── monitoring/                # Dashboard & telemetry
├── notebooks/
│   └── exploracion_quantum.ipynb  # Exploración de datos y resultados
├── risk/                      # Position sizing, circuit breakers, correlation
├── strategy/                  # Scoring engine, signal generators, strategies
├── tests/                     # Unit & integration tests (pytest)
├── .env.example               # Template for credentials & paths
├── requirements.txt           # Python package dependencies (pinned)
├── quantum_hito3.pdf          # Informe IEEE (Hito 3)
├── quantum_bitacora_ia.pdf    # Bitácora IA (PDF anexo)
└── README.md                  # Project documentation
```

---

## Versioning Roadmap

- **V1 - Core Engine & MT5 Integration (Current):**
  - Multi-timeframe synchronization (`H4` / `H1` / `M15`)
  - 5-Confirmation scoring engine
  - Dynamic position sizing & risk management
  - Structural pending-breakout execution, Break-Even, Partial Close, Trailing Stop
  - SQLite trade logging
- **V2 - Notifications & Telemetry:**
  - Telegram bot integration (trade open/close alerts, daily P&L, heartbeat)
  - Economic calendar news filter integration
- **V3 - Advanced Analytics & Backtesting:**
  - Fast vector/event-driven backtester with slippage & commission modeling
  - Performance dashboard (Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor)
- **V4 - Machine Learning Optimization:**
  - Adaptive weight optimization for score components based on market regime
  - Volatility-adjusted dynamic confirmation thresholds
- **V5 - Web Dashboard:**
  - Web-based real-time monitoring and parameter management interface
- **V6 - Multi-Broker & Multi-Asset Support:**
  - Integration with FIX protocol and crypto exchange APIs

---

## License

This project is licensed under the **MIT License**.
