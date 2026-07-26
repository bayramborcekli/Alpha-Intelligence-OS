# Alpha-20 v1

This project is a Python command-line trading bot that runs in PAPER mode only. It reads public Binance USDⓈ-M Futures candle data and never connects to an account or sends live orders.

## Run on Replit

Dependencies are listed in `alpha20_v1/requirements.txt`.

Run one scan:

```bash
python alpha20_v1/alpha20.py --once
```

The configured Replit workflow uses the same one-shot command and writes console output. Generated state is stored in `alpha20_v1/state.json`; logs are written to `alpha20_v1/alpha20.log`.

Reset the virtual account before a scan:

```bash
python alpha20_v1/alpha20.py --reset --once
```

Keep `mode` set to `PAPER`. This project does not require API keys, live trading, deployments, or paid services.