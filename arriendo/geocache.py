"""Memoria de direcciones ya resueltas, portada del radar de remates.

Allá existió por un número (126 propiedades con dirección, 33 con
coordenadas) y acá el número era peor: CERO candidatos con coordenadas,
porque los portales de arriendo no las publican y nadie las buscaba. Sin
coordenadas no hay mapa en el dashboard y el rubro Ubicación puntúa a
ciegas ("Vitacura, sin ubicar en el mapa") para el 100% del tablero.

El geocoding es lo más caro que hace el radar —Nominatim exige un request
por segundo, así que hay un tope por corrida— y la caché es lo que hace
que el cupo rinda: guarda tanto los aciertos como los FRACASOS. Guardar
los fracasos es la mitad del ahorro: sin eso, las direcciones imposibles
("Vitacura 4", "Sólo 3") se llevarían el cupo de cada corrida y las
nuevas nunca llegarían a pedirse.

La caché vive en `state/`, que se commitea con el resto del estado, así
que sobrevive entre corridas de GitHub Actions —que arrancan con un
runner limpio— y una dirección se paga UNA vez en la vida del radar.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ARCHIVO = "geocode.json"

# Una dirección que no resolvió hoy casi nunca resuelve mañana, pero
# OpenStreetMap se edita: un mes después puede existir.
REINTENTO_DIAS = 30

# Cuánto se guarda una dirección sin volver a verla. Medio año alcanza para
# cualquier ciclo de arriendo y el archivo no crece sin fin dentro del repo.
OLVIDO_DIAS = 180


def clave(consulta: str) -> str:
    """Normaliza la consulta: dos formas de escribirla, una sola entrada."""
    t = (consulta or "").lower().strip()
    t = t.replace(".", " ")
    t = re.sub(r"[\s,]+", " ", t)
    return t.strip(" ,")


class Cache:
    """Direcciones consultadas antes, con su resultado — éxitos y fracasos."""

    def __init__(self, directorio: str | Path):
        self.ruta = Path(directorio) / ARCHIVO
        self.datos: dict[str, dict[str, Any]] = self._leer()
        self.sucia = False

    def _leer(self) -> dict[str, dict[str, Any]]:
        try:
            d = json.loads(self.ruta.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (json.JSONDecodeError, OSError):
            # Una caché ilegible cuesta una corrida de geocoding, no la
            # corrida entera: se parte de cero y se vuelve a llenar.
            return {}

    def coords(self, consulta: str) -> tuple[float, float] | None:
        """Las coordenadas ya conocidas, o None si no las hay."""
        e = self.datos.get(clave(consulta))
        if e and e.get("lat") is not None:
            self._usada(e)
            return float(e["lat"]), float(e["lon"])
        return None

    def hay_que_preguntar(self, consulta: str) -> bool:
        """Si vale la pena gastar un request en esta dirección."""
        e = self.datos.get(clave(consulta))
        if e is None:
            return True
        if e.get("lat") is not None:
            return False
        fallo = e.get("fallo")
        if not fallo:
            return True
        try:
            return (date.today() - date.fromisoformat(fallo)
                    >= timedelta(days=REINTENTO_DIAS))
        except ValueError:
            return True

    def anotar(self, consulta: str,
               coords: tuple[float, float] | None) -> None:
        k = clave(consulta)
        if not k:
            return
        hoy = date.today().isoformat()
        if coords:
            self.datos[k] = {"lat": coords[0], "lon": coords[1], "visto": hoy}
        else:
            previo = self.datos.get(k, {})
            self.datos[k] = {
                "fallo": hoy,
                "intentos": int(previo.get("intentos", 0)) + 1,
                "visto": hoy,
            }
        self.sucia = True

    def _usada(self, entrada: dict) -> None:
        hoy = date.today().isoformat()
        if entrada.get("visto") != hoy:
            entrada["visto"] = hoy
            self.sucia = True

    def guardar(self) -> None:
        if not self.sucia:
            return
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ruta.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.datos, ensure_ascii=False, indent=2,
                           sort_keys=True),
                encoding="utf-8")
            tmp.replace(self.ruta)   # atómico: nunca un JSON a medio escribir
            self.sucia = False
        except OSError as e:
            log.warning("No se pudo guardar la caché de direcciones: %s", e)

    def purgar(self, dias: int = OLVIDO_DIAS) -> int:
        """Olvida direcciones que no se han vuelto a ver."""
        corte = date.today() - timedelta(days=dias)
        fuera = []
        for k, e in self.datos.items():
            try:
                if date.fromisoformat(e.get("visto", "")) < corte:
                    fuera.append(k)
            except ValueError:
                continue
        for k in fuera:
            del self.datos[k]
        if fuera:
            self.sucia = True
        return len(fuera)

    def __len__(self) -> int:
        return len(self.datos)
