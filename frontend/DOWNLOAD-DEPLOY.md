# CV Eşleştirme indirme paketi

Download düğmesi Docker gerektirmeyen Windows 10/11 x64 kurulum EXE dosyasını indirir.
Java, Node.js, PostgreSQL ve pgvector pakete dahildir. Kullanıcı kendi OpenAI ve
Anthropic API anahtarlarını başlatıcıda girer. Gerçek anahtar veya CV paketlenmez.
Bu imzasız bir ön sürümdür; normal Windows kullanıcı profilinde grafik kurulum,
kısayollar ve DPAPI anahtar kaydı için son kullanıcı doğrulaması gereklidir.

Büyük EXE Git'e eklenmez. GitHub Release'e yüklenir; Render build adımı dosyayı
indirip SHA-256 ve boyutunu doğrular. Eksik veya bozuk dosyada build durur.
Dosya adı, URL, boyut ve SHA-256 kaynağı src/download.json dosyasıdır.

Yayın sırası:
1. cv-match-native-2026.09.23-preview.1 sürümüne manifestteki EXE dosyasını yükle.
2. Frontend değişikliklerini main dalına birleştir ve Render deploy'unu bekle.
3. Sitedeki Download bağlantısını ve indirilen dosyanın SHA-256 değerini kontrol et.

Yerel build için EXE public/downloads klasörüne kopyalanabilir. npm run build aynı
bütünlük kontrolünü uygular. API anahtarlarını site veya build ortamına koymayın.
Önceki Docker ZIP sürümü GitHub Releases altında ayrı olarak korunmaktadır.