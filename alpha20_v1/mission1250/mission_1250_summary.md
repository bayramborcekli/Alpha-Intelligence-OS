# Mission 1250 — Structural Trade Economics

- **result**: PASS
- **technical_integrity**: PASS
- **deterministic**: True
- **commit**: 4e76f38
- **run_id**: M1250-7b5087c7aa
- **mode**: PAPER (izole replay, gerçek sinyal akışı skor>=65, sf=2.0 sabit)
- **data_period**: {'from': '2026-01-20T00:15:00', 'to': '2026-07-26T12:00:00'}
- **tests**: 349 passed in 4.76s
- **signal_stream_events**: 10332
- **economic_breakeven_stop_pct**: 0.2
- **duration_seconds**: 82.5

## Varyant Karşılaştırması

| metrik | A_baseline | B_wide1 | C_wide2 | D_minstop |
|---|---|---|---|---|
| atr_stop_multiplier | 1.5 | 2.25 | 3.0 | 1.5 |
| min_stop_distance_pct | None | None | None | 0.4 |
| eligible_signals | 1298 | 522 | 275 | 2246 |
| executed_trades | 1185 | 521 | 274 | 1026 |
| rejected_by_economic_filter | 113 | 0 | 0 | 0 |
| rejected_by_min_stop_threshold | 0 | 0 | 0 | 1220 |
| closed_trades | 1185 | 521 | 274 | 1026 |
| avg_stop_distance_pct | 0.664 | 1.0073 | 1.4575 | 0.7305 |
| avg_notional_usdt | 3822.447 | 5059.7935 | 4183.0949 | 3982.0574 |
| avg_position_qty | 9.6986 | 13.0823 | 9.5204 | 13.1643 |
| avg_gross_winner | 45.132 | 84.1284 | 98.3598 | 53.4755 |
| avg_gross_loser | -22.9025 | -41.8979 | -49.4848 | -26.8154 |
| avg_fee_usdt | 7.6447 | 10.1172 | 8.3648 | 7.9636 |
| fee_to_gross_profit_ratio | 0.4993 | 0.3424 | 0.2284 | 0.4429 |
| fee_to_expected_risk_ratio | 0.3355 | 0.2411 | 0.1694 | 0.2973 |
| win_rate_pct | 33.92 | 35.12 | 37.23 | 33.63 |
| net_pnl | -8848.6053 | -4037.036 | -770.6491 | -7982.875 |
| avg_net_pnl_per_trade | -7.4672 | -7.7486 | -2.8126 | -7.7806 |
| gross_profit_factor | 1.0117 | 1.0871 | 1.1787 | 1.0103 |
| expectancy_r | -0.3594 | -0.1932 | -0.0532 | -0.3068 |
| max_drawdown_pct | 89.2773 | 45.1184 | 13.024 | 81.5296 |
| winners_avg_mae_r | 0.4192 | 0.4433 | 0.4453 | 0.4165 |
| losers_avg_mfe_r | 0.6405 | 0.639 | 0.6116 | 0.657 |
| losers_mfe_ge_1r_pct | 25.8 | 22.19 | 26.16 | 27.31 |
| final_balance | 1151.3947 | 5962.964 | 9229.3509 | 2017.125 |
| open_positions | 0 | 0 | 0 | 0 |
| ledger_mismatches | 0 | 0 | 0 | 0 |
| pnl_mismatches | 0 | 0 | 0 | 0 |
| risk_violations | 0 | 0 | 0 | 0 |
| exceptions | 0 | 0 | 0 | 0 |
| warnings | 0 | 1 | 1 | 0 |
