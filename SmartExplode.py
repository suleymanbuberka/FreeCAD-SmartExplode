import FreeCAD
import FreeCADGui
import Part
import math
import os
import tempfile
import sys
import site
import subprocess
import datetime
import csv
import re

# Kullanıcının yerel Python paket yolunu sisteme dahil et
try:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.append(user_site)
except Exception:
    pass

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except ImportError:
    try:
        from PySide2 import QtWidgets, QtGui, QtCore
    except ImportError:
        from PySide import QtGui as QtWidgets
        from PySide import QtGui, QtCore

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False


class FareTekerlekFiltresi(QtCore.QObject):
    """
    Slider üzerinde farenin tekerleği kaydırıldığında patlatma işlemini akıcı hale getirir.
    """
    def eventFilter(self, obj, event):
        try:
            if event.type() == QtCore.QEvent.Wheel:
                delta = event.angleDelta().y() if hasattr(event, 'angleDelta') else event.delta()
                adim = 5
                if delta > 0:
                    obj.setValue(min(obj.maximum(), obj.value() + adim))
                else:
                    obj.setValue(max(obj.minimum(), obj.value() - adim))
                return True
        except Exception:
            pass
        return False


class PatlatilmisGorunumUI(QtWidgets.QWidget):
    def __init__(self):
        super(PatlatilmisGorunumUI, self).__init__()
        self.doc = FreeCAD.ActiveDocument
        self.yukleme_penceresi = None

        self.objects_data = []
        self.gruplar = {}
        self.son_grup_id = 0
        self._liste_secim_guncelleniyor = False
        self._spin_guncelleniyor = False

        self.global_center = FreeCAD.Vector(0, 0, 0)
        self.manuel_merkez_konumu = FreeCAD.Vector(0, 0, 0)
        self.merkez_modu = "Otomatik"
        self.fastener_modu = "dahil"
        self.fastener_klasor_kurallari = ["07", "fastener", "vida", "somun", "civata", "cıvata", "pul", "rondela"]
        self.secili_fastener_klasorleri = set()
        self.varsayilan_kademe_farki = 20.0
        self._ust_klasor_onbellek = {}

        self.zamanlayici = QtCore.QTimer(self)
        self.zamanlayici.timeout.connect(self.animasyon_adimi)
        self.animasyon_yonu = 1
        self.patlama_tipi = "Merkezi (3 Boyutlu Dışa Açılım)"

        self.merkez_takip_timer = QtCore.QTimer(self)
        self.merkez_takip_timer.timeout.connect(self.manuel_merkez_canli_takip)
        self.merkez_takip_timer.start(150)

        # Belge açık olmasa veya sahne boş olsa bile arayüz güvenle açılır
        if self.doc and len(self.doc.Objects) > 0:
            self._yukleme_penceresi_baslat()
            try:
                self.verileri_hazirla()
                if self.objects_data:
                    self._yukleme_durumu_guncelle(85, "⏳ Katman ve kademe mesafeleri hesaplanıyor...")
                    self.kademeleri_hesapla(self.aktif_merkezi_getir())
                self._yukleme_durumu_guncelle(95, "⏳ Kullanıcı arayüzü hazırlanıyor...")
            finally:
                self._yukleme_penceresi_kapat()

        self.arayuzu_olustur()
        self.arayuzu_stillendir()
        FreeCAD.Console.PrintMessage(
            f"[Patlatılmış Görünüm Pro] Arayüz başlatıldı ({len(self.objects_data)} parça yüklü).\n"
        )

    def _yukleme_penceresi_baslat(self):
        try:
            self.yukleme_penceresi = QtWidgets.QProgressDialog(
                "⏳ Makro başlatılıyor, sahnedeki parçalar taranıyor...", None, 0, 100
            )
            self.yukleme_penceresi.setWindowTitle("Patlatılmış Görünüm Pro - Yükleniyor")
            self.yukleme_penceresi.setWindowModality(QtCore.Qt.NonModal)
            self.yukleme_penceresi.setMinimumDuration(0)
            self.yukleme_penceresi.setMinimumSize(420, 105)
            self.yukleme_penceresi.setCancelButton(None)
            self.yukleme_penceresi.setStyleSheet("""
                QProgressDialog { background-color: #1e1e2e; color: #cdd6f4; font-weight: bold; font-size: 12px; }
                QLabel { color: #cdd6f4; font-size: 12px; padding: 6px; }
                QProgressBar { border: 1px solid #45475a; border-radius: 5px; text-align: center; background: #181825; color: #cdd6f4; height: 20px; }
                QProgressBar::chunk { background-color: #89b4fa; border-radius: 4px; }
            """)
            self.yukleme_penceresi.setValue(5)
            self.yukleme_penceresi.show()
            QtWidgets.QApplication.processEvents()
        except Exception:
            self.yukleme_penceresi = None

    def _yukleme_durumu_guncelle(self, yuzde, mesaj):
        try:
            if self.yukleme_penceresi:
                self.yukleme_penceresi.setLabelText(mesaj)
                self.yukleme_penceresi.setValue(int(yuzde))
                QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _yukleme_penceresi_kapat(self):
        try:
            if self.yukleme_penceresi:
                self.yukleme_penceresi.close()
                self.yukleme_penceresi.deleteLater()
                self.yukleme_penceresi = None
                QtWidgets.QApplication.processEvents()
        except Exception:
            self.yukleme_penceresi = None

    def _ust_klasorleri_getir(self, obj):
        """
        Nesnenin bağlı olduğu tüm üst grup, klasör ve App::Part kapsayıcılarını önbellekli olarak döndürür.
        """
        if obj.Name in self._ust_klasor_onbellek:
            return self._ust_klasor_onbellek[obj.Name]

        ustler = set()
        try:
            for u in getattr(obj, "InListRecursive", getattr(obj, "InList", [])):
                ustler.add(u)
        except Exception:
            pass
        try:
            if self.doc:
                for doc_obj in self.doc.Objects:
                    if hasattr(doc_obj, "Group") or doc_obj.TypeId in ("App::DocumentObjectGroup", "App::Part", "App::LinkGroup"):
                        altlar = getattr(doc_obj, "OutListRecursive", getattr(doc_obj, "Group", []))
                        if obj in altlar:
                            ustler.add(doc_obj)
        except Exception:
            pass
        sonuc = list(ustler)
        self._ust_klasor_onbellek[obj.Name] = sonuc
        return sonuc

    def _nesne_gercekten_gorunur_mu(self, obj):
        """
        Nesnenin kendisinin ve içinde bulunduğu tüm üst klasör/Part/Body gruplarının
        3D sahnede gerçekten görünür (Visibility=True) olup olmadığını kontrol eder.
        """
        try:
            if not hasattr(obj, "ViewObject") or not obj.ViewObject or not obj.ViewObject.Visibility:
                return False
            for ust in self._ust_klasorleri_getir(obj):
                if ust.TypeId in ("App::Part", "App::DocumentObjectGroup", "PartDesign::Body", "App::LinkGroup"):
                    if hasattr(ust, "ViewObject") and ust.ViewObject and not ust.ViewObject.Visibility:
                        return False
            return True
        except Exception:
            return False

    def _baglanti_elemani_mi(self, obj, bbox):
        """
        Parçanın vida, somun, cıvata, pul, rondela, perçin gibi bir bağlantı elemanı (Fastener)
        olup olmadığını; kendi adından, üst klasör adından (örn: '07') veya seçilen vida klasörlerinden tespit eder.
        """
        try:
            ust_nesneler = self._ust_klasorleri_getir(obj)
            for ust in ust_nesneler:
                if ust.Name in self.secili_fastener_klasorleri or ust.Label in self.secili_fastener_klasorleri:
                    return True

            ust_isimler = []
            for ust in ust_nesneler:
                if ust.TypeId in ("App::DocumentObjectGroup", "App::Part", "App::LinkGroup") or hasattr(ust, "Group"):
                    ust_isimler.append(getattr(ust, "Name", "").strip().lower())
                    ust_isimler.append(getattr(ust, "Label", "").strip().lower())

            for kural in getattr(self, "fastener_klasor_kurallari", ["07"]):
                k = kural.strip().lower()
                if not k:
                    continue
                for u_isim in ust_isimler:
                    if u_isim.startswith(k) or f"_{k}" in u_isim or f"-{k}" in u_isim or k in u_isim:
                        return True
                obj_label_low = getattr(obj, "Label", "").strip().lower()
                if obj_label_low.startswith(k + "_") or obj_label_low.startswith(k + "-") or obj_label_low.startswith(k + " "):
                    return True

            ad_birlesik = f"{getattr(obj, 'Name', '')} {getattr(obj, 'Label', '')} {getattr(obj, 'TypeId', '')}".lower()
            if hasattr(obj, "Proxy") and obj.Proxy:
                ad_birlesik += " " + str(type(obj.Proxy)).lower()

            anahtar_kelimeler = (
                "screw", "bolt", "nut", "washer", "fastener", "rivet", "pin", "stud", "standoff", "insert", "dowel",
                "vida", "civata", "cıvata", "somun", "pul", "rondela", "percin", "perçin", "saplama", "pim", "setuskur",
                "iso4014", "iso4017", "iso4762", "iso7380", "iso4032", "iso7089", "iso7090", "din912", "din933", "din934", "din125", "din7991"
            )
            for k in anahtar_kelimeler:
                if k in ad_birlesik:
                    return True

            if re.search(r'\bm([2-9]|1[0-9]|2[0-4])([x_-]\d+|\b)', ad_birlesik):
                if max(bbox.XLength, bbox.YLength, bbox.ZLength) < 140.0:
                    return True
        except Exception:
            pass
        return False

    def _ic_unsur_mu(self, obj):
        """
        PartDesign::Body içerisindeki ara işlemleri (Pad, Pocket vb.) ve Origin düzlemlerini eler.
        """
        try:
            if obj.TypeId.startswith("PartDesign::") and obj.TypeId != "PartDesign::Body":
                return True
            for ust in self._ust_klasorleri_getir(obj):
                if ust.TypeId == "App::Origin":
                    return True
                if ust.TypeId == "PartDesign::Body" and obj != ust:
                    return True
                if hasattr(ust, "ViewObject") and ust.ViewObject and not ust.ViewObject.Visibility:
                    if ust.TypeId in ("App::Part", "App::DocumentObjectGroup", "PartDesign::Body"):
                        return True
        except Exception:
            pass
        return False

    def _gecerli_kati_parca_mi(self, obj):
        """
        Nesnenin XY düzlemi (App::Plane), eksen çizgisi (App::Line) veya yüzey gibi
        hacimsiz/sonsuz bir yardımcı unsur olmadığını; gerçek bir 3B parça olduğunu doğrular.
        """
        try:
            if obj.derivedFrom("App::Plane") if hasattr(obj, "derivedFrom") else False:
                return False
            if obj.TypeId in ("App::Plane", "App::Line", "App::Placement", "App::OriginGroupExtension"):
                return False

            ad_etiket = f"{getattr(obj, 'Name', '')} {getattr(obj, 'Label', '')}".lower()
            gecersiz_isimler = (
                "xy_plane", "xz_plane", "yz_plane", "x_axis", "y_axis", "z_axis", "origin",
                "xy düzlemi", "xz düzlemi", "yz düzlemi", "x ekseni", "y ekseni", "z ekseni",
                "xy duzlemi", "xz duzlemi", "yz duzlemi"
            )
            for g_isim in gecersiz_isimler:
                if g_isim in ad_etiket:
                    return False

            sh = getattr(obj, "Shape", None)
            if not sh or sh.isNull():
                return False

            bbox = sh.BoundBox
            if not bbox.isValid():
                return False

            if bbox.DiagonalLength < 1e-3 or bbox.DiagonalLength > 5e5:
                return False

            has_solid = hasattr(sh, "Solids") and len(sh.Solids) > 0
            has_faces = hasattr(sh, "Faces") and len(sh.Faces) > 0
            if not has_solid and not has_faces:
                return False

            kalinliklar = sorted([bbox.XLength, bbox.YLength, bbox.ZLength])
            if kalinliklar[0] < 1e-4 and abs(getattr(sh, "Volume", 0.0)) < 1e-3:
                return False

            return True
        except Exception:
            return False

    def _hassas_sinir_kutusu_getir(self, obj):
        try:
            sh = obj.Shape
            if hasattr(obj, "getGlobalPlacement"):
                g_pl = obj.getGlobalPlacement()
                l_pl = obj.Placement
                if (g_pl.Base - l_pl.Base).Length > 1e-4 or not g_pl.Rotation.isSame(l_pl.Rotation, 1e-4):
                    sh_copy = sh.copy()
                    fark = g_pl.multiply(l_pl.inverse())
                    sh_copy.transformShape(fark.toMatrix())
                    return sh_copy.BoundBox
            return sh.BoundBox
        except Exception:
            return obj.Shape.BoundBox

    def _parca_isaretleme_noktasi_getir(self, obj):
        """
        Parçanın teknik resimde balon okuyla işaretleneceği en ideal 3B noktayı bulur:
        1. Parçanın kütle merkezini (CenterOfMass) hesaplar.
        2. Eğer parça ortası boş bir çerçeve, U-büküm veya L-sac ise ve kütle merkezi
           boşluğa düşüyorsa, kütle merkezine en yakın gerçek parça yüzey noktasını (distToShape) alır.
        3. Üst montaj (Global Placement) dönüşümünü hesaba katar.
        """
        try:
            sh = obj.Shape
            if not sh or sh.isNull():
                return FreeCAD.Vector(0, 0, 0)

            if hasattr( obj, "getGlobalPlacement"):
                g_pl = obj.getGlobalPlacement()
                l_pl = obj.Placement
                if (g_pl.Base - l_pl.Base).Length > 1e-4 or not g_pl.Rotation.isSame(l_pl.Rotation, 1e-4):
                    sh = sh.copy()
                    fark = g_pl.multiply(l_pl.inverse())
                    sh.transformShape(fark.toMatrix())

            hedef_kati = sh.Solids[0] if (hasattr(sh, "Solids") and len(sh.Solids) > 0) else sh

            merkez = None
            if hasattr(hedef_kati, "CenterOfMass"):
                merkez = FreeCAD.Vector(hedef_kati.CenterOfMass)
            elif hasattr(sh, "CenterOfMass"):
                merkez = FreeCAD.Vector(sh.CenterOfMass)
            else:
                merkez = FreeCAD.Vector(hedef_kati.BoundBox.Center)

            # Kütle merkezi parçanın et kalınlığı dışında (boşlukta) kalıyorsa yüzeye mıknatısla
            try:
                v_nokta = Part.Vertex(merkez)
                mesafe, noktalar, _ = hedef_kati.distToShape(v_nokta)
                if mesafe > 0.5 and noktalar and len(noktalar[0]) >= 2:
                    merkez = FreeCAD.Vector(noktalar[0][1])
            except Exception:
                pass

            return merkez
        except Exception:
            try:
                return FreeCAD.Vector(obj.Shape.BoundBox.Center)
            except Exception:
                return FreeCAD.Vector(0, 0, 0)

    def verileri_hazirla(self):
        self.doc = FreeCAD.ActiveDocument
        if not self.doc:
            self.objects_data = []
            return

        self._ust_klasor_onbellek.clear()
        gecerli_nesneler = []
        yasakli_tipler = (
            "Sketcher::",
            "PartDesign::Plane",
            "PartDesign::Line",
            "PartDesign::Point",
            "PartDesign::CoordinateSystem",
            "App::Origin",
            "App::Plane",
            "App::Line",
            "App::Placement",
            "App::Annotation",
            "App::DocumentObjectGroup",
            "App::Part",
            "TechDraw::",
            "Spreadsheet::"
        )

        tum_objeler = list(self.doc.Objects)
        toplam_obj = max(1, len(tum_objeler))

        for idx, obj in enumerate(tum_objeler):
            if idx % 10 == 0:
                oran = 5 + int((idx / toplam_obj) * 35)
                self._yukleme_durumu_guncelle(oran, f"⏳ Sahne taranıyor ({idx}/{toplam_obj})...")
            try:
                if obj.Name == "PATLATMA_MERKEZI":
                    continue
                if obj.TypeId.startswith(yasakli_tipler):
                    continue
                if self._ic_unsur_mu(obj):
                    continue
                if hasattr(obj, "Group") and len(getattr(obj, "Group", [])) > 0:
                    continue
                if hasattr(obj, "Shape") and hasattr(obj, "Placement"):
                    if self._nesne_gercekten_gorunur_mu(obj):
                        if self._gecerli_kati_parca_mi(obj):
                            gecerli_nesneler.append(obj)
            except Exception:
                continue

        self.objects_data = []
        if not gecerli_nesneler:
            return

        min_x = min_y = min_z = float('inf')
        max_x = max_y = max_z = float('-inf')
        toplam_boyut = 0.0
        gecerli_sayisi = len(gecerli_nesneler)

        for idx, obj in enumerate(gecerli_nesneler):
            if idx % 5 == 0 or idx == gecerli_sayisi - 1:
                oran = 40 + int(((idx + 1) / gecerli_sayisi) * 40)
                self._yukleme_durumu_guncelle(
                    oran, f"⏳ Parça geometrileri analiz ediliyor ({idx + 1}/{gecerli_sayisi}): {obj.Label}"
                )
            try:
                orijinal_konum = obj.Placement.copy()
                bbox = self._hassas_sinir_kutusu_getir(obj)
                nesne_merkezi = FreeCAD.Vector(bbox.Center)

                min_x = min(min_x, bbox.XMin)
                min_y = min(min_y, bbox.YMin)
                min_z = min(min_z, bbox.ZMin)
                max_x = max(max_x, bbox.XMax)
                max_y = max(max_y, bbox.YMax)
                max_z = max(max_z, bbox.ZMax)

                ortalama_parca_boyu = (bbox.XLength + bbox.YLength + bbox.ZLength) / 3.0
                toplam_boyut += ortalama_parca_boyu

                fastener_tespit = self._baglanti_elemani_mi(obj, bbox)
                try:
                    hacim_cm3 = abs(obj.Shape.Volume) / 1000.0
                except Exception:
                    hacim_cm3 = (bbox.XLength * bbox.YLength * bbox.ZLength) / 1000.0

                ozel_malzeme = None
                for prop_ad in ("Material", "Mat", "Malzeme"):
                    if hasattr(obj, prop_ad):
                        deger = getattr(obj, prop_ad)
                        if isinstance(deger, str) and deger.strip():
                            ozel_malzeme = deger.strip()
                            break
                        elif hasattr(deger, "Label"):
                            ozel_malzeme = str(deger.Label).strip()
                            break

                self.objects_data.append({
                    'obj': obj,
                    'orijinal_konum': orijinal_konum,
                    'bbox_merkez': nesne_merkezi,
                    'aktif_merkez_ref': FreeCAD.Vector(nesne_merkezi),
                    'manuel_carpan': 1.0,
                    'xmin': bbox.XMin, 'xmax': bbox.XMax,
                    'ymin': bbox.YMin, 'ymax': bbox.YMax,
                    'zmin': bbox.ZMin, 'zmax': bbox.ZMax,
                    'aktif_xmin': bbox.XMin, 'aktif_xmax': bbox.XMax,
                    'aktif_ymin': bbox.YMin, 'aktif_ymax': bbox.YMax,
                    'aktif_zmin': bbox.ZMin, 'aktif_zmax': bbox.ZMax,
                    'uzunluk_x': bbox.XLength,
                    'uzunluk_y': bbox.YLength,
                    'uzunluk_z': bbox.ZLength,
                    'hacim_cm3': hacim_cm3,
                    'ozel_malzeme': ozel_malzeme,
                    'kademe_x': 0,
                    'kademe_y': 0,
                    'kademe_z': 0,
                    'kademe_r': 0,
                    'grup_id': None,
                    'sabit_mi': False,
                    'fastener_mi': fastener_tespit,
                    'kullanici_gizledi': False
                })
            except Exception:
                continue

        if not self.objects_data:
            return

        self.global_center = FreeCAD.Vector((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0)

        mevcut_kure = self.doc.getObject("PATLATMA_MERKEZI")
        if mevcut_kure:
            self.manuel_merkez_konumu = FreeCAD.Vector(mevcut_kure.Placement.Base)
        else:
            self.manuel_merkez_konumu = FreeCAD.Vector(self.global_center)

        ort_boyut = toplam_boyut / max(1, len(self.objects_data))
        self.varsayilan_kademe_farki = max(10.0, min(80.0, round(ort_boyut * 0.45, 1)))

    def grup_geometrilerini_guncelle(self):
        for d in self.objects_data:
            d['grup_id'] = None
            d['sabit_mi'] = (self.fastener_modu in ("sabit", "gizle") and d.get('fastener_mi', False))
            d['aktif_merkez_ref'] = FreeCAD.Vector(d['bbox_merkez'])
            d['aktif_xmin'], d['aktif_xmax'] = d['xmin'], d['xmax']
            d['aktif_ymin'], d['aktif_ymax'] = d['ymin'], d['ymax']
            d['aktif_zmin'], d['aktif_zmax'] = d['zmin'], d['zmax']

        for gid, ginfo in self.gruplar.items():
            uye_datalari = [d for d in self.objects_data if d['obj'] in ginfo['uyeler']]
            if not uye_datalari:
                continue

            grup_tamamen_fastener = all(d.get('fastener_mi', False) for d in uye_datalari)
            if grup_tamamen_fastener:
                ginfo['fastener_mi'] = True

            if ginfo['mod'] == 'sabit':
                for d in uye_datalari:
                    d['grup_id'] = gid
                    d['sabit_mi'] = True
            else:
                g_xmin = min(d['xmin'] for d in uye_datalari)
                g_xmax = max(d['xmax'] for d in uye_datalari)
                g_ymin = min(d['ymin'] for d in uye_datalari)
                g_ymax = max(d['ymax'] for d in uye_datalari)
                g_zmin = min(d['zmin'] for d in uye_datalari)
                g_zmax = max(d['zmax'] for d in uye_datalari)
                ortak_merkez = FreeCAD.Vector(
                    (g_xmin + g_xmax) / 2.0,
                    (g_ymin + g_ymax) / 2.0,
                    (g_zmin + g_zmax) / 2.0
                )
                for d in uye_datalari:
                    d['grup_id'] = gid
                    d['sabit_mi'] = False
                    d['aktif_merkez_ref'] = FreeCAD.Vector(ortak_merkez)
                    d['aktif_xmin'], d['aktif_xmax'] = g_xmin, g_xmax
                    d['aktif_ymin'], d['aktif_ymax'] = g_ymin, g_ymax
                    d['aktif_zmin'], d['aktif_zmax'] = g_zmin, g_zmax
                    d['manuel_carpan'] = ginfo.get('manuel_carpan', 1.0)

    def _eksen_kademesi_ata(self, liste, anahtar_fonk, kademe_adi, ters_sirala=False, katman_toleransi=2.5):
        if not liste:
            return
        sirali = sorted(liste, key=anahtar_fonk, reverse=ters_sirala)
        mevcut_kademe = 1
        onceki_deger = anahtar_fonk(sirali[0])[0]

        for d in sirali:
            guncel_deger = anahtar_fonk(d)[0]
            if abs(guncel_deger - onceki_deger) > katman_toleransi:
                mevcut_kademe += 1
                onceki_deger = guncel_deger
            d[kademe_adi] = -mevcut_kademe if ters_sirala else mevcut_kademe

    def kademeleri_hesapla(self, aktif_merkez):
        self.grup_geometrilerini_guncelle()
        tolerans = 0.5

        hareketli_parcalar = [d for d in self.objects_data if not d['sabit_mi']]
        for d in self.objects_data:
            if d['sabit_mi']:
                d['kademe_x'] = d['kademe_y'] = d['kademe_z'] = d['kademe_r'] = 0

        poz_x = [d for d in hareketli_parcalar if d['aktif_merkez_ref'].x > aktif_merkez.x + tolerans]
        neg_x = [d for d in hareketli_parcalar if d['aktif_merkez_ref'].x < aktif_merkez.x - tolerans]
        mer_x = [d for d in hareketli_parcalar if abs(d['aktif_merkez_ref'].x - aktif_merkez.x) <= tolerans]
        for d in mer_x:
            d['kademe_x'] = 0
        self._eksen_kademesi_ata(poz_x, lambda d: (round(d['aktif_xmax'], 1), round(d['aktif_merkez_ref'].x, 1)), 'kademe_x', ters_sirala=False)
        self._eksen_kademesi_ata(neg_x, lambda d: (round(d['aktif_xmin'], 1), round(d['aktif_merkez_ref'].x, 1)), 'kademe_x', ters_sirala=True)

        poz_y = [d for d in hareketli_parcalar if d['aktif_merkez_ref'].y > aktif_merkez.y + tolerans]
        neg_y = [d for d in hareketli_parcalar if d['aktif_merkez_ref'].y < aktif_merkez.y - tolerans]
        mer_y = [d for d in hareketli_parcalar if abs(d['aktif_merkez_ref'].y - aktif_merkez.y) <= tolerans]
        for d in mer_y:
            d['kademe_y'] = 0
        self._eksen_kademesi_ata(poz_y, lambda d: (round(d['aktif_ymax'], 1), round(d['aktif_merkez_ref'].y, 1)), 'kademe_y', ters_sirala=False)
        self._eksen_kademesi_ata(neg_y, lambda d: (round(d['aktif_ymin'], 1), round(d['aktif_merkez_ref'].y, 1)), 'kademe_y', ters_sirala=True)

        poz_z = [d for d in hareketli_parcalar if d['aktif_merkez_ref'].z > aktif_merkez.z + tolerans]
        neg_z = [d for d in hareketli_parcalar if d['aktif_merkez_ref'].z < aktif_merkez.z - tolerans]
        mer_z = [d for d in hareketli_parcalar if abs(d['aktif_merkez_ref'].z - aktif_merkez.z) <= tolerans]
        for d in mer_z:
            d['kademe_z'] = 0
        self._eksen_kademesi_ata(poz_z, lambda d: (round(d['aktif_zmax'], 1), round(d['aktif_merkez_ref'].z, 1)), 'kademe_z', ters_sirala=False)
        self._eksen_kademesi_ata(neg_z, lambda d: (round(d['aktif_zmin'], 1), round(d['aktif_merkez_ref'].z, 1)), 'kademe_z', ters_sirala=True)

        sirali_r = sorted(hareketli_parcalar, key=lambda d: (d['aktif_merkez_ref'] - aktif_merkez).Length)
        for idx, d in enumerate(sirali_r):
            mesafe = (d['aktif_merkez_ref'] - aktif_merkez).Length
            d['kademe_r'] = 0 if mesafe <= tolerans else idx

    def arayuzu_olustur(self):
        self.setWindowTitle("Gelişmiş Patlatılmış Görünüm ve Teknik Resim Paketi (Pro)")
        self.setWindowFlags(
            QtCore.Qt.Window |
            QtCore.Qt.WindowMinimizeButtonHint |
            QtCore.Qt.WindowMaximizeButtonHint |
            QtCore.Qt.WindowCloseButtonHint
        )

        ekran_geo = QtCore.QRect(0, 0, 1366, 768)
        try:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen:
                ekran_geo = screen.availableGeometry()
        except Exception:
            pass

        hedef_w = min(760, max(360, int(ekran_geo.width() * 0.52)))
        hedef_h = min(860, max(340, int(ekran_geo.height() * 0.86)))
        self.setMinimumSize(260, 220)
        self.resize(hedef_w, hedef_h)

        dis_layout = QtWidgets.QVBoxLayout(self)
        dis_layout.setContentsMargins(6, 6, 6, 6)
        dis_layout.setSpacing(4)

        self.scroll_area = QtWidgets.QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.scroll_area.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        icerik_widget = QtWidgets.QWidget()
        icerik_widget.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        ana_layout = QtWidgets.QVBoxLayout(icerik_widget)
        ana_layout.setContentsMargins(4, 4, 4, 4)
        ana_layout.setSpacing(6)

        self.tekerlek_filtresi = FareTekerlekFiltresi(self)

        grup_ayarlar = QtWidgets.QGroupBox("Patlatma Stili ve Katman Ayarları")
        ayarlar_layout = QtWidgets.QVBoxLayout()
        ayarlar_layout.setContentsMargins(8, 10, 8, 8)
        ayarlar_layout.setSpacing(5)

        tip_layout = QtWidgets.QHBoxLayout()
        tip_layout.addWidget(QtWidgets.QLabel("Patlatma Yönü/Stili:"))
        self.combo_tip = QtWidgets.QComboBox()
        self.combo_tip.addItems([
            "Merkezi (3 Boyutlu Dışa Açılım)",
            "Sadece Z Ekseni (Alt-Üst Katmanlı)",
            "Sadece X Ekseni (Sağ-Sol Katmanlı)",
            "Sadece Y Ekseni (Ön-Arka Katmanlı)",
            "XY Düzlemi (Yatay Çevresel Açılım)"
        ])
        self.combo_tip.currentTextChanged.connect(self.tip_degistir)
        tip_layout.addWidget(self.combo_tip)
        ayarlar_layout.addLayout(tip_layout)

        kademe_layout = QtWidgets.QHBoxLayout()
        lbl_kademe = QtWidgets.QLabel("Katman / Kapak Ayırma Farkı (mm):")
        lbl_kademe.setToolTip("Üst üste duran parçaların patlama sırasında birbirinden ne kadar ekstra açılacağını belirler.")
        kademe_layout.addWidget(lbl_kademe)

        self.slider_kademe = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_kademe.setRange(0, 150)
        self.slider_kademe.setValue(int(self.varsayilan_kademe_farki))
        self.slider_kademe.installEventFilter(self.tekerlek_filtresi)
        kademe_layout.addWidget(self.slider_kademe)

        self.spin_kademe = QtWidgets.QSpinBox()
        self.spin_kademe.setRange(0, 300)
        self.spin_kademe.setSuffix(" mm")
        self.spin_kademe.setValue(int(self.varsayilan_kademe_farki))
        kademe_layout.addWidget(self.spin_kademe)

        self.slider_kademe.valueChanged.connect(self.spin_kademe.setValue)
        self.spin_kademe.valueChanged.connect(self.kademe_farki_degisti)
        ayarlar_layout.addLayout(kademe_layout)

        self.btn_yon_ters_cevir = QtWidgets.QPushButton("🔄 Seçili Parçanın / Grubun Patlama Yönünü Ters Çevir")
        self.btn_yon_ters_cevir.clicked.connect(self.secili_yon_ters_cevir)
        ayarlar_layout.addWidget(self.btn_yon_ters_cevir)

        grup_ayarlar.setLayout(ayarlar_layout)
        ana_layout.addWidget(grup_ayarlar)

        grup_merkez = QtWidgets.QGroupBox("Patlama Merkezi Kontrolü (Seçiminiz Geçişlerde Korunur)")
        merkez_ana_layout = QtWidgets.QVBoxLayout()

        mod_secim_layout = QtWidgets.QHBoxLayout()
        self.btn_oto_merkez = QtWidgets.QPushButton("🌐 Otomatik Merkez")
        self.btn_oto_merkez.setCheckable(True)
        self.btn_oto_merkez.setChecked(True)
        self.btn_oto_merkez.clicked.connect(lambda: self.merkez_modu_ayarla("Otomatik"))

        self.btn_manuel_merkez = QtWidgets.QPushButton("🎯 Manuel Merkez (Sabit / Sürükle)")
        self.btn_manuel_merkez.setCheckable(True)
        self.btn_manuel_merkez.clicked.connect(lambda: self.merkez_modu_ayarla("Manuel"))

        mod_secim_layout.addWidget(self.btn_oto_merkez)
        mod_secim_layout.addWidget(self.btn_manuel_merkez)
        merkez_ana_layout.addLayout(mod_secim_layout)

        self.manuel_merkez_panel = QtWidgets.QWidget()
        manuel_panel_layout = QtWidgets.QVBoxLayout()
        manuel_panel_layout.setContentsMargins(0, 6, 0, 0)

        merkez_aksiyon_layout = QtWidgets.QHBoxLayout()
        self.btn_kure_tasi = QtWidgets.QPushButton("✋ Küreyi 3D Ekranda Taşı")
        self.btn_kure_tasi.clicked.connect(self.kure_tasima_aracini_ac)
        merkez_aksiyon_layout.addWidget(self.btn_kure_tasi)

        self.btn_seciliyi_merkez_yap = QtWidgets.QPushButton("📍 Seçili Parçayı Merkez Yap")
        self.btn_seciliyi_merkez_yap.clicked.connect(self.secili_parcayi_merkez_yap)
        merkez_aksiyon_layout.addWidget(self.btn_seciliyi_merkez_yap)

        self.chk_kure_goster = QtWidgets.QCheckBox("Küreyi Göster")
        self.chk_kure_goster.setChecked(True)
        self.chk_kure_goster.toggled.connect(self.kure_gorunurluk_degistir)
        merkez_aksiyon_layout.addWidget(self.chk_kure_goster)
        manuel_panel_layout.addLayout(merkez_aksiyon_layout)

        koord_layout = QtWidgets.QHBoxLayout()
        koord_layout.addWidget(QtWidgets.QLabel("Merkez Konumu:"))

        self.spin_merkez_x = QtWidgets.QDoubleSpinBox()
        self.spin_merkez_x.setRange(-99999.0, 99999.0)
        self.spin_merkez_x.setPrefix("X: ")
        self.spin_merkez_x.setSingleStep(5.0)
        self.spin_merkez_x.valueChanged.connect(self.spin_merkez_degisti)
        koord_layout.addWidget(self.spin_merkez_x)

        self.spin_merkez_y = QtWidgets.QDoubleSpinBox()
        self.spin_merkez_y.setRange(-99999.0, 99999.0)
        self.spin_merkez_y.setPrefix("Y: ")
        self.spin_merkez_y.setSingleStep(5.0)
        self.spin_merkez_y.valueChanged.connect(self.spin_merkez_degisti)
        koord_layout.addWidget(self.spin_merkez_y)

        self.spin_merkez_z = QtWidgets.QDoubleSpinBox()
        self.spin_merkez_z.setRange(-99999.0, 99999.0)
        self.spin_merkez_z.setPrefix("Z: ")
        self.spin_merkez_z.setSingleStep(5.0)
        self.spin_merkez_z.valueChanged.connect(self.spin_merkez_degisti)
        koord_layout.addWidget(self.spin_merkez_z)

        self.btn_merkez_ortala = QtWidgets.QPushButton("Ortala")
        self.btn_merkez_ortala.clicked.connect(self.manuel_merkezi_ortala)
        koord_layout.addWidget(self.btn_merkez_ortala)

        manuel_panel_layout.addLayout(koord_layout)
        self.manuel_merkez_panel.setLayout(manuel_panel_layout)
        self.manuel_merkez_panel.setVisible(False)
        merkez_ana_layout.addWidget(self.manuel_merkez_panel)

        grup_merkez.setLayout(merkez_ana_layout)
        ana_layout.addWidget(grup_merkez)

        self.spinleri_konumla_esitle(self.manuel_merkez_konumu)

        self.splitter_ana = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.splitter_ana.setChildrenCollapsible(True)
        self.splitter_ana.setHandleWidth(8)

        grup_butunluk = QtWidgets.QGroupBox("Ürün Grupları, Sabit Parçalar ve Vida/Somun Klasörleri")
        butunluk_layout = QtWidgets.QVBoxLayout()
        butunluk_layout.setContentsMargins(8, 10, 8, 8)
        butunluk_layout.setSpacing(5)

        liste_arac_layout = QtWidgets.QHBoxLayout()
        self.txt_grup_ara = QtWidgets.QLineEdit()
        self.txt_grup_ara.setPlaceholderText("🔍 Grup veya parça ara (Hızlı filtrele)...")
        self.txt_grup_ara.setClearButtonEnabled(True)
        self.txt_grup_ara.textChanged.connect(self.grup_listesini_filtrele)
        liste_arac_layout.addWidget(self.txt_grup_ara, 1)

        self.btn_liste_boyut = QtWidgets.QPushButton("↕ Listeyi Genişlet")
        self.btn_liste_boyut.setCheckable(True)
        self.btn_liste_boyut.setToolTip("Grup listesinin yüksekliğini tek tıkla iki katına çıkarır veya küçültür.")
        self.btn_liste_boyut.toggled.connect(self.liste_yuksekligini_degistir)
        liste_arac_layout.addWidget(self.btn_liste_boyut)

        self.btn_ayri_pencere = QtWidgets.QPushButton("🗗 Ayrı Pencerede Yönet")
        self.btn_ayri_pencere.setToolTip("Çok sayıda grup olduğunda listeyi geniş ve bağımsız bir pencerede açar.")
        self.btn_ayri_pencere.clicked.connect(self.grup_yoneticisi_penceresi_ac)
        liste_arac_layout.addWidget(self.btn_ayri_pencere)
        butunluk_layout.addLayout(liste_arac_layout)

        ust_aksiyon_layout = QtWidgets.QHBoxLayout()
        self.btn_grup_olustur = QtWidgets.QPushButton("🔗 Seçilileri Bütün Grup Yap")
        self.btn_grup_olustur.setToolTip("Seçtiğiniz parçaları tek bir bütün kabul eder; kendi içlerinde dağılmadan birlikte hareket ederler.")
        self.btn_grup_olustur.clicked.connect(lambda: self.secililerden_grup_olustur(mod="butun"))
        ust_aksiyon_layout.addWidget(self.btn_grup_olustur)

        self.btn_sabit_yap = QtWidgets.QPushButton("🔒 Seçilileri Açılıma Dahil Etme (Sabit)")
        self.btn_sabit_yap.setToolTip("Seçtiğiniz parçaları patlatma işleminden hariç tutar, oldukları yerde sabit kalırlar.")
        self.btn_sabit_yap.clicked.connect(lambda: self.secililerden_grup_olustur(mod="sabit"))
        ust_aksiyon_layout.addWidget(self.btn_sabit_yap)

        self.btn_agactan_grupla = QtWidgets.QPushButton("📂 Ağaçtaki Grupları Al")
        self.btn_agactan_grupla.setToolTip("FreeCAD unsur ağacındaki mevcut klasör/grup yapılarını otomatik olarak bütün grup yapar.")
        self.btn_agactan_grupla.clicked.connect(self.freecad_gruplarini_ice_aktar)
        ust_aksiyon_layout.addWidget(self.btn_agactan_grupla)
        butunluk_layout.addLayout(ust_aksiyon_layout)

        self.agac_gruplar = QtWidgets.QTreeWidget()
        self.agac_gruplar.setHeaderLabels(["Grup / Parça Adı", "Davranış Modu", "Parça Sayısı", "Yön"])
        self.agac_gruplar.setAlternatingRowColors(True)
        self.agac_gruplar.setRootIsDecorated(True)
        header = self.agac_gruplar.header()
        header.setStretchLastSection(False)
        try:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        except Exception:
            self.agac_gruplar.setColumnWidth(0, 220)
            self.agac_gruplar.setColumnWidth(1, 165)
            self.agac_gruplar.setColumnWidth(2, 85)
            self.agac_gruplar.setColumnWidth(3, 65)

        self.agac_gruplar.setMinimumHeight(90)
        self.agac_gruplar.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.agac_gruplar.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.agac_gruplar.itemSelectionChanged.connect(self.agac_secimi_degisti)
        self.agac_gruplar.itemDoubleClicked.connect(lambda *_: self.secili_grup_modunu_degistir())
        butunluk_layout.addWidget(self.agac_gruplar, 1)

        alt_aksiyon_layout = QtWidgets.QHBoxLayout()
        self.btn_mod_degistir = QtWidgets.QPushButton("🔁 Mod Değiştir (Bütün ↔ Sabit)")
        self.btn_mod_degistir.clicked.connect(self.secili_grup_modunu_degistir)
        alt_aksiyon_layout.addWidget(self.btn_mod_degistir)

        self.btn_gruba_ekle = QtWidgets.QPushButton("➕ 3D Seçiliyi Gruba Ekle")
        self.btn_gruba_ekle.clicked.connect(self.secili_gruba_parca_ekle)
        alt_aksiyon_layout.addWidget(self.btn_gruba_ekle)

        self.btn_grup_sil = QtWidgets.QPushButton("❌ Seçiliyi Kaldır")
        self.btn_grup_sil.clicked.connect(self.secili_grubu_kaldir)
        alt_aksiyon_layout.addWidget(self.btn_grup_sil)

        self.btn_gruplari_temizle = QtWidgets.QPushButton("🧹 Tümünü Çöz")
        self.btn_gruplari_temizle.clicked.connect(self.tum_gruplari_temizle)
        alt_aksiyon_layout.addWidget(self.btn_gruplari_temizle)

        butunluk_layout.addLayout(alt_aksiyon_layout)

        fastener_ust_layout = QtWidgets.QHBoxLayout()
        fastener_sayisi = sum(1 for d in self.objects_data if d.get('fastener_mi'))
        self.lbl_fastener_bilgi = QtWidgets.QLabel(f"🔩 Bağlantı El. ({fastener_sayisi} Adet):")
        fastener_ust_layout.addWidget(self.lbl_fastener_bilgi)

        self.combo_fastener_mod = QtWidgets.QComboBox()
        self.combo_fastener_mod.addItems([
            "Patlatmaya Dahil Et (Normal)",
            "Patlatmadan Hariç Tut (Sabit Kalsın)",
            "Patlatmada Sahneden Gizle"
        ])
        self.combo_fastener_mod.currentIndexChanged.connect(self.fastener_modu_degisti)
        fastener_ust_layout.addWidget(self.combo_fastener_mod, 1)

        self.btn_fastener_isaretle = QtWidgets.QPushButton("🔩 Seçiliyi Vida Yap/Çıkar")
        self.btn_fastener_isaretle.setToolTip("3D ekranda veya ağaçta seçtiğiniz parçaları/klasörü bağlantı elemanı yapar veya çıkarır.")
        self.btn_fastener_isaretle.clicked.connect(self.secili_fastener_durumu_degistir)
        fastener_ust_layout.addWidget(self.btn_fastener_isaretle)
        butunluk_layout.addLayout(fastener_ust_layout)

        fastener_klasor_layout = QtWidgets.QHBoxLayout()
        lbl_kl_kural = QtWidgets.QLabel("📁 Vida Klasör/Önek:")
        lbl_kl_kural.setToolTip("Bu kelimelerle başlayan veya içeren klasörlerin (örn: 07) içindeki tüm parçalar otomatik vida/somun kabul edilir.")
        fastener_klasor_layout.addWidget(lbl_kl_kural)

        self.txt_fastener_kural = QtWidgets.QLineEdit("07, fastener, vida, somun, civata, pul")
        self.txt_fastener_kural.setPlaceholderText("Örn: 07, vida, somun...")
        self.txt_fastener_kural.editingFinished.connect(self.fastener_klasor_kurali_uygula)
        fastener_klasor_layout.addWidget(self.txt_fastener_kural, 1)

        self.btn_klasor_sec_fastener = QtWidgets.QPushButton("📂 Vida Klasörü Seç...")
        self.btn_klasor_sec_fastener.setToolTip("Model ağacındaki klasörlerden hangilerinin vida/somun klasörü olduğunu listeden seçin.")
        self.btn_klasor_sec_fastener.clicked.connect(self.vida_klasoru_secim_diyalogu_ac)
        fastener_klasor_layout.addWidget(self.btn_klasor_sec_fastener)
        butunluk_layout.addLayout(fastener_klasor_layout)

        grup_butunluk.setLayout(butunluk_layout)
        self.splitter_ana.addWidget(grup_butunluk)

        self.sekmeler = QtWidgets.QTabWidget()
        self.sekmeler.setUsesScrollButtons(True)
        self.sekmeler.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

        manuel_tab = QtWidgets.QWidget()
        manuel_layout = QtWidgets.QVBoxLayout()
        self.label = QtWidgets.QLabel("Açılma Oranı: %0\n(Slider üzerine gelin ve farenin tekerleğini kaydırın)")
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        manuel_layout.addWidget(self.label)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(250)
        self.slider.setValue(0)
        self.slider.valueChanged.connect(self.slider_degisti)
        self.slider.installEventFilter(self.tekerlek_filtresi)
        manuel_layout.addWidget(self.slider)

        manuel_tab.setLayout(manuel_layout)
        self.sekmeler.addTab(manuel_tab, "🎛️ Manuel Kontrol")

        oto_tab = QtWidgets.QWidget()
        oto_layout = QtWidgets.QVBoxLayout()
        hiz_layout = QtWidgets.QHBoxLayout()
        hiz_layout.addWidget(QtWidgets.QLabel("Animasyon Hızı (Gecikme ms):"))
        self.spin_hiz = QtWidgets.QSpinBox()
        self.spin_hiz.setRange(10, 200)
        self.spin_hiz.setValue(30)
        hiz_layout.addWidget(self.spin_hiz)
        oto_layout.addLayout(hiz_layout)

        sinir_layout = QtWidgets.QHBoxLayout()
        sinir_layout.addWidget(QtWidgets.QLabel("Açılma Sınırı (Max %):"))
        self.spin_sinir = QtWidgets.QSpinBox()
        self.spin_sinir.setRange(50, 400)
        self.spin_sinir.setValue(120)
        sinir_layout.addWidget(self.spin_sinir)
        oto_layout.addLayout(sinir_layout)

        self.btn_animasyon = QtWidgets.QPushButton("▶ Animasyonu Başlat")
        self.btn_animasyon.setCheckable(True)
        self.btn_animasyon.clicked.connect(self.animasyon_tetikle)
        oto_layout.addWidget(self.btn_animasyon)
        oto_tab.setLayout(oto_layout)
        self.sekmeler.addTab(oto_tab, "🔄 Otomatik Döngü")

        pdf_tab = QtWidgets.QWidget()
        pdf_layout = QtWidgets.QVBoxLayout()
        pdf_layout.setSpacing(6)

        kimlik_layout = QtWidgets.QHBoxLayout()
        kimlik_layout.addWidget(QtWidgets.QLabel("Proje Adı:"))
        self.txt_pdf_baslik = QtWidgets.QLineEdit(self.doc.Label if (self.doc and self.doc.Label) else "Patlatılmış Montaj Resmi")
        kimlik_layout.addWidget(self.txt_pdf_baslik, 2)

        kimlik_layout.addWidget(QtWidgets.QLabel("Doküman Kodu:"))
        dok_varsayilan = self.doc.Label if (self.doc and self.doc.Label) else "PRJ"
        varsayilan_kod = re.sub(r'[^A-Za-z0-9_-]', '_', dok_varsayilan)[:12].upper()
        self.txt_pdf_dok_kod = QtWidgets.QLineEdit(f"TD-{varsayilan_kod}-001")
        self.txt_pdf_dok_kod.setMaximumWidth(130)
        kimlik_layout.addWidget(self.txt_pdf_dok_kod, 1)

        kimlik_layout.addWidget(QtWidgets.QLabel("Revizyon:"))
        self.txt_pdf_rev = QtWidgets.QLineEdit("R00")
        self.txt_pdf_rev.setMaximumWidth(55)
        kimlik_layout.addWidget(self.txt_pdf_rev)
        pdf_layout.addLayout(kimlik_layout)

        kisi_layout = QtWidgets.QHBoxLayout()
        kisi_layout.addWidget(QtWidgets.QLabel("Yapan / Çizen:"))
        kullanici_adi = os.environ.get("USERNAME") or os.environ.get("USER") or "Tasarım Mühendisi"
        self.txt_pdf_cizen = QtWidgets.QLineEdit(kullanici_adi)
        self.txt_pdf_cizen.setPlaceholderText("Ad Soyad / Ünvan")
        kisi_layout.addWidget(self.txt_pdf_cizen)

        kisi_layout.addWidget(QtWidgets.QLabel("Kontrol / Onay:"))
        self.txt_pdf_onay = QtWidgets.QLineEdit("Kalite / Üretim Onayı")
        kisi_layout.addWidget(self.txt_pdf_onay)

        kisi_layout.addWidget(QtWidgets.QLabel("Standart:"))
        self.combo_pdf_standart = QtWidgets.QComboBox()
        self.combo_pdf_standart.addItems([
            "ISO 2768-mK / ISO 128 Teknik Resim",
            "ISO 2768-fH (Hassas Tolerans)",
            "Savunma / Havacılık TDP Normu"
        ])
        kisi_layout.addWidget(self.combo_pdf_standart)
        pdf_layout.addLayout(kisi_layout)

        sayfa_layout = QtWidgets.QHBoxLayout()
        sayfa_layout.addWidget(QtWidgets.QLabel("Kağıt Boyutu:"))
        self.combo_pdf_boyut = QtWidgets.QComboBox()
        self.combo_pdf_boyut.addItems([
            "A4 Yatay (ISO 297x210 mm)",
            "A3 Yatay (ISO 420x297 mm)",
            "A4 Dikey (ISO 210x297 mm)"
        ])
        sayfa_layout.addWidget(self.combo_pdf_boyut)

        sayfa_layout.addWidget(QtWidgets.QLabel("Arka Plan:"))
        self.combo_pdf_arkaplan = QtWidgets.QComboBox()
        self.combo_pdf_arkaplan.addItems([
            "Beyaz (Teknik Resim Standart)",
            "Mevcut 3D Ekran Arka Planı"
        ])
        sayfa_layout.addWidget(self.combo_pdf_arkaplan)
        pdf_layout.addLayout(sayfa_layout)

        oto_pdf_layout = QtWidgets.QHBoxLayout()
        self.chk_pdf_oto_ideal = QtWidgets.QCheckBox("✨ Otomatik İdeal Patlat (%95) ve İzometrik Hizala")
        self.chk_pdf_oto_ideal.setChecked(True)
        oto_pdf_layout.addWidget(self.chk_pdf_oto_ideal)

        self.chk_pdf_tam_sigdir = QtWidgets.QCheckBox("📐 Patlamayı Teknik Resme Tam Sığdır (Boşlukları Kırp)")
        self.chk_pdf_tam_sigdir.setChecked(True)
        oto_pdf_layout.addWidget(self.chk_pdf_tam_sigdir)
        pdf_layout.addLayout(oto_pdf_layout)

        secenek_layout = QtWidgets.QHBoxLayout()
        self.chk_pdf_antet = QtWidgets.QCheckBox("ISO Antet")
        self.chk_pdf_antet.setChecked(True)
        secenek_layout.addWidget(self.chk_pdf_antet)

        self.chk_pdf_bom = QtWidgets.QCheckBox("BOM Tablosu")
        self.chk_pdf_bom.setChecked(True)
        secenek_layout.addWidget(self.chk_pdf_bom)

        self.chk_pdf_balon = QtWidgets.QCheckBox("Poz Balonları")
        self.chk_pdf_balon.setChecked(True)
        secenek_layout.addWidget(self.chk_pdf_balon)

        self.chk_pdf_fastener = QtWidgets.QCheckBox("🔩 Vida/Somunları (Fasteners) Dahil Et")
        self.chk_pdf_fastener.setChecked(False)
        secenek_layout.addWidget(self.chk_pdf_fastener)
        pdf_layout.addLayout(secenek_layout)

        agirlik_layout = QtWidgets.QHBoxLayout()
        self.chk_pdf_agirlik = QtWidgets.QCheckBox("⚖️ Ağırlık Bilgisini Ekle")
        self.chk_pdf_agirlik.setChecked(True)
        agirlik_layout.addWidget(self.chk_pdf_agirlik)

        agirlik_layout.addWidget(QtWidgets.QLabel("Varsayılan Malzeme:"))
        self.combo_pdf_malzeme = QtWidgets.QComboBox()
        self.combo_pdf_malzeme.addItems([
            "Sac / Çelik (DKP / S235 - 7.85 g/cm³)",
            "Paslanmaz Sac (AISI 304 - 7.93 g/cm³)",
            "Galvaniz Sac (DX51D - 7.85 g/cm³)",
            "Alüminyum Sac (5754 / 6061 - 2.70 g/cm³)",
            "Bakır / Pirinç Sac (8.50 g/cm³)",
            "Mühendislik Plastiği / ABS (1.05 g/cm³)"
        ])
        self.chk_pdf_agirlik.toggled.connect(self.combo_pdf_malzeme.setEnabled)
        agirlik_layout.addWidget(self.combo_pdf_malzeme, 1)
        pdf_layout.addLayout(agirlik_layout)

        uretim_secenek_layout = QtWidgets.QVBoxLayout()
        self.chk_pdf_patlamamis_montaj = QtWidgets.QCheckBox(
            "🏢 En Başa 'Patlamamış Genel Montaj Resmi' Ekle (4 Görünüş + Ölçü)"
        )
        self.chk_pdf_patlamamis_montaj.setChecked(True)
        uretim_secenek_layout.addWidget(self.chk_pdf_patlamamis_montaj)

        self.chk_pdf_ayri_parcalar = QtWidgets.QCheckBox(
            "📑 Parçaların Ayrı Ayrı 4 Görünüşlü İmalat Resimlerini Ekle (Ölçü + Revizyon)"
        )
        self.chk_pdf_ayri_parcalar.setChecked(True)
        uretim_secenek_layout.addWidget(self.chk_pdf_ayri_parcalar)

        self.chk_pdf_ayni_parca_izometrik = QtWidgets.QCheckBox(
            "🎯 Aynı Parçalarda (Adet > 1): Ölçü Görünüşlerinde Tek Parça, İzometrikte Birlikte Göster"
        )
        self.chk_pdf_ayni_parca_izometrik.setChecked(True)
        self.chk_pdf_ayni_parca_izometrik.setToolTip(
            "İşaretli olduğunda; birden fazla kullanılan özdeş parçalarda Ön, Yan ve Üst görünüşlerde\n"
            "doğru ölçü almak için tek bir parça gösterilir, İzometrik 3D görünüşte ise tümü birlikte gösterilir.\n"
            "İşaret kaldırıldığında 4 görünüşün tamamında birlikte gösterilir."
        )
        uretim_secenek_layout.addWidget(self.chk_pdf_ayni_parca_izometrik)
        pdf_layout.addLayout(uretim_secenek_layout)

        olcu_kontrol_layout = QtWidgets.QHBoxLayout()
        olcu_kontrol_layout.setSpacing(4)
        self.chk_pdf_olcu_goster = QtWidgets.QCheckBox("📏 Ölçüleri Göster")
        self.chk_pdf_olcu_goster.setChecked(True)
        olcu_kontrol_layout.addWidget(self.chk_pdf_olcu_goster)

        self.chk_pdf_manuel_olcu = QtWidgets.QCheckBox("✍️ Manuel Ölçü:")
        self.chk_pdf_manuel_olcu.setChecked(False)
        olcu_kontrol_layout.addWidget(self.chk_pdf_manuel_olcu)

        oto_x, oto_y, oto_z = self._hesapla_otomatik_montaj_olculeri(fastener_dahil=False)

        self.spin_asm_olcu_x = QtWidgets.QDoubleSpinBox()
        self.spin_asm_olcu_x.setRange(0.1, 999999.0)
        self.spin_asm_olcu_x.setDecimals(1)
        self.spin_asm_olcu_x.setPrefix("X: ")
        self.spin_asm_olcu_x.setSuffix(" mm")
        self.spin_asm_olcu_x.setValue(max(0.1, oto_x))
        self.spin_asm_olcu_x.setEnabled(False)
        olcu_kontrol_layout.addWidget(self.spin_asm_olcu_x)

        self.spin_asm_olcu_y = QtWidgets.QDoubleSpinBox()
        self.spin_asm_olcu_y.setRange(0.1, 999999.0)
        self.spin_asm_olcu_y.setDecimals(1)
        self.spin_asm_olcu_y.setPrefix("Y: ")
        self.spin_asm_olcu_y.setSuffix(" mm")
        self.spin_asm_olcu_y.setValue(max(0.1, oto_y))
        self.spin_asm_olcu_y.setEnabled(False)
        olcu_kontrol_layout.addWidget(self.spin_asm_olcu_y)

        self.spin_asm_olcu_z = QtWidgets.QDoubleSpinBox()
        self.spin_asm_olcu_z.setRange(0.1, 999999.0)
        self.spin_asm_olcu_z.setDecimals(1)
        self.spin_asm_olcu_z.setPrefix("Z: ")
        self.spin_asm_olcu_z.setSuffix(" mm")
        self.spin_asm_olcu_z.setValue(max(0.1, oto_z))
        self.spin_asm_olcu_z.setEnabled(False)
        olcu_kontrol_layout.addWidget(self.spin_asm_olcu_z)

        self.btn_oto_olcu_yenile = QtWidgets.QPushButton("🔄 Oto Hesapla")
        self.btn_oto_olcu_yenile.clicked.connect(self.otomatik_montaj_olculerini_kutulara_yaz)
        olcu_kontrol_layout.addWidget(self.btn_oto_olcu_yenile)

        self.chk_pdf_manuel_olcu.toggled.connect(self._manuel_olcu_modu_degisti)
        self.chk_pdf_fastener.toggled.connect(self._fastener_pdf_degisti_olcu_guncelle)
        pdf_layout.addLayout(olcu_kontrol_layout)

        pdf_butonlar_layout = QtWidgets.QHBoxLayout()
        self.btn_pdf_kayit = QtWidgets.QPushButton("📄 Teknik Resim (.PDF) Kaydet")
        self.btn_pdf_kayit.setObjectName("btn_pdf")
        self.btn_pdf_kayit.clicked.connect(self.teknik_resim_pdf_kaydet)
        pdf_butonlar_layout.addWidget(self.btn_pdf_kayit)

        self.btn_uretim_paketi = QtWidgets.QPushButton("🏭 Üretim Doküman Paketi Çıkar (Klasör + PDF + CSV)")
        self.btn_uretim_paketi.setObjectName("btn_uretim_paketi")
        self.btn_uretim_paketi.clicked.connect(self.as9100_uretim_paketi_olustur)
        pdf_butonlar_layout.addWidget(self.btn_uretim_paketi)
        pdf_layout.addLayout(pdf_butonlar_layout)

        pdf_tab.setLayout(pdf_layout)
        self.sekmeler.addTab(pdf_tab, "📄 Teknik Resim & Üretim")

        video_tab = QtWidgets.QWidget()
        video_layout = QtWidgets.QVBoxLayout()
        video_layout.setSpacing(6)

        mod_grup_layout = QtWidgets.QHBoxLayout()
        mod_grup_layout.addWidget(QtWidgets.QLabel("Kayıt Kontrol Modu:"))
        self.rdo_video_sure = QtWidgets.QRadioButton("⏱️ Süreye Göre (Sabit Süre)")
        self.rdo_video_sure.setChecked(True)
        self.rdo_video_sure.setToolTip("Belirttiğiniz toplam saniye içinde model tam patlar ve tekrar orijinal haline geri döner.")
        self.rdo_video_hiz = QtWidgets.QRadioButton("🚀 Hıza Göre (Sabit Hız)")
        self.rdo_video_hiz.setToolTip("Belirttiğiniz açılma hızında (% / sn) patlar ve aynı hızda geri toplanır.")
        mod_grup_layout.addWidget(self.rdo_video_sure)
        mod_grup_layout.addWidget(self.rdo_video_hiz)
        video_layout.addLayout(mod_grup_layout)

        self.widget_video_sure = QtWidgets.QWidget()
        sure_layout = QtWidgets.QHBoxLayout(self.widget_video_sure)
        sure_layout.setContentsMargins(0, 0, 0, 0)
        sure_layout.addWidget(QtWidgets.QLabel("Toplam Video Süresi (Patlama + Geri Dönüş):"))
        self.spin_video_sure = QtWidgets.QDoubleSpinBox()
        self.spin_video_sure.setRange(1.0, 180.0)
        self.spin_video_sure.setDecimals(1)
        self.spin_video_sure.setSingleStep(1.0)
        self.spin_video_sure.setValue(6.0)
        self.spin_video_sure.setSuffix(" saniye")
        sure_layout.addWidget(self.spin_video_sure)
        video_layout.addWidget(self.widget_video_sure)

        self.widget_video_hiz = QtWidgets.QWidget()
        hiz_video_layout = QtWidgets.QHBoxLayout(self.widget_video_hiz)
        hiz_video_layout.setContentsMargins(0, 0, 0, 0)
        hiz_video_layout.addWidget(QtWidgets.QLabel("Patlama ve Dönüş Hızı (% / Saniye):"))
        self.spin_video_hiz_sn = QtWidgets.QDoubleSpinBox()
        self.spin_video_hiz_sn.setRange(5.0, 600.0)
        self.spin_video_hiz_sn.setDecimals(1)
        self.spin_video_hiz_sn.setSingleStep(10.0)
        self.spin_video_hiz_sn.setValue(40.0)
        self.spin_video_hiz_sn.setSuffix(" %/sn")
        hiz_video_layout.addWidget(self.spin_video_hiz_sn)
        self.widget_video_hiz.setVisible(False)
        video_layout.addWidget(self.widget_video_hiz)

        fps_ve_adim_layout = QtWidgets.QHBoxLayout()
        fps_ve_adim_layout.addWidget(QtWidgets.QLabel("Video Akıcılığı (FPS):"))
        self.spin_video_fps = QtWidgets.QSpinBox()
        self.spin_video_fps.setRange(15, 60)
        self.spin_video_fps.setValue(30)
        self.spin_video_fps.setSuffix(" FPS")
        fps_ve_adim_layout.addWidget(self.spin_video_fps)

        fps_ve_adim_layout.addWidget(QtWidgets.QLabel("Canlı Döngü Adımı:"))
        self.spin_adim = QtWidgets.QSpinBox()
        self.spin_adim.setRange(1, 30)
        self.spin_adim.setValue(2)
        self.spin_adim.setToolTip("Otomatik Döngü sekmesindeki canlı önizleme adım hızını belirler.")
        fps_ve_adim_layout.addWidget(self.spin_adim)
        video_layout.addLayout(fps_ve_adim_layout)

        self.lbl_video_ozet = QtWidgets.QLabel("")
        self.lbl_video_ozet.setStyleSheet("color: #89dceb; font-size: 11px; padding: 2px;")
        video_layout.addWidget(self.lbl_video_ozet)

        self.rdo_video_sure.toggled.connect(self._video_kontrol_modu_degisti)
        self.spin_video_sure.valueChanged.connect(self._video_ozet_guncelle)
        self.spin_video_hiz_sn.valueChanged.connect(self._video_ozet_guncelle)
        self.spin_video_fps.valueChanged.connect(self._video_ozet_guncelle)
        self.spin_sinir.valueChanged.connect(self._video_ozet_guncelle)
        self._video_ozet_guncelle()

        if HAS_OPENCV:
            self.btn_video_kayit = QtWidgets.QPushButton("🎬 Patlatma Animasyonunu MP4 Olarak Kaydet")
            self.btn_video_kayit.setObjectName("btn_video")
            self.btn_video_kayit.clicked.connect(self.video_kaydet)
            video_layout.addWidget(self.btn_video_kayit)
            video_layout.addWidget(QtWidgets.QLabel("<small style='color:#a6e3a1;'>✓ Sistem MP4 kaydına hazır (OpenCV Aktif).</small>"))
        else:
            self.btn_video_kayit = QtWidgets.QPushButton("📷 Animasyonu PNG Görüntü Dizisi Olarak Kaydet")
            self.btn_video_kayit.setObjectName("btn_video_png")
            self.btn_video_kayit.clicked.connect(self.video_kaydet)
            video_layout.addWidget(self.btn_video_kayit)

            self.btn_kur = QtWidgets.QPushButton("⚙️ MP4 Özelliğini Aktifleştir (OpenCV Kur)")
            self.btn_kur.setObjectName("btn_kur")
            self.btn_kur.clicked.connect(self.opencv_kur)
            video_layout.addWidget(self.btn_kur)

        video_tab.setLayout(video_layout)
        self.sekmeler.addTab(video_tab, "🎬 Video (MP4)")

        self.sekmeler.currentChanged.connect(self.sekme_degisti)
        self.splitter_ana.addWidget(self.sekmeler)
        self.splitter_ana.setStretchFactor(0, 3)
        self.splitter_ana.setStretchFactor(1, 2)
        ana_layout.addWidget(self.splitter_ana, 1)

        self.scroll_area.setWidget(icerik_widget)
        dis_layout.addWidget(self.scroll_area, 1)

        buton_layout = QtWidgets.QHBoxLayout()
        buton_layout.setSpacing(4)
        self.btn_sahne_yenile = QtWidgets.QPushButton("🔄 Gizlenenleri / Sahneyi Yenile")
        self.btn_sahne_yenile.setToolTip("Yeni yüklenen parçaları veya 3D ekranda gizlediğiniz/açtığınız parçaları algılar ve listeyi yeniler.")
        self.btn_sahne_yenile.clicked.connect(self.sahneyi_manuel_yenile)
        buton_layout.addWidget(self.btn_sahne_yenile)

        self.btn_sifirla = QtWidgets.QPushButton("Orijinale Döndür")
        self.btn_sifirla.clicked.connect(self.konumlari_sifirla)
        buton_layout.addWidget(self.btn_sifirla)

        self.btn_kapat = QtWidgets.QPushButton("Kapat")
        self.btn_kapat.setObjectName("btn_kapat")
        self.btn_kapat.clicked.connect(self.close)
        buton_layout.addWidget(self.btn_kapat)

        dis_layout.addLayout(buton_layout)

        # PySide6 (Qt6) uyumlu tekil sınıf döngüsü (Tuple TypeError hatasını önler)
        for widget_tipi in (QtWidgets.QPushButton, QtWidgets.QComboBox, QtWidgets.QLineEdit, QtWidgets.QCheckBox):
            for w in self.findChildren(widget_tipi):
                w.setMinimumWidth(20)

    def arayuzu_stillendir(self):
        koyu_tema = """
        QWidget {
            background-color: #1e1e2e;
            color: #cdd6f4;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 12px;
        }
        QScrollArea {
            border: none;
            background-color: transparent;
        }
        QScrollBar:vertical {
            background: #181825;
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #45475a;
            min-height: 24px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: #89b4fa;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar:horizontal {
            background: #181825;
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background: #45475a;
            min-width: 24px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #89b4fa;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        QGroupBox {
            border: 1px solid #45475a;
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 12px;
            font-weight: bold;
            color: #89b4fa;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            top: -8px;
        }
        QLabel, QRadioButton, QCheckBox {
            color: #cdd6f4;
        }
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            background-color: #313244;
            border: 1px solid #45475a;
            border-radius: 4px;
            padding: 5px;
            color: #cdd6f4;
        }
        QTreeWidget {
            background-color: #181825;
            alternate-background-color: #1e1e2e;
            border: 1px solid #45475a;
            border-radius: 5px;
            color: #cdd6f4;
        }
        QTreeWidget::item {
            padding: 4px 2px;
        }
        QTreeWidget::item:selected {
            background-color: #45475a;
            color: #89b4fa;
        }
        QSplitter::handle:vertical {
            background-color: #313244;
            border: 1px solid #45475a;
            height: 6px;
            margin: 2px 40px;
            border-radius: 3px;
        }
        QSplitter::handle:vertical:hover {
            background-color: #89b4fa;
        }
        QHeaderView::section {
            background-color: #313244;
            color: #89b4fa;
            padding: 4px;
            border: 1px solid #45475a;
            font-weight: bold;
        }
        QPushButton {
            background-color: #45475a;
            color: #cdd6f4;
            border-radius: 5px;
            padding: 7px 10px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #585b70;
            color: #ffffff;
        }
        QPushButton:pressed {
            background-color: #313244;
        }
        QPushButton:checked {
            background-color: #89b4fa;
            color: #11111b;
            border: 1px solid #b4befe;
        }
        QPushButton#btn_pdf {
            background-color: #cba6f7;
            color: #11111b;
            padding: 9px;
        }
        QPushButton#btn_pdf:hover {
            background-color: #b4befe;
        }
        QPushButton#btn_uretim_paketi {
            background-color: #89dceb;
            color: #11111b;
            padding: 9px;
        }
        QPushButton#btn_uretim_paketi:hover {
            background-color: #a6e3a1;
        }
        QPushButton#btn_video {
            background-color: #a6e3a1;
            color: #11111b;
        }
        QPushButton#btn_video_png {
            background-color: #f9e2af;
            color: #11111b;
        }
        QPushButton#btn_kur {
            background-color: #89b4fa;
            color: #11111b;
            margin-top: 8px;
        }
        QPushButton#btn_kapat {
            background-color: #f38ba8;
            color: #11111b;
        }
        QTabWidget::pane {
            border: 1px solid #45475a;
            border-radius: 6px;
            background-color: #181825;
            top: -1px;
        }
        QTabBar::tab {
            background-color: #313244;
            border: 1px solid #45475a;
            padding: 7px 12px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            color: #a6adc8;
        }
        QTabBar::tab:selected {
            background-color: #181825;
            color: #89b4fa;
            font-weight: bold;
        }
        QSlider::groove:horizontal {
            border: 1px solid #45475a;
            height: 10px;
            background: #11111b;
            border-radius: 5px;
        }
        QSlider::handle:horizontal {
            background: #89b4fa;
            border: 1px solid #b4befe;
            width: 22px;
            margin: -6px 0;
            border-radius: 11px;
        }
        """
        self.setStyleSheet(koyu_tema)

    def sahneyi_manuel_yenile(self):
        self.doc = FreeCAD.ActiveDocument
        if not self.doc:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Şu anda açık bir FreeCAD belgesi bulunmuyor.")
            return

        if not self.objects_data:
            self._yukleme_penceresi_baslat()
            try:
                self.verileri_hazirla()
                if self.objects_data:
                    self.kademeleri_hesapla(self.aktif_merkezi_getir())
                    self.grup_listesini_yenile()
                    self._fastener_sayisini_guncelle()
            finally:
                self._yukleme_penceresi_kapat()
        else:
            self.gizlilik_durumlarini_senkronize_et()
            self._fastener_sayisini_guncelle()

    def aktif_merkezi_getir(self):
        if self.merkez_modu == "Manuel":
            kure = self.doc.getObject("PATLATMA_MERKEZI") if self.doc else None
            if kure:
                self.manuel_merkez_konumu = FreeCAD.Vector(kure.Placement.Base)
            return self.manuel_merkez_konumu
        return self.global_center

    def merkez_kuresini_getir_veya_olustur(self):
        if not self.doc:
            return None
        merkez_objesi = self.doc.getObject("PATLATMA_MERKEZI")
        if not merkez_objesi:
            merkez_objesi = self.doc.addObject("Part::Sphere", "PATLATMA_MERKEZI")
            merkez_objesi.Label = "PATLATMA_MERKEZI"
            merkez_objesi.Radius = max(5.0, min(25.0, self.varsayilan_kademe_farki * 0.5))
            if hasattr(merkez_objesi, "ViewObject") and merkez_objesi.ViewObject:
                merkez_objesi.ViewObject.ShapeColor = (1.0, 0.2, 0.2)
                merkez_objesi.ViewObject.Transparency = 30
            merkez_objesi.Placement.Base = self.manuel_merkez_konumu
        return merkez_objesi

    def spinleri_konumla_esitle(self, konum):
        self._spin_guncelleniyor = True
        self.spin_merkez_x.setValue(konum.x)
        self.spin_merkez_y.setValue(konum.y)
        self.spin_merkez_z.setValue(konum.z)
        self._spin_guncelleniyor = False

    def merkez_modu_ayarla(self, yeni_mod):
        self.merkez_modu = yeni_mod
        self.btn_oto_merkez.setChecked(yeni_mod == "Otomatik")
        self.btn_manuel_merkez.setChecked(yeni_mod == "Manuel")
        self.manuel_merkez_panel.setVisible(yeni_mod == "Manuel")

        if yeni_mod == "Manuel" and self.doc:
            kure = self.merkez_kuresini_getir_veya_olustur()
            if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                kure.ViewObject.Visibility = self.chk_kure_goster.isChecked()
                self.spinleri_konumla_esitle(kure.Placement.Base)
        else:
            if self.doc:
                kure = self.doc.getObject("PATLATMA_MERKEZI")
                if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                    kure.ViewObject.Visibility = False

        aktif_merkez = self.aktif_merkezi_getir()
        self.kademeleri_hesapla(aktif_merkez)
        self.slider_degisti(self.slider.value())

    def kure_tasima_aracini_ac(self):
        if not self.doc:
            return
        if self.merkez_modu != "Manuel":
            self.merkez_modu_ayarla("Manuel")
        kure = self.merkez_kuresini_getir_veya_olustur()
        self.chk_kure_goster.setChecked(True)
        if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
            kure.ViewObject.Visibility = True
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self.doc.Name, kure.Name)
        try:
            FreeCADGui.runCommand('Std_TransformManip')
        except Exception:
            pass

    def secili_parcayi_merkez_yap(self):
        secimler = [s for s in FreeCADGui.Selection.getSelection() if s.Name != "PATLATMA_MERKEZI"]
        if not secimler:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Lütfen önce 3D ekrandan merkez yapmak istediğiniz parçayı seçin.")
            return

        secili_obj = secimler[0]
        hedef_konum = FreeCAD.Vector(secili_obj.Shape.BoundBox.Center)
        for d in self.objects_data:
            if d['obj'] == secili_obj:
                hedef_konum = FreeCAD.Vector(d['bbox_merkez'])
                break

        self.manuel_merkez_konumu = hedef_konum
        kure = self.merkez_kuresini_getir_veya_olustur()
        if kure:
            kure.Placement.Base = hedef_konum
        self.spinleri_konumla_esitle(hedef_konum)
        self.kademeleri_hesapla(hedef_konum)
        self.slider_degisti(self.slider.value())

    def manuel_merkezi_ortala(self):
        self.manuel_merkez_konumu = FreeCAD.Vector(self.global_center)
        kure = self.merkez_kuresini_getir_veya_olustur()
        if kure:
            kure.Placement.Base = self.manuel_merkez_konumu
        self.spinleri_konumla_esitle(self.manuel_merkez_konumu)
        self.kademeleri_hesapla(self.manuel_merkez_konumu)
        self.slider_degisti(self.slider.value())

    def kure_gorunurluk_degistir(self, gorunur):
        if not self.doc:
            return
        kure = self.doc.getObject("PATLATMA_MERKEZI")
        if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
            kure.ViewObject.Visibility = gorunur
            FreeCADGui.updateGui()

    def spin_merkez_degisti(self):
        if self._spin_guncelleniyor or not self.doc:
            return
        yeni_konum = FreeCAD.Vector(
            self.spin_merkez_x.value(),
            self.spin_merkez_y.value(),
            self.spin_merkez_z.value()
        )
        self.manuel_merkez_konumu = yeni_konum
        kure = self.merkez_kuresini_getir_veya_olustur()
        if kure:
            kure.Placement.Base = yeni_konum
        self.kademeleri_hesapla(yeni_konum)
        self.slider_degisti(self.slider.value())

    def manuel_merkez_canli_takip(self):
        if self.merkez_modu != "Manuel" or not self.doc:
            return
        try:
            kure = self.doc.getObject("PATLATMA_MERKEZI")
            if not kure:
                return
            guncel_konum = kure.Placement.Base
            if (guncel_konum - self.manuel_merkez_konumu).Length > 0.05:
                self.manuel_merkez_konumu = FreeCAD.Vector(guncel_konum)
                self.spinleri_konumla_esitle(self.manuel_merkez_konumu)
                self.kademeleri_hesapla(self.manuel_merkez_konumu)
                self.slider_degisti(self.slider.value())
        except Exception:
            pass

    def kademe_farki_degisti(self, deger):
        if self.slider_kademe.value() != min(self.slider_kademe.maximum(), deger):
            self.slider_kademe.blockSignals(True)
            self.slider_kademe.setValue(min(self.slider_kademe.maximum(), deger))
            self.slider_kademe.blockSignals(False)
        self.slider_degisti(self.slider.value())

    def gecerli_secili_parcalari_al(self):
        secilenler = FreeCADGui.Selection.getSelection()
        gecerli_obj_seti = {d['obj'] for d in self.objects_data}
        bulunanlar = []

        def nesne_coz(obj):
            if obj in gecerli_obj_seti and obj not in bulunanlar:
                bulunanlar.append(obj)
            if hasattr(obj, "OutList"):
                for alt in obj.OutList:
                    nesne_coz(alt)

        for s in secilenler:
            if s.Name != "PATLATMA_MERKEZI":
                nesne_coz(s)
        return bulunanlar

    def secililerden_grup_olustur(self, mod="butun"):
        secili_parcalar = self.gecerli_secili_parcalari_al()
        if not secili_parcalar:
            QtWidgets.QMessageBox.warning(
                self, "Uyarı",
                "Lütfen önce 3D ekrandan veya Model Ağacından gruplamak / sabitlemek istediğiniz parçaları seçin."
            )
            return

        self._parcalari_mevcut_gruplardan_cikar(secili_parcalar)

        self.son_grup_id += 1
        gid = self.son_grup_id
        ilk_etiketler = ", ".join(o.Label for o in secili_parcalar[:2])
        if len(secili_parcalar) > 2:
            ilk_etiketler += "..."

        grup_adi = f"Grup {gid} ({ilk_etiketler})" if mod == "butun" else f"Sabit {gid} ({ilk_etiketler})"
        self.gruplar[gid] = {
            'ad': grup_adi,
            'uyeler': secili_parcalar,
            'mod': mod,
            'manuel_carpan': 1.0,
            'fastener_mi': False
        }

        self.grup_listesini_yenile(secilecek_gid=gid)
        self.kademeleri_hesapla(self.aktif_merkezi_getir())
        self.slider_degisti(self.slider.value())

    def _parcalari_mevcut_gruplardan_cikar(self, parcalar):
        silinecek_gidler = []
        for gid, ginfo in self.gruplar.items():
            ginfo['uyeler'] = [u for u in ginfo['uyeler'] if u not in parcalar]
            if not ginfo['uyeler']:
                silinecek_gidler.append(gid)
        for gid in silinecek_gidler:
            del self.gruplar[gid]

    def freecad_gruplarini_ice_aktar(self):
        if not self.doc:
            return
        gecerli_obj_seti = {d['obj'] for d in self.objects_data}
        eklenen_sayisi = 0

        for obj in self.doc.Objects:
            if obj.TypeId in ("App::DocumentObjectGroup", "App::Part") or hasattr(obj, "Group"):
                alt_parcalar = []
                for alt in getattr(obj, "Group", []):
                    if alt in gecerli_obj_seti:
                        alt_parcalar.append(alt)
                if len(alt_parcalar) >= 2:
                    self._parcalari_mevcut_gruplardan_cikar(alt_parcalar)
                    self.son_grup_id += 1
                    gid = self.son_grup_id
                    self.gruplar[gid] = {
                        'ad': f"{obj.Label}",
                        'uyeler': alt_parcalar,
                        'mod': 'butun',
                        'manuel_carpan': 1.0,
                        'fastener_mi': False
                    }
                    eklenen_sayisi += 1

        if eklenen_sayisi == 0:
            QtWidgets.QMessageBox.information(
                self, "Bilgi",
                "Unsur ağacında birden fazla görünür katı parça içeren hazır bir Grup/Part bulunamadı.\n"
                "3D ekrandan parçaları seçip 'Seçilileri Bütün Grup Yap' butonunu kullanabilirsiniz."
            )
            return

        self.grup_listesini_yenile()
        self.kademeleri_hesapla(self.aktif_merkezi_getir())
        self.slider_degisti(self.slider.value())

    def grup_listesini_yenile(self, secilecek_gid=None):
        self._liste_secim_guncelleniyor = True
        self.agac_gruplar.clear()

        arama_metni = ""
        if hasattr(self, "txt_grup_ara"):
            arama_metni = self.txt_grup_ara.text().strip().lower()

        for gid, ginfo in self.gruplar.items():
            uye_isimleri = ", ".join(u.Label for u in ginfo['uyeler'])
            if arama_metni and (arama_metni not in ginfo['ad'].lower() and arama_metni not in uye_isimleri.lower()):
                continue

            mod_metni = "🔗 Bütün (Birlikte Açılır)" if ginfo['mod'] == 'butun' else "🔒 Sabit (Açılmaz)"
            yon_metni = "Ters (-)" if ginfo.get('manuel_carpan', 1.0) < 0 else "Normal (+)"
            item = QtWidgets.QTreeWidgetItem([
                ginfo['ad'],
                mod_metni,
                f"{len(ginfo['uyeler'])} Parça",
                yon_metni
            ])
            item.setData(0, QtCore.Qt.UserRole, gid)
            item.setToolTip(0, "Grubu genişletmek için solundaki oka tıklayın.\nÇift tıklayarak Bütün/Sabit modunu değiştirebilirsiniz.\nÜyeler: " + uye_isimleri)

            renk = QtGui.QColor("#89b4fa") if ginfo['mod'] == 'butun' else QtGui.QColor("#f9e2af")
            item.setForeground(1, QtGui.QBrush(renk))

            for uye_obj in ginfo['uyeler']:
                alt_item = QtWidgets.QTreeWidgetItem([
                    f"↳ {uye_obj.Label}",
                    "Grup Üyesi",
                    "1 Parça",
                    yon_metni
                ])
                alt_item.setData(0, QtCore.Qt.UserRole, gid)
                alt_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#a6adc8")))
                item.addChild(alt_item)

            self.agac_gruplar.addTopLevelItem(item)
            if secilecek_gid is not None and gid == secilecek_gid:
                item.setSelected(True)

        self._liste_secim_guncelleniyor = False

    def grup_listesini_filtrele(self, _):
        self.grup_listesini_yenile()

    def liste_yuksekligini_degistir(self, genis_mi):
        if genis_mi:
            self.agac_gruplar.setMinimumHeight(280)
            self.btn_liste_boyut.setText("↕ Listeyi Küçült")
        else:
            self.agac_gruplar.setMinimumHeight(90)
            self.btn_liste_boyut.setText("↕ Listeyi Genişlet")

    def grup_yoneticisi_penceresi_ac(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Geniş Ekran Ürün Grupları ve Sabit Parça Yöneticisi")
        dlg.resize(820, 560)
        dlg.setStyleSheet(self.styleSheet())

        layout = QtWidgets.QVBoxLayout(dlg)

        bilgi_lbl = QtWidgets.QLabel(
            "💡 İpucu: Bir gruba çift tıklayarak 'Bütün ↔ Sabit' modunu hızlıca değiştirebilirsiniz. "
            "Solundaki oka tıklayarak grup içindeki alt parçaları görebilirsiniz."
        )
        layout.addWidget(bilgi_lbl)

        ara_input = QtWidgets.QLineEdit()
        ara_input.setPlaceholderText("🔍 Grup veya alt parça adına göre filtrele...")
        ara_input.setClearButtonEnabled(True)
        layout.addWidget(ara_input)

        agac_buyuk = QtWidgets.QTreeWidget()
        agac_buyuk.setHeaderLabels(["Grup / Parça Adı", "Davranış Modu", "Parça Sayısı", "Yön"])
        agac_buyuk.setAlternatingRowColors(True)
        agac_buyuk.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        header = agac_buyuk.header()
        try:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        except Exception:
            agac_buyuk.setColumnWidth(0, 340)
            agac_buyuk.setColumnWidth(1, 200)
            agac_buyuk.setColumnWidth(2, 100)
            agac_buyuk.setColumnWidth(3, 80)
        layout.addWidget(agac_buyuk, 1)

        def buyuk_listeyi_doldur():
            agac_buyuk.clear()
            filtre = ara_input.text().strip().lower()
            for gid, ginfo in self.gruplar.items():
                uye_metni = ", ".join(u.Label for u in ginfo['uyeler'])
                if filtre and (filtre not in ginfo['ad'].lower() and filtre not in uye_metni.lower()):
                    continue
                mod_m = "🔗 Bütün (Birlikte Açılır)" if ginfo['mod'] == 'butun' else "🔒 Sabit (Açılmaz)"
                yon_m = "Ters (-)" if ginfo.get('manuel_carpan', 1.0) < 0 else "Normal (+)"
                it = QtWidgets.QTreeWidgetItem([ginfo['ad'], mod_m, f"{len(ginfo['uyeler'])} Parça", yon_m])
                it.setData(0, QtCore.Qt.UserRole, gid)
                renk = QtGui.QColor("#89b4fa") if ginfo['mod'] == 'butun' else QtGui.QColor("#f9e2af")
                it.setForeground(1, QtGui.QBrush(renk))
                for uye_obj in ginfo['uyeler']:
                    ait = QtWidgets.QTreeWidgetItem([f"↳ {uye_obj.Label}", "Grup Üyesi", "1 Parça", yon_m])
                    ait.setData(0, QtCore.Qt.UserRole, gid)
                    ait.setForeground(0, QtGui.QBrush(QtGui.QColor("#a6adc8")))
                    it.addChild(ait)
                agac_buyuk.addTopLevelItem(it)

        def buyuk_secim_senkron():
            secili_gids = {i.data(0, QtCore.Qt.UserRole) for i in agac_buyuk.selectedItems()}
            self._liste_secim_guncelleniyor = True
            for idx in range(self.agac_gruplar.topLevelItemCount()):
                ana_it = self.agac_gruplar.topLevelItem(idx)
                ana_it.setSelected(ana_it.data(0, QtCore.Qt.UserRole) in secili_gids)
            self._liste_secim_guncelleniyor = False
            self.agac_secimi_degisti()

        agac_buyuk.itemSelectionChanged.connect(buyuk_secim_senkron)
        ara_input.textChanged.connect(lambda *_: buyuk_listeyi_doldur())

        def aksiyon_calistir(fonk):
            buyuk_secim_senkron()
            fonk()
            buyuk_listeyi_doldur()

        agac_buyuk.itemDoubleClicked.connect(lambda *_: aksiyon_calistir(self.secili_grup_modunu_degistir))

        btn_bar = QtWidgets.QHBoxLayout()
        b_butun = QtWidgets.QPushButton("🔗 3D Seçilileri Bütün Grup Yap")
        b_butun.clicked.connect(lambda: aksiyon_calistir(lambda: self.secililerden_grup_olustur(mod="butun")))
        btn_bar.addWidget(b_butun)

        b_sabit = QtWidgets.QPushButton("🔒 3D Seçilileri Sabit Yap")
        b_sabit.clicked.connect(lambda: aksiyon_calistir(lambda: self.secililerden_grup_olustur(mod="sabit")))
        btn_bar.addWidget(b_sabit)

        b_mod = QtWidgets.QPushButton("🔁 Mod Değiştir")
        b_mod.clicked.connect(lambda: aksiyon_calistir(self.secili_grup_modunu_degistir))
        btn_bar.addWidget(b_mod)

        b_yon = QtWidgets.QPushButton("🔄 Yön Çevir")
        b_yon.clicked.connect(lambda: aksiyon_calistir(self.secili_yon_ters_cevir))
        btn_bar.addWidget(b_yon)

        b_sil = QtWidgets.QPushButton("❌ Seçiliyi Kaldır")
        b_sil.clicked.connect(lambda: aksiyon_calistir(self.secili_grubu_kaldir))
        btn_bar.addWidget(b_sil)

        b_kapat = QtWidgets.QPushButton("Kapat")
        b_kapat.setObjectName("btn_kapat")
        b_kapat.clicked.connect(dlg.accept)
        btn_bar.addWidget(b_kapat)

        layout.addLayout(btn_bar)
        buyuk_listeyi_doldur()
        dlg.exec()

    def agac_secimi_degisti(self):
        if self._liste_secim_guncelleniyor or not self.doc:
            return
        secili_itemler = self.agac_gruplar.selectedItems()
        if not secili_itemler:
            return

        FreeCADGui.Selection.clearSelection()
        for item in secili_itemler:
            gid = item.data(0, QtCore.Qt.UserRole)
            if gid in self.gruplar:
                for uye in self.gruplar[gid]['uyeler']:
                    try:
                        FreeCADGui.Selection.addSelection(self.doc.Name, uye.Name)
                    except Exception:
                        pass

    def secili_grup_modunu_degistir(self):
        secili_itemler = self.agac_gruplar.selectedItems()
        if not secili_itemler:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Lütfen listeden modunu değiştirmek istediğiniz grubu seçin.")
            return

        ilk_gid = None
        for item in secili_itemler:
            gid = item.data(0, QtCore.Qt.UserRole)
            if gid in self.gruplar:
                ilk_gid = gid
                mevcut_mod = self.gruplar[gid]['mod']
                yeni_mod = 'sabit' if mevcut_mod == 'butun' else 'butun'
                self.gruplar[gid]['mod'] = yeni_mod
                if self.gruplar[gid]['ad'].startswith("Grup ") and yeni_mod == 'sabit':
                    self.gruplar[gid]['ad'] = self.gruplar[gid]['ad'].replace("Grup ", "Sabit ", 1)
                elif self.gruplar[gid]['ad'].startswith("Sabit ") and yeni_mod == 'butun':
                    self.gruplar[gid]['ad'] = self.gruplar[gid]['ad'].replace("Sabit ", "Grup ", 1)

        self.grup_listesini_yenile(secilecek_gid=ilk_gid)
        self.kademeleri_hesapla(self.aktif_merkezi_getir())
        self.slider_degisti(self.slider.value())

    def secili_gruba_parca_ekle(self):
        secili_itemler = self.agac_gruplar.selectedItems()
        if not secili_itemler:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Lütfen önce listeden ekleme yapmak istediğiniz grubu seçin.")
            return

        hedef_gid = secili_itemler[0].data(0, QtCore.Qt.UserRole)
        if hedef_gid not in self.gruplar:
            return

        yeni_parcalar = self.gecerli_secili_parcalari_al()
        eklenecekler = [p for p in yeni_parcalar if p not in self.gruplar[hedef_gid]['uyeler']]
        if not eklenecekler:
            QtWidgets.QMessageBox.information(self, "Bilgi", "3D ekranda bu gruba eklenecek farklı bir parça seçilmedi.")
            return

        self._parcalari_mevcut_gruplardan_cikar(eklenecekler)
        self.gruplar[hedef_gid]['uyeler'].extend(eklenecekler)
        self.grup_listesini_yenile(secilecek_gid=hedef_gid)
        self.kademeleri_hesapla(self.aktif_merkezi_getir())
        self.slider_degisti(self.slider.value())

    def secili_grubu_kaldir(self):
        secili_itemler = self.agac_gruplar.selectedItems()
        if not secili_itemler:
            return
        for item in secili_itemler:
            gid = item.data(0, QtCore.Qt.UserRole)
            if gid in self.gruplar:
                del self.gruplar[gid]
        self.grup_listesini_yenile()
        self.kademeleri_hesapla(self.aktif_merkezi_getir())
        self.slider_degisti(self.slider.value())

    def tum_gruplari_temizle(self):
        self.gruplar.clear()
        self.grup_listesini_yenile()
        self.kademeleri_hesapla(self.aktif_merkezi_getir())
        self.slider_degisti(self.slider.value())

    def secili_yon_ters_cevir(self):
        secimler = self.gecerli_secili_parcalari_al()
        secili_itemler = self.agac_gruplar.selectedItems()

        if not secimler and not secili_itemler:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Lütfen önce 3D ekrandan bir parça veya listeden bir grup seçin.")
            return

        degisen_gruplar = set()
        for item in secili_itemler:
            gid = item.data(0, QtCore.Qt.UserRole)
            if gid in self.gruplar:
                degisen_gruplar.add(gid)

        for obj in secimler:
            for d in self.objects_data:
                if d['obj'] == obj:
                    if d['grup_id'] and d['grup_id'] in self.gruplar:
                        degisen_gruplar.add(d['grup_id'])
                    else:
                        d['manuel_carpan'] *= -1.0

        for gid in degisen_gruplar:
            self.gruplar[gid]['manuel_carpan'] = self.gruplar[gid].get('manuel_carpan', 1.0) * -1.0
            for d in self.objects_data:
                if d['obj'] in self.gruplar[gid]['uyeler']:
                    d['manuel_carpan'] = self.gruplar[gid]['manuel_carpan']

        self.grup_listesini_yenile()
        self.slider_degisti(self.slider.value())
        FreeCADGui.Selection.clearSelection()

    def gizlilik_durumlarini_senkronize_et(self):
        self._ust_klasor_onbellek.clear()
        for d in self.objects_data:
            obj = d['obj']
            if d.get('fastener_mi') and self.fastener_modu == "gizle":
                continue
            gercekten_gorunur = self._nesne_gercekten_gorunur_mu(obj)
            d['kullanici_gizledi'] = not gercekten_gorunur

        if self.objects_data:
            self.kademeleri_hesapla(self.aktif_merkezi_getir())
            self.otomatik_montaj_olculerini_kutulara_yaz()
            self.slider_degisti(self.slider.value())

    def _fastener_sayisini_guncelle(self):
        fastener_sayisi = sum(1 for d in self.objects_data if d.get('fastener_mi') and not d.get('kullanici_gizledi', False))
        if hasattr(self, "lbl_fastener_bilgi"):
            self.lbl_fastener_bilgi.setText(f"🔩 Bağlantı El. ({fastener_sayisi} Adet):")
        self.otomatik_montaj_olculerini_kutulara_yaz()

    def fastener_klasor_kurali_uygula(self):
        metin = self.txt_fastener_kural.text().strip()
        self.fastener_klasor_kurallari = [k.strip() for k in metin.split(",") if k.strip()]
        for d in self.objects_data:
            bbox = self._hassas_sinir_kutusu_getir(d['obj'])
            d['fastener_mi'] = self._baglanti_elemani_mi(d['obj'], bbox)
            if hasattr(d['obj'], "ViewObject") and d['obj'].ViewObject and not d.get('kullanici_gizledi', False):
                if d['fastener_mi'] and self.fastener_modu == "gizle":
                    d['obj'].ViewObject.Visibility = False
                else:
                    d['obj'].ViewObject.Visibility = True
        self._fastener_sayisini_guncelle()
        if self.objects_data:
            self.kademeleri_hesapla(self.aktif_merkezi_getir())
            self.slider_degisti(self.slider.value())

    def vida_klasoru_secim_diyalogu_ac(self):
        if not self.doc:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Aktif belge bulunamadı.")
            return

        klasorler = []
        for obj in self.doc.Objects:
            if obj.TypeId in ("App::DocumentObjectGroup", "App::Part", "App::LinkGroup") or hasattr(obj, "Group"):
                klasorler.append(obj)

        if not klasorler:
            QtWidgets.QMessageBox.information(self, "Bilgi", "Model ağacında klasör veya grup (Part/Group) bulunamadı.")
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Vida / Somun (Bağlantı Elemanı) Klasörlerini Seçin")
        dlg.resize(480, 380)
        dlg.setStyleSheet(self.styleSheet())
        v_lay = QtWidgets.QVBoxLayout(dlg)

        aciklama = QtWidgets.QLabel(
            "İçinde vida, somun, pul bulunan klasörleri işaretleyin.\n"
            "(Not: PDF sekmesinde 'Vida/Somunları Dahil Et' kutusu kapalıysa bu klasördeki parçalar teknik resimlere eklenmez):"
        )
        aciklama.setWordWrap(True)
        v_lay.addWidget(aciklama)

        liste_w = QtWidgets.QListWidget()
        for kl in klasorler:
            it = QtWidgets.QListWidgetItem(f"📁 {kl.Label} ({kl.Name})")
            it.setData(QtCore.Qt.UserRole, kl.Name)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            otomatik_eslesti = any(
                kl.Label.lower().startswith(k.lower()) or kl.Name.lower().startswith(k.lower())
                for k in self.fastener_klasor_kurallari if k
            )
            secili_mi = (kl.Name in self.secili_fastener_klasorleri) or otomatik_eslesti
            it.setCheckState(QtCore.Qt.Checked if secili_mi else QtCore.Qt.Unchecked)
            liste_w.addItem(it)
        v_lay.addWidget(liste_w, 1)

        chk_pdf_gizle = QtWidgets.QCheckBox("Teknik Resimlerde ve BOM Listesinde Bu Vida Klasörlerini Gizle (Önerilen)")
        chk_pdf_gizle.setChecked(not self.chk_pdf_fastener.isChecked())
        v_lay.addWidget(chk_pdf_gizle)

        btn_h = QtWidgets.QHBoxLayout()
        btn_uygula = QtWidgets.QPushButton("✓ Seçimi Uygula")
        btn_uygula.clicked.connect(dlg.accept)
        btn_iptal = QtWidgets.QPushButton("İptal")
        btn_iptal.setObjectName("btn_kapat")
        btn_iptal.clicked.connect(dlg.reject)
        btn_h.addWidget(btn_uygula)
        btn_h.addWidget(btn_iptal)
        v_lay.addLayout(btn_h)

        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.secili_fastener_klasorleri.clear()
            secilen_klasor_objeleri = []
            for i in range(liste_w.count()):
                it = liste_w.item(i)
                kl_name = it.data(QtCore.Qt.UserRole)
                if it.checkState() == QtCore.Qt.Checked:
                    self.secili_fastener_klasorleri.add(kl_name)
                    kl_obj = self.doc.getObject(kl_name)
                    if kl_obj:
                        secilen_klasor_objeleri.append(kl_obj)

            klasor_alt_parcalari = set()
            for kl_obj in secilen_klasor_objeleri:
                for alt in getattr(kl_obj, "OutListRecursive", getattr(kl_obj, "Group", [])):
                    klasor_alt_parcalari.add(alt)

            for d in self.objects_data:
                if d['obj'] in klasor_alt_parcalari:
                    d['fastener_mi'] = True
                else:
                    bbox = self._hassas_sinir_kutusu_getir(d['obj'])
                    d['fastener_mi'] = self._baglanti_elemani_mi(d['obj'], bbox)

                if hasattr(d['obj'], "ViewObject") and d['obj'].ViewObject and not d.get('kullanici_gizledi', False):
                    d['obj'].ViewObject.Visibility = not (d['fastener_mi'] and self.fastener_modu == "gizle")

            self.chk_pdf_fastener.setChecked(not chk_pdf_gizle.isChecked())
            self._fastener_sayisini_guncelle()
            if self.objects_data:
                self.kademeleri_hesapla(self.aktif_merkezi_getir())
                self.slider_degisti(self.slider.value())

    def fastener_modu_degisti(self, index):
        if index == 0:
            self.fastener_modu = "dahil"
        elif index == 1:
            self.fastener_modu = "sabit"
        else:
            self.fastener_modu = "gizle"
            if hasattr(self, "chk_pdf_fastener"):
                self.chk_pdf_fastener.setChecked(False)

        for d in self.objects_data:
            if d.get('fastener_mi') and not d.get('kullanici_gizledi', False):
                obj = d['obj']
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = (self.fastener_modu != "gizle")

        if self.objects_data:
            self.kademeleri_hesapla(self.aktif_merkezi_getir())
            self.slider_degisti(self.slider.value())

    def secili_fastener_durumu_degistir(self):
        secimler = self.gecerli_secili_parcalari_al()
        if not secimler:
            QtWidgets.QMessageBox.warning(
                self, "Uyarı",
                "Lütfen 3D ekrandan veya unsur ağacından bağlantı elemanı (vida/somun/pul) durumunu değiştirmek istediğiniz parçaları veya klasörü seçin."
            )
            return

        mevcut_durumlar = [d.get('fastener_mi', False) for d in self.objects_data if d['obj'] in secimler]
        yeni_durum = not all(mevcut_durumlar)

        for obj in secimler:
            for d in self.objects_data:
                if d['obj'] == obj:
                    d['fastener_mi'] = yeni_durum
                    if hasattr(obj, "ViewObject") and obj.ViewObject and not d.get('kullanici_gizledi', False):
                        if d['fastener_mi'] and self.fastener_modu == "gizle":
                            obj.ViewObject.Visibility = False
                        else:
                            obj.ViewObject.Visibility = True

        self._fastener_sayisini_guncelle()
        if self.objects_data:
            self.kademeleri_hesapla(self.aktif_merkezi_getir())
            self.slider_degisti(self.slider.value())

    def tip_degistir(self, yeni_tip):
        self.patlama_tipi = yeni_tip
        self.slider_degisti(self.slider.value())

    def sekme_degisti(self, index):
        if index != 1 and hasattr(self, 'zamanlayici') and self.zamanlayici.isActive():
            self.btn_animasyon.setChecked(False)
            self.animasyon_tetikle()

    def animasyon_tetikle(self):
        if self.btn_animasyon.isChecked():
            self.btn_animasyon.setText("⏸ Animasyonu Durdur")
            self.zamanlayici.start(self.spin_hiz.value())
        else:
            self.btn_animasyon.setText("▶ Animasyonu Başlat")
            self.zamanlayici.stop()

    def animasyon_adimi(self):
        mevcut_deger = self.slider.value()
        max_sinir = self.spin_sinir.value()
        adim = self.spin_adim.value()

        if self.animasyon_yonu == 1:
            yeni_deger = mevcut_deger + adim
            if yeni_deger >= max_sinir:
                yeni_deger = max_sinir
                self.animasyon_yonu = -1
        else:
            yeni_deger = mevcut_deger - adim
            if yeni_deger <= 0:
                yeni_deger = 0
                self.animasyon_yonu = 1

        self.slider.setValue(yeni_deger)

    def _video_kontrol_modu_degisti(self, sure_modu_aktif):
        self.widget_video_sure.setVisible(sure_modu_aktif)
        self.widget_video_hiz.setVisible(not sure_modu_aktif)
        self._video_ozet_guncelle()

    def _video_kare_dizisi_hesapla(self):
        fps = max(15, int(self.spin_video_fps.value())) if hasattr(self, "spin_video_fps") else 30
        max_sinir = float(self.spin_sinir.value()) if hasattr(self, "spin_sinir") else 120.0

        if hasattr(self, "rdo_video_sure") and self.rdo_video_sure.isChecked():
            toplam_sure = max(1.0, float(self.spin_video_sure.value()))
            toplam_kare = max(4, int(round(toplam_sure * fps)))
            yari_kare = max(2, toplam_kare // 2)
        else:
            hiz_sn = max(1.0, float(self.spin_video_hiz_sn.value())) if hasattr(self, "spin_video_hiz_sn") else 40.0
            yari_sure = max_sinir / hiz_sn
            yari_kare = max(2, int(round(yari_sure * fps)))

        acilma = [(i / float(yari_kare)) * max_sinir for i in range(yari_kare + 1)]
        kapanma = [((yari_kare - i) / float(yari_kare)) * max_sinir for i in range(1, yari_kare + 1)]
        oran_dizisi = acilma + kapanma
        gercek_sure = len(oran_dizisi) / float(fps)
        gercek_hiz = max_sinir / max(0.01, (yari_kare / float(fps)))
        return oran_dizisi, fps, gercek_sure, gercek_hiz

    def _video_ozet_guncelle(self, *_):
        if not hasattr(self, "lbl_video_ozet"):
            return
        oran_dizisi, fps, gercek_sure, gercek_hiz = self._video_kare_dizisi_hesapla()
        max_sinir = self.spin_sinir.value()
        self.lbl_video_ozet.setText(
            f"ℹ️ Özet: %0 → %{max_sinir} → %0 | Toplam Süre: {gercek_sure:.1f} sn "
            f"({gercek_sure/2.0:.1f} sn patlama + {gercek_sure/2.0:.1f} sn dönüş) | "
            f"Hız: {gercek_hiz:.1f} %/sn | {len(oran_dizisi)} Kare ({fps} FPS)"
        )

    def slider_degisti(self, deger):
        self._patlama_orani_uygula(float(deger), slider_senkronla=False)

    def _patlama_orani_uygula(self, deger_float, slider_senkronla=True):
        if slider_senkronla:
             self.slider.blockSignals(True)
             self.slider.setValue(int(round(deger_float)))
             self.slider.blockSignals(False)

        self.label.setText(f"Açılma Oranı: %{deger_float:.0f}\n(Slider üzerine gelin ve farenin tekerleğini kaydırın)")
        if not self.objects_data:
            return

        carpan = float(deger_float) / 100.0
        kademe_farki = float(self.spin_kademe.value())
        aktif_merkez = self.aktif_merkezi_getir()

        for d in self.objects_data:
            obj = d['obj']
            orijinal_konum = d['orijinal_konum']

            if d.get('sabit_mi', False):
                obj.Placement = orijinal_konum.copy()
                continue

            m_carpan = d['manuel_carpan']
            referans_merkez = d.get('aktif_merkez_ref', d['bbox_merkez'])
            dinamik_yon = referans_merkez - aktif_merkez

            if "X Ekseni" in self.patlama_tipi:
                vx = (dinamik_yon.x + (d['kademe_x'] * kademe_farki)) * m_carpan
                filtrelenmis_vektor = FreeCAD.Vector(vx, 0.0, 0.0)
            elif "Y Ekseni" in self.patlama_tipi:
                vy = (dinamik_yon.y + (d['kademe_y'] * kademe_farki)) * m_carpan
                filtrelenmis_vektor = FreeCAD.Vector(0.0, vy, 0.0)
            elif "Z Ekseni" in self.patlama_tipi:
                vz = (dinamik_yon.z + (d['kademe_z'] * kademe_farki)) * m_carpan
                filtrelenmis_vektor = FreeCAD.Vector(0.0, 0.0, vz)
            elif "XY Düzlemi" in self.patlama_tipi:
                vx = (dinamik_yon.x + (d['kademe_x'] * kademe_farki * 0.7)) * m_carpan
                vy = (dinamik_yon.y + (d['kademe_y'] * kademe_farki * 0.7)) * m_carpan
                filtrelenmis_vektor = FreeCAD.Vector(vx, vy, 0.0)
            else:
                vx = (dinamik_yon.x + (d['kademe_x'] * kademe_farki * 0.5)) * m_carpan
                vy = (dinamik_yon.y + (d['kademe_y'] * kademe_farki * 0.5)) * m_carpan
                vz = (dinamik_yon.z + (d['kademe_z'] * kademe_farki * 0.7)) * m_carpan
                filtrelenmis_vektor = FreeCAD.Vector(vx, vy, vz)

            yeni_konum = orijinal_konum.copy()
            yeni_konum.Base = orijinal_konum.Base + (filtrelenmis_vektor * carpan)
            obj.Placement = yeni_konum

        FreeCADGui.updateGui()

    def _goruntuyu_akilli_kirp(self, qimg, marj_orani=0.05):
        if qimg.isNull():
            return qimg, QtCore.QRect(0, 0, 1, 1)

        w, h = qimg.width(), qimg.height()
        bg_color = qimg.pixelColor(2, 2)
        bg_r, bg_g, bg_b = bg_color.red(), bg_color.green(), bg_color.blue()

        adim = max(2, min(w, h) // 250)
        min_x, min_y = w, h
        max_x, max_y = 0, 0
        bulundu = False
        esik = 18

        for y in range(0, h, adim):
            for x in range(0, w, adim):
                c = qimg.pixelColor(x, y)
                if abs(c.red() - bg_r) > esik or abs(c.green() - bg_g) > esik or abs(c.blue() - bg_b) > esik:
                    if x < min_x: min_x = x
                    if x > max_x: max_x = x
                    if y < min_y: min_y = y
                    if y > max_y: max_y = y
                    bulundu = True

        if not bulundu or max_x <= min_x or max_y <= min_y:
            tam_rect = QtCore.QRect(0, 0, w, h)
            return qimg, tam_rect

        icerik_w = max_x - min_x
        icerik_h = max_y - min_y
        pad_x = int(icerik_w * marj_orani) + 16
        pad_y = int(icerik_h * marj_orani) + 16

        cx = max(0, min_x - pad_x)
        cy = max(0, min_y - pad_y)
        cw = min(w - cx, icerik_w + 2 * pad_x)
        ch = min(h - cy, icerik_h + 2 * pad_y)

        kirp_rect = QtCore.QRect(cx, cy, max(1, cw), max(1, ch))
        return qimg.copy(kirp_rect), kirp_rect

    def _temiz_parca_kodu_olustur(self, metin):
        temiz = re.sub(r'[^A-Za-z0-9_-]', '_', str(metin).strip())
        temiz = re.sub(r'_+', '_', temiz).strip('_')
        return temiz[:32] or "PARCA"

    def _varsayilan_malzeme_bilgisi_getir(self, ozel_malzeme=None):
        secim = self.combo_pdf_malzeme.currentText()
        if "Paslanmaz" in secim:
            varsayilan_ad, yogunluk = "Paslanmaz Sac (AISI 304)", 7.93
        elif "Galvaniz" in secim:
            varsayilan_ad, yogunluk = "Galvaniz Sac (DX51D)", 7.85
        elif "Alüminyum" in secim:
            varsayilan_ad, yogunluk = "Alüminyum Sac (5754)", 2.70
        elif "Bakır" in secim:
            varsayilan_ad, yogunluk = "Bakır / Pirinç Sac", 8.50
        elif "Plastiği" in secim:
            varsayilan_ad, yogunluk = "ABS / Plastik", 1.05
        else:
            varsayilan_ad, yogunluk = "Sac (DKP / S235)", 7.85

        if ozel_malzeme:
            return ozel_malzeme, yogunluk
        return varsayilan_ad, yogunluk

    def _agirlik_formatla(self, gram):
        if gram >= 1000.0:
            return f"{gram / 1000.0:.2f} kg"
        return f"{gram:.1f} g"

    def _hesapla_otomatik_montaj_olculeri(self, fastener_dahil=False):
        min_x = min_y = min_z = float('inf')
        max_x = max_y = max_z = float('-inf')

        for d in self.objects_data:
            if d.get('kullanici_gizledi', False):
                continue
            if not fastener_dahil and d.get('fastener_mi', False):
                continue
            min_x = min(min_x, d['xmin'])
            max_x = max(max_x, d['xmax'])
            min_y = min(min_y, d['ymin'])
            max_y = max(max_y, d['ymax'])
            min_z = min(min_z, d['zmin'])
            max_z = max(max_z, d['zmax'])

        if min_x == float('inf'):
            return 0.0, 0.0, 0.0
        return max(0.0, max_x - min_x), max(0.0, max_y - min_y), max(0.0, max_z - min_z)

    def _manuel_olcu_modu_degisti(self, manuel_aktif):
        self.spin_asm_olcu_x.setEnabled(manuel_aktif)
        self.spin_asm_olcu_y.setEnabled(manuel_aktif)
        self.spin_asm_olcu_z.setEnabled(manuel_aktif)
        if not manuel_aktif:
            self.otomatik_montaj_olculerini_kutulara_yaz()

    def _fastener_pdf_degisti_olcu_guncelle(self, _):
        if hasattr(self, "chk_pdf_manuel_olcu") and not self.chk_pdf_manuel_olcu.isChecked():
            self.otomatik_montaj_olculerini_kutulara_yaz()

    def otomatik_montaj_olculerini_kutulara_yaz(self):
        fastener_dahil = self.chk_pdf_fastener.isChecked() if hasattr(self, "chk_pdf_fastener") else False
        ox, oy, oz = self._hesapla_otomatik_montaj_olculeri(fastener_dahil=fastener_dahil)
        if hasattr(self, "spin_asm_olcu_x"):
            self.spin_asm_olcu_x.setValue(max(0.1, ox))
            self.spin_asm_olcu_y.setValue(max(0.1, oy))
            self.spin_asm_olcu_z.setValue(max(0.1, oz))

    def _genel_montaj_olculerini_getir(self, fastener_dahil=False):
        if hasattr(self, "chk_pdf_manuel_olcu") and self.chk_pdf_manuel_olcu.isChecked():
            return (
                float(self.spin_asm_olcu_x.value()),
                float(self.spin_asm_olcu_y.value()),
                float(self.spin_asm_olcu_z.value())
            )
        return self._hesapla_otomatik_montaj_olculeri(fastener_dahil=fastener_dahil)

    def _kamerayi_net_ayarla(self, view, yon_kodu, ortografik_zorla=True):
        try:
            if hasattr(view, "setAnimationEnabled"):
                view.setAnimationEnabled(False)
        except Exception:
            pass

        if ortografik_zorla:
            try:
                view.setCameraType("Orthographic")
            except Exception:
                pass

        try:
            if yon_kodu == "front":
                view.viewFront()
            elif yon_kodu == "right":
                view.viewRight()
            elif yon_kodu == "top":
                view.viewTop()
            else:
                view.viewIsometric()
        except Exception:
            pass

        try:
            FreeCADGui.updateGui()
            QtWidgets.QApplication.processEvents()
            view.fitAll()
            FreeCADGui.updateGui()
            QtWidgets.QApplication.processEvents()
            QtCore.QThread.msleep(75)
            FreeCADGui.updateGui()
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _pdf_icin_bom_ogelerini_hazirla(self, fastener_dahil=True):
        bom_listesi = []
        islenen_gruplar = set()
        bireysel_anahtar_map = {}

        for d in self.objects_data:
            if d.get('kullanici_gizledi', False):
                continue
            if not fastener_dahil and d.get('fastener_mi', False):
                continue

            gid = d.get('grup_id')
            if gid and gid in self.gruplar:
                if gid in islenen_gruplar:
                    continue
                islenen_gruplar.add(gid)
                ginfo = self.gruplar[gid]
                uyeler = [
                    u for u in self.objects_data
                    if u['obj'] in ginfo['uyeler']
                    and not u.get('kullanici_gizledi', False)
                    and (fastener_dahil or not u.get('fastener_mi', False))
                ]
                if not uyeler:
                    continue
                grup_tamamen_fastener = all(u.get('fastener_mi', False) for u in uyeler) or ginfo.get('fastener_mi', False)
                if not fastener_dahil and grup_tamamen_fastener:
                    continue

                uye_noktalari = [self._parca_isaretleme_noktasi_getir(u['obj']) for u in uyeler]
                gx = sum(pt.x for pt in uye_noktalari) / len(uye_noktalari)
                gy = sum(pt.y for pt in uye_noktalari) / len(uye_noktalari)
                gz = sum(pt.z for pt in uye_noktalari) / len(uye_noktalari)
                ortak_nokta = FreeCAD.Vector(gx, gy, gz)
                # Grup merkezine en yakın üyenin gerçek kütle/yüzey noktasını seç (boşluğa düşmemesi için)
                en_yakin_nokta = min(uye_noktalari, key=lambda pt: (pt - ortak_nokta).Length)

                bx = max(u['xmax'] for u in uyeler) - min(u['xmin'] for u in uyeler)
                by = max(u['ymax'] for u in uyeler) - min(u['ymin'] for u in uyeler)
                bz = max(u['zmax'] for u in uyeler) - min(u['zmin'] for u in uyeler)
                toplam_hacim = sum(u.get('hacim_cm3', 0.0) for u in uyeler)
                malz_ad, yogunluk = self._varsayilan_malzeme_ve_yogunluk_getir_grup(uyeler)
                birim_agirlik_g = toplam_hacim * yogunluk
                durum = "Bağlantı Grubu" if grup_tamamen_fastener else ("Sabit Grup" if ginfo['mod'] == 'sabit' else "Alt Montaj")
                bom_listesi.append({
                    'ad': ginfo['ad'],
                    'adet': 1,
                    'parca_adedi': len(uyeler),
                    'olcu': f"{bx:.1f}x{by:.1f}x{bz:.1f}",
                    'bx': bx, 'by': by, 'bz': bz,
                    'hacim_cm3': toplam_hacim,
                    'malzeme': malz_ad,
                    'birim_agirlik_g': birim_agirlik_g,
                    'toplam_agirlik_g': birim_agirlik_g,
                    'durum': durum,
                    'merkez_3d': en_yakin_nokta,
                    'temsili_datalari': uyeler,
                    'fastener_mi': grup_tamamen_fastener,
                    'grup_mu': True
                })
            else:
                obj = d['obj']
                temel_ad = re.sub(r'[\s_-]*\d{3,}$', '', obj.Label).strip() or obj.Label
                olcu_anahtar = (
                    temel_ad.lower(),
                    round(d['uzunluk_x'], 1),
                    round(d['uzunluk_y'], 1),
                    round(d['uzunluk_z'], 1),
                    bool(d.get('fastener_mi', False))
                )
                if olcu_anahtar in bireysel_anahtar_map:
                    mevcut_kalem = bireysel_anahtar_map[olcu_anahtar]
                    mevcut_kalem['adet'] += 1
                    mevcut_kalem['toplam_agirlik_g'] = mevcut_kalem['birim_agirlik_g'] * mevcut_kalem['adet']
                    mevcut_kalem['temsili_datalari'].append(d)
                else:
                    guncel_merkez = self._parca_isaretleme_noktasi_getir(obj)
                    malz_ad, yogunluk = self._varsayilan_malzeme_bilgisi_getir(d.get('ozel_malzeme'))
                    hacim = d.get('hacim_cm3', 0.0)
                    birim_agirlik_g = hacim * yogunluk
                    yeni_kalem = {
                        'ad': temel_ad,
                        'adet': 1,
                        'parca_adedi': 1,
                        'olcu': f"{d['uzunluk_x']:.1f}x{d['uzunluk_y']:.1f}x{d['uzunluk_z']:.1f}",
                        'bx': d['uzunluk_x'], 'by': d['uzunluk_y'], 'bz': d['uzunluk_z'],
                        'hacim_cm3': hacim,
                        'malzeme': malz_ad,
                        'birim_agirlik_g': birim_agirlik_g,
                        'toplam_agirlik_g': birim_agirlik_g,
                        'durum': "Bağlantı El." if d.get('fastener_mi') else ("Sabit" if d.get('sabit_mi') else "İmalat"),
                        'merkez_3d': guncel_merkez,
                        'temsili_datalari': [d],
                        'fastener_mi': d.get('fastener_mi', False),
                        'grup_mu': False
                    }
                    bireysel_anahtar_map[olcu_anahtar] = yeni_kalem
                    bom_listesi.append(yeni_kalem)

        dok_kodu = self.txt_pdf_dok_kod.text().strip() or "TD-001"
        for idx, kalem in enumerate(bom_listesi, start=1):
            kalem['poz'] = idx
            kalem['parca_no'] = f"{dok_kodu}-P{idx:02d}"
        return bom_listesi

    def _bom_verisini_sayfalara_bol(self, bom_verisi, sayfa_w=3508, sayfa_h=2480, max_kalem_sayfa=25):
        """
        BOM tablosundaki kalem sayısı tablo yüksekliğini veya sayfa başına maksimum balon/satır
        sınırını (varsayılan 25) aştığında, aynı patlatılmış görünüm üzerinde Pafta 1/2, Pafta 2/2
        olarak ardışık sayfalarda gösterilmek üzere BOM listesini sayfa dilimlerine böler.
        """
        if not bom_verisi:
            return [[]]

        sayfa_w = max(1200, int(sayfa_w))
        sayfa_h = max(900, int(sayfa_h))
        kenar_bosluk = int(min(sayfa_w, sayfa_h) * 0.032)
        cerceve_w = sayfa_w - (2 * kenar_bosluk)
        cerceve_h = sayfa_h - (2 * kenar_bosluk)
        antet_yukseklik = int(sayfa_h * 0.135) if self.chk_pdf_antet.isChecked() else 0
        bom_genislik = int(cerceve_w * 0.33)
        bom_h = cerceve_h - antet_yukseklik
        baslik_h = int(sayfa_h * 0.032)
        temel_satir_h = int(sayfa_h * 0.023)
        kullanilabilir_h = max(100, bom_h - baslik_h)

        test_img = QtGui.QImage(32, 32, QtGui.QImage.Format_ARGB32)
        test_painter = QtGui.QPainter(test_img)
        test_painter.setFont(QtGui.QFont("Arial", 7))
        agirlik_goster = self.chk_pdf_agirlik.isChecked()

        sayfalar = []
        mevcut_sayfa = []
        biriken_h = 0

        for kalem in bom_verisi:
            if agirlik_goster:
                ag_str = self._agirlik_formatla(kalem.get('toplam_agirlik_g', 0.0))
                satir_metni = f"{kalem['poz']:02d} | {kalem['parca_no']} - {kalem['ad']} | {kalem['adet']} Ad. | {kalem['olcu']} | {ag_str}"
            else:
                satir_metni = f"{kalem['poz']:02d} | {kalem['parca_no']} - {kalem['ad']} | {kalem['adet']} Ad. | {kalem['olcu']}"

            satir_h = self._metin_yuksekligi_hesapla(test_painter, satir_metni, bom_genislik - 16, temel_satir_h, dolgulu_bosluk=8)

            if mevcut_sayfa and (biriken_h + satir_h > kullanilabilir_h or len(mevcut_sayfa) >= max_kalem_sayfa):
                sayfalar.append(mevcut_sayfa)
                mevcut_sayfa = [kalem]
                biriken_h = satir_h
            else:
                mevcut_sayfa.append(kalem)
                biriken_h += satir_h

        test_painter.end()
        if mevcut_sayfa:
            sayfalar.append(mevcut_sayfa)

        return sayfalar

    def _varsayilan_malzeme_ve_yogunluk_getir_grup(self, uyeler):
        for u in uyeler:
            if u.get('ozel_malzeme'):
                return self._varsayilan_malzeme_bilgisi_getir(u.get('ozel_malzeme'))
        return self._varsayilan_malzeme_bilgisi_getir(None)

    def _metin_yuksekligi_hesapla(self, painter, metin, genislik, min_yukseklik, dolgulu_bosluk=10):
        rect = painter.boundingRect(
            QtCore.QRect(0, 0, max(20, genislik), 2000),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter | QtCore.Qt.TextWordWrap,
            str(metin)
        )
        return max(min_yukseklik, rect.height() + dolgulu_bosluk)

    def _bilgi_karti_dinamik_ciz(self, painter, kart_x, kart_y, bilgi_genislik, cizim_h, baslik_metni, baslik_renk_hex, satirlar, sayfa_h, kalin_kalem, ince_kalem):
        painter.setPen(kalin_kalem)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#f8fafc")))
        painter.drawRect(kart_x, kart_y, bilgi_genislik, cizim_h)

        kart_baslik_h = int(sayfa_h * 0.036)
        painter.fillRect(kart_x, kart_y, bilgi_genislik, kart_baslik_h, QtGui.QColor(baslik_renk_hex))
        painter.drawRect(kart_x, kart_y, bilgi_genislik, kart_baslik_h)

        font_kart_b = QtGui.QFont("Arial", 8)
        font_kart_b.setBold(True)
        painter.setFont(font_kart_b)
        painter.setPen(QtGui.QColor("#0f172a"))
        painter.drawText(
            QtCore.QRect(kart_x + 10, kart_y, bilgi_genislik - 20, kart_baslik_h),
            QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap,
            baslik_metni
        )

        temel_satir_h = int(sayfa_h * 0.026)
        etiket_w = int(bilgi_genislik * 0.44)
        deger_w = bilgi_genislik - etiket_w - 20
        deger_x = kart_x + etiket_w + 10

        painter_font = QtGui.QFont("Arial", 8)
        painter.setFont(painter_font)

        guncel_y = kart_y + kart_baslik_h
        for etiket, deger in satirlar:
            etiket_str = f"{etiket}:"
            deger_str = str(deger)
            h_etiket = self._metin_yuksekligi_hesapla(painter, etiket_str, etiket_w - 12, temel_satir_h)
            h_deger = self._metin_yuksekligi_hesapla(painter, deger_str, deger_w, temel_satir_h)
            dinamik_h = max(h_etiket, h_deger)

            if guncel_y + dinamik_h > kart_y + cizim_h:
                break

            painter.setPen(ince_kalem)
            painter.drawLine(kart_x, guncel_y + dinamik_h, kart_x + bilgi_genislik, guncel_y + dinamik_h)
            painter.drawLine(kart_x + etiket_w, guncel_y, kart_x + etiket_w, guncel_y + dinamik_h)

            painter_font.setBold(True)
            painter.setFont(painter_font)
            painter.setPen(QtGui.QColor("#334155"))
            painter.drawText(
                QtCore.QRect(kart_x + 8, guncel_y + 2, etiket_w - 12, dinamik_h - 4),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap,
                etiket_str
            )

            painter_font.setBold(False)
            painter.setFont(painter_font)
            painter.setPen(QtGui.QColor("#0f172a"))
            painter.drawText(
                QtCore.QRect(deger_x, guncel_y + 2, deger_w, dinamik_h - 4),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap,
                deger_str
            )
            guncel_y += dinamik_h

    def _as9100_antet_ciz(self, painter, cerceve_x, cerceve_y, cerceve_w, cerceve_h, antet_yukseklik, baslik_metni, dokuman_no, sayfa_no_metni, aciklama_alt):
        antet_x = cerceve_x
        antet_y = cerceve_y + cerceve_h - antet_yukseklik
        antet_w = cerceve_w
        antet_h = antet_yukseklik

        kalin_kalem = QtGui.QPen(QtGui.QColor("#0f172a"), max(4, int(cerceve_w * 0.002)))
        ince_kalem = QtGui.QPen(QtGui.QColor("#334155"), max(2, int(cerceve_w * 0.001)))

        painter.setPen(kalin_kalem)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#f8fafc")))
        painter.drawRect(antet_x, antet_y, antet_w, antet_h)

        bolme_1 = antet_x + int(antet_w * 0.46)
        bolme_2 = antet_x + int(antet_w * 0.75)

        h1 = int(antet_h * 0.38)
        h2 = int(antet_h * 0.31)
        h3 = antet_h - h1 - h2
        satir_1_y = antet_y + h1
        satir_2_y = satir_1_y + h2

        painter.setPen(ince_kalem)
        painter.drawLine(bolme_1, antet_y, bolme_1, antet_y + antet_h)
        painter.drawLine(bolme_2, antet_y, bolme_2, antet_y + antet_h)
        painter.drawLine(antet_x, satir_1_y, antet_x + antet_w, satir_1_y)
        painter.drawLine(antet_x, satir_2_y, antet_x + antet_w, satir_2_y)

        tarih_str = datetime.datetime.now().strftime("%d.%m.%Y")
        cizen_str = self.txt_pdf_cizen.text().strip() or "Tasarım Mühendisi"
        onay_str = self.txt_pdf_onay.text().strip() or "Kalite / Üretim Onayı"
        standart_str = self.combo_pdf_standart.currentText()
        rev_no = self.txt_pdf_rev.text().strip() or "R00"
        wrap_flag = QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap

        font_baslik = QtGui.QFont("Arial", 9)
        font_baslik.setBold(True)
        painter.setFont(font_baslik)
        painter.setPen(QtGui.QColor("#0f172a"))
        painter.drawText(
            QtCore.QRect(antet_x + 14, antet_y + 2, bolme_1 - antet_x - 22, h1 - 4),
            wrap_flag,
            f"BAŞLIK: {baslik_metni}"
        )

        font_orta = QtGui.QFont("Arial", 8)
        painter.setFont(font_orta)
        painter.setPen(QtGui.QColor("#1e293b"))
        painter.drawText(
            QtCore.QRect(antet_x + 14, satir_1_y + 2, bolme_1 - antet_x - 22, h2 - 4),
            wrap_flag,
            f"GENEL TOLERANS: {standart_str} (Birim: mm)"
        )
        painter.drawText(
            QtCore.QRect(antet_x + 14, satir_2_y + 2, bolme_1 - antet_x - 22, h3 - 4),
            wrap_flag,
            aciklama_alt
        )

        painter.drawText(
            QtCore.QRect(bolme_1 + 14, antet_y + 2, bolme_2 - bolme_1 - 22, h1 - 4),
            wrap_flag,
            f"HAZIRLAYAN / ÇİZEN: {cizen_str}"
        )
        painter.drawText(
            QtCore.QRect(bolme_1 + 14, satir_1_y + 2, bolme_2 - bolme_1 - 22, h2 - 4),
            wrap_flag,
            f"KONTROL / ONAY: {onay_str}"
        )
        painter.drawText(
            QtCore.QRect(bolme_1 + 14, satir_2_y + 2, bolme_2 - bolme_1 - 22, h3 - 4),
            wrap_flag,
            f"TARİH: {tarih_str}  |  PROJEKSİYON: ISO-E"
        )

        font_sag = QtGui.QFont("Arial", 8)
        font_sag.setBold(True)
        painter.setFont(font_sag)
        painter.setPen(QtGui.QColor("#0f172a"))
        painter.drawText(
            QtCore.QRect(bolme_2 + 14, antet_y + 2, antet_x + antet_w - bolme_2 - 22, h1 - 4),
            wrap_flag,
            f"DOKÜMAN NO: {dokuman_no}"
        )
        painter.setPen(QtGui.QColor("#b91c1c"))
        painter.drawText(
            QtCore.QRect(bolme_2 + 14, satir_1_y + 2, antet_x + antet_w - bolme_2 - 22, h2 - 4),
            wrap_flag,
            f"REVİZYON NO: {rev_no}"
        )
        painter.setPen(QtGui.QColor("#0f172a"))
        painter.drawText(
            QtCore.QRect(bolme_2 + 14, satir_2_y + 2, antet_x + antet_w - bolme_2 - 22, h3 - 4),
            wrap_flag,
            f"SAYFA: {sayfa_no_metni}"
        )

    def _olcu_cizgisi_ciz(self, painter, x1, y1, x2, y2, metin, dikey_mi=False):
        kalem = QtGui.QPen(QtGui.QColor("#0284c7"), max(2, int(abs(x2 - x1 + y2 - y1) * 0.008)))
        painter.setPen(kalem)
        painter.drawLine(x1, y1, x2, y2)

        ok_boyu = 10
        if not dikey_mi:
            painter.drawLine(x1, y1 - 8, x1, y1 + 8)
            painter.drawLine(x2, y2 - 8, x2, y2 + 8)
            painter.drawLine(x1, y1, x1 + ok_boyu, y1 - 5)
            painter.drawLine(x1, y1, x1 + ok_boyu, y1 + 5)
            painter.drawLine(x2, y2, x2 - ok_boyu, y2 - 5)
            painter.drawLine(x2, y2, x2 - ok_boyu, y2 + 5)
            metin_rect = QtCore.QRect((x1 + x2) // 2 - 90, y1 - 28, 180, 24)
            painter.fillRect(metin_rect, QtGui.QColor("#ffffff"))
            painter.drawText(metin_rect, QtCore.Qt.AlignCenter, metin)
        else:
            painter.drawLine(x1 - 8, y1, x1 + 8, y1)
            painter.drawLine(x2 - 8, y2, x2 + 8, y2)
            painter.drawLine(x1, y1, x1 - 5, y1 + ok_boyu)
            painter.drawLine(x1, y1, x1 + 5, y1 + ok_boyu)
            painter.drawLine(x2, y2, x2 - 5, y2 - ok_boyu)
            painter.drawLine(x2, y2, x2 + 5, y2 - ok_boyu)
            metin_rect = QtCore.QRect(x1 + 8, (y1 + y2) // 2 - 12, 135, 24)
            painter.fillRect(metin_rect, QtGui.QColor("#ffffff"))
            painter.drawText(metin_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, metin)

    def _genel_montaj_patlamamis_sayfasini_ciz(self, painter, view, temp_dir, bom_verisi, sayfa_no_metni):
        sayfa_w = painter.device().width()
        sayfa_h = painter.device().height()
        painter.fillRect(0, 0, sayfa_w, sayfa_h, QtGui.QColor("#ffffff"))

        kenar_bosluk = int(min(sayfa_w, sayfa_h) * 0.032)
        cerceve_x = kenar_bosluk
        cerceve_y = kenar_bosluk
        cerceve_w = sayfa_w - (2 * kenar_bosluk)
        cerceve_h = sayfa_h - (2 * kenar_bosluk)
        antet_yukseklik = int(sayfa_h * 0.135) if self.chk_pdf_antet.isChecked() else 0
        bilgi_genislik = int(cerceve_w * 0.28)

        kalin_kalem = QtGui.QPen(QtGui.QColor("#0f172a"), max(4, int(sayfa_w * 0.002)))
        ince_kalem = QtGui.QPen(QtGui.QColor("#475569"), max(2, int(sayfa_w * 0.001)))

        painter.setPen(kalin_kalem)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(cerceve_x, cerceve_y, cerceve_w, cerceve_h)

        fastener_dahil = self.chk_pdf_fastener.isChecked()
        for d in self.objects_data:
            obj = d['obj']
            gorunur_mu = (not d.get('kullanici_gizledi', False)) and (fastener_dahil or not d.get('fastener_mi', False))
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Visibility = gorunur_mu
            obj.Placement = d['orijinal_konum'].copy()

        asm_bx, asm_by, asm_bz = self._genel_montaj_olculerini_getir(fastener_dahil=fastener_dahil)
        FreeCADGui.updateGui()

        cizim_w = cerceve_w - bilgi_genislik
        cizim_h = cerceve_h - antet_yukseklik
        hucre_w = cizim_w // 2
        hucre_h = cizim_h // 2

        gorunusler = [
            ("ÖN GÖRÜNÜŞ (GENEL MONTAJ - FRONT)", "front", cerceve_x, cerceve_y, asm_bx, asm_bz),
            ("SOL YAN GÖRÜNÜŞ (GENEL MONTAJ - SIDE)", "right", cerceve_x + hucre_w, cerceve_y, asm_by, asm_bz),
            ("ÜST GÖRÜNÜŞ (GENEL MONTAJ - TOP)", "top", cerceve_x, cerceve_y + hucre_h, asm_bx, asm_by),
            ("İZOMETRİK 3D NİHAİ MONTAJ (PATLAMAMIŞ)", "iso", cerceve_x + hucre_w, cerceve_y + hucre_h, None, None)
        ]

        painter.setPen(ince_kalem)
        painter.drawLine(cerceve_x + hucre_w, cerceve_y, cerceve_x + hucre_w, cerceve_y + cizim_h)
        painter.drawLine(cerceve_x, cerceve_y + hucre_h, cerceve_x + cizim_w, cerceve_y + hucre_h)

        beyaz_arkaplan = "Beyaz" in self.combo_pdf_arkaplan.currentText()

        for baslik, yon_kodu, gx, gy, olcu_yatay, olcu_dikey in gorunusler:
            try:
                self._kamerayi_net_ayarla(view, yon_kodu, ortografik_zorla=True)

                g_path = os.path.join(temp_dir, f"asm_unexploded_{yon_kodu}.png")
                try:
                    if beyaz_arkaplan:
                        view.saveImage(g_path, 1400, 1050, "White")
                    else:
                        view.saveImage(g_path, 1400, 1050, "Current")
                except Exception:
                    view.saveImage(g_path)

                ham_img = QtGui.QImage(g_path)
                kirpilmis, _ = self._goruntuyu_akilli_kirp(ham_img, marj_orani=0.05)

                etiket_h = int(hucre_h * 0.10)
                font_etiket = QtGui.QFont("Arial", 8)
                font_etiket.setBold(True)
                painter.setFont(font_etiket)
                painter.setPen(QtGui.QColor("#0f172a"))
                painter.drawText(
                    QtCore.QRect(gx + 16, gy + 8, hucre_w - 32, etiket_h),
                    QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                    baslik
                )

                ic_x = gx + int(hucre_w * 0.11)
                ic_y = gy + etiket_h + int(hucre_h * 0.05)
                ic_w = int(hucre_w * 0.70)
                ic_h = int(hucre_h * 0.70)

                if not kirpilmis.isNull():
                    oran_img = kirpilmis.width() / float(max(1, kirpilmis.height()))
                    oran_ic = ic_w / float(max(1, ic_h))
                    if oran_img > oran_ic:
                        rw = ic_w
                        rh = int(ic_w / oran_img)
                        rx = ic_x
                        ry = ic_y + (ic_h - rh) // 2
                    else:
                        rh = ic_h
                        rw = int(ic_h * oran_img)
                        rx = ic_x + (ic_w - rw) // 2
                        ry = ic_y

                    painter.drawImage(QtCore.QRect(rx, ry, rw, rh), kirpilmis)

                    if self.chk_pdf_olcu_goster.isChecked() and olcu_yatay is not None and olcu_dikey is not None and olcu_yatay > 0 and olcu_dikey > 0:
                        font_olcu = QtGui.QFont("Arial", 8)
                        font_olcu.setBold(True)
                        painter.setFont(font_olcu)
                        self._olcu_cizgisi_ciz(painter, rx, ry + rh + 24, rx + rw, ry + rh + 24, f"{olcu_yatay:.1f} mm", dikey_mi=False)
                        self._olcu_cizgisi_ciz(painter, rx + rw + 22, ry, rx + rw + 22, ry + rh, f"{olcu_dikey:.1f} mm", dikey_mi=True)
            except Exception:
                continue

        kart_x = cerceve_x + cizim_w
        kart_y = cerceve_y

        proje_baslik = self.txt_pdf_baslik.text().strip() or (self.doc.Label if self.doc else "Montaj")
        dok_kod = self.txt_pdf_dok_kod.text().strip() or "TD-001"
        rev_no = self.txt_pdf_rev.text().strip() or "R00"
        agirlik_goster = self.chk_pdf_agirlik.isChecked()
        malz_kisa, _ = self._varsayilan_malzeme_bilgisi_getir(None)

        toplam_benzersiz_kalem = len(bom_verisi)
        toplam_parca_adedi = sum(k.get('parca_adedi', 1) * k.get('adet', 1) for k in bom_verisi)
        toplam_hacim_cm3 = sum(k.get('hacim_cm3', 0.0) * k.get('adet', 1) for k in bom_verisi)
        toplam_agirlik_g = sum(k.get('toplam_agirlik_g', 0.0) for k in bom_verisi)

        satirlar = [
            ("ÜRÜN / PROJE ADI", proje_baslik),
            ("MONTAJ KODU", f"{dok_kod}-ASM-00"),
            ("REVİZYON NO", rev_no),
            ("GÖRÜNÜM DURUMU", "Patlamamış (Toplu Montaj)"),
            ("TOPLAM KALEM (POZ)", f"{toplam_benzersiz_kalem} Kalem"),
            ("TOPLAM PARÇA ADEDİ", f"{toplam_parca_adedi} Adet"),
        ]

        if agirlik_goster:
            satirlar.append(("ANA MALZEME CİNSİ", malz_kisa))

        satirlar.extend([
            ("DIŞ GENİŞLİK (X)", f"{asm_bx:.2f} mm"),
            ("DIŞ DERİNLİK (Y)", f"{asm_by:.2f} mm"),
            ("DIŞ YÜKSEKLİK (Z)", f"{asm_bz:.2f} mm"),
        ])

        if agirlik_goster:
            satirlar.extend([
                ("TOPLAM NET HACİM", f"{toplam_hacim_cm3:.2f} cm³"),
                ("TOPLAM AĞIRLIK", self._agirlik_formatla(toplam_agirlik_g)),
            ])

        satirlar.extend([
            ("BAĞLANTI ELEMANLARI", "Dahil" if fastener_dahil else "Hariç Tutuldu"),
            ("GENEL TOLERANS", "ISO 2768 - mK"),
            ("KENAR İŞLEMİ", "Çapakları Alınmış / Keskin Köşe Yok")
        ])

        self._bilgi_karti_dinamik_ciz(
            painter, kart_x, kart_y, bilgi_genislik, cizim_h,
            "GENEL MONTAJ BİLGİ KARTI", "#dbeafe", satirlar, sayfa_h, kalin_kalem, ince_kalem
        )

        if self.chk_pdf_antet.isChecked():
            if agirlik_goster:
                aciklama_alt = (
                    f"GENEL MONTAJ DIŞ ÖLÇÜLERİ: {asm_bx:.1f}x{asm_by:.1f}x{asm_bz:.1f} mm | "
                    f"TOPLAM AĞIRLIK: {self._agirlik_formatla(toplam_agirlik_g)} | PARÇA: {toplam_parca_adedi} ADET"
                )
            else:
                aciklama_alt = (
                    f"GENEL MONTAJ DIŞ ÖLÇÜLERİ: {asm_bx:.1f} x {asm_by:.1f} x {asm_bz:.1f} mm | "
                    f"TOPLAM KALEM: {toplam_benzersiz_kalem} ({toplam_parca_adedi} Parça)"
                )
            self._as9100_antet_ciz(
                painter, cerceve_x, cerceve_y, cerceve_w, cerceve_h, antet_yukseklik,
                f"{proje_baslik} (GENEL MONTAJ)", f"{dok_kod}-ASM-00", sayfa_no_metni, aciklama_alt
            )

    def _montaj_sayfasini_ciz(self, painter, view, temp_dir, bom_verisi, sayfa_no_metni, tum_bom_verisi=None, pafta_no=1, toplam_pafta=1):
        if tum_bom_verisi is None:
            tum_bom_verisi = bom_verisi

        sayfa_w = painter.device().width()
        sayfa_h = painter.device().height()
        painter.fillRect(0, 0, sayfa_w, sayfa_h, QtGui.QColor("#ffffff"))

        fastener_dahil = self.chk_pdf_fastener.isChecked()
        for d in self.objects_data:
            obj = d['obj']
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Visibility = (not d.get('kullanici_gizledi', False)) and (fastener_dahil or not d.get('fastener_mi', False))
        FreeCADGui.updateGui()

        kenar_bosluk = int(min(sayfa_w, sayfa_h) * 0.032)
        cerceve_x = kenar_bosluk
        cerceve_y = kenar_bosluk
        cerceve_w = sayfa_w - (2 * kenar_bosluk)
        cerceve_h = sayfa_h - (2 * kenar_bosluk)

        antet_yukseklik = int(sayfa_h * 0.135) if self.chk_pdf_antet.isChecked() else 0
        bom_genislik = int(cerceve_w * 0.33) if self.chk_pdf_bom.isChecked() else 0

        resim_alani_x = cerceve_x + int(kenar_bosluk * 0.5)
        resim_alani_y = cerceve_y + int(kenar_bosluk * 0.5)
        resim_alani_w = cerceve_w - bom_genislik - kenar_bosluk
        resim_alani_h = cerceve_h - antet_yukseklik - kenar_bosluk

        if self.chk_pdf_oto_ideal.isChecked():
            self._kamerayi_net_ayarla(view, "iso", ortografik_zorla=True)
        elif self.chk_pdf_tam_sigdir.isChecked():
            try:
                if hasattr(view, "setAnimationEnabled"):
                    view.setAnimationEnabled(False)
                view.fitAll()
                FreeCADGui.updateGui()
                QtWidgets.QApplication.processEvents()
                QtCore.QThread.msleep(60)
            except Exception:
                pass

        try:
            vw, vh = view.getSize()
            vw, vh = max(400, int(vw)), max(300, int(vh))
        except Exception:
            vw, vh = 1600, 1200

        # Kamera projeksiyonu (getPointOnScreen) ile kaydedilen resmin en-boy oranını birebir aynı tut
        olcek_carpan = max(2.0, 1600.0 / float(max(1, vw)), 1200.0 / float(max(1, vh)))
        hedef_render_w = int(round(vw * olcek_carpan))
        hedef_render_h = int(round(vh * olcek_carpan))

        temp_img = os.path.join(temp_dir, "exploded_render.png")
        beyaz_arkaplan = "Beyaz" in self.combo_pdf_arkaplan.currentText()
        try:
            if beyaz_arkaplan:
                view.saveImage(temp_img, hedef_render_w, hedef_render_h, "White")
            else:
                view.saveImage(temp_img, hedef_render_w, hedef_render_h, "Current")
        except Exception:
            view.saveImage(temp_img)

        ham_qimg = QtGui.QImage(temp_img)
        ham_w = max(1, ham_qimg.width())
        ham_h = max(1, ham_qimg.height())

        if self.chk_pdf_tam_sigdir.isChecked() and not ham_qimg.isNull():
            qimg, kirp_rect = self._goruntuyu_akilli_kirp(ham_qimg, marj_orani=0.06)
        else:
            qimg = ham_qimg
            kirp_rect = QtCore.QRect(0, 0, ham_w, ham_h)

        balon_payi = int(resim_alani_w * 0.08) if self.chk_pdf_balon.isChecked() else 10
        ic_alan_x = resim_alani_x + balon_payi
        ic_alan_y = resim_alani_y + int(resim_alani_h * 0.03)
        ic_alan_w = max(100, resim_alani_w - 2 * balon_payi)
        ic_alan_h = max(100, int(resim_alani_h * 0.94))

        cizilen_x, cizilen_y, cizilen_w, cizilen_h = ic_alan_x, ic_alan_y, ic_alan_w, ic_alan_h
        if not qimg.isNull():
            oran_img = qimg.width() / float(max(1, qimg.height()))
            oran_alan = ic_alan_w / float(max(1, ic_alan_h))
            if oran_img > oran_alan:
                cizilen_w = ic_alan_w
                cizilen_h = int(ic_alan_w / oran_img)
                cizilen_x = ic_alan_x
                cizilen_y = ic_alan_y + (ic_alan_h - cizilen_h) // 2
            else:
                cizilen_h = ic_alan_h
                cizilen_w = int(ic_alan_h * oran_img)
                cizilen_x = ic_alan_x + (ic_alan_w - cizilen_w) // 2
                cizilen_y = ic_alan_y

            painter.drawImage(QtCore.QRect(cizilen_x, cizilen_y, cizilen_w, cizilen_h), qimg)

        # Bu paftaya ait olan BOM dilimindeki poz numaralarının balonlarını çiz
        if self.chk_pdf_balon.isChecked() and bom_verisi:
            balon_cap = max(36, int(min(sayfa_w, sayfa_h) * 0.021))
            font_balon = QtGui.QFont("Arial", 9)
            font_balon.setBold(True)
            painter.setFont(font_balon)

            hedef_noktalar = []
            for kalem in bom_verisi:
                try:
                    # Patlatılmış güncel sahne konumundan parçanın gerçek kütle/yüzey merkezini al
                    if kalem.get('temsili_datalari'):
                        if kalem.get('grup_mu', False):
                            pts = [self._parca_isaretleme_noktasi_getir(u['obj']) for u in kalem['temsili_datalari']]
                            ort = FreeCAD.Vector(
                                sum(p.x for p in pts) / len(pts),
                                sum(p.y for p in pts) / len(pts),
                                sum(p.z for p in pts) / len(pts)
                            )
                            hedef_3d = min(pts, key=lambda p: (p - ort).Length)
                        else:
                            hedef_3d = self._parca_isaretleme_noktasi_getir(kalem['temsili_datalari'][0]['obj'])
                    else:
                        hedef_3d = kalem['merkez_3d']

                    ekran_pt = view.getPointOnScreen(hedef_3d)
                    if ekran_pt:
                        sx, sy = ekran_pt
                        ix = (sx / float(max(1, vw))) * ham_w
                        iy = (1.0 - (sy / float(max(1, vh)))) * ham_h
                        u = (ix - kirp_rect.x()) / float(max(1, kirp_rect.width()))
                        v = (iy - kirp_rect.y()) / float(max(1, kirp_rect.height()))
                        u = max(0.02, min(0.98, u))
                        v = max(0.02, min(0.98, v))
                        px = int(cizilen_x + u * cizilen_w)
                        py = int(cizilen_y + v * cizilen_h)
                        hedef_noktalar.append((kalem['poz'], px, py))
                except Exception:
                    continue

            hedef_noktalar.sort(key=lambda t: t[2])
            orta_x = cizilen_x + (cizilen_w // 2)
            sol_hedefler = [h for h in hedef_noktalar if h[1] <= orta_x]
            sag_hedefler = [h for h in hedef_noktalar if h[1] > orta_x]

            def balon_grubu_ciz(liste, kenar_x):
                if not liste:
                    return
                adim_y = max(balon_cap + 10, resim_alani_h // max(1, len(liste) + 1))
                for i, (poz_no, px, py) in enumerate(liste):
                    bx = kenar_x
                    by = resim_alani_y + int((i + 0.8) * adim_y)
                    by = max(resim_alani_y + balon_cap, min(resim_alani_y + resim_alani_h - balon_cap, by))

                    painter.setPen(QtGui.QPen(QtGui.QColor("#1e293b"), max(2, balon_cap // 14)))
                    painter.drawLine(px, py, bx, by)

                    painter.setBrush(QtGui.QBrush(QtGui.QColor("#dc2626")))
                    nokta_r = max(5, balon_cap // 6)
                    painter.drawEllipse(QtCore.QPoint(px, py), nokta_r, nokta_r)

                    painter.setPen(QtGui.QPen(QtGui.QColor("#0f172a"), max(3, balon_cap // 10)))
                    painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
                    painter.drawEllipse(QtCore.QPoint(bx, by), balon_cap // 2, balon_cap // 2)

                    b_rect = QtCore.QRect(bx - balon_cap // 2, by - balon_cap // 2, balon_cap, balon_cap)
                    painter.drawText(b_rect, QtCore.Qt.AlignCenter, str(poz_no))

            balon_grubu_ciz(sol_hedefler, resim_alani_x + balon_cap // 2 + 6)
            balon_grubu_ciz(sag_hedefler, resim_alani_x + resim_alani_w - balon_cap // 2 - 6)

        kalin_kalem = QtGui.QPen(QtGui.QColor("#0f172a"), max(4, int(sayfa_w * 0.002)))
        ince_kalem = QtGui.QPen(QtGui.QColor("#334155"), max(2, int(sayfa_w * 0.001)))

        if self.chk_pdf_antet.isChecked() or self.chk_pdf_bom.isChecked():
            painter.setPen(kalin_kalem)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(cerceve_x, cerceve_y, cerceve_w, cerceve_h)

        poz_aralik_etiketi = ""
        if bom_verisi:
            ilk_poz = bom_verisi[0]['poz']
            son_poz = bom_verisi[-1]['poz']
            if toplam_pafta > 1:
                poz_aralik_etiketi = f" (POZ {ilk_poz:02d}-{son_poz:02d})"

        if self.chk_pdf_bom.isChecked() and bom_verisi:
            bom_x = cerceve_x + cerceve_w - bom_genislik
            bom_y = cerceve_y
            bom_h = cerceve_h - antet_yukseklik

            painter.setPen(kalin_kalem)
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#f8fafc")))
            painter.drawRect(bom_x, bom_y, bom_genislik, bom_h)

            baslik_h = int(sayfa_h * 0.032)
            painter.fillRect(bom_x, bom_y, bom_genislik, baslik_h, QtGui.QColor("#e2e8f0"))
            painter.drawRect(bom_x, bom_y, bom_genislik, baslik_h)

            agirlik_goster = self.chk_pdf_agirlik.isChecked()
            font_bom_baslik = QtGui.QFont("Arial", 8)
            font_bom_baslik.setBold(True)
            painter.setFont(font_bom_baslik)
            painter.setPen(QtGui.QColor("#0f172a"))
            baslik_satiri = (
                f"POZ{poz_aralik_etiketi} | KOD & AD | ADET | ÖLÇÜ (mm) | AĞIRLIK"
                if agirlik_goster else
                f"POZ{poz_aralik_etiketi} | PARÇA KODU & ADI (BOM) | ADET | ÖLÇÜ (mm)"
            )
            painter.drawText(
                QtCore.QRect(bom_x + 8, bom_y, bom_genislik - 16, baslik_h),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                baslik_satiri
            )

            temel_satir_h = int(sayfa_h * 0.023)
            painter.setFont(QtGui.QFont("Arial", 7))
            guncel_bom_y = bom_y + baslik_h

            for kalem in bom_verisi:
                if agirlik_goster:
                    ag_str = self._agirlik_formatla(kalem.get('toplam_agirlik_g', 0.0))
                    satir_metni = f"{kalem['poz']:02d} | {kalem['parca_no']} - {kalem['ad']} | {kalem['adet']} Ad. | {kalem['olcu']} | {ag_str}"
                else:
                    satir_metni = f"{kalem['poz']:02d} | {kalem['parca_no']} - {kalem['ad']} | {kalem['adet']} Ad. | {kalem['olcu']}"

                dinamik_satir_h = self._metin_yuksekligi_hesapla(painter, satir_metni, bom_genislik - 16, temel_satir_h, dolgulu_bosluk=8)
                if guncel_bom_y + dinamik_satir_h > bom_y + bom_h:
                    break

                painter.setPen(ince_kalem)
                painter.drawLine(bom_x, guncel_bom_y + dinamik_satir_h, bom_x + bom_genislik, guncel_bom_y + dinamik_satir_h)
                painter.setPen(QtGui.QColor("#1e293b"))
                painter.drawText(
                    QtCore.QRect(bom_x + 8, guncel_bom_y + 2, bom_genislik - 16, dinamik_satir_h - 4),
                    QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap,
                    satir_metni
                )
                guncel_bom_y += dinamik_satir_h

        if self.chk_pdf_antet.isChecked():
            proje_baslik = self.txt_pdf_baslik.text().strip() or (self.doc.Label if self.doc else "Montaj")
            dok_kod = self.txt_pdf_dok_kod.text().strip() or "TD-001"
            fastener_notu = "Bağlantı El.: Dahil" if self.chk_pdf_fastener.isChecked() else "Bağlantı El.: Hariç"
            pafta_ek = f" - PAFTA {pafta_no}/{toplam_pafta}{poz_aralik_etiketi}" if toplam_pafta > 1 else ""
            if self.chk_pdf_agirlik.isChecked():
                toplam_montaj_g = sum(k.get('toplam_agirlik_g', 0.0) for k in tum_bom_verisi)
                malz_kisa, _ = self._varsayilan_malzeme_bilgisi_getir(None)
                aciklama_alt = (
                    f"MALZEME: {malz_kisa} | TOPLAM AĞIRLIK: {self._agirlik_formatla(toplam_montaj_g)} | "
                    f"TOPLAM KALEM: {len(tum_bom_verisi)} ({fastener_notu})"
                )
            else:
                aciklama_alt = (
                    f"PATLATMA: %{self.slider.value()} (Katman: {self.spin_kademe.value()} mm) | "
                    f"TOPLAM KALEM: {len(tum_bom_verisi)} | {fastener_notu}"
                )
            self._as9100_antet_ciz(
                painter, cerceve_x, cerceve_y, cerceve_w, cerceve_h, antet_yukseklik,
                f"{proje_baslik} (PATLATILMIŞ MONTAJ{pafta_ek})", f"{dok_kod}-ASM", sayfa_no_metni, aciklama_alt
            )

    def _parca_imalat_sayfasini_ciz(self, painter, view, temp_dir, kalem, sayfa_no_metni):
        sayfa_w = painter.device().width()
        sayfa_h = painter.device().height()
        painter.fillRect(0, 0, sayfa_w, sayfa_h, QtGui.QColor("#ffffff"))

        kenar_bosluk = int(min(sayfa_w, sayfa_h) * 0.032)
        cerceve_x = kenar_bosluk
        cerceve_y = kenar_bosluk
        cerceve_w = sayfa_w - (2 * kenar_bosluk)
        cerceve_h = sayfa_h - (2 * kenar_bosluk)
        antet_yukseklik = int(sayfa_h * 0.135)
        bilgi_genislik = int(cerceve_w * 0.27)

        kalin_kalem = QtGui.QPen(QtGui.QColor("#0f172a"), max(4, int(sayfa_w * 0.002)))
        ince_kalem = QtGui.QPen(QtGui.QColor("#475569"), max(2, int(sayfa_w * 0.001)))

        painter.setPen(kalin_kalem)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(cerceve_x, cerceve_y, cerceve_w, cerceve_h)

        hedef_datalari = kalem['temsili_datalari']
        tum_hedef_objeler = {u['obj'] for u in hedef_datalari}
        tek_hedef_obje = {hedef_datalari[0]['obj']}

        # Seçenek aktifse ve birden fazla özdeş parçadan oluşuyorsa (alt montaj grubu değilse):
        # Ön, Yan ve Üst görünüşlerde doğru ölçü için tek parça, İzometrikte ise birlikte gösterilir.
        ayni_parca_tek_olcu_modu = (
            hasattr(self, "chk_pdf_ayni_parca_izometrik")
            and self.chk_pdf_ayni_parca_izometrik.isChecked()
            and not kalem.get('grup_mu', False)
            and len(hedef_datalari) > 1
        )

        for d in self.objects_data:
            if d['obj'] in tum_hedef_objeler:
                d['obj'].Placement = d['orijinal_konum'].copy()

        cizim_w = cerceve_w - bilgi_genislik
        cizim_h = cerceve_h - antet_yukseklik
        hucre_w = cizim_w // 2
        hucre_h = cizim_h // 2

        gorunusler = [
            ("ÖN GÖRÜNÜŞ (FRONT)", "front", cerceve_x, cerceve_y, kalem['bx'], kalem['bz']),
            ("SOL YAN GÖRÜNÜŞ (SIDE)", "right", cerceve_x + hucre_w, cerceve_y, kalem['by'], kalem['bz']),
            ("ÜST GÖRÜNÜŞ (TOP)", "top", cerceve_x, cerceve_y + hucre_h, kalem['bx'], kalem['by']),
            ("İZOMETRİK 3D GÖRÜNÜŞ", "iso", cerceve_x + hucre_w, cerceve_y + hucre_h, None, None)
        ]

        painter.setPen(ince_kalem)
        painter.drawLine(cerceve_x + hucre_w, cerceve_y, cerceve_x + hucre_w, cerceve_y + cizim_h)
        painter.drawLine(cerceve_x, cerceve_y + hucre_h, cerceve_x + cizim_w, cerceve_y + hucre_h)

        onceki_aktif_set = None

        for baslik, yon_kodu, gx, gy, olcu_yatay, olcu_dikey in gorunusler:
            try:
                if ayni_parca_tek_olcu_modu and yon_kodu != "iso":
                    aktif_objeler = tek_hedef_obje
                else:
                    aktif_objeler = tek_hedef_obje if len(hedef_datalari) == 1 else tum_hedef_objeler

                if aktif_objeler != onceki_aktif_set:
                    for d in self.objects_data:
                        obj = d['obj']
                        if hasattr(obj, "ViewObject") and obj.ViewObject:
                            obj.ViewObject.Visibility = (obj in aktif_objeler)
                    FreeCADGui.updateGui()
                    onceki_aktif_set = aktif_objeler

                self._kamerayi_net_ayarla(view, yon_kodu, ortografik_zorla=True)

                g_path = os.path.join(temp_dir, f"part_{kalem['poz']}_{yon_kodu}.png")
                try:
                    view.saveImage(g_path, 1200, 900, "White")
                except Exception:
                    view.saveImage(g_path)

                ham_img = QtGui.QImage(g_path)
                kirpilmis, _ = self._goruntuyu_akilli_kirp(ham_img, marj_orani=0.05)

                etiket_h = int(hucre_h * 0.10)
                font_etiket = QtGui.QFont("Arial", 8)
                font_etiket.setBold(True)
                painter.setFont(font_etiket)
                painter.setPen(QtGui.QColor("#0f172a"))
                painter.drawText(
                    QtCore.QRect(gx + 16, gy + 8, hucre_w - 32, etiket_h),
                    QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                    baslik
                )

                ic_x = gx + int(hucre_w * 0.12)
                ic_y = gy + etiket_h + int(hucre_h * 0.06)
                ic_w = int(hucre_w * 0.68)
                ic_h = int(hucre_h * 0.68)

                if not kirpilmis.isNull():
                    oran_img = kirpilmis.width() / float(max(1, kirpilmis.height()))
                    oran_ic = ic_w / float(max(1, ic_h))
                    if oran_img > oran_ic:
                        rw = ic_w
                        rh = int(ic_w / oran_img)
                        rx = ic_x
                        ry = ic_y + (ic_h - rh) // 2
                    else:
                        rh = ic_h
                        rw = int(ic_h * oran_img)
                        rx = ic_x + (ic_w - rw) // 2
                        ry = ic_y

                    painter.drawImage(QtCore.QRect(rx, ry, rw, rh), kirpilmis)

                    if self.chk_pdf_olcu_goster.isChecked() and olcu_yatay is not None and olcu_dikey is not None:
                        font_olcu = QtGui.QFont("Arial", 8)
                        font_olcu.setBold(True)
                        painter.setFont(font_olcu)
                        self._olcu_cizgisi_ciz(painter, rx, ry + rh + 24, rx + rw, ry + rh + 24, f"{olcu_yatay:.1f} mm", dikey_mi=False)
                        self._olcu_cizgisi_ciz(painter, rx + rw + 22, ry, rx + rw + 22, ry + rh, f"{olcu_dikey:.1f} mm", dikey_mi=True)
            except Exception:
                continue

        kart_x = cerceve_x + cizim_w
        kart_y = cerceve_y

        hacim = kalem.get('hacim_cm3', 0.0)
        malzeme_adi = kalem.get('malzeme', 'Sac (DKP / S235)')
        birim_ag_g = kalem.get('birim_agirlik_g', 0.0)
        toplam_ag_g = kalem.get('toplam_agirlik_g', 0.0)
        rev_no = self.txt_pdf_rev.text().strip() or "R00"
        agirlik_goster = self.chk_pdf_agirlik.isChecked()

        satirlar = [
            ("POZ NUMARASI", f"POZ {kalem['poz']:02d}"),
            ("PARÇA KODU", kalem['parca_no']),
            ("PARÇA ADI", kalem['ad']),
            ("REVİZYON NO", rev_no),
            ("MONTAJ ADEDİ", f"{kalem['adet']} Adet"),
        ]

        if agirlik_goster:
            satirlar.append(("MALZEME CİNSİ", malzeme_adi))

        satirlar.extend([
            ("SINIF / DURUM", kalem['durum']),
            ("GENİŞLİK (X)", f"{kalem['bx']:.2f} mm"),
            ("DERİNLİK (Y)", f"{kalem['by']:.2f} mm"),
            ("YÜKSEKLİK (Z)", f"{kalem['bz']:.2f} mm"),
        ])

        if agirlik_goster:
            satirlar.extend([
                ("NET HACİM", f"{hacim:.2f} cm³"),
                ("BİRİM AĞIRLIK", self._agirlik_formatla(birim_ag_g)),
                ("TOPLAM AĞIRLIK", self._agirlik_formatla(toplam_ag_g)),
            ])

        satirlar.extend([
            ("GENEL TOLERANS", "ISO 2768 - mK"),
            ("YÜZEY KALİTESİ", "Ra 3.2 µm (Köşeleri Kır)"),
            ("KENAR İŞLEMİ", "Çapakları Alınmış / Keskin Köşe Yok")
        ])

        self._bilgi_karti_dinamik_ciz(
            painter, kart_x, kart_y, bilgi_genislik, cizim_h,
            "PARÇA İMALAT BİLGİ KARTI", "#e2e8f0", satirlar, sayfa_h, kalin_kalem, ince_kalem
        )

        if agirlik_goster:
            aciklama_alt = (
                f"MALZEME: {malzeme_adi} | ÖLÇÜ: {kalem['bx']:.1f}x{kalem['by']:.1f}x{kalem['bz']:.1f} mm | "
                f"ADET: {kalem['adet']} | BİRİM AĞIRLIK: {self._agirlik_formatla(birim_ag_g)}"
            )
        else:
            aciklama_alt = (
                f"PARÇA ÖLÇÜLERİ: {kalem['bx']:.1f} x {kalem['by']:.1f} x {kalem['bz']:.1f} mm | "
                f"GEREKLİ MİKTAR: {kalem['adet']} ADET"
            )
        self._as9100_antet_ciz(
            painter, cerceve_x, cerceve_y, cerceve_w, cerceve_h, antet_yukseklik,
            f"İMALAT RESMİ: {kalem['ad']}", kalem['parca_no'], sayfa_no_metni, aciklama_alt
        )

    def _sahne_durumunu_yedekle(self, view):
        durum = {
            'slider': self.slider.value(),
            'kamera': None,
            'kamera_tipi': None,
            'animasyon_aktif': None,
            'gorunurlukler': {},
            'konumlar': {}
        }
        try:
            durum['kamera'] = view.getCamera()
        except Exception:
            pass
        try:
            durum['kamera_tipi'] = view.getCameraType()
        except Exception:
            pass
        try:
            if hasattr(view, "isAnimationEnabled"):
                durum['animasyon_aktif'] = view.isAnimationEnabled()
        except Exception:
            pass
        for d in self.objects_data:
            obj = d['obj']
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                durum['gorunurlukler'][obj.Name] = obj.ViewObject.Visibility
            durum['konumlar'][obj.Name] = obj.Placement.copy()
        return durum

    def _sahne_durumunu_geri_yukle(self, view, durum):
        for d in self.objects_data:
            obj = d['obj']
            if obj.Name in durum['gorunurlukler'] and hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Visibility = durum['gorunurlukler'][obj.Name]
        self.slider.setValue(durum['slider'])
        self.slider_degisti(durum['slider'])
        if durum.get('kamera_tipi'):
            try:
                view.setCameraType(durum['kamera_tipi'])
            except Exception:
                pass
        if durum.get('kamera'):
            try:
                view.setCamera(durum['kamera'])
            except Exception:
                pass
        if durum.get('animasyon_aktif') is not None:
            try:
                view.setAnimationEnabled(durum['animasyon_aktif'])
            except Exception:
                pass
        FreeCADGui.updateGui()

    def _pdf_yazici_olustur(self, dosya_yolu):
        pdf_writer = QtGui.QPdfWriter(dosya_yolu)
        pdf_writer.setResolution(300)
        sayfa_secimi = self.combo_pdf_boyut.currentText()
        try:
            boyut_id = QtGui.QPageSize.A3 if "A3" in sayfa_secimi else QtGui.QPageSize.A4
            pdf_writer.setPageSize(QtGui.QPageSize(boyut_id))
            yon_id = QtGui.QPageLayout.Portrait if "Dikey" in sayfa_secimi else QtGui.QPageLayout.Landscape
            pdf_writer.setPageOrientation(yon_id)
        except Exception:
            pass
        return pdf_writer

    def teknik_resim_pdf_kaydet(self):
        if not self.objects_data:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Sahnede yazdırılacak parça bulunamadı. Lütfen 'Sahneyi Yenile' butonuna basın.")
            return

        if hasattr(self, 'zamanlayici') and self.zamanlayici.isActive():
            self.btn_animasyon.setChecked(False)
            self.animasyon_tetikle()

        dok_kodu = self._temiz_parca_kodu_olustur(self.txt_pdf_dok_kod.text() or (self.doc.Label if self.doc else "TD_001"))
        rev_no = self._temiz_parca_kodu_olustur(self.txt_pdf_rev.text() or "R00")
        varsayilan_ad = f"{dok_kodu}_Patlatilmis_Teknik_Resim_{rev_no}.pdf"

        dosya_yolu, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Teknik Resim PDF Olarak Kaydet", varsayilan_ad, "PDF Dosyası (*.pdf)"
        )
        if not dosya_yolu:
            return
        if not dosya_yolu.lower().endswith(".pdf"):
            dosya_yolu += ".pdf"

        view = FreeCADGui.ActiveDocument.ActiveView
        yedek_durum = self._sahne_durumunu_yedekle(view)

        kure = self.doc.getObject("PATLATMA_MERKEZI") if self.doc else None
        kure_eski_gorunurluk = False
        if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
            kure_eski_gorunurluk = kure.ViewObject.Visibility
            kure.ViewObject.Visibility = False
        FreeCADGui.Selection.clearSelection()

        try:
            self.gizlilik_durumlarini_senkronize_et()
            fastener_dahil = self.chk_pdf_fastener.isChecked()
            for d in self.objects_data:
                obj = d['obj']
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    gorunur_olacak = (not d.get('kullanici_gizledi', False)) and (fastener_dahil or not d.get('fastener_mi', False))
                    obj.ViewObject.Visibility = gorunur_olacak

            if self.chk_pdf_oto_ideal.isChecked() and self.slider.value() < 15:
                self.slider.setValue(95)
            hedef_patlama_orani = self.slider.value()
            self.slider_degisti(hedef_patlama_orani)

            bom_verisi = self._pdf_icin_bom_ogelerini_hazirla(fastener_dahil=fastener_dahil)
            patlamamis_ekle = self.chk_pdf_patlamamis_montaj.isChecked()
            imalat_kalemleri = [k for k in bom_verisi if not k.get('fastener_mi', False)] if self.chk_pdf_ayri_parcalar.isChecked() else []

            temp_dir = tempfile.mkdtemp()
            pdf_writer = self._pdf_yazici_olustur(dosya_yolu)
            painter = QtGui.QPainter(pdf_writer)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)

            bom_sayfalari = self._bom_verisini_sayfalara_bol(
                bom_verisi, painter.device().width(), painter.device().height()
            )
            toplam_pafta = len(bom_sayfalari)
            toplam_sayfa = (1 if patlamamis_ekle else 0) + toplam_pafta + len(imalat_kalemleri)

            aktif_sayfa = 1

            if patlamamis_ekle:
                self._genel_montaj_patlamamis_sayfasini_ciz(painter, view, temp_dir, bom_verisi, f"{aktif_sayfa} / {toplam_sayfa}")
                aktif_sayfa += 1
                pdf_writer.newPage()
                self.slider_degisti(hedef_patlama_orani)

            for pafta_idx, sayfa_bom_dilimi in enumerate(bom_sayfalari, start=1):
                if pafta_idx > 1:
                    aktif_sayfa += 1
                    pdf_writer.newPage()
                self._montaj_sayfasini_ciz(
                    painter, view, temp_dir, sayfa_bom_dilimi,
                    f"{aktif_sayfa} / {toplam_sayfa}",
                    tum_bom_verisi=bom_verisi, pafta_no=pafta_idx, toplam_pafta=toplam_pafta
                )

            for kalem in imalat_kalemleri:
                aktif_sayfa += 1
                pdf_writer.newPage()
                self._parca_imalat_sayfasini_ciz(painter, view, temp_dir, kalem, f"{aktif_sayfa} / {toplam_sayfa}")

            painter.end()

            QtWidgets.QMessageBox.information(
                self, "Başarılı",
                f"Teknik Resim (Toplam {toplam_sayfa} Sayfa, Rev: {rev_no}) başarıyla oluşturuldu:\n\n{dosya_yolu}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "PDF Hatası", f"Teknik Resim PDF oluşturulurken hata oluştu:\n{str(e)}")
        finally:
            self._sahne_durumunu_geri_yukle(view, yedek_durum)
            if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                kure.ViewObject.Visibility = kure_eski_gorunurluk
            FreeCADGui.updateGui()

    def as9100_uretim_paketi_olustur(self):
        if not self.objects_data:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Sahnede parça bulunamadı. Lütfen 'Sahneyi Yenile' butonuna basın.")
            return

        if hasattr(self, 'zamanlayici') and self.zamanlayici.isActive():
            self.btn_animasyon.setChecked(False)
            self.animasyon_tetikle()

        ana_dizin = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Üretim Dokümantasyon Paketinin Oluşturulacağı Klasörü Seçin"
        )
        if not ana_dizin:
            return

        dok_kodu = self._temiz_parca_kodu_olustur(self.txt_pdf_dok_kod.text() or (self.doc.Label if self.doc else "TD_001"))
        rev_no = self._temiz_parca_kodu_olustur(self.txt_pdf_rev.text() or "R00")
        tarih_kod = datetime.datetime.now().strftime("%Y%m%d")

        paket_klasoru = os.path.join(ana_dizin, f"URETIM_PAKETI_{dok_kodu}_REV-{rev_no}_{tarih_kod}")
        klasor_montaj = os.path.join(paket_klasoru, "00_MONTAJ_TEKNIK_RESMI")
        klasor_parcalar = os.path.join(paket_klasoru, "01_PARCA_IMALAT_RESIMLERI")
        klasor_bom = os.path.join(paket_klasoru, "02_BOM_MALZEME_LISTESI")
        klasor_rapor = os.path.join(paket_klasoru, "03_TASARIM_CIKTI_INDEKSI")

        for k in (klasor_montaj, klasor_parcalar, klasor_bom, klasor_rapor):
            os.makedirs(k, exist_ok=True)

        view = FreeCADGui.ActiveDocument.ActiveView
        yedek_durum = self._sahne_durumunu_yedekle(view)

        kure = self.doc.getObject("PATLATMA_MERKEZI") if self.doc else None
        kure_eski_gorunurluk = False
        if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
            kure_eski_gorunurluk = kure.ViewObject.Visibility
            kure.ViewObject.Visibility = False
        FreeCADGui.Selection.clearSelection()

        try:
            self.gizlilik_durumlarini_senkronize_et()
            fastener_dahil = self.chk_pdf_fastener.isChecked()
            for d in self.objects_data:
                obj = d['obj']
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = (not d.get('kullanici_gizledi', False)) and (fastener_dahil or not d.get('fastener_mi', False))

            if self.chk_pdf_oto_ideal.isChecked() and self.slider.value() < 15:
                self.slider.setValue(95)
            hedef_patlama_orani = self.slider.value()
            self.slider_degisti(hedef_patlama_orani)

            bom_verisi = self._pdf_icin_bom_ogelerini_hazirla(fastener_dahil=fastener_dahil)
            imalat_kalemleri = [k for k in bom_verisi if not k.get('fastener_mi', False)]
            temp_dir = tempfile.mkdtemp()

            # 1) Birleşik Montaj Seti (Sayfa 1: Patlamamış Genel Montaj + Sonraki Sayfalar: Patlatılmış Montaj Paftaları)
            montaj_seti_pdf_yolu = os.path.join(klasor_montaj, f"{dok_kodu}_ASM_00_Genel_ve_Patlatilmis_Montaj_Seti_REV-{rev_no}.pdf")
            set_writer = self._pdf_yazici_olustur(montaj_seti_pdf_yolu)
            set_painter = QtGui.QPainter(set_writer)
            set_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            set_painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)

            bom_sayfalari = self._bom_verisini_sayfalara_bol(
                bom_verisi, set_painter.device().width(), set_painter.device().height()
            )
            toplam_pafta = len(bom_sayfalari)
            set_toplam_sayfa = 1 + toplam_pafta

            self._genel_montaj_patlamamis_sayfasini_ciz(set_painter, view, temp_dir, bom_verisi, f"1 / {set_toplam_sayfa}")
            self.slider_degisti(hedef_patlama_orani)
            for pafta_idx, sayfa_bom_dilimi in enumerate(bom_sayfalari, start=1):
                set_writer.newPage()
                self._montaj_sayfasini_ciz(
                    set_painter, view, temp_dir, sayfa_bom_dilimi,
                    f"{1 + pafta_idx} / {set_toplam_sayfa}",
                    tum_bom_verisi=bom_verisi, pafta_no=pafta_idx, toplam_pafta=toplam_pafta
                )
            set_painter.end()

            # Tekil 01_Patlamamis ve 02_Patlatilmis PDF dosyaları
            patlamamis_pdf_yolu = os.path.join(klasor_montaj, f"{dok_kodu}_ASM_01_Genel_Montaj_Patlamamis_REV-{rev_no}.pdf")
            gm_writer = self._pdf_yazici_olustur(patlamamis_pdf_yolu)
            gm_painter = QtGui.QPainter(gm_writer)
            gm_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            gm_painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            self._genel_montaj_patlamamis_sayfasini_ciz(gm_painter, view, temp_dir, bom_verisi, "1 / 1")
            gm_painter.end()

            self.slider_degisti(hedef_patlama_orani)
            montaj_pdf_yolu = os.path.join(klasor_montaj, f"{dok_kodu}_ASM_02_Patlatilmis_Montaj_BOM_REV-{rev_no}.pdf")
            montaj_writer = self._pdf_yazici_olustur(montaj_pdf_yolu)
            montaj_painter = QtGui.QPainter(montaj_writer)
            montaj_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            montaj_painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            for pafta_idx, sayfa_bom_dilimi in enumerate(bom_sayfalari, start=1):
                if pafta_idx > 1:
                    montaj_writer.newPage()
                self._montaj_sayfasini_ciz(
                    montaj_painter, view, temp_dir, sayfa_bom_dilimi,
                    f"{pafta_idx} / {toplam_pafta}",
                    tum_bom_verisi=bom_verisi, pafta_no=pafta_idx, toplam_pafta=toplam_pafta
                )
            montaj_painter.end()

            # 2) Her Parça İçin Ayrı İsimlendirilmiş İmalat Teknik Resmi PDF
            olusturulan_parca_pdfleri = []
            for kalem in imalat_kalemleri:
                temiz_ad = self._temiz_parca_kodu_olustur(kalem['ad'])
                parca_dosya_adi = f"{kalem['parca_no']}_{temiz_ad}_REV-{rev_no}.pdf"
                parca_pdf_yolu = os.path.join(klasor_parcalar, parca_dosya_adi)

                p_writer = self._pdf_yazici_olustur(parca_pdf_yolu)
                p_painter = QtGui.QPainter(p_writer)
                p_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
                p_painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
                self._parca_imalat_sayfasini_ciz(p_painter, view, temp_dir, kalem, "1 / 1")
                p_painter.end()
                olusturulan_parca_pdfleri.append(parca_dosya_adi)

            # 3) Excel Uyumlu BOM Malzeme Listesi (.CSV)
            agirlik_goster = self.chk_pdf_agirlik.isChecked()
            csv_yolu = os.path.join(klasor_bom, f"{dok_kodu}_BOM_Malzeme_Listesi_REV-{rev_no}.csv")
            with open(csv_yolu, mode="w", newline="", encoding="utf-8-sig") as f_csv:
                writer = csv.writer(f_csv, delimiter=";")
                basliklar = [
                    "POZ NO", "PARCA KODU", "PARCA / GRUP ADI", "REVIZYON", "MIKTAR (ADET)",
                    "MALZEME CINSI", "SINIF", "GENISLIK X (mm)", "DERINLIK Y (mm)", "YUKSEKLIK Z (mm)"
                ]
                if agirlik_goster:
                    basliklar.extend(["NET HACIM (cm3)", "BIRIM AGIRLIK (g)", "TOPLAM AGIRLIK (g)"])
                basliklar.append("CIZEN / HAZIRLAYAN")
                writer.writerow(basliklar)

                cizen_str = self.txt_pdf_cizen.text().strip() or "-"
                for k in bom_verisi:
                    satir = [
                        f"{k['poz']:02d}", k['parca_no'], k['ad'], rev_no, k['adet'],
                        k.get('malzeme', 'Sac (DKP / S235)'), k['durum'],
                        f"{k['bx']:.2f}", f"{k['by']:.2f}", f"{k['bz']:.2f}"
                    ]
                    if agirlik_goster:
                        satir.extend([
                            f"{k.get('hacim_cm3', 0.0):.2f}",
                            f"{k.get('birim_agirlik_g', 0.0):.1f}",
                            f"{k.get('toplam_agirlik_g', 0.0):.1f}"
                        ])
                    satir.append(cizen_str)
                    writer.writerow(satir)

            # 4) Tasarım Çıktısı ve Konfigürasyon Doğrulama İndeksi (.TXT)
            rapor_yolu = os.path.join(klasor_rapor, f"{dok_kodu}_Tasarim_Cikti_Raporu_REV-{rev_no}.txt")
            with open(rapor_yolu, mode="w", encoding="utf-8") as f_txt:
                f_txt.write("========================================================================\n")
                f_txt.write("              TEKNİK VERİ PAKETİ (TDP) KONFİGÜRASYON İNDEKSİ            \n")
                f_txt.write("========================================================================\n\n")
                f_txt.write(f"Proje Başlığı       : {self.txt_pdf_baslik.text().strip()}\n")
                f_txt.write(f"Doküman Kodu        : {dok_kodu}\n")
                f_txt.write(f"Revizyon Numarası   : {rev_no}\n")
                f_txt.write(f"Hazırlayan / Çizen  : {self.txt_pdf_cizen.text().strip()}\n")
                f_txt.write(f"Kontrol / Onaylayan : {self.txt_pdf_onay.text().strip()}\n")
                f_txt.write(f"Uygulanan Standart  : {self.combo_pdf_standart.currentText()}\n")
                f_txt.write(f"Oluşturulma Tarihi  : {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
                f_txt.write(f"Fastener Durumu     : {'Dahil Edildi' if fastener_dahil else 'Hariç Tutuldu (Filtrelendi)'}\n\n")
                f_txt.write("------------------------------------------------------------------------\n")
                f_txt.write("ÜRETİLEN TEKNİK RESİM DOSYALARI LİSTESİ:\n")
                f_txt.write("------------------------------------------------------------------------\n")
                f_txt.write(f"1. [MONTAJ SETİ]        00_MONTAJ_TEKNIK_RESMI/{os.path.basename(montaj_seti_pdf_yolu)}\n")
                f_txt.write(f"2. [GENEL MONTAJ]       00_MONTAJ_TEKNIK_RESMI/{os.path.basename(patlamamis_pdf_yolu)}\n")
                f_txt.write(f"3. [PATLATILMIŞ MONTAJ] 00_MONTAJ_TEKNIK_RESMI/{os.path.basename(montaj_pdf_yolu)}\n")
                for idx, p_ad in enumerate(olusturulan_parca_pdfleri, start=4):
                    f_txt.write(f"{idx}. [İMALAT]             01_PARCA_IMALAT_RESIMLERI/{p_ad}\n")
                f_txt.write(f"\n[BOM CSV]   02_BOM_MALZEME_LISTESI/{os.path.basename(csv_yolu)}\n")

            QtWidgets.QMessageBox.information(
                self, "Üretim Paketi Hazır",
                f"Üretim Dokümantasyon Paketi başarıyla oluşturuldu!\n\n"
                f"📂 Klasör: {paket_klasoru}\n"
                f"📄 Montaj Resmi: 3 Adet PDF\n"
                f"🛠️ Ayrı Parça İmalat Resmi: {len(olusturulan_parca_pdfleri)} Adet PDF\n"
                f"📊 BOM Malzeme Listesi: 1 Adet Excel CSV\n"
                f"📋 Konfigürasyon Raporu: 1 Adet"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Üretim Paketi Hatası", f"Üretim paketi oluşturulurken hata oluştu:\n{str(e)}")
        finally:
            self._sahne_durumunu_geri_yukle(view, yedek_durum)
            if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                kure.ViewObject.Visibility = kure_eski_gorunurluk
            FreeCADGui.updateGui()

    def opencv_kur(self):
        try:
            python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
            if not os.path.exists(python_exe):
                python_exe = sys.executable

            self.btn_kur.setText("⏳ Kuruluyor, Lütfen Bekleyin (Konsola Bakınız)...")
            self.btn_kur.setEnabled(False)
            FreeCADGui.updateGui()

            FreeCAD.Console.PrintMessage("\n[OpenCV Kurulumu] Basladi...\n")
            subprocess.check_call([python_exe, "-m", "pip", "install", "opencv-python", "--user"])
            FreeCAD.Console.PrintMessage("[OpenCV Kurulumu] Basariyla Tamamlandi!\n\n")

            QtWidgets.QMessageBox.information(
                self,
                "Başarılı",
                "OpenCV başarıyla kuruldu!\n\nMP4 özelliğini kullanabilmek için LÜTFEN FREECAD'i TAMAMEN KAPATIP YENİDEN AÇIN."
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Hata", f"Kurulum başarısız oldu:\n{str(e)}")
            self.btn_kur.setEnabled(True)
            self.btn_kur.setText("⚙️ MP4 Özelliğini Aktifleştir (OpenCV Kur)")

    def video_kaydet(self):
        global HAS_OPENCV
        if not self.objects_data:
            QtWidgets.QMessageBox.warning(self, "Uyarı", "Sahnede kaydedilecek parça bulunamadı.")
            return

        if hasattr(self, 'zamanlayici') and self.zamanlayici.isActive():
            self.btn_animasyon.setChecked(False)
            self.animasyon_tetikle()

        oran_dizisi, fps, gercek_sure, gercek_hiz = self._video_kare_dizisi_hesapla()
        toplam_kare = len(oran_dizisi)

        kure = self.doc.getObject("PATLATMA_MERKEZI") if self.doc else None
        kure_eski_gorunurluk = False
        if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
            kure_eski_gorunurluk = kure.ViewObject.Visibility
            kure.ViewObject.Visibility = False

        if HAS_OPENCV:
            dosya_yolu, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "MP4 Olarak Kaydet", "Patlatma_Animasyonu.mp4", "Video Files (*.mp4)"
            )
            if not dosya_yolu:
                if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                    kure.ViewObject.Visibility = kure_eski_gorunurluk
                return

            FreeCAD.Console.PrintMessage(
                f"\n[Video Kaydı] MP4 işleniyor ({gercek_sure:.1f} sn, {gercek_hiz:.1f} %/sn, {toplam_kare} kare, {fps} FPS)...\nHedef: {dosya_yolu}\n"
            )
            temp_dir = tempfile.mkdtemp()

            self._patlama_orani_uygula(0.0, slider_senkronla=True)
            FreeCADGui.updateGui()
            test_img = os.path.join(temp_dir, "test.png")
            FreeCADGui.ActiveDocument.ActiveView.saveImage(test_img)

            frame = cv2.imread(test_img)
            height, width, _ = frame.shape

            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            video = cv2.VideoWriter(dosya_yolu, fourcc, float(fps), (width, height))

            for idx, oran in enumerate(oran_dizisi):
                self._patlama_orani_uygula(oran, slider_senkronla=True)
                tmp_path = os.path.join(temp_dir, "frame.png")
                FreeCADGui.ActiveDocument.ActiveView.saveImage(tmp_path)
                video.write(cv2.imread(tmp_path))
                if idx % 5 == 0:
                    QtWidgets.QApplication.processEvents()

            video.release()
            self.konumlari_sifirla()
            if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                kure.ViewObject.Visibility = kure_eski_gorunurluk
            FreeCAD.Console.PrintMessage("Video kaydi basariyla bitti!\n")
            QtWidgets.QMessageBox.information(
                self, "Başarılı",
                f"MP4 Video ({gercek_sure:.1f} sn / {toplam_kare} Kare) başarıyla kaydedildi:\n\n{dosya_yolu}"
            )
        else:
            klasor = QtWidgets.QFileDialog.getExistingDirectory(self, "Görüntülerin Kaydedileceği Klasörü Seçin")
            if not klasor:
                if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                    kure.ViewObject.Visibility = kure_eski_gorunurluk
                return

            FreeCAD.Console.PrintMessage(
                f"Görüntü dizisi (PNG) kaydı başlıyor ({gercek_sure:.1f} sn, {toplam_kare} kare)... Hedef: {klasor}\n"
            )
            for kare_no, oran in enumerate(oran_dizisi):
                self._patlama_orani_uygula(oran, slider_senkronla=True)
                FreeCADGui.ActiveDocument.ActiveView.saveImage(os.path.join(klasor, f"kare_{kare_no:04d}.png"))
                if kare_no % 5 == 0:
                    QtWidgets.QApplication.processEvents()

            self.konumlari_sifirla()
            if kure and hasattr(kure, "ViewObject") and kure.ViewObject:
                kure.ViewObject.Visibility = kure_eski_gorunurluk
            FreeCAD.Console.PrintMessage("Görüntü kaydı başarıyla tamamlandı!\n")
            QtWidgets.QMessageBox.information(
                self, "Başarılı",
                f"Tüm PNG kareleri ({toplam_kare} adet / {gercek_sure:.1f} sn) kaydedildi:\n{klasor}"
            )

    def konumlari_sifirla(self):
        if hasattr(self, 'zamanlayici') and self.zamanlayici.isActive():
            self.btn_animasyon.setChecked(False)
            self.animasyon_tetikle()

        self.slider.blockSignals(True)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.label.setText("Açılma Oranı: %0\n(Slider üzerine gelin ve farenin tekerleğini kaydırın)")

        for d in self.objects_data:
            try:
                d['obj'].Placement = d['orijinal_konum'].copy()
                d['manuel_carpan'] = 1.0
            except Exception:
                pass
        FreeCADGui.updateGui()

    def closeEvent(self, event):
        try:
            self._yukleme_penceresi_kapat()
            if hasattr(self, 'zamanlayici') and self.zamanlayici.isActive():
                self.zamanlayici.stop()
            if hasattr(self, 'merkez_takip_timer') and self.merkez_takip_timer.isActive():
                self.merkez_takip_timer.stop()
            self.konumlari_sifirla()
            if self.doc:
                m_obj = self.doc.getObject("PATLATMA_MERKEZI")
                if m_obj:
                    self.doc.removeObject(m_obj.Name)
        except Exception:
            pass
        event.accept()


if __name__ == "__main__":
    global patlatma_gui
    try:
        if 'patlatma_gui' in globals() and patlatma_gui is not None:
            patlatma_gui.close()
    except Exception:
        pass

    patlatma_gui = PatlatilmisGorunumUI()
    patlatma_gui.show()
    patlatma_gui.raise_()
    patlatma_gui.activateWindow()
