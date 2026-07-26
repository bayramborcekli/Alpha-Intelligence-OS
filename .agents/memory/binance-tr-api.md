---
name: Binance TR API quirks
description: Response shapes and history coverage limits of the Binance TR Open API
---
- Base `https://www.trbinance.com`, paths `/open/v1/...`, HMAC-SHA256 over query string, `X-MBX-APIKEY` header. Payloads wrapped as `{code, msg, data}`; `data` may be a list OR a dict (sometimes with `rows`/`list` inner key) — always type-guard before slicing.
- Deposit/withdraw endpoints cover only on-chain/internal transfers, NOT spot trades or conversions; account history may also be truncated by the API. **Why:** balance reconciliation from movements alone can never fully match — report PARTIAL, never manufacture opening balances.
- `transferType == 1` marks internal transfers; classify separately from DEPOSIT/WITHDRAWAL and exclude from balance reconstruction.
