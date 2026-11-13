"""Descarga los precios oficiales (Ministerio para la Transición Ecológica)
de TODAS las gasolineras de Navarra y los guarda en data/.

- Se guardan todas las estaciones que publica el Ministerio, también las que no
  publican ningún precio, identificadas por su código oficial (IDEESS).
- Cada estación lleva "c": 1 si está en Pamplona y comarca (vista por defecto de la app):
  municipio de la Mancomunidad de la Comarca de Pamplona o a menos de 14 km del centro.

- data/precios.json   -> última lectura (la usa la web)
- data/historial.json -> precio medio por día, para el "mejor día para llenar"
"""
import hashlib, json, os, re, ssl, subprocess, sys, time, unicodedata, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

API = ("https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/"
       "PreciosCarburantes/EstacionesTerrestres/FiltroProvincia/31")  # 31 = Navarra
FUENTE = "Ministerio para la Transición Ecológica (geoportalgasolineras.es)"

# Municipios de la comarca de Pamplona (primer nombre oficial, sin tildes, en minúsculas).
# Se guardan todas las gasolineras de Navarra; estas se marcan como "comarca".
MUNICIPIOS = [
    "pamplona", "burlada", "villava", "ansoain", "berriozar", "berrioplano",
    "huarte", "egues", "valle de egues", "aranguren", "baranain", "zizur mayor",
    "cizur", "cendea de cizur", "orkoien", "orcoyen", "olza", "cendea de olza",
    "galar", "cendea de galar", "noain", "elortzibar", "beriain",
    "ezcabarte", "ezkabarte", "esteribar", "juslapena", "olaibar", "iza", "cendea de iza",
    "tiebas", "tiebas-muruarte de reta", "etxauri", "belascoain", "goni", "ollo",
    "bidaurreta", "ciriza", "echarri", "zabalza", "anue", "odieta", "ultzama",
    "biurrun", "biurrun-olcoz", "ucar", "enériz", "eneriz", "adios", "imotz", "atez",
]
# Combustibles principales de la app (listas por combustible y medias del historial)
COMBUSTIBLES = {"diesel": "Precio Gasoleo A", "g95": "Precio Gasolina 95 E5",
                "g98": "Precio Gasolina 98 E5", "dpremium": "Precio Gasoleo Premium"}
# Todos los productos que publica el Ministerio (se guardan todos en "estaciones")
CODIGOS = {
    "Precio Gasoleo A": "diesel", "Precio Gasoleo Premium": "dpremium", "Precio Gasoleo B": "gb",
    "Precio Gasolina 95 E5": "g95", "Precio Gasolina 95 E10": "g95e10",
    "Precio Gasolina 95 E5 Premium": "g95p", "Precio Gasolina 98 E5": "g98",
    "Precio Gasolina 98 E10": "g98e10", "Precio Gases licuados del petróleo": "glp",
    "Precio Gas Natural Comprimido": "gnc", "Precio Gas Natural Licuado": "gnl",
    "Precio Hidrogeno": "h2", "Precio Biodiesel": "biod", "Precio Bioetanol": "bioe",
    "Precio Diésel Renovable": "dren", "Precio Gasolina Renovable": "gren",
    "Precio Adblue": "adblue", "Precio Amoniaco": "nh3", "Precio Metanol": "metanol",
    "Precio Biogas Natural Comprimido": "bgnc", "Precio Biogas Natural Licuado": "bgnl",
}
CENTRO = (42.8180, -1.6430)  # Plaza del Castillo, Pamplona
RADIO_COMARCA_KM = 14


def codigo(campo):
    if campo in CODIGOS:
        return CODIGOS[campo]
    return re.sub(r"[^a-z0-9]+", "", norm(campo.replace("Precio", "")))[:12] or None


