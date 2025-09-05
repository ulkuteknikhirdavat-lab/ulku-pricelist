# -*- coding: utf-8 -*-
"""
Gencer bayi scraper (final, sağlam)
- Login (name/id/placeholder/label + tüm iframeler; çerez banner kapatma)
- Fiyat listesine gider
- Her sayfada scroll + 7sn bekler (lazy-load img)
- Tablodan: image_url, sku, title, stock, kdv, birim, price, currency
- Döviz satırda yoksa tablo başlığından (TL/USD/EUR) düşer
- 149 sayfa gezer (sağdan sola numaralandırma da destekli)
- Çıktı: products.csv, products.json
- Görselleri SKU.ext olarak _downloads/ klasörüne indirir
- Login hatasında _downloads/login_fail*.png, .html dump bırakır
"""

import os, re, time, json, sys, datetime
from pathlib import Path
import pandas as pd
import requests
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# --- yollar ---
ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "products.csv"
JSON_PATH = ROOT / "products.json"
DOWNLOADS = ROOT / "_downloads"
LOGS = ROOT / "logs"
for d in (DOWNLOADS, LOGS):
    d.mkdir(parents=True, exist_ok=True)

def log(msg): print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}")

def screenshot_dump(driver, tag=""):
    try:
        (DOWNLOADS / f"login_source{('_'+tag if tag else '')}.html").write_text(
            driver.page_source, encoding="utf-8")
        driver.save_screenshot(str(DOWNLOADS / f"login_fail{('_'+tag if tag else '')}.png"))
        log(f"Login sayfası dump alındı: {DOWNLOADS}")
    except Exception:
        pass

# --- Chrome driver ---
def init_driver():
    opts = Options()
    # opts.add_argument("--headless=new")  # görünmez çalıştırmak istersen aç
    opts.add_argument("--window-size=1366,900")
    opts.add_experimental_option("excludeSwitches", ["enable-automation","enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=tr-TR,tr")
    opts.add_argument("--log-level=3")
    opts.set_capability("pageLoadStrategy", "eager")
    service = Service(ChromeDriverManager().install())
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(40)
    try:
        # webdriver izini gizle
        drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
            {"source":"Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"})
    except Exception:
        pass
    return drv

# --- yardımcılar ---
def scroll_whole_page(driver, step=700, pause=0.25):
    """Lazy-load görseller açılsın diye tüm sayfayı gez."""
    try:
        last = driver.execute_script("return document.body.scrollHeight") or 3000
        y = 0
        while y < last:
            driver.execute_script(f"window.scrollTo(0,{y});")
            time.sleep(pause)
            y += step
        time.sleep(1.0)
        driver.execute_script("window.scrollTo(0,0);")
        time.sleep(0.5)
    except Exception:
        pass

