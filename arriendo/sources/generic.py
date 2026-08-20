"""Extractor genérico de arriendos desde HTML arbitrario.

Este módulo existe porque cada portal tiene un HTML distinto y ninguno publica
API. En vez de un parser por sitio —frágil, se rompe con cada rediseño— se usan
tres pasadas en orden de confiabilidad:

  1. **JSON-LD** (schema.org). Cuando el sitio lo emite, los datos vienen
     estructurados y exactos. Es el camino preferido y no depende del diseño.
  2. **Estado embebido**. Los portales hechos en React/Next dejan el listado
     completo en un `<script>` como JSON (`__NEXT_DATA__`, `__NUXT__`). Es tan
     bueno como el JSON-LD y lo emiten muchos más sitios.
  3. **Heurística sobre tarjetas.** Bloques que contienen un enlace más
     señales de arriendo (un monto en pesos, m², dormitorios) a los que se les
     aplica el parser de texto libre.

La pasada 3 produce ruido; los filtros duros y el umbral de alerta lo filtran.

Nota sobre la calibración: el entorno donde se escribió esto tiene bloqueado
el acceso de red a todos los portales objetivo (403 en CONNECT). Escribir
selectores CSS a ciegas habría sido adivinar, así que las tres pasadas son
deliberadamente independientes del diseño de cada sitio. `arriendo calibrar`
corre en GitHub Actions, que sí tiene internet abierto, y deja los HTML crudos
como artifact para poder escribir los selectores que falten.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import parse as P
from ..models import Arriendo
from .base import FuenteConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Qué parece un aviso de arriendo y qué no
# ---------------------------------------------------------------------------

# Señal fuerte: un dato cuantitativo que solo aparece en un aviso real.
#
# Exigir un número es lo que separa una tarjeta de la barra de navegación.
# Aceptar la palabra "arriendo" suelta hace que menús como "Inicio Arriendos
# Venta Contacto" entren como propiedades.
_SENAL_NUMERICA = re.compile(
    r"(\$\s*\d[\d.]{4,}"                     # $1.550.000
    r"|\d[\d.,]*\s*(?:m2|m²|mt2|mts2)"       # 134 m²
    r"|\b\d{1,2}\s*(?:dormitorios?|dorm\b|piezas?)"
    r"|\b\d{1,2}\s*d\s*/?\s*\d{1,2}\s*b\b"   # 3D/2B
    r"|\bu\.?f\.?\s*\d)",
    re.I,
)

# Cabecera, menú de cuenta y pie de página. Traen el valor UF del día o cifras
# de marketing, así que pasan el filtro de señal numérica sin ser un aviso.
_CHROME_DEL_SITIO = re.compile(
    r"\b(iniciar sesi[oó]n|reg[ií]strate|registrarse|mi cuenta|cerrar sesi[oó]n"
    r"|publica tu propiedad|publicar aviso|descarga la app|todos los derechos"
    r"|pol[ií]tica de privacidad|valor uf hoy"
    # Encabezados de sección, no de aviso: un bloque que los CONTIENE se tragó
    # el widget entero (ver _CODIGO_EN_TARJETA, que atrapa al mismo culpable
    # por otro costado).
    r"|propiedades destacadas|listado de propiedades)\b",
    re.I,
)

# El panel de filtros, leído como si fuera un aviso. Un panel ENUMERA rangos:
#
#   "Hasta $500.000  De $500.000 a $800.000  De $800.000 a $1.200.000 ..."
#
# Un aviso de verdad nombra su precio una vez. Se exigen TRES rangos porque un
# aviso puede legítimamente decir "de 120 m2 a 3 cuadras del metro" y hasta
# encadenar dos medidas, pero nunca tres.
_RANGO_DE_FILTRO = re.compile(
    r"\bde\s+\$?\s*[\d.,]+\s*(?:mt2|m2|m²|uf)?\s*a\s+\$?\s*[\d.,]+", re.I)
_MIN_RANGOS_PARA_SER_FILTRO = 3

# El widget de "Propiedades Destacadas", leído como si fuera una tarjeta.
#
# Caso real (propiedades.cl, corrida del 17-08): un bloque lateral que mezcla
# TRES propiedades —un departamento en VENTA en Santiago, una OFICINA en Las
# Condes y una casa en Ñuñoa— pasó como aviso. El extractor tomó la dirección
# de una, el canon de otra (¡el arriendo de la oficina, UF 22!) y el programa
# de la mezcla, y esa quimera alertó en el teléfono con puntaje 71.
#
# Lo delata lo mismo que delata al panel de filtros: la ENUMERACIÓN. Un aviso
# tiene UN código de publicación; un bloque con dos o más es una lista de
# avisos, y leerla como si fuera uno solo produce campos de propiedades
# distintas cosidos entre sí.
_CODIGO_EN_TARJETA = re.compile(r"\bcod\.?\s*:?\s*-?\s*[\d.]{2,}", re.I)
_MAX_CODIGOS_POR_TARJETA = 1

# Enlaces que nunca son un aviso.
#
# Las rutas de filtro son el caso importante en portales de arriendo: cada
# portal enlaza sus propias búsquedas facetadas —/arriendo/departamento/
# 3-dormitorios/vitacura— y sin esto cada faceta entra como un departamento
# distinto. Son decenas de avisos fantasma, todos apuntando a un listado.
_HREF_IGNORAR = re.compile(
    r"^(#|javascript:|mailto:|tel:)"
    r"|/(login|ingresar|registro|contacto|terminos|privacidad|nosotros|blog"
    r"|faq|ayuda|planes|precios|publicar|suscri(?:pcion|birse))(/|$|\?)"
    r"|applied_filter_id|applied_value_id"
    r"|_(?:price\*?range|covered\*?area|total\*?area|land\*?area)_"
    r"|/(?:sin-dormitorios|\d+-dormitorios?|mas-de-\d+-dormitorios?)(/|$)"
    r"|/(?:venta|comprar)(/|$)"
    # El catálogo completo de propiedades.cl, que era el "link al aviso" de la
    # quimera del widget: un enlace que muestra todo, de todas las comunas, no
    # apunta a ningún departamento.
    r"|/(?:todos_los_tipos|venta_y_arriendo|todas_las_comunas)(/|$)"
    # Proyectos EN VENTA colados en el listado de arriendo (toctoc), y los
    # servicios externos que algún portal mete como link de la tarjeta: en el
    # diagnóstico real una "ficha" terminó siendo maps.app.goo.gl.
    r"|/compranuevo(/|$)"
    r"|maps\.app\.goo\.gl|google\.[a-z.]+/maps|goo\.gl/maps"
    r"|wa\.me/|api\.whatsapp\.com|facebook\.com|instagram\.com|youtube\.com"
    r"|linkedin\.com|twitter\.com|x\.com/",
    re.I,
)

# El ticker de indicadores que las corredoras ponen en su encabezado:
# "19/08/2026 UF 40.856,64 USD 914,19" entró como "aviso" de Magnolia en la
# corrida del 19-08, con ficha propia y todo — puros números y siglas de
# moneda, sin una palabra de departamento. Un bloque cuyo texto COMPLETO es
# eso no es una tarjeta.
_PURO_INDICADOR = re.compile(
    r"^[\s\d/.:,·|-]*(?:(?:UF|USD|US\$|EUROS?|UTM|IPC|D[oó]lar(?:es)?)"
    r"[\s\d/.:,·|%$-]*){1,4}"
    # El botón de la tarjeta se pega al final del texto ("ver más"): hasta
    # dos palabras cortas de residuo siguen siendo un ticker. Un aviso real
    # trae dormitorios, dirección o descripción — mucho más que eso.
    r"(?:[a-záéíóúñ]{1,8}(?:\s+[a-záéíóúñ]{1,8})?)?\s*$", re.I)

# Un bloque más largo que esto ya no es una tarjeta, es la página entera.
_MAX_TEXTO_CARD = 1800
_MIN_TEXTO_CARD = 30


def _es_panel_de_filtros(texto: str) -> bool:
    return len(_RANGO_DE_FILTRO.findall(texto or "")) >= _MIN_RANGOS_PARA_SER_FILTRO


def _tiene_senal(texto: str) -> bool:
    """¿Este bloque parece un aviso de arriendo?"""
    if _CHROME_DEL_SITIO.search(texto):
        return False
    if _es_panel_de_filtros(texto):
        return False
    if len(_CODIGO_EN_TARJETA.findall(texto)) > _MAX_CODIGOS_POR_TARJETA:
        return False
    return bool(_SENAL_NUMERICA.search(texto))


def _absoluto(href: str, base: str) -> str:
    return urljoin(base, (href or "").strip())


def _texto(el: Any) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return P.parse_numero(v.strip()) or None
    if isinstance(v, dict):  # QuantitativeValue
        return _num(v.get("value"))
    if isinstance(v, list):
        # TocToc publica los campos como rangos del edificio entero:
        # "superficie": [140] o [80, 124]. El primero es la unidad publicada.
        return _num(v[0]) if v else None
    return None


def _entero(v: Any, tope: int = 20) -> int | None:
    n = _num(v)
    if n is None:
        return None
    return int(n) if 0 < n <= tope else None


# ---------------------------------------------------------------------------
# Pasada 1: JSON-LD
# ---------------------------------------------------------------------------

_TIPOS_LD = {
    "product", "offer", "residence", "apartment", "house", "place",
    "singlefamilyresidence", "realestatelisting", "accommodation",
    "rentaction", "rentalproperty",
    # houm publica cada aviso como ApartmentComplex (medido contra su página
    # real): nombre, url y el canon adentro de potentialAction.
    "apartmentcomplex",
}


def _walk_ld(nodo: Any, salida: list[dict]) -> None:
    """Recorre el árbol JSON-LD juntando nodos que parezcan una propiedad."""
    if isinstance(nodo, list):
        for x in nodo:
            _walk_ld(x, salida)
        return
    if not isinstance(nodo, dict):
        return

    tipos = nodo.get("@type", "")
    tipos = [tipos] if isinstance(tipos, str) else (tipos if isinstance(tipos, list) else [])
    if any(str(t).lower() in _TIPOS_LD for t in tipos):
        salida.append(nodo)

    for v in nodo.values():
        if isinstance(v, (dict, list)):
            _walk_ld(v, salida)


def _precio_ld(nodo: dict) -> tuple[float | None, str]:
    """El precio de un nodo JSON-LD y su moneda."""
    offers = nodo.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None, ""

    precio = _num(offers.get("price"))
    if precio is None:
        spec = offers.get("priceSpecification")
        if isinstance(spec, list):
            spec = spec[0] if spec else None
        if isinstance(spec, dict):
            precio = _num(spec.get("price"))
    return precio, str(offers.get("priceCurrency") or "").upper()


def _precio_de_accion(nodo: dict) -> tuple[float | None, str]:
    """El canon dentro de potentialAction, que es donde lo pone houm:
    ApartmentComplex → RentAction → PriceSpecification."""
    accion = nodo.get("potentialAction")
    if isinstance(accion, list):
        accion = accion[0] if accion else None
    if not isinstance(accion, dict):
        return None, ""
    spec = accion.get("priceSpecification")
    if isinstance(spec, list):
        spec = spec[0] if spec else None
    if not isinstance(spec, dict):
        return None, ""
    return _num(spec.get("price")), str(spec.get("priceCurrency") or "").upper()


def _desde_jsonld(soup: BeautifulSoup, base_url: str, fuente: FuenteConfig,
                  valor_uf: float | None = None) -> list[Arriendo]:
    nodos: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            _walk_ld(json.loads(tag.string or "{}"), nodos)
        except (json.JSONDecodeError, TypeError):
            continue

    salida: list[Arriendo] = []
    for n in nodos:
        url = n.get("url") or n.get("@id") or ""
        url = _absoluto(url, base_url) if url else base_url
        nombre = str(n.get("name") or "")
        desc = str(n.get("description") or "")

        # Un nodo que SOLO tiene nombre no es un aviso: es el breadcrumb del
        # sitio. En el diagnóstico real, propertypartners producía cuatro
        # "avisos" que eran Place {name: "Región Metropolitana"} — cuatro
        # cascarones sin un solo dato entrando al tablero.
        if not (n.get("address") or n.get("geo") or n.get("offers")
                or n.get("potentialAction") or n.get("floorSize")
                or n.get("numberOfRooms") or n.get("numberOfBedrooms")
                or n.get("numberOfBathroomsTotal")):
            continue

        direccion, comuna = "", ""
        addr = n.get("address")
        if isinstance(addr, dict):
            # Sin repetir lo ya dicho: los metabuscadores mandan un
            # streetAddress que ya trae comuna, región y hasta el país ("Los
            # Acantos, Lo Castillo, Vitacura, …, Chile"), y pegarle
            # addressLocality y addressRegion atrás producía direcciones con
            # "Vitacura" dos veces — así salió una en el teléfono el 17-08.
            partes: list[str] = []
            for k in ("streetAddress", "addressLocality", "addressRegion"):
                p = str(addr.get(k) or "").strip()
                if p and P.norm(p) not in P.norm(", ".join(partes)):
                    partes.append(p)
            direccion = ", ".join(partes)
            comuna = P.parse_comuna(str(addr.get("addressLocality") or "")) or ""
        elif isinstance(addr, str):
            direccion = addr

        lat = lon = None
        geo = n.get("geo")
        if isinstance(geo, dict):
            lat, lon = _num(geo.get("latitude")), _num(geo.get("longitude"))

        blob = " ".join([nombre, desc, direccion]).strip()
        a = _armar(blob, url, fuente, base_url, valor_uf)
        a.title = nombre or a.title
        a.direccion = direccion or a.direccion
        a.comuna = comuna or a.comuna
        a.lat, a.lon = lat, lon
        a.extras["via"] = "json-ld"

        # Los campos estructurados PISAN lo que dedujo el parser de texto: si
        # el sitio los declara, son exactos y el texto es una aproximación.
        if (v := _num(n.get("floorSize"))) is not None:
            a.m2_totales = v
        if (v := _entero(n.get("numberOfRooms"))) is not None:
            a.dormitorios = v
        if (v := _entero(n.get("numberOfBedrooms"))) is not None:
            a.dormitorios = v
        if (v := _entero(n.get("numberOfBathroomsTotal"))) is not None:
            a.banos = v
        if (v := _num(n.get("yearBuilt"))) and 1900 <= v <= P.hoy().year + 2:
            a.ano_construccion = int(v)
            a.antiguedad_anos = P.hoy().year - int(v)

        precio, moneda = _precio_ld(n)
        if precio is None:
            precio, moneda = _precio_de_accion(n)
        if precio:
            if moneda == "CLF":            # CLF es el código ISO de la UF
                a.arriendo_uf = precio
                a.arriendo_clp = round(precio * (valor_uf or P.VALOR_UF_DEFECTO))
            elif precio >= 200_000 or moneda == "CLP":
                a.arriendo_clp = precio
            else:
                # Un "precio" de tres cifras en un aviso chileno de arriendo no
                # son pesos: es la UF sin declarar la moneda.
                a.arriendo_uf = precio
                a.arriendo_clp = round(precio * (valor_uf or P.VALOR_UF_DEFECTO))

        if a.url and (a.direccion or a.title):
            salida.append(a)

    return salida


# ---------------------------------------------------------------------------
# Pasada 2: estado embebido de las SPA
#
# Los portales hechos en React o Nuxt arman el listado en el navegador a partir
# de un objeto que dejan escrito en la página. Leer ese objeto es mejor que
# leer las tarjetas por dos razones: no hay nada que adivinar, y sobrevive a un
# rediseño del CSS.
# ---------------------------------------------------------------------------

_ESTADO_EMBEBIDO = [
    re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S),
    re.compile(r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S),
    re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S),
]

# Cómo se llaman los campos en los payloads de los portales chilenos. Cada
# lista va de la forma más específica a la más genérica.
_LLAVES = {
    "url": ("url", "link", "permalink", "href", "detailUrl", "urlDetalle",
            "urlFicha"),
    "titulo": ("title", "titulo", "name", "nombre", "descripcionCorta"),
    "descripcion": ("description", "descripcion", "detalle", "observaciones"),
    "direccion": ("address", "direccion", "streetAddress", "ubicacion",
                  "location"),
    "comuna": ("comuna", "commune", "district", "addressLocality", "sector"),
    "precio": ("price", "precio", "valor", "monto", "arriendo", "rentPrice",
               "precioArriendo"),
    "moneda": ("currency", "moneda", "priceCurrency", "tipoMoneda"),
    "gastos": ("gastosComunes", "gastos_comunes", "commonExpenses",
               "expensas", "gc"),
    "m2_totales": ("totalArea", "superficieTotal", "superficie_total",
                   "mtsTotales", "m2Totales", "superficie"),
    "m2_utiles": ("coveredArea", "superficieUtil", "superficie_util",
                  "mtsUtiles", "m2Utiles", "builtArea"),
    "dormitorios": ("bedrooms", "dormitorios", "rooms", "habitaciones",
                    "nroDormitorios"),
    "banos": ("bathrooms", "banos", "baños", "nroBanos"),
    "estacionamientos": ("parkingSpaces", "estacionamientos", "parking"),
    "lat": ("lat", "latitude", "latitud"),
    "lon": ("lng", "lon", "longitude", "longitud"),
    "ano": ("yearBuilt", "anoConstruccion", "año_construccion", "antiguedad"),
}


def _busca(d: dict, llaves: tuple[str, ...]) -> Any:
    for k in llaves:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _monto_de_payload(d: dict) -> tuple[float | None, float | None]:
    """(clp, uf) cuando el payload trae el precio como lista de monedas.

    Es la forma de TocToc, medida contra su página real: `precios:
    [{prefix: "UF", value: "49"}, {prefix: "$", value: "2.001.911"}]` — el
    mismo canon en las dos monedas. Sin leerla, TocToc entero daba CERO
    avisos con el inventario a la vista en su NEXT_DATA.
    """
    precios = d.get("precios")
    if not isinstance(precios, list):
        return None, None
    clp = uf = None
    for p in precios:
        if not isinstance(p, dict):
            continue
        v = _num(p.get("value"))
        if v is None:
            continue
        prefijo = str(p.get("prefix") or "").upper()
        if "UF" in prefijo:
            uf = v
        elif "$" in prefijo or "CLP" in prefijo:
            clp = v
    return clp, uf


def _parece_aviso(d: Any) -> bool:
    """¿Este dict del payload es un aviso y no un nodo cualquiera del árbol?

    Se exige un precio Y algo que lo ubique o lo mida. Solo el precio no
    alcanza: los payloads traen tablas de tarifas, rangos de filtro y
    configuración de la moneda del sitio, todos con un campo `price`.
    """
    if not isinstance(d, dict):
        return False
    if _busca(d, _LLAVES["precio"]) is None \
            and _monto_de_payload(d) == (None, None):
        return False
    return any(_busca(d, _LLAVES[k]) is not None
               for k in ("direccion", "comuna", "m2_totales", "m2_utiles",
                         "dormitorios"))


def _recolectar_avisos(nodo: Any, salida: list[dict], profundidad: int = 0) -> None:
    if profundidad > 12 or len(salida) > 400:
        return
    if isinstance(nodo, list):
        for x in nodo:
            _recolectar_avisos(x, salida, profundidad + 1)
        return
    if not isinstance(nodo, dict):
        return
    if _parece_aviso(nodo):
        salida.append(nodo)
        # No se sigue bajando: los hijos de un aviso son sus fotos y su
        # corredora, no más avisos.
        return
    for v in nodo.values():
        if isinstance(v, (dict, list)):
            _recolectar_avisos(v, salida, profundidad + 1)


def _desde_estado_embebido(html: str, base_url: str, fuente: FuenteConfig,
                           valor_uf: float | None = None) -> list[Arriendo]:
    crudos: list[dict] = []
    for patron in _ESTADO_EMBEBIDO:
        for m in patron.finditer(html or ""):
            try:
                _recolectar_avisos(json.loads(m.group(1)), crudos)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        if crudos:
            break

    salida: list[Arriendo] = []
    for d in crudos:
        url = str(_busca(d, _LLAVES["url"]) or "")
        url = _absoluto(url, base_url) if url else base_url
        titulo = str(_busca(d, _LLAVES["titulo"]) or "")
        desc = str(_busca(d, _LLAVES["descripcion"]) or "")
        direccion = _direccion_de_json(
            _texto_de(_busca(d, _LLAVES["direccion"])))
        comuna_cruda = _texto_de(_busca(d, _LLAVES["comuna"]))

        blob = " ".join([titulo, desc, direccion, comuna_cruda]).strip()
        a = _armar(blob, url, fuente, base_url, valor_uf)
        a.title = titulo or a.title
        a.direccion = direccion or a.direccion
        a.comuna = P.parse_comuna(comuna_cruda) or a.comuna
        a.extras["via"] = "estado-embebido"

        for campo, llave, tope in (("dormitorios", "dormitorios", 20),
                                   ("banos", "banos", 20),
                                   ("estacionamientos", "estacionamientos", 10)):
            if (v := _entero(_busca(d, _LLAVES[llave]), tope)) is not None:
                setattr(a, campo, v)

        for campo, llave in (("m2_totales", "m2_totales"),
                             ("m2_utiles", "m2_utiles")):
            v = _num(_busca(d, _LLAVES[llave]))
            if v is not None and 15 <= v <= 1200:
                setattr(a, campo, v)

        lat, lon = _num(_busca(d, _LLAVES["lat"])), _num(_busca(d, _LLAVES["lon"]))
        # Santiago está cerca de (-33.4, -70.6). Un par fuera de Chile es un
        # campo homónimo del payload, no la ubicación del departamento.
        if lat is not None and lon is not None and -56 <= lat <= -17 and -76 <= lon <= -66:
            a.lat, a.lon = lat, lon

        v = _num(_busca(d, _LLAVES["ano"]))
        if v is not None:
            if 1900 <= v <= P.hoy().year + 2:
                a.ano_construccion, a.antiguedad_anos = int(v), P.hoy().year - int(v)
            elif 0 <= v <= 120:          # el campo traía la antigüedad, no el año
                a.antiguedad_anos = int(v)
                a.ano_construccion = P.hoy().year - int(v)

        precio = _num(_busca(d, _LLAVES["precio"]))
        moneda = str(_texto_de(_busca(d, _LLAVES["moneda"])) or "").upper()
        if precio:
            if "UF" in moneda or moneda == "CLF" or (not moneda and precio < 1000):
                a.arriendo_uf = precio
                a.arriendo_clp = round(precio * (valor_uf or P.VALOR_UF_DEFECTO))
            else:
                a.arriendo_clp = precio
        else:
            # La forma lista de TocToc. Manda el valor en pesos, que es el
            # que el portal calculó; la UF queda anotada igual.
            clp_lista, uf_lista = _monto_de_payload(d)
            if clp_lista or uf_lista:
                a.arriendo_uf = uf_lista
                a.arriendo_clp = clp_lista or round(
                    (uf_lista or 0) * (valor_uf or P.VALOR_UF_DEFECTO))

        gastos = _num(_busca(d, _LLAVES["gastos"]))
        if gastos is not None and 15_000 <= gastos <= 1_500_000:
            a.gastos_comunes_clp = gastos

        if a.url and (a.direccion or a.title):
            salida.append(a)

    return salida


def _texto_de(v: Any) -> str:
    """Aplana un campo que puede venir como string o como objeto anidado."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("name", "nombre", "value", "label", "descripcion", "text"):
            if isinstance(v.get(k), str):
                return v[k]
        return " ".join(str(x) for x in v.values() if isinstance(x, (str, int, float)))
    if isinstance(v, list):
        return " ".join(_texto_de(x) for x in v)
    return "" if v is None else str(v)


