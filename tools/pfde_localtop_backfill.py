"""PFDE FAZ 5 — tek seferlik TARİHSEL yerel-tepe kanıtı (salt okunur).

Kapanmış işlemlerin giriş anındaki 20/30/50 mum zirvesine uzaklığını
public 1m klines ile geri-doldurur ve zirveye yakın girişlerin gerçek
başarı oranını raporlar. Hiçbir durum dosyasına YAZMAZ.

Kullanım: python tools/pfde_localtop_backfill.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402


def main() -> None:
    rt = json.load(open(ROOT / "alpha20_v1/dual_model_runtime.json"))
    trades = [t for t in rt.get("trades", [])
              if t.get("net_pnl") is not None and t.get("opened_at")]
    by_sym: dict[str, list] = {}
    for t in trades:
        by_sym.setdefault(t["symbol"], []).append(t)
    rows = []
    skipped = 0
    for sym, ts in sorted(by_sym.items()):
        try:
            kl = dm.fetch_spot_klines(sym, "1m", 1000)
        except Exception as exc:
            print(f"{sym}: klines alınamadı ({exc}) — atlandı")
            skipped += len(ts)
            continue
        if not kl:
            skipped += len(ts)
            continue
        opens = [int(k[0]) for k in kl]
        closes = [float(k[4]) for k in kl]
        for t in ts:
            ots = datetime.fromisoformat(t["opened_at"]).timestamp() \
                * 1000
            # giriş mumunun indeksi
            idx = None
            for i, o in enumerate(opens):
                if o <= ots < o + 60_000:
                    idx = i
                    break
            if idx is None or idx < 51:
                skipped += 1
                continue
            row = {"symbol": sym,
                   "win": 1 if float(t["net_pnl"]) > 0 else 0}
            for n in (20, 30, 50):
                hi = max(closes[idx - n:idx])
                row[f"d{n}"] = (hi - closes[idx]) / hi * 100 \
                    if hi > 0 else None
            rows.append(row)
    print(f"kapsanan işlem: {len(rows)} / atlanan: {skipped}")
    for n in (20, 30, 50):
        near = [r for r in rows if r[f"d{n}"] is not None
                and r[f"d{n}"] < 0.15]
        far = [r for r in rows if r[f"d{n}"] is not None
               and r[f"d{n}"] >= 0.15]

        def wr(rs):
            return round(100 * sum(r["win"] for r in rs) / len(rs), 1) \
                if rs else None
        print(f"lookback {n}: zirveye<%0.15 n={len(near)} "
              f"WR={wr(near)}% | uzak n={len(far)} WR={wr(far)}%")
    out = ROOT / "alpha20_v1" / "pfde_localtop_backfill.json"
    print("NOT: sonuç dosyaya yazılmadı (salt okunur kanıt koşusu);"
          f" istenirse {out} hedefi kullanılabilir.")


if __name__ == "__main__":
    main()
