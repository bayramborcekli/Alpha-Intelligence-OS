# Mission 1200 — Real Signal Validation

- **result**: PASS
- **technical_integrity**: PASS
- **real_signal_baseline**: PASS
- **commit**: f8383a6
- **run_id**: M1200-f4253a009c
- **mode**: PAPER (izole replay, gerçek Binance 15m verisi, GERÇEK sinyal akışı skor>=65, ekonomik filtre aktif)
- **data_period**: {'from': '2026-01-20T00:00:00', 'to': '2026-07-26T11:45:00'}
- **tests**: 349 passed in 3.88s
- **signal_stream_events**: 10333
- **duration_seconds**: 95.7

## Varyant Karşılaştırması

| metrik | baseline | sf_1_5 | sf_2_0 | sf_2_5 |
|---|---|---|---|---|
| fee_safety_factor | 2.0 | 1.5 | 2.0 | 2.5 |
| eligible_signals | 1298 | 1207 | 1298 | 1383 |
| executed_trades | 1185 | 1193 | 1185 | 1158 |
| rejected_by_economic_filter | 113 | 14 | 113 | 225 |
| closed_trades | 1185 | 1193 | 1185 | 1158 |
| win_rate_pct | 33.92 | 34.28 | 33.92 | 34.02 |
| gross_profit | 15035.2654 | 15119.1492 | 15035.2654 | 15393.8572 |
| gross_loss | 23883.8146 | 23976.5332 | 23883.8146 | 24051.9031 |
| total_fees | 9058.9424 | 9277.3729 | 9058.9424 | 8982.1634 |
| fee_to_gross_profit_ratio | 0.6025 | 0.6136 | 0.6025 | 0.5835 |
| net_pnl | -8848.5492 | -8857.384 | -8848.5492 | -8658.0459 |
| avg_net_pnl_per_trade | -7.4671 | -7.4245 | -7.4671 | -7.4767 |
| profit_factor | 0.6295 | 0.6306 | 0.6295 | 0.64 |
| max_drawdown_pct | 89.2773 | 89.3383 | 89.2773 | 87.5604 |
| avg_signal_score | 80.32 | 80.34 | 80.32 | 80.25 |
| final_balance | 1151.4508 | 1142.616 | 1151.4508 | 1341.9541 |
