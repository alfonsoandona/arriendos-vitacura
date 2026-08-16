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
    r"|pol[ií]tica de privacidad|valor uf hoy)\b",
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
    r"|/(?:venta|comprar)(/|$)",
    re.I,
)

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

        direccion, comuna = "", ""
        addr = n.get("address")
        if isinstance(addr, dict):
            partes = [str(addr.get(k, "")) for k in
                      ("streetAddress", "addressLocality", "addressRegion")]
            direccion = ", ".join(p for p in partes if p)
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
    "url": ("url", "link", "permalink", "href", "detailUrl", "urlDetalle"),
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
                   "mtsTotales", "m2Totales"),
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


def _parece_aviso(d: Any) -> bool:
    """¿Este dict del payload es un aviso y no un nodo cualquiera del árbol?

    Se exige un precio Y algo que lo ubique o lo mida. Solo el precio no
    alcanza: los payloads traen tablas de tarifas, rangos de filtro y
    configuración de la moneda del sitio, todos con un campo `price`.
    """
    if not isinstance(d, dict):
        return False
    if _busca(d, _LLAVES["precio"]) is None:
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
        direccion = _texto_de(_busca(d, _LLAVES["direccion"]))
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


def _titulo_desde(texto: str) -> str:
    for trozo in _CORTE_TITULO.split(texto or ""):
        limpio = trozo.strip()
        if len(limpio) >= 12:
            return limpio[:150]
    return (texto or "").strip()[:150]


def _armar(texto: str, url: str, fuente: FuenteConfig, base_url: str = "",
           valor_uf: float | None = None) -> Arriendo:
    """Convierte texto libre en un `Arriendo`, aplicando todo el parser.

    Es el único lugar donde se decide qué campo sale de qué función, así que
    las tres pasadas producen objetos comparables entre sí. Cada pasada puede
    después pisar lo que sepa mejor.
    """
    a = Arriendo(
        source=fuente.id,
        url=url or base_url,
        title=_titulo_desde(texto),
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

    a.ano_construccion, a.antiguedad_anos = P.parse_antiguedad(texto)
    if (techo := P.techo_antiguedad(texto)) is not None:
        a.extras["antiguedad_techo"] = techo

    if (unidad := P.parse_unidad(texto)):
        a.extras["unidad"] = unidad
    if P.es_particular(texto):
        a.extras["particular"] = True

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
_UNIDAD_TRAS_NUMERO = re.compile(
    r"^\s*(?:m2|m²|mt2|mts2|mts|metros|uf|a[nñ]os?|d\b|b\b|%)", re.I)

_TOKEN = re.compile(r"[^\s,;·•|]+")
_ES_ALTURA = re.compile(r"^(?:n[°ºo]\.?|#)?(\d{1,5})$", re.I)

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
        # "134 m²" tiene forma de altura y es una superficie.
        if _UNIDAD_TRAS_NUMERO.match(t[fin:fin + 8]):
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

        calle = " ".join(palabras + [altura.group(1)])
        return f"{calle}, {comuna}" if comuna else calle

    return ""


# ---------------------------------------------------------------------------
# Entrada del módulo
# ---------------------------------------------------------------------------

def extraer(html: str, base_url: str, fuente: FuenteConfig,
            valor_uf: float | None = None) -> list[Arriendo]:
    """Extrae los avisos de una página, con las tres pasadas en orden.

    Las pasadas no se suman: se prefiere la primera que dé resultado. Sumarlas
    produciría el mismo aviso dos veces con distinta calidad de datos, y aunque
    la deduplicación lo arreglaría después, la copia peor podría ganar el
    desempate y perderíamos los datos buenos.
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
            return resultado

    return []


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