# ---------------------------------------------------------------------------
# Pasada 3: heurística sobre tarjetas
# ---------------------------------------------------------------------------

def _candidatos(soup: BeautifulSoup, fuente: FuenteConfig) -> list[Any]:
    """Los bloques que podrían ser una tarjeta de aviso.

    Con `selector_card` configurado se usa y punto: es lo que deja la
    calibración contra el HTML real y siempre es mejor que adivinar.

    Sin él se buscan los ancestros de cada enlace, de más cerca a más lejos, y
    se elige el primero que tenga señal de aviso y un tamaño de tarjeta. Subir
    de a poco importa: el <a> solo trae el título, y el contenedor de la
    grilla trae las veinte tarjetas juntas.
    """
    if fuente.selector_card:
        return soup.select(fuente.selector_card)

    vistos: set[int] = set()
    tarjetas: list[Any] = []

    for a in soup.find_all("a", href=True):
        nodo = a
        for _ in range(4):
            nodo = nodo.parent
            if nodo is None or nodo.name in ("body", "html", "[document]"):
                break
            if id(nodo) in vistos:
                break
            texto = _texto(nodo)
            if len(texto) < _MIN_TEXTO_CARD:
                continue
            if len(texto) > _MAX_TEXTO_CARD:
                break
            if _tiene_senal(texto):
                vistos.add(id(nodo))
                tarjetas.append(nodo)
                break

    return _sin_anidados(tarjetas)


