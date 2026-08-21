"""Libreta de edificios: lo que un edificio enseña UNA vez, sirve siempre.

El año de construcción es el criterio SÍ O SÍ del perfil —nada de más de 30
años— y a la vez el dato más escaso de todos: en la corrida del 21-08 lo
traía el 6% de los candidatos. El resto no se puede ni aceptar ni descartar,
que es la peor posición posible.

Pero el año no es del AVISO: es del EDIFICIO. Dos departamentos en Espoz
4200 se construyeron el mismo año, los publique quien los publique y los
publique cuando los publique. Así que cuando UN aviso trae el año, lo que
en realidad enseñó es el año de una dirección — y esa lección vale para
todos los avisos de esa dirección, en esta corrida y en todas las que
vienen.

Es la idea del usuario ("un poblado inicial de rol por dirección y sacamos
mucha info de todo Vitacura") por el camino gratis: en vez de comprar el
rol en el SII, el radar se lo va enseñando a sí mismo con lo que los
portales ya publican. Cada corrida deja la libreta más gorda.

DOS RESGUARDOS, y los dos importan:

1. Solo direcciones CON ALTURA. "Candelaria Goyenechea, Lo Castillo" es una
   calle entera con edificios de distintas décadas; "Candelaria Goyenechea
   4400" es un edificio. Sin altura no se anota ni se consulta. Es la misma
   regla que usa la huella para no fusionar departamentos distintos.

2. Un desacuerdo BORRA la entrada en vez de elegir. Si dos avisos de la
   misma dirección declaran años distintos, uno de los dos está mal y no hay
   forma de saber cuál — y un año equivocado acá no es un dato feo, es un
   descarte falso de un departamento que sí servía, o una alerta de uno que
   no. Ante la duda, la libreta se calla.

La libreta vive en `state/`, que se commitea con el resto del estado, así
que sobrevive a los runners limpios de GitHub Actions.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from .models import NUNCA_EN_UNA_CALLE, Arriendo, clave_direccion

log = logging.getLogger(__name__)

ARCHIVO = "edificios.json"

# Antes de esto no hay departamentos en Vitacura, y después de hoy tampoco.
# Un año fuera de la banda es un error de lectura, no un edificio.
BANDA_ANO = (1900, date.today().year + 3)


def _llave_valida(clave: str) -> bool:
    """¿Esta llave es la que el extractor de hoy produciría?"""
    partes = (clave or "").split()
    if not any(p.isdigit() and len(p) >= 3 for p in partes):
        return False
    return not any(p in NUNCA_EN_UNA_CALLE for p in partes)


def _clave_de_edificio(a: Arriendo) -> str:
    """La dirección reducida a edificio, o "" si no identifica uno.

    Exige altura: una calle sin número no es un edificio, es una calle.
    """
    clave = clave_direccion(a.direccion, a.comuna)
    if not clave:
        return ""
    return clave if any(p.isdigit() and len(p) >= 3
                        for p in clave.split()) else ""


class Libreta:
    """Qué año se construyó cada edificio que el radar ya conoce."""

    def __init__(self, directorio: str | Path):
        self.ruta = Path(directorio) / ARCHIVO
        # Antes de leer: `_leer` puede ensuciarla al olvidar entradas viejas.
        self.sucia = False
        self.datos: dict[str, dict[str, Any]] = self._leer()

    def _leer(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self.ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(datos, dict):
            return {}
        # La libreta se limpia sola al abrirla. Lo que aprendió con un
        # extractor viejo puede tener llaves que hoy no serían direcciones
        # —"id 44348 las condes", "312 metropolitana juan xxiii 6859 301"—:
        # esas entradas ya son inalcanzables, porque nadie va a volver a
        # producir esa llave, y dejarlas ahí solo engorda el archivo y
        # confunde al que lo abra. Se van con la misma regla que las rechaza.
        vivas = {k: v for k, v in datos.items() if _llave_valida(k)}
        if len(vivas) != len(datos):
            log.info("Libreta: %d entradas de un extractor viejo se olvidan",
                     len(datos) - len(vivas))
            self.sucia = True
        return vivas

    def guardar(self) -> None:
        if not self.sucia:
            return
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(self.datos, f, ensure_ascii=False, indent=1,
                      sort_keys=True)
        self.sucia = False

    # -- enseñar --------------------------------------------------------

    def anotar(self, a: Arriendo) -> None:
        """Aprende el año de este aviso, si trae uno y una dirección."""
        clave = _clave_de_edificio(a)
        ano = a.ano_construccion
        if not clave or not ano or not BANDA_ANO[0] <= ano <= BANDA_ANO[1]:
            return

        previo = self.datos.get(clave)
        if previo is None:
            self.datos[clave] = {"ano": ano, "de": a.source,
                                 "url": a.url, "anotado": date.today().isoformat()}
            self.sucia = True
            log.debug("Libreta: %s se construyó en %d (%s)", clave, ano, a.source)
            return

        if previo.get("ano") == ano or previo.get("ano") is None:
            return

        # Dos avisos de la misma dirección con años distintos: uno miente y
        # no hay forma de saber cuál. La entrada se anula —no se elige— y
        # queda anulada para siempre: quien la escribió una vez la volvería
        # a escribir igual.
        log.info("Libreta: %s en disputa (%s dice %d, %s decía %d); se anula",
                 clave, a.source, ano, previo.get("de"), previo.get("ano"))
        self.datos[clave] = {"ano": None, "disputa": [previo.get("ano"), ano],
                             "anotado": date.today().isoformat()}
        self.sucia = True

    # -- responder ------------------------------------------------------

    def ano_de(self, a: Arriendo) -> int | None:
        """El año que la libreta sabe de la dirección de este aviso."""
        clave = _clave_de_edificio(a)
        return self.datos.get(clave, {}).get("ano") if clave else None


def aplicar(avisos: list[Arriendo], libreta: Libreta,
            hoy: int | None = None) -> int:
    """Enseña primero, responde después. Devuelve cuántos ganaron el año.

    El orden importa y es de una sola pasada: si respondiera antes de
    aprender, un aviso que trae el año no le enseñaría nada a su vecino de
    la misma corrida — y los duplicados de un mismo edificio suelen llegar
    juntos, desde portales distintos, uno con año y otro sin.
    """
    for a in avisos:
        libreta.anotar(a)

    hoy = hoy or date.today().year
    ganaron = 0
    for a in avisos:
        if a.ano_construccion is not None:
            continue
        ano = libreta.ano_de(a)
        if ano is None:
            continue
        a.ano_construccion = ano
        if a.antiguedad_anos is None:
            a.antiguedad_anos = max(0, hoy - ano)
        # Queda dicho de dónde salió: el usuario tiene que poder distinguir
        # "el aviso publica 2010" de "el edificio de al lado publicó 2010".
        a.extras["ano_de_libreta"] = True
        ganaron += 1
    return ganaron
