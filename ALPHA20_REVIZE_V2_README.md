# Alpha20 Revize v2 — Windows Kurulum

Bu paket, mevcut `D:\GENESIS2000\Alpha-Intelligence-OS` uygulamasının
üzerine **klasör yolları korunarak** açılmak üzere hazırlanmıştır.

## Kurulum

1. Çalışan Alpha penceresini `stop_alpha.cmd` ile kapatın.
2. ZIP içeriğini doğrudan
   `D:\GENESIS2000\Alpha-Intelligence-OS` köküne çıkarın.
3. Dosya birleştirme/üzerine yazma sorusunu onaylayın.
4. `VERIFY_ALPHA20_REVIZE_V2_WINDOWS.cmd` dosyasını çalıştırın.

Paket `.env`, anahtar, parola, yerel yönetici kaydı, runtime/state, işlem
geçmişi veya log içermez. Bu nedenle mevcut kullanıcı ayarları ve Paper
geçmişi arşivden gelmez ve üzerine yazılmaz.

## Güvenlik

- Çalışma modu yalnız PAPER'dır.
- Canlı emirler `DISABLED` kalır.
- Futures, transfer, çekim ve borsa yazma yolu eklenmemiştir.
- Stop-loss minimum tutma süresinden bağımsız olarak daima aktiftir.

