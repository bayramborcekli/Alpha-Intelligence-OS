# Mission 1260 — Out-of-Sample Validation

- **result**: PASS
- **technical_integrity**: PASS
- **deterministic**: True
- **structural_advantage_repeated**: True
- **commit**: 9d332b9
- **run_id**: M1260-5bfa99af17
- **config_diff**: {'atr_stop_multiplier': {'old': 1.5, 'new': 3.0}}
- **mode**: PAPER (izole replay, out-of-sample dönem, gerçek sinyal akışı skor>=65, sf=2.0)
- **data_period**: {'from': '2025-07-16T12:00:00', 'to': '2026-01-19T23:45:00', 'leakage_boundary_utc': '2026-01-20T00:00:00'}
- **selection_period_not_overlapping**: 2026-01-20 → 2026-07-26 (Mission 1200/1250) ile kesişim yok
- **tests**: 349 passed in 4.20s
- **signal_stream_events**: 10776
- **duration_seconds**: 48.7

## Karşılaştırma (A kontrol vs C aday)

| metrik | A_control_1_5 | C_candidate_3_0 |
|---|---|---|
| atr_stop_multiplier | 1.5 | 3.0 |
| trades_sha256 | 8735c7cab2c755fff2bfda723b6ccaac287e4fa49f50e4596547d075d1eec96e | 86dc34a45d3278b62ff34772493241e5210859c251395d5e871e19f91737711a |
| eligible_signals | 1315 | 269 |
| executed_trades | 1161 | 267 |
| rejected_by_economic_filter | 153 | 1 |
| closed_trades | 1161 | 267 |
| win_rate_pct | 32.39 | 33.33 |
| gross_profit | 15186.9452 | 8073.6987 |
| gross_loss | 15183.7764 | 8082.9076 |
| total_fees | 9210.8268 | 2164.628 |
| fee_to_expected_risk_ratio | 0.4044 | 0.1786 |
| fee_to_gross_profit_ratio | 0.6065 | 0.2681 |
| net_pnl | -9207.658 | -2173.837 |
| avg_net_pnl_per_trade | -7.9308 | -8.1417 |
| expectancy_r | -0.4312 | -0.1785 |
| gross_profit_factor | 1.0002 | 0.9989 |
| net_profit_factor | 0.5694 | 0.7716 |
| max_drawdown_pct | 92.2211 | 27.4418 |
| avg_stop_distance_pct | 0.6362 | 1.3892 |
| avg_holding_time_min | 190.8915 | 918.0337 |
| winners_avg_mae_r | 0.4223 | 0.4466 |
| losers_avg_mfe_r | 0.5852 | 0.6716 |
| losers_mfe_ge_1r_pct | 22.04 | 25.28 |
| top5_winners_pnl | 420.4187 | 491.0257 |
| top5_winners_share_of_net_profit_pct | 3.45 | 6.68 |
| final_balance | 792.342 | 7826.163 |
| open_positions | 0 | 0 |
| ledger_mismatches | 0 | 0 |
| pnl_mismatches | 0 | 0 |
| risk_violations | 0 | 0 |
| exceptions | 0 | 0 |
| warnings | 1 | 1 |