def find_in_any_frame(driver, xpaths, timeout=12):
    """xpaths listesi için önce ana sayfa, sonra tüm iframeler içinde arar.
       Bulursa (frame_index, xpath) döner. frame_index None ise ana sayfadır."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for xp in xpaths:
            els = driver.find_elements(By.XPATH, xp)
            if els:
                return None, xp
        time.sleep(0.2)

    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for i, fr in enumerate(frames):
        try:
            driver.switch_to.frame(fr)
            deadline = time.time() + timeout
            while time.time() < deadline:
                for xp in xpaths:
                    els = driver.find_elements(By.XPATH, xp)
                    if els:
                        driver.switch_to.default_content()
                        return i, xp
                time.sleep(0.2)
            driver.switch_to.default_content()
        except Exception:
            try: driver.switch_to.default_content()
            except Exception: pass
            continue
    return None, None

# --- LOGIN ---
def login(driver):
    load_dotenv()
    MUSTERI   = os.getenv("GENCER_MUSTERI", "").strip()
    KULLANICI = os.getenv("GENCER_KULLANICI", "").strip()
    SIFRE     = os.getenv("GENCER_SIFRE", "").strip()
    if not (MUSTERI and KULLANICI and SIFRE):
        log("HATA: .env eksik (GENCER_MUSTERI, GENCER_KULLANICI, GENCER_SIFRE).")
        sys.exit(1)

    log("Login sayfası yükleniyor...")
    driver.get("https://bayi.gencerteknik.com.tr/Login.asp")

    # Çerez/popup kapat
    try:
        for xp in [
            "//button[contains(.,'Kabul') or contains(.,'Kabul Ediyorum') or contains(.,'Onayla')]",
            "//a[contains(.,'Kapat') or contains(.,'Tamam')]",
            "//*[@id='onetrust-accept-btn-handler']",
        ]:
            els = driver.find_elements(By.XPATH, xp)
            if els:
                driver.execute_script("arguments[0].click();", els[0]); time.sleep(0.5)
    except Exception:
        pass

    # Aranacak desenler
    musteri_xp = [
        "//input[@name='MUSTERI' or @id='MUSTERI']",
        "//input[contains(translate(@placeholder,'üşİIÖĞÇ','usIIOGC'),'muster')]",
        "//label[contains(.,'Müşteri') or contains(.,'Musteri')]/following::input[1]",
        "(//input[@type='text' or not(@type)])[1]"
    ]
    kullanici_xp = [
        "//input[@name='KULLANICI' or @id='KULLANICI']",
        "//input[contains(translate(@placeholder,'üşİIÖĞÇ','usIIOGC'),'kullan')]",
        "//label[contains(.,'Kullanıcı') or contains(.,'Kullanici')]/following::input[1]",
        "(//input[@type='text' or not(@type)])[2]"
    ]
    sifre_xp = [
        "//input[@type='password']",
        "//label[contains(.,'Şifre') or contains(.,'Sifre')]/following::input[@type='password'][1]"
    ]
    submit_xp = [
        "//button[contains(.,'Oturum Aç') or contains(.,'Giriş') or contains(.,'GIRIS')]",
        "//input[@type='submit']",
        "//a[contains(.,'Oturum Aç') or contains(.,'Giriş')]"
    ]

    # Eleman yerlerini tespit
    m_ifr, m_sel = find_in_any_frame(driver, musteri_xp, timeout=12)
    k_ifr, k_sel = find_in_any_frame(driver, kullanici_xp, timeout=12)
    s_ifr, s_sel = find_in_any_frame(driver, sifre_xp, timeout=12)

    # Bulunamadıysa bir kez daha dene + dump
    if not (m_sel and k_sel and s_sel):
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(0.6)
            driver.execute_script("window.scrollTo(0, 0);"); time.sleep(0.4)
        except Exception:
            pass
        m_ifr, m_sel = find_in_any_frame(driver, musteri_xp, timeout=6)
        k_ifr, k_sel = find_in_any_frame(driver, kullanici_xp, timeout=6)
        s_ifr, s_sel = find_in_any_frame(driver, sifre_xp, timeout=6)

    if not (m_sel and k_sel and s_sel):
        screenshot_dump(driver, "not_found")
        raise NoSuchElementException("Login alanları bulunamadı (name/id/placeholder/label + iframe denendi).")

    # Doğru frame içine geçip yaz
    def type_in(frame_index, selector, text):
        driver.switch_to.default_content()
        if frame_index is not None:
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            if frame_index < len(frames):
                driver.switch_to.frame(frames[frame_index])
        el = driver.find_element(By.XPATH, selector)
        try: el.clear()
        except Exception: pass
        el.click(); time.sleep(0.1); el.send_keys(text)
        driver.switch_to.default_content()

    type_in(m_ifr, m_sel, MUSTERI)
    type_in(k_ifr, k_sel, KULLANICI)
    type_in(s_ifr, s_sel, SIFRE)

    # Gönder
    clicked = False
    for xp in submit_xp:
        f_ifr, f_sel = find_in_any_frame(driver, [xp], timeout=3)
        if f_sel:
            try:
                driver.switch_to.default_content()
                if f_ifr is not None:
                    frames = driver.find_elements(By.TAG_NAME, "iframe")
                    if f_ifr < len(frames):
                        driver.switch_to.frame(frames[f_ifr])
                btn = driver.find_element(By.XPATH, f_sel)
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                driver.switch_to.default_content()
                break
            except Exception:
                driver.switch_to.default_content()
                continue
    if not clicked:
        # password alanına enter
        try:
            driver.switch_to.default_content()
            if s_ifr is not None:
                frames = driver.find_elements(By.TAG_NAME, "iframe")
                if s_ifr < len(frames):
                    driver.switch_to.frame(frames[s_ifr])
            el = driver.find_element(By.XPATH, s_sel)
            el.send_keys("\n")
            driver.switch_to.default_content()
            clicked = True
        except Exception:
            pass

    # Sonucu bekle
    try:
        WebDriverWait(driver, 30).until(
            lambda d: any(w in d.page_source.lower()
                          for w in ["fiyat","liste","ürün","urun","stok","sepet","çıkış","cikis","katalog"]))
        log("Login sonrası sayfa yüklemesi: tamam")
    except TimeoutException:
        screenshot_dump(driver, "post_submit")
        raise

# --- FİYAT LİSTESİ ---
def goto_price_list(driver):
    log("Fiyat listesine gidiliyor...")
    # öncelik: menüden tıklama
    for xp in [
        "//a[contains(., 'Fiyat Listesi')]","//a[contains(., 'Fiyat Teklifi')]",
        "//button[contains(., 'Fiyat Listesi') or contains(., 'Fiyat Teklifi')]",
        "//span[contains(., 'Fiyat Listesi') or contains(., 'Fiyat Teklifi')]"
    ]:
        try:
            # ana + iframe ara
            f_ifr, f_sel = find_in_any_frame(driver, [xp], timeout=6)
            if f_sel:
                driver.switch_to.default_content()
                if f_ifr is not None:
                    frames = driver.find_elements(By.TAG_NAME, "iframe")
                    if f_ifr < len(frames):
                        driver.switch_to.frame(frames[f_ifr])
                el = driver.find_element(By.XPATH, f_sel)
                driver.execute_script("arguments[0].click();", el)
                driver.switch_to.default_content()
                break
        except Exception:
            continue
    else:
        # doğrudan URL dene (site düzenine göre alternatifler)
        for url in [
            "https://bayi.gencerteknik.com.tr/FiyatListesi.asp",
            "https://bayi.gencerteknik.com.tr/?page=fiyat-listesi",
        ]:
            try:
                driver.get(url); break
            except Exception:
                continue

    try:
        WebDriverWait(driver, 20).until(
            lambda d: d.find_elements(By.CSS_SELECTOR,"table tr") or
                      d.find_elements(By.CSS_SELECTOR,".product,.urun,.card"))
        log("Fiyat listesi açıldı.")
        return True
    except TimeoutException:
        log("Fiyat listesi bulunamadı."); return False

# --- PARSE ---
CURRENCY_MAP = {"₺":"TRY","TL":"TRY","TRY":"TRY","$":"USD","USD":"USD","€":"EUR","EUR":"EUR"}

def parse_price_currency(s):
    s = (s or "").strip()
    if not s: return 0.0, ""
    cur = ""
    u = s.upper()
    for k,v in CURRENCY_MAP.items():
        if k in u or k in s: cur = v; break
    sn = s.replace(" ","").replace(".","").replace(",",".")
    m = re.findall(r"[-+]?\d*\.?\d+", sn)
    val = float(m[0]) if m else 0.0
    return val, cur

def detect_table_currency(driver):
    """Tablo başlığında 'TL', 'USD', 'EUR' geçiyorsa onu döndürür."""
    try:
        ths = driver.find_elements(By.CSS_SELECTOR, "table thead th")
        if ths:
            txt = " ".join([t.text for t in ths]).upper()
            for k, v in CURRENCY_MAP.items():
                if k in txt:
                    return v
    except Exception:
        pass
    return "TRY"

def parse_table(driver) -> pd.DataFrame:
    rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
    data = []
    if not rows:
        return pd.DataFrame()

    default_cur = detect_table_currency(driver)

    for r in rows[1:]:  # başlığı atla
        tds = r.find_elements(By.CSS_SELECTOR, "td")
        if len(tds) < 4: continue

        # 0. kolonda resim olabilir
        img = ""
        base = 0
        try:
            if tds[0].find_elements(By.TAG_NAME,"img"):
                im = tds[0].find_element(By.TAG_NAME,"img")
                for attr in ["src","data-src","data-original","data-lazy","data-echo","data-image"]:
                    val = im.get_attribute(attr) or ""
                    if val and not val.startswith("data:image"): img = val; break
                if not img:
                    style = tds[0].get_attribute("style") or ""
                    m = re.search(r'url\([\'"]?([^\'")]+)[\'"]?\)', style)
                    if m: img = m.group(1)
                base = 1
        except Exception:
            pass

        def td(i):
            try: return tds[i].text.strip()
            except Exception: return ""

        sku   = td(base+0)
        title = td(base+1)
        stock = td(base+2) if len(tds)>base+2 else ""
        kdv   = td(base+3) if len(tds)>base+3 else ""
        birim = td(base+4) if len(tds)>base+4 else ""
        price_t = tds[-1].text.strip() if tds else ""
        price, currency = parse_price_currency(price_t)
        if not currency: currency = default_cur

        data.append({
            "image_url": img,
            "sku": sku,
            "title": title,
            "stock": stock,
            "kdv": kdv,
            "birim": birim,
            "price": price,
            "currency": currency
        })
    return pd.DataFrame(data)

def parse_cards(driver) -> pd.DataFrame:
    cards = driver.find_elements(By.CSS_SELECTOR, ".product,.urun,.card,.product-card")
    data=[]
    for c in cards:
        try:
            def first(sel_list):
                for sel in sel_list:
                    try:
                        t = c.find_element(By.CSS_SELECTOR, sel).text.strip()
                        if t: return t
                    except Exception: pass
                return ""
            title = first(["h3","h4",".title",".product-name",".urun-adi",".name"]) or c.text.split("\n")[0].strip()
            price_t = first([".price",".fiyat",".satis",".satış","[class*='price']"])
            price, currency = parse_price_currency(price_t)
            sku = first([".sku",".kod",".stok",".barkod","[class*='sku']","[class*='kod']"])
            img = ""
            try:
                im = c.find_element(By.CSS_SELECTOR,"img")
                for attr in ["src","data-src","data-original","data-lazy","data-echo","data-image"]:
                    val = im.get_attribute(attr) or ""
                    if val and not val.startswith("data:image"): img = val; break
            except Exception: pass
            data.append({"image_url": img, "title": title, "sku": sku,
                         "price": price, "currency": currency, "stock":"", "kdv":"", "birim":""})
        except Exception:
            continue
    return pd.DataFrame(data)

def parse_current_page(driver) -> pd.DataFrame:
    df = parse_table(driver)
    if not df.empty: return df
    return parse_cards(driver)

# --- SAYFALAMA ---
def click_next(driver) -> bool:
    # numara → ileri → rel=next → aria-label
    for xp in [
        "//a[normalize-space(text())='»' or normalize-space(.)='›' or contains(.,'Sonraki') or contains(.,'İleri')]",
        "//button[normalize-space(text())='»' or normalize-space(.)='›' or contains(.,'Sonraki') or contains(.,'İleri')]",
        "//li[contains(@class,'next')]/a"
    ]:
        try:
            el = WebDriverWait(driver,4).until(EC.element_to_be_clickable((By.XPATH, xp)))
            driver.execute_script("arguments[0].click();", el)
            time.sleep(1.0); return True
        except Exception:
            continue
    for css in ["a[rel='next']","a[aria-label*='Sonraki']","button[aria-label*='Sonraki']",
                "a[aria-label*='Next']","button[aria-label*='Next']","li.next a"]:
        try:
            el = WebDriverWait(driver,3).until(EC.element_to_be_clickable((By.CSS_SELECTOR, css)))
            driver.execute_script("arguments[0].click();", el)
            time.sleep(1.0); return True
        except Exception:
            continue
    return False

def click_page_number(driver, page_no: int) -> bool:
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.3)
    except Exception:
        pass
    xp = f"//a[normalize-space(text())='{page_no}'] | //button[normalize-space(text())='{page_no}']"
    try:
        el = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xp)))
        driver.execute_script("arguments[0].click();", el)
        time.sleep(1.0); return True
    except Exception:
        return False

def collect_all_pages(driver, max_pages=149) -> pd.DataFrame:
    frames = []
    page = 1
    while page <= max_pages:
        log(f"Sayfa {page}: lazy-load için scroll yapılıyor...")
        scroll_whole_page(driver)
        log(f"Sayfa {page}: 7 sn bekleniyor (görseller insin)...")
        time.sleep(7)

        try:
            WebDriverWait(driver, 20).until(
                lambda d: d.find_elements(By.CSS_SELECTOR,"table tr") or
                          d.find_elements(By.CSS_SELECTOR,".product,.urun,.card,.product-card"))
        except TimeoutException:
            log(f"Sayfa {page}: içerik gelmedi"); break

        df = parse_current_page(driver)
        log(f"Sayfa {page}: {len(df)} kayıt")
        if not df.empty: frames.append(df)

        if not (click_page_number(driver, page+1) or click_next(driver)):
            break
        page += 1

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

# --- normalize & kaydet ---
def normalize_and_save(df: pd.DataFrame):
    out = pd.DataFrame()
    out["image_url"] = df.get("image_url","").fillna("").astype(str)
    out["sku"] = df.get("sku","").fillna("").astype(str)
    out["title"] = df.get("title","").fillna("").astype(str)
    out["stock"] = df.get("stock","").fillna("").astype(str)
    out["kdv"] = df.get("kdv","").fillna("").astype(str)
    out["birim"] = df.get("birim","").fillna("").astype(str)
    out["price"] = pd.to_numeric(df.get("price",0), errors="coerce").fillna(0.0).astype(float)
    out["currency"] = df.get("currency","").fillna("").astype(str)

    # SKU boşsa başlıktan türet
    def make_sku(row):
        if row["sku"]: return row["sku"]
        base = re.sub(r"[^\w\.-]+","_", row["title"], flags=re.U).strip("_")
        return base[:90] or ("SKU_"+str(abs(hash(row["title"])))[:12])
    out["sku"] = out.apply(make_sku, axis=1)

    out = out.drop_duplicates(subset=["sku"], keep="first")

    out.to_csv(CSV_PATH, index=False, encoding="utf-8")
    JSON_PATH.write_text(json.dumps(out.to_dict(orient="records"), ensure_ascii=False, indent=2),
                         encoding="utf-8")
    log(f"Yazıldı: {len(out)} ürün -> products.csv + products.json")

def sanitize_name(s):
    s = re.sub(r"[^\w\.-]+","_", s, flags=re.U)
    return s.strip("_")[:80] or "IMG"

def download_images(df: pd.DataFrame, outdir: Path = DOWNLOADS):
    outdir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for _, row in df.iterrows():
        url = (row.get("image_url") or "").strip()
        sku = sanitize_name((row.get("sku") or "").strip())
        if not url or not sku:
            continue
        try:
            ext = os.path.splitext(url.split("?")[0])[1].lower()
            if ext not in [".jpg",".jpeg",".png",".webp",".gif",".bmp"]:
                ext = ".jpg"
            fpath = outdir / f"{sku}{ext}"
            if fpath.exists():
                continue
            r = requests.get(url, timeout=20)
            if r.ok and r.content:
                fpath.write_bytes(r.content)
                ok += 1
        except Exception as e:
            log(f"Görsel indirilemedi ({sku}): {e}")
    log(f"Görsel indirme tamam: {ok} dosya")

# --- akış ---
def run():
    drv = init_driver()
    try:
        login(drv)
        if not goto_price_list(drv):
            log("HATA: Fiyat listesi sayfası bulunamadı."); return
        df = collect_all_pages(drv, max_pages=149)
        if df.empty:
            log("Uyarı: Hiç kayıt bulunamadı."); return
        normalize_and_save(df)
        try: download_images(df)
        except Exception as e: log(f"Görsel indirme atlandı: {e}")
    finally:
        try: drv.quit()
        except Exception: pass

if __name__ == "__main__":
    run()
