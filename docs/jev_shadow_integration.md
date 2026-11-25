# Jev shadow integration

The TypeSafe Jev connector is an optional observer for candidates that already
passed the bot's deterministic strategy, risk, and execution checks. Its
structured opinion is written to the normal bot log with the signal ID; it
cannot approve or reject a candidate, change size, or submit an order. Requests
run on a bounded background queue so Jev latency does not hold up the trading
loop.

## Enable a research profile

Set the API key in the process environment or the local `.env` file:

```text
TYPESAFE_API_KEY=<your TypeSafe API key>
```

Then opt in only the profile being evaluated by adding this to that profile's
`settings.yaml`:

```yaml
jev:
  enabled: true
  shadow_mode: true
  model: jev-latest
  timeout_seconds: 3.0
```

The default is disabled. The adapter deliberately ignores `shadow_mode: false`;
this first integration has no Jev-controlled execution path.

## Data sent

For a trade-ready candidate, the request sends the symbol, timeframe, side,
closed-bar time, bot score components, normalized stop/target distances,
reward/risk, spread relative to ATR, and selected derived market facts such as
session, regime, liquidity sweep labels, and SMC/Fibonacci alignment. Alchemist
candidates also include the detected model, POI/liquidity labels, and named
confluences. The request does not include MT5 credentials, account identifiers,
balance/equity, open positions, order volume, or raw price levels.

The remote service is TypeSafe's `POST /v1/systemone` endpoint and receives this
candidate summary. Keep the API key in the environment; do not put it in YAML,
dashboard code, or source control. Disable `jev.enabled` to stop requests.

## Interpreting the output

Jev returns a thesis-alignment category and evidence-quality score with
confidence/probabilities. These describe consistency of the supplied facts;
they are not win probabilities or evidence of profitability. Compare logged
answers by `signal_id` with the bot's subsequent trade records over a meaningful
out-of-sample period before considering any change to deterministic rules.
Vendor documentation says confidence depends on the returned probability
distribution and recommends calibrating thresholds to the use case; this
integration does not set a threshold or use confidence to trade.
