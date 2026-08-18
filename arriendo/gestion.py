"""Tu gestión de los departamentos: lo que viste, llamaste o descartaste.

La idea viene del radar de remates (`manual.py` + `datos.yml`), adaptada al
problema real de un flujo de arriendos: cada día llegan avisos nuevos, hay
tiempo para mirar algunos y otros no, y el radar no tenía cómo saber cuáles
ya no valía la pena volver a mostrar. Un aviso que descartaste por teléfono
seguía ocupando su fila del tablero, compitiendo por tu atención con los que
todavía no mirabas.

Este módulo lee `gestion.yml` —un archivo en la raíz, editable desde el
navegador del teléfono igual que `perfil.yml`— con una entrada por
departamento, identificado por su código (el `#ABC12` que llega en cada
alerta y aparece en el tablero):

    departamentos:
      - codigo: ABC12
        estado: descartado          # descartado | visto | contactado | visita
        nota: "da al poniente, muy oscuro"
      - codigo: XYZ89
        estado: visita
        gastos_comunes_clp: 250000
        ano_construccion: 2015
        piso: 8

Dos cosas salen de ahí:

1. El ESTADO. `descartado` lo saca de los candidatos y apaga sus alertas
   para siempre —aunque baje de precio: ya dijiste que no—. Los demás
   estados (`visto`, `contactado`, `visita`) arman tu lista corta al tope
   del tablero y aparecen en la ficha.

2. Los DATOS que averiguaste. Lo que te dijeron por teléfono le GANA al
   aviso —los avisos mienten o quedan viejos; tu llamada de ayer no— y
   entra al puntaje como cualquier otro dato, marcado como tuyo. Es la
   misma economía del radar de remates: medio minuto de trabajo tuyo,
   convertido en memoria permanente.

El código identifica al aviso deduplicado. Si el aviso desaparece del
mercado, su entrada acá queda simplemente sin efecto — no hay que limpiarla.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from . import scoring as S
from .config import RAIZ
from .models import Arriendo

log = logging.getLogger(__name__)

RUTA_GESTION = RAIZ / "gestion.yml"

ESTADOS_VALIDOS = ("descartado", "visto", "contactado", "visita")

# Los campos que se pueden completar a mano, con su validación de rango.
# La dirección queda fuera a propósito: es la llave de deduplicación, y
# cambiarla desde acá le cambiaría el código al aviso — la entrada se
# quedaría apuntando a un departamento que ya no existe.
_CAMPOS = {
    "gastos_comunes_clp": (10_000, 2_000_000),
    "ano_construccion": (1900, 2100),
    "antiguedad_anos": (0, 120),
    "piso": (1, 60),
    "dormitorios": (1, 15),
    "banos": (1, 15),
    "estacionamientos": (0, 10),
    "m2_totales": (20, 2_000),
    "m2_utiles": (20, 2_000),
}


class GestionInvalida(ValueError):
    """Un gestion.yml mal escrito. Se dice QUÉ entrada y QUÉ campo."""


def cargar(ruta: str | Path | None = None) -> dict[str, dict]:
    """Lee gestion.yml y devuelve {codigo: entrada}, validado.

    Falla con el código y el campo en el mensaje: este archivo se edita a
    mano desde un teléfono, y un error que solo se nota al correr deja la
    gestión muda sin decir por qué.
    """
    ruta = Path(ruta) if ruta else RUTA_GESTION
    if not ruta.exists():
        return {}

    with open(ruta, encoding="utf-8") as f:
        datos = yaml.safe_load(f) or {}

    crudas = datos.get("departamentos") or []
    if not isinstance(crudas, list):
        raise GestionInvalida("'departamentos' tiene que ser una lista")

    salida: dict[str, dict] = {}
    for i, d in enumerate(crudas):
        if not isinstance(d, dict):
            raise GestionInvalida(f"La entrada #{i + 1} no es un bloque de campos")
        codigo = str(d.get("codigo") or "").strip().lstrip("#").upper()
        if not codigo:
            raise GestionInvalida(f"La entrada #{i + 1} no tiene 'codigo'")
        if codigo in salida:
            raise GestionInvalida(f"Código repetido: '{codigo}'")

        estado = str(d.get("estado") or "").strip().lower()
        if estado and estado not in ESTADOS_VALIDOS:
            raise GestionInvalida(
                f"[{codigo}] estado '{estado}' no existe. "
                f"Válidos: {', '.join(ESTADOS_VALIDOS)}")

        entrada: dict = {"estado": estado, "nota": str(d.get("nota") or "").strip()}
        for campo, (lo, hi) in _CAMPOS.items():
            if campo not in d or d[campo] is None:
                continue
            try:
                valor = float(d[campo])
            except (TypeError, ValueError):
                raise GestionInvalida(
                    f"[{codigo}] {campo} tiene que ser un número, "
                    f"no {d[campo]!r}") from None
            if not lo <= valor <= hi:
                raise GestionInvalida(
                    f"[{codigo}] {campo}={valor:g} fuera de rango "
                    f"({lo}–{hi})")
            entrada[campo] = valor

        desconocidos = (set(d) - set(_CAMPOS)
                        - {"codigo", "estado", "nota"})
        if desconocidos:
            raise GestionInvalida(
                f"[{codigo}] campos que no existen: "
                f"{', '.join(sorted(desconocidos))}")

        salida[codigo] = entrada
    return salida


def aplicar(avisos: list[Arriendo], gestion: dict[str, dict],
            perfil: dict) -> int:
    """Aplica tu gestión sobre los avisos de la corrida. Devuelve cuántos.

    Los datos tuyos PISAN los del aviso y el puntaje se recalcula. El estado
    `descartado` marca el aviso como descartado —sale de los candidatos y
    `debe_alertar` lo ignora para siempre—; los demás estados solo viajan en
    extras, para el tablero y la ficha.
    """
    if not gestion:
        return 0

    aplicados = 0
    for a in avisos:
        entrada = gestion.get(a.codigo)
        if entrada is None:
            continue
        aplicados += 1

        tuyos: list[str] = []
        for campo in _CAMPOS:
            if campo not in entrada:
                continue
            valor = entrada[campo]
            if campo in ("gastos_comunes_clp",):
                valor = float(valor)
            elif campo in ("m2_totales", "m2_utiles"):
                valor = float(valor)
            else:
                valor = int(valor)
            if getattr(a, campo) != valor:
                setattr(a, campo, valor)
                tuyos.append(campo)
        # El año y la antigüedad se derivan mutuamente, como en el parser.
        # SIEMPRE que diste el año: tu dato le gana también a la antigüedad
        # vieja que el aviso haya traído.
        if "ano_construccion" in tuyos:
            from .parse import hoy
            a.antiguedad_anos = hoy().year - int(a.ano_construccion)
        if tuyos:
            a.extras["datos_tuyos"] = sorted(
                set(a.extras.get("datos_tuyos", [])) | set(tuyos))
            S.evaluar(a, perfil)

        estado, nota = entrada.get("estado", ""), entrada.get("nota", "")
        if estado:
            a.extras["gestion"] = {"estado": estado, "nota": nota}
        if estado == "descartado":
            a.descartado = True
            a.clase_descarte = "gestion"
            a.motivo_descarte = ("lo descartaste tú"
                                 + (f": {nota}" if nota else ""))
        elif nota and not estado:
            a.extras["gestion"] = {"estado": "", "nota": nota}

    if aplicados:
        log.info("Gestión aplicada a %d avisos (gestion.yml)", aplicados)
    return aplicados
