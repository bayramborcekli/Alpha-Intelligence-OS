# Mission 1000 — Yönetici Özeti

## Teknik Altyapı Doğrulaması
- **result**: PASS
- **commit**: 0e2e964
- **run_id**: M1000-f142fdb18d
- **mode**: PAPER (izole replay, gerçek Binance 15m verisi, ekonomik filtre aktif)
- **tests**: 349 passed in 4.64s
- **closed_trades**: 1000
- **ledger_validation_count**: 2001
- **ledger_mismatch_count**: 0
- **pnl_mismatch_count**: 0
- **risk_violation_count**: 0
- **exception_count**: 0
- **warning_count**: 0
- **recovery_count**: 0
- **open_positions**: 0
- **duration_seconds**: 210.3

## Strateji Performansı (ayrı değerlendirme)
- **opened_trades**: 1000
- **skipped_trades**: 3
- **wins**: 349
- **losses**: 651
- **win_rate_pct**: 34.9
- **gross_profit**: 15819.8095
- **gross_loss**: 23193.7051
- **total_fees**: 8193.7874
- **net_pnl**: -7373.8956
- **profit_factor**: 0.6821
- **max_drawdown_pct**: 74.1609
- **starting_balance**: 10000.0
- **final_balance**: 2626.1044

## Gözlem Metrikleri
- **avg_atr**: 51.215258
- **avg_duration_minutes**: 213.2
- **avg_notional_usdt**: 4095.76
- **avg_fee_usdt**: 8.1938
- **fee_to_gross_profit_ratio**: 0.5179

### by_symbol
- BTCUSDT: 332 işlem, net -3313.91, WR 35.24%
- ETHUSDT: 334 işlem, net -3259.47, WR 34.43%
- SOLUSDT: 334 işlem, net -800.51, WR 35.03%

### by_regime
- RANGE: 339 işlem, net -3258.54, WR 34.51%
- TREND_DOWN: 121 işlem, net -469.76, WR 40.5%
- TREND_UP: 540 işlem, net -3645.60, WR 33.89%

### by_score_bucket
- 000-019: 18 işlem, net -77.86, WR 38.89%
- 020-039: 130 işlem, net -1507.14, WR 33.85%
- 040-059: 356 işlem, net -3157.45, WR 34.27%
- 060-079: 215 işlem, net -1315.77, WR 34.88%
- 080-099: 196 işlem, net -1101.54, WR 34.18%
- 100-119: 85 işlem, net -214.13, WR 40.0%