def km(a, b):
    import math
    r = math.pi / 180
    h = (math.sin((b[0]-a[0])*r/2)**2 +
         math.cos(a[0]*r)*math.cos(b[0]*r)*math.sin((b[1]-a[1])*r/2)**2)
    return 2*6371*math.asin(math.sqrt(h))

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
MINUSC = {"de", "del", "la", "las", "el", "los", "y", "s/n", "sn", "a", "en"}


def norm(t):
    t = unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.lower()).strip()


def municipio_ok(m):
    m = norm(m).split("/")[0].strip()
    return any(m == k or m.startswith(k + " ") or m.startswith(k + "(") for k in MUNICIPIOS)


def bonito(t, marca=False):
    """'CALLE SADAR S/N' -> 'Calle Sadar s/n'; marcas: 'REPSOL' -> 'Repsol', 'BP' -> 'BP'."""
    t = re.sub(r"\s+", " ", (t or "").strip())
    out = []
    for i, w in enumerate(t.split(" ")):
        lw = w.lower()
        if marca and len(w) <= 3 and w.isalpha():
            out.append(w.upper())
        elif i > 0 and lw in MINUSC:
            out.append(lw)
        elif re.fullmatch(r"[a-z]-?\d+.*", lw) or re.fullmatch(r"[a-z]{1,2}-\d+.*", lw):
            out.append(w.upper())  # N-121, A-15, NA-150
        else:
            out.append(lw[:1].upper() + lw[1:])
    s = " ".join(out)
    if marca:
        s = re.sub(r"\.(\w)", lambda m: "." + m.group(1).upper(), s)  # E.leclerc -> E.Leclerc
    return s.replace("S/n", "s/n").replace("Km.", "km").replace("Km ", "km ")


def precio(v):
    v = (v or "").strip().replace(",", ".")
    try:
        p = float(v)
        return p if 0.5 < p < 5 else None
    except ValueError:
        return None


def descargar(intentos=3):
    """El servidor del Ministerio a veces corta la conexión (SSL EOF).
    Se reintenta varias veces, alternando Python y curl, con esperas crecientes."""
    ctx = ssl.create_default_context()
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (gasolineras-pamplona)"}
    ultimo = None
    for i in range(1, intentos + 1):
        try:
            if i % 2:
                req = urllib.request.Request(API, headers=headers)
                with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
                    texto = r.read().decode("utf-8-sig")
            else:
                texto = subprocess.run(
                    ["curl", "-sS", "--fail", "--retry", "2", "--max-time", "90",
                     "-H", "Accept: application/json", "-A", headers["User-Agent"], API],
                    check=True, capture_output=True, text=True).stdout.lstrip("\ufeff")
            return json.loads(texto)
        except Exception as e:  # noqa: BLE001
            ultimo = e
            espera = 15 * i
            print(f"Intento {i}/{intentos} fallido: {e}. Reintento en {espera} s.")
            if i < intentos:
                time.sleep(espera)
    print(f"No se pudo descargar del Ministerio tras {intentos} intentos: {ultimo}. "
          "Se reintentará en la próxima comprobación.")
    sys.exit(0)


