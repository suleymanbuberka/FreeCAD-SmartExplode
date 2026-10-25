# FreeCAD-SmartExplode

FreeCAD-SmartExplode, FreeCAD ortamında dışarıdan aktarılan ham .STEP montajlarını veya çok parçalı tasarımları hiçbir manuel yön ya da rota tanımlaması yapmadan, saniyeler içinde matematiksel katman analizine göre patlatmak, alt grupları yönetmek ve sunum kalitesinde MP4 animasyonlar üretmek için geliştirilmiş bir makrodur.

## 🎯 Neden FreeCAD-SmartExplode? (Hangi Sorunu Çözüyor?)

FreeCAD ekosistemindeki mevcut araçlar (ExplodedAssembly, A2plus vb.), bir montajı patlatmak için kullanıcının her parçayı tek tek seçmesini, her birine yön vektörü ile mesafe tanımlamasını veya parçaları özel montaj kısıtlamalarıyla (constraints) baştan kurgulamasını gerektirir.

FreeCAD-SmartExplode ise "Sıfır Ön Hazırlık (Zero-Prep)" felsefesiyle çalışır:
- ✅ **Rota Tanımlama Zorunluluğu Yoktur:** Parçaların sınır kutularını ve dizilim sırasını otomatik analiz eder; fare tekerleğini çevirdiğiniz anda tüm modeli katman katman açar.
- ✅ **CAD Bağımsızdır (STEP Dostu):** Montajın A2plus, Assembly4, PartDesign ile çizilmiş olması veya dışarıdan gelen ham bir .STEP dosyası olması fark etmez; sahnedeki tüm görünür katı gövdeleri anında tanır.
- ✅ **İç Parçalar Dış Kapağı Delmez:** Akıllı kademe algoritması sayesinde en dıştaki kapak veya sac parça daha uzağa, içteki parçalar ise daha yakına kademeli olarak açılır.

## 🔥 Temel Patlatma ve Animasyon Özellikleri

### 1. ⚡ 5 Farklı Stilde Akıllı Katmanlı Patlatma
Tek bir açılır menü ve fare tekerleği (scroll) yardımıyla montajınızı anında 5 farklı geometrik disiplinde açabilirsiniz:
- **Merkezi (3 Boyutlu Dışa Açılım):** Küresel ve radyal katmanlarla 3 boyutlu patlatma sağlar.
- **Sadece Z Ekseni (Alt-Üst Katmanlı):** Üst kapak, gövde ve taban plakası tarzı dikey dizilimler için idealdir.
- **Sadece X Ekseni (Sağ-Sol Katmanlı):** Yatay mil ve yan kapak montajları için kullanılır.
- **Sadece Y Ekseni (Ön-Arka Katmanlı):** Ön panel ve arka gövde dizilimleri için uygundur.
- **XY Düzlemi (Yatay Çevresel Açılım):** Yüksekliği sabit tutup parçaları yatay düzlemde dışa açmak için kullanılır.
- **Katman / Kapak Ayırma Farkı (mm):** Üst üste duran parçaların birbirinden ne kadar ekstra mesafe ile ayrılacağını canlı kaydırıcı (slider) ile milimetrik olarak ayarlayabilme imkânı sunar.

### 2. 🎯 Canlı 3B Patlatma Merkezi Küresi (Otomatik veya Sürüklenebilir)
- **Otomatik Merkez:** Tüm montajın geometrik ağırlık merkezini otomatik olarak hesaplar.
- **Manuel Canlı Küre:** 3B sahnede oluşturulan yarı saydam kırmızı referans küresini FreeCAD'in taşıma aracıyla canlı olarak sürüklediğiniz anda, tüm parçaların patlama yönleri ve katmanları gerçek zamanlı olarak yeniden hesaplanır. İsterseniz tek tıkla seçili bir parçayı da patlama merkezi yapabilirsiniz.

### 3. 🔗 Gelişmiş Ürün Gruplama ve Sabit Parça Yöneticisi
Montajdaki bazı alt parçaların kendi içinde dağılmadan tek bir bütün olarak hareket etmesini veya ana şasinin sabit kalmasını sağlayabilirsiniz:
- **Seçilileri Bütün Grup Yap:** Seçilen parçaları ortak bir sınır kutusunda birleştirir; parçalar kendi içlerinde dağılmadan birlikte hareket eder.
- **Seçilileri Sabit Yap:** Seçilen gövdeleri patlatma işleminden hariç tutar; parçalar oldukları yerde sabit kalır.
- **Ağaçtaki Grupları Tek Tıkla İçe Aktar:** FreeCAD unsur ağacındaki mevcut Part veya Grup (Klasör) yapılarını otomatik olarak bütün gruba dönüştürür.
- **Tek Tuşla Yön Ters Çevirme (🔄):** İstediğiniz parçanın veya grubun açılma yönünü (+ / -) tek tıkla tersine çevirebilirsiniz.
- **Geniş Ekran Grup Yöneticisi ve Hızlı Arama:** Çok parçalı projelerde grupları bağımsız büyük pencerede arama çubuğuyla filtreleyerek yönetebilirsiniz.

