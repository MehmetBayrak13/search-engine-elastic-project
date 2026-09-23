# CV Eşleştirme indirme paketi

Üst menüdeki Download bağlantısı gerçek Windows ZIP paketini `/downloads/` üzerinden
indirir. Backend config yüklenirken veya hata verdiğinde de düğme görünür.
Bu Docker Desktop gerektiren yerel pakettir, imzalı bir EXE değildir.

Paket büyük olduğundan ZIP Git'e eklenmez. Önce GitHub Release'e yüklenir;
Render'ın `npm run build` adımı sürümdeki dosyayı indirir ve SHA-256 ile doğrular.
Release yoksa build hata verir; eksik dosyayla canlıya çıkılmaz.
Dosya adı, sürüm adresi ve checksum `src/download.json` içindedir.

Yayın sırası:

1. `cv-match-2026.09.22` etiketiyle MehmetBayrak13/search-engine-elastic-project
   deposunda release oluşturup manifestteki ZIP ve `.sha256` dosyasını yükleyin.
2. Frontend değişikliklerini mevcut `main` dalına gönderin; Render deploy'unu bekleyin.
3. `https://mehmetbayrak.ai/downloads/CV-Eslestirme-Windows-2026.09.22.zip` adresini
   indirip checksum'u doğrulayın. Sitede Download düğmesini kontrol edin.

Yerel geliştirmede ZIP `public/downloads/` içine konabilir. `npm run build` aynı
checksum kontrolünü uygular. API anahtarlarını build ortamına veya pakete eklemeyin.
