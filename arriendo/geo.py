"""Distancia al ancla y geocodificación de direcciones."""

from __future__ import annotations

import math
import re
import time
from typing import Any

RADIO_TIERRA_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta entre dos puntos, en km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Geocodificación
#
# Requiere red. En el entorno donde se escribió este código el acceso a
# Nominatim está bloqueado, así que todo el resto del sistema funciona sin
# coordenadas: la comuna es la red de seguridad y el radio solo se aplica
# cuando de verdad se pudo ubicar la propiedad.
# ---------------------------------------------------------------------------

# Palabras que anuncian que lo que sigue ES el nombre de la calle. Si la
# comuna viene justo después de una de ellas, la comuna ES la calle.
_TIPO_DE_VIA = ("av", "avda", "avenida", "calle", "pasaje", "psje", "camino",
                "rotonda", "costanera", "alameda")


def limpiar_direccion(direccion: str, comuna: str) -> str:
    """Saca la comuna cuando viene metida DENTRO del nombre de la calle.

    Varios portales publican así:

        "Avenida Santa María Vitacura 9500"

    La comuna queda entre la calle y la numeración, y el resultado no existe
    para ningún geocodificador.

    La salvaguarda es la que evita el desastre obvio: hay calles que se llaman
    como su comuna. "Avenida Vitacura 7777" tiene que quedar intacta, y queda,
    porque ahí la comuna viene pegada a un "Avenida".
    """
    from .parse import norm

    if not direccion or not comuna:
        return (direccion or "").strip()

    objetivo = norm(comuna).split()
    palabras = direccion.split()
    n = len(objetivo)

    salida: list[str] = []
    i = 0
    while i < len(palabras):
        calza = [norm(p) for p in palabras[i:i + n]] == objetivo
        tras_via = bool(salida) and norm(salida[-1]).strip(".") in _TIPO_DE_VIA
        if calza and not tras_via:
            i += n
            continue
        salida.append(palabras[i])
        i += 1

    limpia = " ".join(salida).strip(" ,")
    # Si al sacarla no queda calle, la comuna ERA la calle.
    return limpia if len(salida) >= 2 else direccion.strip()


# La marca del número es para leerla, no para buscarla: Nominatim entiende
# "Blanes 6204" y no "Blanes Nº6204".
#
# El "#" va fuera del \b a propósito: no es letra, así que entre el espacio y
# el "#" no hay frontera de palabra.
_MARCA_DE_NUMERO = re.compile(
    r"(?:\b(?:n[°ºo]\.?|nro\.?|num(?:ero)?\.?)|#)\s*(?=\d)", re.I)
_MARCA_HUERFANA = re.compile(
    r"\s*(?:\b(?:n[°ºo]\.?|nro\.?|num(?:ero)?\.?)|#)\s*$", re.I)
# La altura con separador de miles es una sola: "2.136" es el 2136, no "2".
_NUMERO_FINAL = re.compile(r"[\s,]*\d{1,3}(?:[.,]\d{3})+\s*$|[\s,]+\d{1,6}\s*$")

# El tipo de vía al principio, que el mapa a veces no quiere:
#
#     Calle Blanes 6204, Vitacura, Chile   ✗
#     Blanes 6204, Vitacura, Chile         ✓
#
# OSM guarda esa vía sin el prefijo y el prefijo hace fallar el calce entero.
_VIA_INICIAL = re.compile(rf"^(?:{'|'.join(_TIPO_DE_VIA)})\.?\s+", re.I)

# El número del departamento no ubica nada y rompe el calce: "Alonso de
# Córdova 4200 depto 802" no existe para el mapa, "Alonso de Córdova 4200" sí.
# Es específico de arriendos, donde casi todos los avisos lo traen.
_UNIDAD = re.compile(
    r"[,\s]*\b(?:depto\.?|dpto\.?|departamento|of\.?|oficina|casa)\s*"
    r"n?[°º]?\s*[\dA-Z]{1,5}\b", re.I)


def consultas_geocode(direccion: str, comuna: str) -> list[str]:
    """Las formas de preguntar por una dirección, de más precisa a menos.

    Hay tres maneras de aflojar la pregunta y las tres hicieron falta: soltar
    el número del departamento, soltar el tipo de vía —"Calle Blanes" no
    existe para el mapa, "Blanes" sí— y soltar la altura, porque Nominatim
    falla seguido en la numeración exacta y sí conoce la calle.

    Para decidir si algo está dentro de un radio de 1,2 km la cuadra alcanza:
    el error de quedarse sin coordenadas es mucho mayor que el de tenerlas con
    media cuadra de holgura.
    """
    limpia = limpiar_direccion(direccion, comuna)
    limpia = _UNIDAD.sub("", limpia).strip(" ,")
    limpia = _MARCA_DE_NUMERO.sub("", limpia).strip(" ,")
    # La altura sin separador de miles: "#2.136" es el 2136 de esa calle, y
    # con el punto adentro el geocodificador lee otra cosa.
    limpia = re.sub(r"\b(\d{1,3})[.,](\d{3})\b", r"\1\2", limpia)
    if not limpia:
        return []

    def sin_altura(d: str) -> str:
        return _MARCA_HUERFANA.sub("", _NUMERO_FINAL.sub("", d)).strip(" ,")

    sin_via = _VIA_INICIAL.sub("", limpia).strip(" ,")

    # Con altura antes que sin altura: la altura ubica la cuadra, y el tipo de
    # vía no ubica nada.
    candidatas = [limpia, sin_via, sin_altura(limpia), sin_altura(sin_via)]

    cola = f", {comuna}, Chile" if comuna else ", Chile"
    formas: list[str] = []
    for c in candidatas:
        # Una calle sin nada que la nombre no se pregunta. "Av. 12" se queda
        # en "Av." al soltarle la altura, y "Av., Vitacura, Chile" devuelve el
        # centro de la comuna: entraría como si fuera el departamento, con
        # coordenadas de verdad y todo, que es el peor de los errores.
        nombre = _VIA_INICIAL.sub("", c).strip(" ,.")
        if len(nombre) < 3 or not any(ch.isalpha() for ch in nombre):
            continue
        if (forma := c + cola) not in formas:
            formas.append(forma)
    return formas


NOMINATIM = "https://nominatim.openstreetmap.org/search"
_UA = "radar-arriendos/1.0 (uso personal, busqueda de arriendo)"

# Nominatim exige máximo 1 request por segundo.
_ULTIMA_LLAMADA = 0.0


def geocode(direccion: str, session: Any = None,
            timeout: int = 20) -> tuple[float, float] | None:
    """Resuelve una dirección chilena a (lat, lon). None si no se puede."""
    global _ULTIMA_LLAMADA

    if not direccion or not direccion.strip():
        return None

    import requests

    espera = 1.0 - (time.time() - _ULTIMA_LLAMADA)
    if espera > 0:
        time.sleep(espera)
    _ULTIMA_LLAMADA = time.time()

    s = session or requests
    try:
        r = s.get(
            NOMINATIM,
            params={
                "q": direccion,
                "format": "json",
                "limit": 1,
                "countrycodes": "cl",
            },
            headers={"User-Agent": _UA},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    if not data:
        return None
    try:
        return float(data[0]["lat"]), float(data[0]["lon"])
    except (KeyError, ValueError, IndexError, TypeError):
        return None