### 4. 🔩 Akıllı Bağlantı Elemanı (Vida, Somun, Pul) Filtresi
- Model ağacında 07 ile başlayan klasörleri veya adında standart bağlantı elemanı geçen parçaları (M3-M24, DIN, ISO, Vida, Somun, Cıvata, Pul, Rondela, Fastener) otomatik olarak tespit eder.
- Bağlantı elemanlarını tek tıkla Patlatmaya Dahil Edebilir, Olduğu Yerde Sabit Tutabilir veya Sahneden Tamamen Gizleyebilirsiniz.
- Ayrıca "📂 Vida Klasörü Seç..." penceresinden model ağacındaki kendi özel klasörlerinizi işaretleyebilirsiniz.

### 5. 🎬 Süreye veya Hıza Duyarlı MP4 Video ve Animasyon Stüdyosu
Sunumlar, montaj kılavuzları ve müşteri paylaşımları için dahili animasyon motoru içerir:
- **⏱️ Süreye Göre Kayıt:** Toplam video süresini (örneğin: 6,0 saniye) belirleyin; model tam istediğiniz sürede pürüzsüzce patlasın ve tekrar orijinal haline toplansın.
- **🚀 Hıza Göre Kayıt:** Açılma hızını (% / saniye) ve kare hızı değerini (15 - 60 FPS) belirleyin; sabit hızda açılıp kapanan .MP4 (OpenCV ile) veya .PNG kare dizisi çıktısı alın.

## 📄 Ek Modül: Hızlı Ön İmalat ve Malzeme Listesi (BOM) Çıktısı (Beta / Geliştiriliyor)

Makro, patlatma özelliklerine ek olarak atölyeye veya teklif süreçlerine hızlıca ön doküman sunabilmek için dahili bir Hızlı PDF ve BOM Üretici barındırır:
- Genel montajın patlamamış 4 görünüşlü (Ön, Yan, Üst, İzometrik) dış ölçü sayfası,
- Parçaların kütle merkezine hizalanan otomatik poz balonlu Patlatılmış Montaj ve BOM Tablosu (25'ten fazla kalemde otomatik Pafta 1/2, 2/2 bölme desteği),
- Her parça için hızlı 4 görünüşlü imalat bilgi kartı ve Excel uyumlu .CSV malzeme listesi.

## 🚀 Kurulum ve Kullanım

1. Bu depodaki `ExplodedViewAdvanced.py` (veya `.FCMacro`) dosyasını indirin.
2. FreeCAD üst menüsünden **Makro -> Makrolar...** penceresini açın ve dosyayı **Kullanıcı makroları konumu** klasörüne kopyalayın.
3. Herhangi bir montaj veya .STEP dosyasını açın (dosya açmadan da makroyu başlatıp modeli sonradan yükleyebilir ve **🔄 Gizlenenleri / Sahneyi Yenile** butonuna basabilirsiniz).
4. Makroyu çalıştırın ve **🎛️ Manuel Kontrol** sekmesindeki kaydırıcı (slider) üzerinde fare tekerleğinizi çevirerek montajınızı anında patlatın!

## 🗺️ Gelecek Yol Haritası (Roadmap)

- [x] Çok eksenli akıllı katman algoritması ve canlı merkez küresi
- [x] Alt grup (Bütün / Sabit) yöneticisi ve bağlantı elemanı filtresi
- [x] Süre ve hız kontrollü MP4 animasyon motoru
- [ ] Teknik resimde her parça için önizleme ile aşağıdaki kısımların her parça için güncellenebilmesi:
  - GENEL TOLERANS: ISO 2768 - mK
  - YÜZEY KALİTESİ: Ra 3.2 µm (Köşeleri Kır)
  - KENAR İŞLEMİ: Çapakları Alınmış / Keskin Köşe Yok
- [ ] Gelişmiş Teknik Resim Motoru (v2.0): Patlatma kılavuz çizgileri (Explode Trace Lines), gerçek vektörel kesit/detay görünümleri ve gelişmiş otomatik ölçülendirme entegrasyonu
- [ ] Özel patlama senaryolarını (Adım 1: Kapak açılır, Adım 2: Vida çıkar vb.) kaydetme ve geri yükleme

## 📜 Lisans ve Kullanım İzinleri

Bu proje MIT Lisansı altında açık kaynaklı olarak sunulmaktadır. Hem ticari (şirket, mühendislik bürosu, üretim) hem de özel projelerde hiçbir kısıtlama olmaksızın özgürce kullanılabilir, değiştirilebilir ve dağıtılabilir.

## 🤝 Katkıda Bulunma

Önerileriniz, hata bildirimleriniz (Issues) ve geliştirme katkılarınız (Pull Requests) memnuniyetle karşılanır!
