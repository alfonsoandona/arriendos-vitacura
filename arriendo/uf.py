"""El valor de la UF, que en este radar es un dato de decisión y no un detalle.

Por qué existe este módulo
--------------------------

Al principio la UF era una constante en `parse.py`. Parecía suficiente: para
saber si un arriendo está cerca de 1,6 millones, un 1% de error da lo mismo.

Dejó de dar lo mismo al mirar un aviso real. **Yapo publica buena parte de su
inventario de Vitacura en UF** —"CLF 46.00", "CLF 33.00"— así que para una de
las fuentes más grandes el precio en pesos no lo publica nadie: lo calcula
este radar. Y ahí la constante decide veredictos:

    UF 39  ×  $40.800 (la constante)  =  $1.591.200   → entra
    UF 39  ×  $41.500 (la UF real)    =  $1.618.500   → se pasa del tope

El mismo departamento, dos respuestas, y la diferencia es un número que
envejece solo. La UF sube todos los meses.

La cascada
----------

En orden, y cada escalón existe por un motivo distinto:

1. **`VALOR_UF` del entorno.** Una decisión explícita de una persona gana
   sobre todo lo demás. Sirve para fijar el valor y para reproducir una
   corrida vieja.
2. **La API pública.** El valor de hoy, que es lo correcto.
3. **La caché de la última corrida.** Cuando la API se cae, un valor de hace
   dos días es muchísimo mejor que uno escrito a mano hace meses: la UF se
   mueve unos pocos pesos al día.
4. **La constante.** Última red, solo para la primera corrida sin internet.

Nada de esto puede levantar una excepción ni colgar la corrida: es un dato
auxiliar, y quedarse sin él nunca puede costar los avisos.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# API pública del Banco Central republicada por mindicador.cl. Se eligió por
# ser la que no pide credenciales: la de la CMF exige registrar una API key, y
# un secret más que configurar es un motivo más para que esto no funcione.
API_UF = "https://mindicador.cl/api/uf"

# Cuánto esperar. Corto a propósito: es un dato auxiliar y no puede comerse el
# presupuesto de tiempo de la corrida.
TIMEOUT = 8

ARCHIVO_CACHE = "uf.json"

# Fuera de esta banda el valor no es una UF: es un error de la API, un typo en
# la variable de entorno, o el HTML de una página de error parseado a la mala.
MINIMO, MAXIMO = 20_000.0, 100_000.0


def _plausible(valor: Any) -> float | None:
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return v if MINIMO <= v <= MAXIMO else None


def valor_uf(state_dir: Path | None = None, entorno: dict | None = None,
             session: Any = None) -> tuple[float, str]:
    """La UF a usar, y de dónde salió.

    Devuelve la fuente además del número porque la bitácora lo muestra: si una
    corrida convirtió con la constante en vez de con la UF del día, eso
    explica por qué un aviso quedó justo al otro lado del tope.
    """
    import os

    env = os.environ if entorno is None else entorno

    if (v := _plausible((env.get("VALOR_UF") or "").replace(".", "").replace(",", "."))):
        return v, "variable VALOR_UF"

    if (v := _del_api(session)) is not None:
        if state_dir:
            _guardar_cache(v, Path(state_dir))
        return v, "API del día"

    if state_dir and (guardado := _de_cache(Path(state_dir))):
        v, cuando = guardado
        dias = (date.today() - cuando).days
        return v, f"caché de hace {dias} día(s) — la API no respondió"

    from .parse import VALOR_UF_DEFECTO

    return VALOR_UF_DEFECTO, "constante del código — SIN API NI CACHÉ"


def _del_api(session: Any = None) -> float | None:
    """La UF de hoy. None ante cualquier problema, sin levantar nunca."""
    try:
        import requests

        s = session or requests
        r = s.get(API_UF, timeout=TIMEOUT,
                  headers={"Accept": "application/json"})
        if r.status_code != 200:
            log.warning("La API de la UF respondió %s", r.status_code)
            return None
        serie = (r.json() or {}).get("serie") or []
        if not serie:
            return None
        return _plausible(serie[0].get("valor"))
    except Exception as e:                                       # noqa: BLE001
        # Cualquier cosa: sin red, JSON cambiado, timeout. Es un dato
        # auxiliar y su ausencia no puede costar la corrida.
        log.info("No se pudo traer la UF del día (%s); se usa el respaldo",
                 type(e).__name__)
        return None


def _guardar_cache(valor: float, state_dir: Path) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / ARCHIVO_CACHE).write_text(
            json.dumps({"valor": valor, "cuando": date.today().isoformat()},
                       ensure_ascii=False),
            encoding="utf-8")
    except OSError as e:
        log.debug("No se pudo guardar la UF en caché: %s", e)


def _de_cache(state_dir: Path) -> tuple[float, date] | None:
    try:
        d = json.loads((state_dir / ARCHIVO_CACHE).read_text(encoding="utf-8"))
        valor = _plausible(d.get("valor"))
        cuando = datetime.fromisoformat(d["cuando"]).date()
    except Exception:                                            # noqa: BLE001
        return None
    return (valor, cuando) if valor else None
