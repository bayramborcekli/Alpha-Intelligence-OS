---
name: Binance TR API quirks
description: Response shapes and history coverage limits of the Binance TR Open API
---
- Base is `https://www.binance.tr` (the old trbinance.com base is deprecated — never reintroduce it), paths `/open/v1/...`, HMAC-SHA256 over the exact query string (`timestamp=<server ts>&recvWindow=5000`, signature appended last), `X-MBX-APIKEY` header. Single adapter: `binance_tr_client.py` (session with `trust_env=False`, default TLS verify). Payloads wrapped as `{code, msg, data}`; `data` may be a list OR a dict (sometimes with `rows`/`list` inner key) — always type-guard before slicing.
- Deposit/withdraw endpoints cover only on-chain/internal transfers, NOT spot trades or conversions; account history may also be truncated by the API. **Why:** balance reconciliation from movements alone can never fully match — report PARTIAL, never manufacture opening balances.
- `transferType == 1` marks internal transfers; classify separately from DEPOSIT/WITHDRAWAL and exclude from balance reconstruction.