def _sin_anidados(tarjetas: list[Any]) -> list[Any]:
    """Se queda con la tarjeta de afuera cuando una contiene a la otra.

    Una tarjeta de portal tiene varios enlaces —la foto, el título, un botón—
    y cada uno sube por su propia rama: el de la foto llega al <article> y el
    del título se queda en el <div> de los datos. Los dos tienen señal de
    aviso, así que el mismo departamento entraba dos veces.

    Gana el ancestro porque es el que trae la tarjeta completa: el <div>
    interior de los datos se pierde la foto, y en otros portales se pierde el
    precio, que vive en un hermano.

    No hay riesgo de quedarse con la grilla entera: un bloque con las veinte
    tarjetas adentro pasa de `_MAX_TEXTO_CARD` y nunca llegó a ser candidato.
    """
    ids = {id(t) for t in tarjetas}
    salida = []
    for t in tarjetas:
        padre = t.parent
        anidada = False
        while padre is not None:
            if id(padre) in ids:
                anidada = True
                break
            padre = padre.parent
        if not anidada:
            salida.append(t)
    return salida


def _enlace(card: Any, base_url: str, fuente: FuenteConfig) -> str:
    """El enlace al aviso dentro de la tarjeta.

    Se prefiere el que apunte al mismo dominio y no esté en la lista de
    ignorados. Sin este cuidado, la alerta termina apuntando a la página de
    planes del portal: varios meten un "Publica tu propiedad" dentro de cada
    tarjeta.
    """
    host = urlparse(base_url).netloc
    respaldo = ""
    for a in card.select(fuente.selector_link or "a"):
        href = (a.get("href") or "").strip()
        if not href or _HREF_IGNORAR.search(href):
            continue
        absoluto = _absoluto(href, base_url)
        destino = urlparse(absoluto).netloc
        if destino and destino != host:
            respaldo = respaldo or absoluto
            continue
        return absoluto
    return respaldo


