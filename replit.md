# Alpha-20 v1

This project contains a Flask control panel for the Python command-line trading bot. The bot runs in PAPER mode only: it reads public Binance USDⓈ-M Futures candle data and never connects to an account or sends live orders.

## Run on Replit

Dependencies are listed in `requirements.txt`.

Start the settings page:

```bash
python app.py
```

The page listens on `0.0.0.0` and uses the `PORT` environment variable, falling back to port 5000. It safely manages bot status, PAPER settings, symbols, and recent trade history through `alpha20_v1/config.json`, `state.json`, and `alpha20.log`.

The panel can start and stop only the bot process it records in `alpha20_v1/.bot.pid`. It starts the bot with `python alpha20_v1/alpha20.py`, never uses shell command interpolation, and does not add live trading or API credentials.

Run one scan:

```bash
python alpha20_v1/alpha20.py --once
```

Generated state is stored in `alpha20_v1/state.json`; logs are written to `alpha20_v1/alpha20.log`. The Replit workflow runs the control panel with `python app.py`.

Reset the virtual account before a scan:

```bash
python alpha20_v1/alpha20.py --reset --once
```

Keep `mode` set to `PAPER`. This project does not require API keys, live trading, deployments, or paid services.