def main():
    raw = descargar()
    lista = raw.get("ListaEESSPrecio") or []
    if not lista:
        print("La API no ha devuelto gasolineras: no se guarda nada.")
        sys.exit(0)

    estaciones, municipios, ids = [], set(), set()
    for e in lista:
        ide = str(e.get("IDEESS") or "").strip()
        mun = norm(e.get("Municipio")).split("/")[0]
        loc = bonito(e.get("Localidad", "").split("/")[0])
        d = bonito(e.get("Dirección", ""))
        if not mun.startswith("pamplona") and loc:
            d += f" ({loc})"
        n = bonito(e.get("Rótulo", ""), marca=True) or "Sin marca"
        try:
            lat = float(e.get("Latitud", "").replace(",", "."))
            lon = float(e.get("Longitud (WGS84)", "").replace(",", "."))
        except ValueError:
            lat = lon = None
        comarca = municipio_ok(e.get("Municipio")) or (
            lat is not None and km(CENTRO, (lat, lon)) <= RADIO_COMARCA_KM)
        if comarca:
            municipios.add(e.get("Municipio"))
        pr = {}
        for campo, v in e.items():
            if campo.startswith("Precio "):
                p, c = precio(v), codigo(campo)
                if p is not None and c:
                    pr[c] = p
        if not ide:  # sin código oficial: se genera uno estable con nombre y dirección
            ide = "x" + hashlib.md5(f"{n}|{d}".encode()).hexdigest()[:10]
        if ide in ids:
            continue
        ids.add(ide)
        est = {"id": ide, "n": n, "d": d, "c": 1 if comarca else 0, "pr": pr}
        h = re.sub(r"\s+", " ", (e.get("Horario") or "").strip())
        if h:
            est["h"] = h
        if lat is not None:
            est["lat"], est["lon"] = round(lat, 6), round(lon, 6)
        estaciones.append(est)

    if not estaciones:
        print("La API no ha devuelto gasolineras de Navarra: no se guarda nada.")
        sys.exit(0)
    estaciones.sort(key=lambda x: x["id"])

    # Listas por combustible principal (las usa la app y el historial)
    out = {k: [] for k in COMBUSTIBLES}
    for est in estaciones:
        for k in COMBUSTIBLES:
            if k in est["pr"]:
                fila = {"id": est["id"], "n": est["n"], "d": est["d"], "p": est["pr"][k], "c": est["c"]}
                for extra in ("h", "lat", "lon"):
                    if extra in est:
                        fila[extra] = est[extra]
                out[k].append(fila)
    for k in out:
        out[k].sort(key=lambda x: x["p"])

    ahora = datetime.now(ZoneInfo("Europe/Madrid"))
    try:
        previo = json.load(open(os.path.join(DATA, "precios.json"), encoding="utf-8"))
    except (OSError, ValueError):
        previo = {}
    def firma(lst):
        return sorted((x["id"], x["n"], x["d"], x.get("h"), sorted(x["pr"].items())) for x in lst)
    if previo.get("fecha") == ahora.date().isoformat() and firma(previo.get("estaciones", [])) == firma(estaciones):
        print(f"Sin cambios desde {previo.get('ts')} (Ministerio: {raw.get('Fecha')}).")
        return

    doc = {"ts": ahora.isoformat(timespec="seconds"), "fecha": ahora.date().isoformat(),
           "fuente": FUENTE, "fecha_ministerio": raw.get("Fecha"), **out, "estaciones": estaciones}
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "precios.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))

    hist_path = os.path.join(DATA, "historial.json")
    try:
        hist = json.load(open(hist_path, encoding="utf-8"))
    except (OSError, ValueError):
        hist = {}
    # Medias del día: comarca en la raíz y toda Navarra en "nav"
    dia = hist.setdefault(doc["fecha"], {})
    nav = dia.setdefault("nav", {})
    for k in out:
        ps = [x["p"] for x in out[k] if x["c"]]
        if ps:
            dia[k] = round(sum(ps) / len(ps), 4)
        pn = [x["p"] for x in out[k]]
        if pn:
            nav[k] = round(sum(pn) / len(pn), 4)
    hist = dict(sorted(hist.items())[-400:])
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    sin = [x for x in estaciones if not x["pr"]]
    print(f"Estaciones de Navarra en el Ministerio: {len(lista)} · guardadas: {len(estaciones)} "
          f"· en la comarca: {sum(x['c'] for x in estaciones)} · sin ningún precio: {len(sin)}")
    print(f"Con precio -> diésel {len(out['diesel'])} · diésel premium {len(out['dpremium'])} · "
          f"g95 {len(out['g95'])} · g98 {len(out['g98'])}")
    if sin:
        print("Sin precio publicado: " + "; ".join(f"{x['n']} ({x['d']})" for x in sin))
    print(f"Municipios de la comarca con gasolinera: {sorted(municipios)}")


if __name__ == "__main__":
    main()