def _desde_tarjetas(soup: BeautifulSoup, base_url: str, fuente: FuenteConfig,
                    valor_uf: float | None = None) -> list[Arriendo]:
    salida: list[Arriendo] = []
    for card in _candidatos(soup, fuente):
        texto = _texto(card)
        if _PURO_INDICADOR.match((texto or "").strip()):
            continue
        url = _enlace(card, base_url, fuente)
        if not url:
            continue
        a = _armar(texto, url, fuente, base_url, valor_uf)
        a.extras["via"] = "tarjeta"
        # Sin nada que lo identifique no se puede deduplicar ni mostrar.
        if a.direccion or a.title:
            salida.append(a)
    return salida


# ---------------------------------------------------------------------------
# El armado común
# ---------------------------------------------------------------------------

# El título del aviso: la primera frase con sustancia del bloque.
_CORTE_TITULO = re.compile(r"\s+[·|•]\s+|\s{2,}|\n")


# Botones de la tarjeta que se pegan al principio del texto. "Añadir a
# favoritos Leticia Caceres Vitacura Departamento…" fue un título REAL enviado
# por Telegram en la corrida del 17-08: el corazón de guardar y el nombre de
# la corredora, leídos como si fueran el nombre del departamento.
_CHROME_DE_TARJETA = re.compile(
    r"^(?:a[ñn]adir\s+a\s+favoritos|agregar\s+a\s+favoritos|guardar|"
    r"destacado|nuevo|exclusivo)\s*[:·-]?\s*", re.I)

# La fecha y la ruta de categorías con que chilepropiedades encabeza sus
# tarjetas. "15/08/2026 Arriendo Mensual / Departamento / Vitacura" fue un
# título REAL en el teléfono (captura del 17-08): dice cuándo se publicó y
# dónde está el listado, pero nada del departamento. Quitándolo, el título
# cae a la dirección, que es lo que sirve.
_FECHA_Y_CATEGORIA = re.compile(
    r"^(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\s*)?"
    r"(?:arriendo\s+mensual|arriendo|venta)?\s*"
    r"(?:/\s*departamento\s*(?:/\s*[\wáéíóúñ ]+)?)\s*", re.I)

# El lastre numérico con que algunas tarjetas encabezan su texto. "1/28
# 1.250.000 $ Mensual 30,60 UF 2 2 5 103 Departamento Vitacura…" fue un
# título REAL de RE/MAX en el teléfono: el paginador del carrusel de fotos,
# el precio en pesos y en UF y la fila de iconos, todo ANTES de la primera
# palabra con sustancia. Cada token del lastre es un número (con sus puntos,
# comas y barras), un signo peso o una de las palabritas que los rotulan; el
# título de verdad empieza donde esa racha se corta.
_LASTRE_DE_TITULO = re.compile(
    r"^(?:(?:[\d$][\d$./,]*|uf|clp|m2|m²|mensual|usada?|destacado|nuevo"
    r"|exclusivo)\s+)+", re.I)


# Un título sin sustancia: puro precio, chrome y botones. "$ 770.000
# Arriendo Ver más Contactar" ALERTÓ con 87 puntos en la corrida del 18-08 —
# doomos escribe sus tarjetas así, con la descripción real en el slug de la
# URL ("1465886_arriendo-departamento-en-av-kennedy-vitacura.html").
_TITULO_HUECO = re.compile(
    r"^(?:[\s\d$.,/]|uf\.?|clp|arriendos?|ventas?|ver\s+m[aá]s|contactar"
    r"|detalles?|mensual|desde)+$", re.I)


