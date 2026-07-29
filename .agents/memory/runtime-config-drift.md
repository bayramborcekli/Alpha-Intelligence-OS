---
name: Runtime config drift into git baseline
description: The running bot mutates alpha20_v1 config files; platform auto-commits can bake unsafe runtime state into the committed baseline.
---
The live bot rewrites `alpha20_v1/config.json` and `alpha20_v1/smart_config.json` at runtime (e.g. flipping `adaptive_system.enabled` or advisory mode to automatic). Platform auto-commits ("Update configuration...") can capture that mutated state, silently regressing paper-safety defaults and breaking `tests/test_adaptive.py` guards.

**Why:** Happened during Mission 1700 (Agents 03–04): an auto-commit baked `adaptive_system.enabled: true` into HEAD; `git checkout --` no longer helped because HEAD itself was wrong. Fix was `git show <last-intentional-commit>:alpha20_v1/config.json > alpha20_v1/config.json` and committing the revert.

**How to apply:** Before each regression run, revert `alpha20_v1/config.json` + `smart_config.json`; if the guard test still fails with a clean tree, diff the file against the last *intentional* commit — the drift may already be committed. Keep `adaptive_system.enabled=false` as baseline. For a fully clean regression, the workflow can mutate configs mid-run; re-check after.

**Ek 2 (2026-07-29, kill_switch olayı):** Asıl tekrar eden mekanizma bulundu: tam test paketi koşusu `/adaptive/kill-switch` POST testiyle GERÇEK config.json'a `kill_switch=true` yazıyordu ve geri almıyordu; değer bir sonraki commit'e gömülüp Windows'a git pull ile taşındı ve panelde "ACİL DURDURULDU" bayat kilidi üretti. Test artık dosyayı byte-byte geri yüklüyor; `tests/test_paper_emergency_stop.py::TestRepoBaseline` taban çizgisini (kill_switch=false) koruyor. Kural: gerçek config'e yazan HER rota testi finally bloğunda dosyayı geri yüklemeli; tam paket sonrası `git diff alpha20_v1/config.json` kontrol et.

**Ek (2026-07-29):** Flask test client ile /settings, /smart/settings gibi POST rotalarını doğrulamak da config.json/smart_config.json'u gerçekten yazar ve platform auto-commit ile HEAD'e bakar; code review bunu kapsam dışı davranış değişikliği olarak reddeder. Testlerde dosyayı yedekle/geri yükle veya save fonksiyonunu monkeypatch'le (bkz. tests/test_settings_override_note.py).
