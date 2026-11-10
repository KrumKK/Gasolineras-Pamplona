"""Descarga los precios oficiales (Ministerio para la Transición Ecológica)
de las gasolineras de Pamplona y comarca y los guarda en data/.

- data/precios.json   -> última lectura (la usa la web)
- data/historial.json -> precio medio por día, para el "mejor día para llenar"
"""
import json, os, re, ssl, subprocess, sys, time, unicodedata, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

API = ("https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/"
       "PreciosCarburantes/EstacionesTerrestres/FiltroProvincia/31")  # 31 = Navarra
FUENTE = "Ministerio para la Transición Ecológica (geoportalgasolineras.es)"

# Municipios de la comarca (primer nombre oficial, sin tildes, en minúsculas)
MUNICIPIOS = [
    "pamplona", "burlada", "villava", "ansoain", "berriozar", "berrioplano",
    "huarte", "egues", "valle de egues", "aranguren", "baranain", "zizur mayor",
    "cizur", "cendea de cizur", "orkoien", "orcoyen", "olza", "cendea de olza",
    "galar", "noain", "elortzibar", "beriain",
]
COMBUSTIBLES = {"diesel": "Precio Gasoleo A", "g95": "Precio Gasolina 95 E5",
                "g98": "Precio Gasolina 98 E5"}

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

    out = {k: [] for k in COMBUSTIBLES}
    vistos = {k: set() for k in COMBUSTIBLES}
    municipios = set()
    for e in lista:
        if not municipio_ok(e.get("Municipio")):
            continue
        municipios.add(e.get("Municipio"))
        mun = norm(e.get("Municipio")).split("/")[0]
        loc = bonito(e.get("Localidad", "").split("/")[0])
        es_pamplona = mun.startswith("pamplona")
        d = bonito(e.get("Dirección", ""))
        if not es_pamplona and loc:
            d += f" ({loc})"
        n = bonito(e.get("Rótulo", ""), marca=True) or "Sin marca"
        try:
            lat = float(e.get("Latitud", "").replace(",", "."))
            lon = float(e.get("Longitud (WGS84)", "").replace(",", "."))
        except ValueError:
            lat = lon = None
        for k, campo in COMBUSTIBLES.items():
            p = precio(e.get(campo))
            if p is None:
                continue
            clave = (n, d, p)
            if clave in vistos[k]:
                continue
            vistos[k].add(clave)
            fila = {"n": n, "d": d, "p": p}
            if lat is not None:
                fila["lat"], fila["lon"] = round(lat, 6), round(lon, 6)
            out[k].append(fila)

    if not any(out.values()):
        sys.exit("Ninguna gasolinera de la comarca con precio: no se guarda nada.")
    for k in out:
        out[k].sort(key=lambda x: x["p"])

    ahora = datetime.now(ZoneInfo("Europe/Madrid"))
    try:
        previo = json.load(open(os.path.join(DATA, "precios.json"), encoding="utf-8"))
    except (OSError, ValueError):
        previo = {}
    def firma(d):
        return {k: sorted((x["n"], x["d"], x["p"]) for x in d.get(k, [])) for k in COMBUSTIBLES}
    if previo.get("fecha") == ahora.date().isoformat() and firma(previo) == firma(out):
        print(f"Sin cambios de precio desde {previo.get('ts')} (Ministerio: {raw.get('Fecha')}).")
        return

    doc = {"ts": ahora.isoformat(timespec="seconds"), "fecha": ahora.date().isoformat(),
           "fuente": FUENTE, "fecha_ministerio": raw.get("Fecha"), **out}
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "precios.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    hist_path = os.path.join(DATA, "historial.json")
    try:
        hist = json.load(open(hist_path, encoding="utf-8"))
    except (OSError, ValueError):
        hist = {}
    dia = hist.setdefault(doc["fecha"], {})
    for k in out:
        ps = [x["p"] for x in out[k]]
        if ps:
            dia[k] = round(sum(ps) / len(ps), 4)
    hist = dict(sorted(hist.items())[-400:])
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    print(f"Municipios: {sorted(municipios)}")
    print(f"Diésel: {len(out['diesel'])} · G95: {len(out['g95'])} · "
          f"más barato diésel {out['diesel'][0]['p'] if out['diesel'] else '-'} · "
          f"g95 {out['g95'][0]['p'] if out['g95'] else '-'} · "
          f"g98: {len(out['g98'])}")


if __name__ == "__main__":
    main()