def _titulo_de_slug(url: str) -> str:
    """El título que el portal escribió en la RUTA del aviso."""
    segmento = (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    segmento = re.sub(r"\.[a-z]{2,5}$", "", segmento, flags=re.I)
    segmento = re.sub(r"^\d+[_-]*", "", segmento)
    texto = re.sub(r"[-_]+", " ", segmento).strip()
    if len(texto) < 12 or not re.search(r"[a-záéíóúñ]{3}", texto, re.I):
        return ""
    return (texto[0].upper() + texto[1:])[:150]


def _titulo_desde(texto: str) -> str:
    texto = _CHROME_DE_TARJETA.sub("", (texto or "").strip())
    texto = _FECHA_Y_CATEGORIA.sub("", texto).strip()
    # Solo si después del recorte queda con qué titular: un aviso que ES puro
    # número ("$1.500.000 depto") prefiere el texto completo antes que nada.
    sin_lastre = _LASTRE_DE_TITULO.sub("", texto).strip()
    if len(sin_lastre) >= 12:
        texto = sin_lastre
    for trozo in _CORTE_TITULO.split(texto or ""):
        limpio = trozo.strip()
        if len(limpio) >= 12:
            return limpio[:150]
    return (texto or "").strip()[:150]


# Un número pelado de la fila de iconos: sin moneda, sin decimales, sin
# porcentaje pegado. "$1.250.000" y "UF38,00" no calzan; el "2" de "2 1 2" sí.
_ICONO = re.compile(r"(?<![\w.,/%$°–-])(\d{1,2})(?![\w.,/%°–-])")

_CAMPOS_DE_ICONO = {"d": "dormitorios", "b": "banos",
                    "e": "estacionamientos"}


def _programa_de_iconos(texto: str, orden: str) -> dict[str, int]:
    """El programa que la tarjeta muestra como fila de iconos sin rotular.

    Yapo cierra cada tarjeta en "$1.250.000 2 1 2 Compara este anuncio": el
    2-1-2 son dormitorios, estacionamientos y baños junto a un iconito que el
    texto plano no trae. Era el 77% de los yapo sin dormitorios en el tablero
    real, con el dato a la vista en la tarjeta.

    Solo se lee la ÚLTIMA racha de números pelados del texto, y solo si
    alcanza para el orden configurado: una racha corta al final ("hace 3
    días") no calza y no inventa nada. De la racha se toman los últimos N,
    porque el "m 2" de la superficie ("75 m 2 2 1 2") se pega por delante.
    """
    campos = orden.split()
    rachas: list[list[int]] = []
    actual: list[int] = []
    fin_anterior: int | None = None
    for m in _ICONO.finditer(texto):
        if fin_anterior is not None and texto[fin_anterior:m.start()].strip():
            if actual:
                rachas.append(actual)
            actual = []
        actual.append(int(m.group(1)))
        fin_anterior = m.end()
    if actual:
        rachas.append(actual)

    if not rachas or len(rachas[-1]) < len(campos):
        return {}
    numeros = rachas[-1][-len(campos):]
    salida = {}
    for letra, n in zip(campos, numeros):
        campo = _CAMPOS_DE_ICONO.get(letra)
        if campo and 0 < n <= 15:
            salida[campo] = n
    return salida


def _armar(texto: str, url: str, fuente: FuenteConfig, base_url: str = "",
           valor_uf: float | None = None) -> Arriendo:
    """Convierte texto libre en un `Arriendo`, aplicando todo el parser.

    Es el único lugar donde se decide qué campo sale de qué función, así que
    las tres pasadas producen objetos comparables entre sí. Cada pasada puede
    después pisar lo que sepa mejor.
    """
    titulo = _titulo_desde(texto)
    if _TITULO_HUECO.match(titulo or "") and url and url != base_url:
        titulo = _titulo_de_slug(url) or titulo
    a = Arriendo(
        source=fuente.id,
        url=url or base_url,
        title=titulo,
        raw_text=texto[:2000],
        operacion=P.parse_operacion(texto, fuente.operacion_default),
        tipo=P.parse_tipo(texto),
        comuna=P.parse_comuna(texto, P.COMUNAS_CONOCIDAS),
        orientacion=P.parse_orientacion(texto),
        amoblado=P.parse_amoblado(texto),
        mascotas=P.parse_mascotas(texto),
        piso=P.parse_piso(texto),
        ultimo_piso=P.es_ultimo_piso(texto),
        disponible_desde=P.parse_disponibilidad(texto),
        publicado_el=P.parse_publicado(texto),
    )

    montos = P.parse_montos(texto, valor_uf)
    a.arriendo_clp = montos.get("arriendo_clp")
    a.arriendo_uf = montos.get("arriendo_uf")
    a.gastos_comunes_clp = montos.get("gastos_comunes_clp")
    a.garantia_meses = montos.get("garantia_meses")
    # Un precio de venta encontrado en el texto es la señal más dura de que
    # este aviso no es un arriendo, aunque la palabra no aparezca.
    if montos.get("precio_venta_clp") and not a.arriendo_clp:
        a.operacion = "venta"

    for campo, valor in P.parse_superficies(texto).items():
        setattr(a, campo, valor)

    programa = P.parse_programa(texto)
    a.dormitorios = programa.get("dormitorios")
    a.banos = programa.get("banos")
    a.estacionamientos = programa.get("estacionamientos")
    a.bodega = programa.get("bodega")
    if programa.get("pieza_servicio"):
        a.extras["pieza_servicio"] = True

    # La fila de iconos del portal, SOLO para rellenar lo que el texto
    # rotulado no dijo: un "3 dormitorios" escrito le gana siempre al icono.
    if fuente.fila_iconos and (a.dormitorios is None or a.banos is None
                               or a.estacionamientos is None):
        for campo, v in _programa_de_iconos(texto, fuente.fila_iconos).items():
            if getattr(a, campo) is None:
                setattr(a, campo, v)

    a.ano_construccion, a.antiguedad_anos = P.parse_antiguedad(texto)
    if (techo := P.techo_antiguedad(texto)) is not None:
        a.extras["antiguedad_techo"] = techo

    if (unidad := P.parse_unidad(texto)):
        a.extras["unidad"] = unidad
    if P.es_particular(texto):
        a.extras["particular"] = True

    # El nombre legible del portal, para poder escribir "Ver en TocToc" en vez
    # de "Ver en toctoc". El id sirve para la configuración; el nombre es lo
    # que se le muestra a una persona.
    if fuente.nombre:
        a.extras["portal"] = fuente.nombre

    # ¿El link lleva AL AVISO o al listado completo? Los metabuscadores como
    # Mitula a veces no dan href por tarjeta, y el aviso queda apuntando a la
    # página de búsqueda. El usuario lo descubrió tocando "Ver en Mitula" en
    # su teléfono y cayendo en los 2.277 resultados de Vitacura. La marca
    # permite dos cosas: que la deduplicación prefiera la copia con link
    # directo, y que el mensaje sea honesto cuando no lo hay.
    if not url or url == base_url:
        a.extras["sin_link_directo"] = True

    # La comuna del listado, si el aviso no la dijo.
    #
    # Va acá y no en el constructor porque tiene que correr DESPUÉS de las
    # pasadas de JSON-LD y estado embebido, que saben mejor: si alguna de
    # ellas encontró la comuna de verdad, esta no toca nada.
    #
    # Por qué hace falta: una tarjeta de un listado ya filtrado no repite lo
    # que el usuario acaba de elegir. En la corrida real quedaron 62 avisos de
    # 328 sin comuna, y aunque solo 3 vinieran de listados filtrados por
    # comuna, esos 3 se puntuaban como si no se supiera dónde están — que en
    # un radar cuya primera regla es "prioriza Vitacura comuna entera" es
    # mandarlos al fondo del tablero.
    #
    # Queda marcado como DEDUCIDO, no como publicado. No es un tecnicismo: un
    # listado filtrado por comuna igual trae avisos colados de Las Condes, y
    # `evaluar_zona` usa esa marca para dejar que las coordenadas desmientan a
    # la comuna cuando se contradicen. Un dato deducido presentado como
    # publicado es peor que uno ausente.
    # Antes que la comuna del listado, la que dice la RUTA del propio aviso:
    # el listado de Vitacura de houm trae colados avisos de Las Condes con un
    # JSON-LD que no dice comuna utilizable, y la del listado se los
    # apropiaba. Igual queda como deducida: las coordenadas pueden
    # desmentirla.
    if not a.comuna and url and url != base_url:
        if (c := P.parse_comuna_de_url(url)):
            a.comuna = c
            a.extras["comuna_origen"] = "de la URL del aviso"
    if not a.comuna and fuente.comuna_default:
        a.comuna = fuente.comuna_default
        a.extras["comuna_origen"] = "del listado, que ya venía filtrado por comuna"

    a.direccion = _direccion_desde(texto, a.comuna)
    return a


# ---------------------------------------------------------------------------
# La dirección
#
# Es la pieza con la que se decide si dos avisos son el mismo departamento, y
# por eso vale la pena hacerla bien: una dirección con basura pegada adelante
# —"Manquehue Avenida Santa María 6800"— no calza con la misma dirección
# limpia que publica otro portal, y el departamento alerta dos veces.
#
# El camino es al revés de lo intuitivo: se busca primero la ALTURA y desde
# ahí se camina hacia atrás. La altura es lo único inequívoco de una dirección
# chilena; el nombre de la calle es un puñado de palabras con mayúscula que se
# ven exactamente igual que el sector, la comuna o el nombre del edificio.
# ---------------------------------------------------------------------------

# Palabras que se pegan al nombre de la calle sin ser parte de él.
_CONECTORES = {"de", "del", "la", "las", "los", "el", "y"}

# Tipos de vía que CIERRAN el nombre hacia atrás: al encontrarlos se sabe que
# ahí empieza la dirección y no hay que seguir subiendo.
#
# "costanera" y "alameda" quedan fuera a propósito, aunque también son tipos
# de vía: en Vitacura "Nueva Costanera" es el nombre de la calle, y cortar en
# "Costanera" la dejaría como "Costanera 3600", que es otra dirección.
_VIAS_QUE_CIERRAN = {"av", "av.", "avda", "avda.", "avenida", "calle",
                     "pasaje", "psje", "psje.", "pje", "pje.", "camino"}

# Palabras que marcan una unidad, no una calle: el número que las sigue es el
# del departamento.
_MARCAS_DE_UNIDAD = {"depto", "depto.", "dpto", "dpto.", "departamento",
                     "dep", "dep.", "casa", "oficina", "of", "of.", "piso",
                     "torre", "block", "blok"}

# Unidades que descalifican al número: "134 m²" no es una altura.
#
# La lista de programa —dormitorios, baños, estacionamientos— se agregó al ver
# un aviso real de Yapo: "Departamento en Luis Carrera 3 Dormitorios por CLP
# 1600000.00" producía la dirección "Luis Carrera 3", tomando como altura el
# número de dormitorios.
#
# No es cosmético: la dirección es la llave con la que se deduplica entre
# portales, así que una inventada puede fusionar dos departamentos distintos
# o impedir que se junten dos copias del mismo.
#
# "piso" queda deliberadamente fuera: en "Luis Carrera 1200 piso 5" el 1200 sí
# es la altura, y descartarlo por la palabra que viene después sería perder la
# dirección de verdad.
_UNIDAD_TRAS_NUMERO = re.compile(
    r"^\s*(?:m2|m²|mt2|mts2|mts|metros|uf|clp|clf|a[nñ]os?|%"
    r"|dormitorios?|dorm\b|piezas?|habitaciones?|ba[nñ]os?"
    r"|estacionamientos?|bodegas?|d\b|b\b)", re.I)

_TOKEN = re.compile(r"[^\s,;·•|]+")
# Sin cero inicial: ninguna numeración chilena parte en 0, pero los decimales
# partidos por la coma sí — "UF38,00" se tokeniza como "UF38" y "00", y ese
# "00" pasaba por altura. Salió en el teléfono como dirección "UF38 00".
_ES_ALTURA = re.compile(r"^(?:n[°ºo]\.?|#)?([1-9]\d{0,4})$", re.I)

# Cuántas palabras hacia atrás puede tener el nombre de una calle. Cuatro
# alcanza para "Avenida Santa María de Manquehue" y corta antes de tragarse
# la frase anterior.
_MAX_PALABRAS_CALLE = 4


def _es_nombre_de_calle(token: str) -> bool:
    """¿Esta palabra puede ser parte del nombre de una calle?"""
    if token.lower() in _CONECTORES:
        return True
    if not token[:1].isalpha():
        return False
    # Mayúscula inicial, o todo en mayúsculas como escriben varios portales.
    return token[:1].isupper()


def _quitar_comuna_inicial(palabras: list[str]) -> list[str]:
    """Saca la comuna cuando quedó pegada al principio del nombre.

    Pasa siempre que el aviso escribe "…en Las Condes Isabel La Católica
    4800": las dos palabras de la comuna tienen mayúscula y se ven igual que
    el nombre de la calle.

    Se prueban prefijos de tres, dos y una palabra porque las comunas chilenas
    tienen hasta tres ("San José de Maipo") y hay que sacarla entera o no
    sacarla: dejar "Condes Isabel La Católica" sería peor que no tocar nada.
    """
    conocidas = {P.norm(c) for c in P.COMUNAS_CONOCIDAS}
    for largo in (3, 2, 1):
        if len(palabras) > largo and P.norm(" ".join(palabras[:largo])) in conocidas:
            return palabras[largo:]
    return palabras


# Palabras de programa que no son calles. "Baños: 3" salió como DIRECCIÓN en
# un aviso real —y por lo tanto como llave de deduplicación y como título del
# mensaje—: el extractor tomó "Baños" como nombre de calle y el 3 como
# numeración.
#
# La lista creció con la corrida del 17-08: "Mensual 30, Vitacura" fue la
# dirección REAL de un aviso de RE/MAX ("1.250.000 $ Mensual 30,60 UF": el
# rótulo del precio como calle y la parte entera de la UF como altura), y
# "UF38 00" la de otro de Yapo. Ninguna de esas palabras encabeza una calle
# chilena; todas encabezan un precio, un código o un dato del programa.
# Lo que un portal mete en su campo "dirección" y NO es una dirección,
# medido en la auditoría del 19-08 contra el estado real: "Disponibilidad:
# Agosto 2026", "Linea 7" (¡la línea del metro!), "CORAZÓN DE VITACURA
# DICIEMBRE 2026", "GRAN DEPARTAMENT DISPONIBLE...". Contaminan el
# fingerprint (la identidad del aviso ES su dirección), el link de Google
# Maps y la tabla del dashboard. Un campo de dirección con fecha de
# disponibilidad, mes, o palabras de marketing no es una calle.
_DIRECCION_JSON_INVALIDA = re.compile(
    # Los REQUISITOS del arriendo, que traen número y parecen numeración:
    # "Acreditar renta 3 veces" llegó como la dirección "Acreditar 3,
    # Vitacura" — con puntaje 84, o sea compitiendo en serio, con su link
    # de Maps apuntando a una calle que no existe.
    r"acredita|requisito|liquidaci[oó]n|\baval(?:es)?\b|garant[ií]a|contrato"
    r"|disponib|entrega\s|l[ií]nea\s*\d|\bmetro\b"
    r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto"
    r"|septiembre|octubre|noviembre|diciembre|\b20\d{2}\b"
    r"|amoblad|seguridad|coraz[oó]n|estacionamiento|oportunidad"
    r"|imperdible|exclusiv|espectacular|impecable", re.I)


def _direccion_de_json(direccion: str) -> str:
    """El campo dirección de un payload, con la basura de marketing afuera."""
    d = (direccion or "").strip()
    if not d or _DIRECCION_JSON_INVALIDA.search(d):
        return ""
    return d


_NO_ES_CALLE = re.compile(
    r"^(?:ba[ñn]os?|dormitorios?|piezas?|estacionamientos?|bodegas?"
    r"|pisos?|m2|mts?2?|uf\s*\d*|clp|cod\.?|c[oó]digo|mensual"
    # "Sólo 3, Vitacura" alertó en la corrida del 18-08: el "Sólo 3
    # (disponibles)" del marketing, con el 3 de altura. Y sus parientes.
    r"|s[oó]lo|quedan?|[uú]ltim[oa]s?|desde|hasta"
    r"|arriendo|venta)\b[\s:.,]*[\d\s.,]*$", re.I)

# Una palabra de precio EN MEDIO del nombre lo delata entero: "COMERCIAL EN
# ARRIENDO! UF 75" y "San Sebastián Arriendo: UF 90" fueron direcciones
# reales del diagnóstico — el título del aviso tragado como calle, con la UF
# de altura. Ninguna calle chilena se llama UF, CLP ni Arriendo.
_PALABRA_DE_PRECIO = re.compile(
    r"^(?:uf|clp|clf|\$+|arriendos?|ventas?|cod\.?|c[oó]digo)[:!.,]*$", re.I)


def _direccion_desde(texto: str, comuna: str) -> str:
    """La dirección del aviso, si el texto trae una.

    Devuelve "" antes que devolver algo dudoso: una dirección inventada
    fusiona dos departamentos distintos, que es peor que no deduplicar.
    """
    t = texto or ""
    tokens = [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(t)]

    for i, (token, _, fin) in enumerate(tokens):
        altura = _ES_ALTURA.match(token)
        if not altura:
            continue
        # "30,60 UF" partido por la coma deja un "30" con toda la pinta de
        # altura. Si lo que sigue pegado al número es ",dígito", el token es
        # la parte entera de un decimal — un precio o una medida, nunca una
        # numeración. Así "$ Mensual 30,60 UF" dejó de producir la dirección
        # "Mensual 30" que llegó al teléfono el 17-08.
        if re.match(r",\d", t[fin:fin + 2]):
            continue
        # "134 m²" tiene forma de altura y es una superficie.
        #
        # La ventana tiene que alcanzar para la palabra más larga de la lista
        # ("estacionamientos", 16 letras). Con la ventana de 8 que había, el
        # patrón nunca llegaba a leer "Dormitorios" y el número de dormitorios
        # se colaba como altura de la calle.
        #
        # Ampliarla es seguro porque el patrón está anclado en `^\s*`: la
        # unidad tiene que venir pegada al número. En "Luis Carrera 1200, 3
        # dormitorios" lo que sigue al 1200 es una coma, y no calza.
        if _UNIDAD_TRAS_NUMERO.match(t[fin:fin + 20]):
            continue
        # El número que sigue a "Depto" es la unidad, no la calle.
        if i and tokens[i - 1][0].lower() in _MARCAS_DE_UNIDAD:
            continue

        palabras: list[str] = []
        for anterior, _, _ in reversed(tokens[:i]):
            if anterior.lower() in _MARCAS_DE_UNIDAD:
                break
            if anterior.lower().rstrip(".") in _VIAS_QUE_CIERRAN:
                palabras.insert(0, anterior)
                break
            if not _es_nombre_de_calle(anterior):
                break
            palabras.insert(0, anterior)
            # Se recogen dos palabras de más y se recorta después. La holgura
            # es para que quepa la comuna entera cuando viene pegada: sin
            # ella, "Las Condes Isabel La Católica 4800" llegaba al tope con
            # "Condes Isabel La Católica" y el "Las" nunca se alcanzaba a
            # leer, así que la comuna no se podía reconocer para sacarla.
            if len(palabras) >= _MAX_PALABRAS_CALLE + 2:
                break

        palabras = _quitar_comuna_inicial(palabras)
        palabras = palabras[-_MAX_PALABRAS_CALLE:]
        # Un conector suelto al principio quedó de la frase anterior.
        while palabras and palabras[0].lower() in _CONECTORES:
            palabras.pop(0)
        # Sin al menos una palabra con letras, lo que queda es un número.
        if not palabras:
            continue

        if any(_PALABRA_DE_PRECIO.match(p) for p in palabras):
            continue
        calle = " ".join(palabras + [altura.group(1)])
        if _NO_ES_CALLE.match(calle):
            # Se sigue buscando en vez de rendirse: "Baños: 3" al principio
            # del texto no impide que más adelante venga la dirección real.
            continue
        # "CORAZÓN DE VITACURA DICIEMBRE 2026": el año de disponibilidad
        # tiene toda la pinta de altura y el mes de nombre de calle. La
        # misma lista negra que limpia el campo dirección de los payloads.
        if _DIRECCION_JSON_INVALIDA.search(calle):
            continue
        return f"{calle}, {comuna}" if comuna else calle

    return ""


# ---------------------------------------------------------------------------
# Entrada del módulo
# ---------------------------------------------------------------------------

def _clave_url(url: str) -> str:
    """La URL reducida a lo que identifica al aviso, para poder calzarlas."""
    u = (url or "").split("#")[0].split("?")[0].rstrip("/").lower()
    u = re.sub(r"^https?://", "", u)
    return u.removeprefix("www.")


def _completar_con_tarjetas(avisos: list[Arriendo], soup: BeautifulSoup,
                            base_url: str, fuente: FuenteConfig,
                            valor_uf: float | None) -> None:
    """Rellena los huecos del JSON-LD con la tarjeta visible del MISMO aviso.

    El diagnóstico contra páginas reales mostró el patrón en todos los
    metabuscadores: el JSON-LD trae dormitorios, baños, superficie y hasta la
    dirección — y NO trae el precio, que vive solo en la tarjeta visible
    (nuroa: 100% dormitorios, 88% m², 12% precio). Como la pasada JSON-LD
    gana, la tarjeta se descartaba entera y el aviso quedaba sin canon: sin
    filtro de presupuesto y comprimido bajo el techo de los sin-precio.

    Se calza POR URL —la tarjeta enlaza al mismo aviso que declara el
    JSON-LD— y solo se rellenan huecos: nada de lo que el JSON-LD ya dijo se
    toca. Sin calce, no se toca nada.
    """
    from ..store import _fusionar

    # Primera vía, por URL. Ojo con rendirse temprano: que no haya tarjetas
    # CON link no significa que no haya tarjetas — mitula no pone href por
    # tarjeta, y la segunda vía existe justamente para eso.
    por_url: dict[str, Arriendo] = {}
    for t in _desde_tarjetas(soup, base_url, fuente, valor_uf):
        clave = _clave_url(t.url)
        if clave and clave != _clave_url(base_url):
            por_url.setdefault(clave, t)

    for a in avisos:
        t = por_url.get(_clave_url(a.url))
        if t is None:
            continue
        if a.arriendo_clp is None and a.arriendo_uf is None:
            a.arriendo_clp = t.arriendo_clp
            a.arriendo_uf = t.arriendo_uf
        _fusionar(a, t)

    # Segunda vía, por TÍTULO: mitula no pone href por tarjeta NI url en su
    # JSON-LD (medido contra su nodo real), así que el calce por URL no
    # existe ahí. El título exacto, normalizado y ÚNICO en la página,
    # identifica igual de bien; con dos tarjetas del mismo título no se cree
    # ninguna. Las tarjetas sin link no salen de `_desde_tarjetas` —exige
    # URL— así que acá se leen los candidatos crudos.
    sin_precio = [a for a in avisos
                  if a.arriendo_clp is None and a.arriendo_uf is None]
    if not sin_precio:
        return
    # Con las posiciones INTACTAS (tarjetas vacías incluidas): la tercera
    # vía alinea por índice, y filtrar acá correría todos los índices.
    tarjetas_crudas = [(P.norm(texto), texto) for card in
                       _candidatos(soup, fuente)
                       for texto in [_texto(card) or ""]]
    for a in sin_precio:
        titulo = P.norm(a.title or "")[:80]
        if len(titulo) < 12:
            continue
        calzan = [texto for ntexto, texto in tarjetas_crudas
                  if texto and titulo in ntexto]
        if len(calzan) != 1:
            continue
        t = _armar(calzan[0], "", fuente, base_url, valor_uf)
        a.arriendo_clp = t.arriendo_clp
        a.arriendo_uf = t.arriendo_uf
        _fusionar(a, t)

    # Tercera vía, por ORDEN con anclas de verificación. El caso que las
    # dos anteriores no alcanzan es mitula con el título genérico repetido:
    # media página se llama "Departamento en arriendo en VITACURA", así que
    # el calce por título único se rinde — 40 de 49 avisos sin precio en la
    # corrida del 18-08, con el precio A LA VISTA en cada tarjeta ("90 UF").
    #
    # La hipótesis es que la página es una lista pareja: la tarjeta i-ésima
    # ES el aviso i-ésimo. Y no se cree gratis — se verifica con anclas:
    # cada aviso cuyo título aparece en ALGUNA tarjeta tiene que aparecer
    # exactamente en la SUYA. Una sola ancla fuera de lugar aborta todo,
    # porque significa que el orden no es el que creemos, y un precio del
    # vecino es peor que ningún precio. Con tres anclas confirmadas y el
    # mismo largo en ambas listas, los repetidos heredan su tarjeta.
    sin_precio = [a for a in avisos
                  if a.arriendo_clp is None and a.arriendo_uf is None]
    if not sin_precio or len(tarjetas_crudas) != len(avisos):
        return
    anclas = 0
    for i, a in enumerate(avisos):
        titulo = P.norm(a.title or "")[:80]
        if len(titulo) < 12:
            continue
        if not any(titulo in ntexto for ntexto, _ in tarjetas_crudas):
            continue
        if titulo not in tarjetas_crudas[i][0]:
            return
        anclas += 1
    if anclas < 3:
        return
    for i, a in enumerate(avisos):
        if a.arriendo_clp is not None or a.arriendo_uf is not None:
            continue
        if not tarjetas_crudas[i][1]:
            continue
        t = _armar(tarjetas_crudas[i][1], "", fuente, base_url, valor_uf)
        a.arriendo_clp = t.arriendo_clp
        a.arriendo_uf = t.arriendo_uf
        _fusionar(a, t)


def extraer(html: str, base_url: str, fuente: FuenteConfig,
            valor_uf: float | None = None) -> list[Arriendo]:
    """Extrae los avisos de una página, con las tres pasadas en orden.

    Las pasadas no se suman: se prefiere la primera que dé resultado. Sumarlas
    produciría el mismo aviso dos veces con distinta calidad de datos, y aunque
    la deduplicación lo arreglaría después, la copia peor podría ganar el
    desempate y perderíamos los datos buenos.

    La única mezcla permitida calza por URL: lo que la tarjeta visible sabe
    del MISMO aviso rellena los huecos del JSON-LD (ver
    `_completar_con_tarjetas`). Eso no duplica nada.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")

    for nombre, pasada in (
        ("json-ld", lambda: _desde_jsonld(soup, base_url, fuente, valor_uf)),
        ("estado-embebido",
         lambda: _desde_estado_embebido(html, base_url, fuente, valor_uf)),
        ("tarjetas", lambda: _desde_tarjetas(soup, base_url, fuente, valor_uf)),
    ):
        resultado = pasada()
        if resultado:
            log.debug("[%s] %d avisos vía %s", fuente.id, len(resultado), nombre)
            if nombre == "json-ld":
                _completar_con_tarjetas(resultado, soup, base_url, fuente,
                                        valor_uf)
                _completar_con_serp(resultado, soup, base_url, valor_uf)
                _completar_con_enlace(resultado, soup, base_url, valor_uf)
            return resultado

    return []


def _completar_con_enlace(avisos: list[Arriendo], soup: BeautifulSoup,
                          base_url: str, valor_uf: float | None) -> None:
    """El precio desde el bloque que ENLAZA al aviso.

    El caso medido (goplaceit, 19-08): 30 avisos JSON-LD con URL propia y
    CERO con precio — y el canon pintado en la página, al lado del link,
    en un bloque que no tiene forma de tarjeta para `_candidatos`. El link
    del aviso es un identificador que ninguna heurística de forma necesita:
    se busca el <a> que apunta a ESTE aviso y se sube por sus padres hasta
    el primer bloque corto que traiga un monto. Corto (≤ 600 caracteres) es
    la defensa: la grilla entera o el panel de filtros no caben ahí.
    """
    sin = [a for a in avisos
           if a.arriendo_clp is None and a.arriendo_uf is None
           and a.url and a.url != base_url]
    for a in sin:
        cola = urlparse(a.url).path
        if len(cola) < 8:
            continue
        ancla = soup.find("a", href=lambda h: h and cola in h)
        if ancla is None:
            continue
        nodo = ancla
        for _ in range(4):
            if nodo.parent is None:
                break
            nodo = nodo.parent
            texto = " ".join(nodo.get_text(" ").split())
            if len(texto) > 600:
                break
            montos = P.parse_montos(texto, valor_uf)
            clp, uf = montos.get("arriendo_clp"), montos.get("arriendo_uf")
            if clp or uf:
                a.arriendo_clp, a.arriendo_uf = clp, uf
                if a.gastos_comunes_clp is None and \
                        montos.get("gastos_comunes_clp"):
                    a.gastos_comunes_clp = montos["gastos_comunes_clp"]
                break


def _completar_con_serp(avisos: list[Arriendo], soup: BeautifulSoup,
                        base_url: str, valor_uf: float | None) -> None:
    """El estado de impresiones del buscador de mitula, alineado por posición.

    Medido contra la página real del 19-08: el HTML del servidor NO trae
    ninguna tarjeta con precio —la tarjeta visible se pinta con JavaScript—
    así que las tres vías de `_completar_con_tarjetas` no tienen de dónde
    sacar el canon (40 de 49 avisos sin precio, con el "90 UF" a la vista
    en el navegador del usuario). Lo que el servidor SÍ manda es
    `window.serpSectionImpressionData.listings`: una entrada por aviso con
    `position`, el precio (`CLP` en pesos, `CLF` en UF), dormitorios,
    baños, m² y el `listingId` del link directo (/adform/<id>).

    El calce es por posición y no se cree gratis: dormitorios y baños del
    blob tienen que coincidir con los del JSON-LD en cada par donde ambos
    existen — vienen de la misma base de datos, así que UNA discrepancia
    significa que el orden no es el que creemos y se aborta entero.
    """
    datos = None
    for tag in soup.find_all("script"):
        contenido = tag.string or ""
        if "serpSectionImpressionData" not in contenido or \
                '"listings"' not in contenido:
            continue
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", contenido, re.S)
        if not m:
            continue
        try:
            datos = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        break
    if not isinstance(datos, dict):
        return
    listings = [x for x in (datos.get("listings") or [])
                if isinstance(x, dict)]
    if not listings or len(listings) != len(avisos):
        return
    listings.sort(key=lambda x: x.get("position") or 0)

    anclas = 0
    for a, l in zip(avisos, listings):
        for mio, suyo in ((a.dormitorios, l.get("numberOfBedrooms")),
                          (a.banos, l.get("numberOfBathrooms"))):
            if mio is None or not suyo:
                continue
            if int(mio) != int(suyo):
                return
            anclas += 1
    if anclas < 3:
        return

    for a, l in zip(avisos, listings):
        precio = next((o.get("price") for o in (l.get("operations") or [])
                       if isinstance(o, dict)
                       and o.get("operationType") == "RENT"
                       and isinstance(o.get("price"), dict)), None)
        if precio and a.arriendo_clp is None and a.arriendo_uf is None:
            valor = precio.get("value")
            moneda = str(precio.get("currency") or "").upper()
            if isinstance(valor, (int, float)) and valor > 0:
                if moneda == "CLF" and 5 <= valor <= 500:
                    a.arriendo_uf = float(valor)
                    if valor_uf:
                        a.arriendo_clp = round(valor * valor_uf)
                elif moneda == "CLP" and \
                        P.BANDA_ARRIENDO[0] <= valor <= P.BANDA_ARRIENDO[1]:
                    a.arriendo_clp = float(valor)
        lid = str(l.get("listingId") or "")
        if lid and a.extras.get("sin_link_directo") and \
                re.fullmatch(r"[\w-]{20,80}", lid):
            a.url = urljoin(base_url, f"/adform/{lid}")
            a.extras.pop("sin_link_directo", None)


# Campos que NO se aceptan del texto suelto de una ficha. La página completa
# trae la dirección de la corredora en el pie, el menú del sitio ("Arriendos
# Amueblados") y los avisos del widget de similares: cualquiera de estos
# campos leído de ahí tiene tantas chances de ser de la página como del
# departamento. Los medibles rotulados (dormitorios, m², GC, año, piso) sí
# se aceptan, porque un rótulo pegado a su número es del aviso.
_NO_DEL_TEXTO_DE_FICHA = ("direccion", "comuna", "lat", "lon", "tipo",
                          "amoblado", "mascotas", "disponible_desde",
                          "publicado_el", "corredora")


def candidato_de_texto(html: str, url: str, fuente: FuenteConfig,
                       valor_uf: float | None = None,
                       titulo: str = "", direccion: str = "") -> Arriendo | None:
    """Un candidato armado del TEXTO VISIBLE de una ficha, solo lo rotulado.

    Existe porque el diagnóstico contra fichas reales mostró páginas donde
    las tres pasadas extraen CERO —goplaceit e iCasas no ponen JSON-LD de la
    propiedad en su ficha— mientras el texto visible dice todo: "4
    Habitaciones / 3 Baños", "Superficie 142 m2 totales", "Gastos comunes:
    UF 15,15", "Año de construcción: 1.978", "Orientación: Sur-Oriente".

    Dos reglas lo hacen seguro. De una página completa solo se cree lo
    ROTULADO: los montos pasan por `montos_rotulados` (los números sueltos
    de una ficha son los promedios del sector y los avisos vecinos), y los
    campos de identidad —dirección, comuna, tipo— no se toman de acá. Y si
    el título o la dirección del aviso aparecen en la página, el texto se
    corta DESDE ahí: la sección de la propiedad empieza en su título; lo que
    va antes es el sitio y lo que reconoce el ancla queda marcado
    (`texto_anclado`), para que quien fusiona sepa cuánta fe tenerle.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    texto = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    if len(texto) < 40:
        return None

    anclado = False
    for ancla in (titulo, (direccion or "").split(",")[0]):
        corto = re.sub(r"\s+", " ", ancla or "").strip()[:40]
        if len(corto) < 12:
            continue
        m = re.search(re.escape(corto), texto, re.I)
        if m:
            texto = texto[m.start():]
            anclado = True
            break
    # El tope de largo corta el fondo de la página, que es donde viven los
    # similares y el pie del sitio; la propiedad se describe arriba.
    texto = texto[:20_000]

    a = _armar(texto, url, fuente, "", valor_uf)
    rotulados = P.montos_rotulados(texto, valor_uf)
    a.arriendo_clp = rotulados.get("arriendo_clp")
    a.arriendo_uf = None
    a.gastos_comunes_clp = rotulados.get("gastos_comunes_clp")
    a.garantia_meses = None
    for campo in _NO_DEL_TEXTO_DE_FICHA:
        vacio = "" if isinstance(getattr(a, campo), str) else None
        setattr(a, campo, vacio)
    a.extras["de_texto_de_ficha"] = True
    a.extras["texto_anclado"] = anclado
    return a


def enlaces_de_detalle(html: str, base_url: str, patron: str,
                       tope: int = 20) -> list[str]:
    """Los enlaces a fichas de detalle que hay en un listado.

    En arriendo esto rinde mucho más que en otros radares: los gastos comunes,
    la superficie total y el año casi nunca están en la tarjeta y casi siempre
    están en la ficha. Sin seguirlas, el radar puntúa a ciegas justo en los
    campos que más pesan.
    """
    if not html or not patron:
        return []

    soup = BeautifulSoup(html, "lxml")
    regex = re.compile(patron)
    vistos: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not regex.search(href) or _HREF_IGNORAR.search(href):
            continue
        absoluto = _absoluto(href, base_url)
        if absoluto not in vistos:
            vistos.append(absoluto)
        if len(vistos) >= tope:
            break
    return vistos
