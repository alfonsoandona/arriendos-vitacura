"""Carga y validación del perfil de búsqueda."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parent.parent
PERFIL_DEFAULT = RAIZ / "perfil.yml"
FUENTES_DEFAULT = RAIZ / "fuentes.yml"


def dir_estado(entorno: dict[str, str] | None = None) -> Path:
    """Dónde vive la memoria de lo ya alertado, según ARRIENDO_STATE_DIR.

    Se redirige porque state/ está versionado: un ensayo local que lo deje
    vacío y se commitee le borra al radar el recuerdo de todo lo que ya avisó,
    y la corrida siguiente vuelve a mandar cada aviso de nuevo.
    """
    env = os.environ if entorno is None else entorno
    return Path(env.get("ARRIENDO_STATE_DIR") or RAIZ / "state")


def dir_logs(entorno: dict[str, str] | None = None) -> Path:
    """Dónde escribir la bitácora, según ARRIENDO_LOGS_DIR.

    Hace falta por lo mismo que el estado: la bitácora se versiona, y una
    corrida de prueba escribiría "FALLÓ" en el archivo que se commitea. Ese
    registro falso es peor que no tener ninguno.
    """
    env = os.environ if entorno is None else entorno
    return Path(env.get("ARRIENDO_LOGS_DIR") or RAIZ / "logs")


def dir_alertas(entorno: dict[str, str] | None = None) -> Path:
    """Dónde se escriben las fichas, según ARRIENDO_ALERTAS_DIR."""
    env = os.environ if entorno is None else entorno
    return Path(env.get("ARRIENDO_ALERTAS_DIR") or RAIZ / "alertas")


def dir_docs(entorno: dict[str, str] | None = None) -> Path:
    """Dónde se escriben los dashboards HTML, según ARRIENDO_DOCS_DIR.

    `docs/` y no otro nombre porque es la única carpeta que GitHub Pages
    puede servir directamente sin workflow de deploy.
    """
    env = os.environ if entorno is None else entorno
    return Path(env.get("ARRIENDO_DOCS_DIR") or RAIZ / "docs")


STATE_DIR = dir_estado()
LOGS_DIR = dir_logs()
ALERTAS_DIR = dir_alertas()


class PerfilInvalido(ValueError):
    pass


def cargar_perfil(ruta: str | Path | None = None) -> dict[str, Any]:
    ruta = Path(ruta) if ruta else PERFIL_DEFAULT
    if not ruta.exists():
        raise PerfilInvalido(f"No existe el perfil: {ruta}")

    with open(ruta, encoding="utf-8") as f:
        perfil = yaml.safe_load(f) or {}

    validar_perfil(perfil)
    return perfil


def validar_perfil(perfil: dict[str, Any]) -> None:
    """Falla temprano y con mensaje claro: este archivo lo edita una persona.

    Cada chequeo de acá corresponde a un error que dejaría el radar corriendo
    pero mintiendo, que es peor que uno que lo detiene. Un `max` menor que un
    `min` no rompe nada: descarta todo el inventario en silencio.
    """
    req = perfil.get("requisitos")
    if not req:
        raise PerfilInvalido("El perfil no define 'requisitos'")

    if not req.get("tipo"):
        raise PerfilInvalido("'requisitos.tipo' no puede estar vacío")

    for campo in ("m2_totales", "dormitorios", "arriendo_clp"):
        rango = req.get(campo)
        if rango is None:
            continue
        if not isinstance(rango, dict):
            raise PerfilInvalido(
                f"'requisitos.{campo}' debe ser un rango con min/max")
        lo, hi = rango.get("min"), rango.get("max")
        if lo is not None and hi is not None and lo > hi:
            raise PerfilInvalido(
                f"'requisitos.{campo}': min ({lo}) mayor que max ({hi})")

    tope = (req.get("arriendo_clp") or {}).get("max")
    if tope is not None and tope <= 0:
        raise PerfilInvalido("'requisitos.arriendo_clp.max' debe ser positivo")

    comparar = req.get("comparar", "arriendo")
    if comparar not in ("arriendo", "total"):
        raise PerfilInvalido(
            f"'requisitos.comparar' debe ser 'arriendo' o 'total', no '{comparar}'")

    ancla = perfil.get("ancla", {})
    if ancla.get("lat") is not None:
        lat, lon = ancla["lat"], ancla.get("lon")
        if not (-90 <= lat <= 90) or lon is None or not (-180 <= lon <= 180):
            raise PerfilInvalido(f"Coordenadas del ancla fuera de rango: {lat}, {lon}")

    radios = perfil.get("radio_km", {})
    if radios.get("preferente") and radios.get("anillo"):
        if radios["preferente"] > radios["anillo"]:
            raise PerfilInvalido(
                "radio_km.preferente no puede ser mayor que radio_km.anillo: "
                "la zona caminable tiene que caber dentro del anillo")

    antig = perfil.get("antiguedad", {})
    tramos = [antig.get(k) for k in ("ideal_max", "bueno_max", "aceptable_max")]
    presentes = [t for t in tramos if t is not None]
    if presentes != sorted(presentes):
        raise PerfilInvalido(
            "Los tramos de 'antiguedad' tienen que ir de menor a mayor: "
            "ideal_max <= bueno_max <= aceptable_max")

    if not perfil.get("comunas", {}).get("nucleo"):
        raise PerfilInvalido("'comunas.nucleo' no puede estar vacío")


def comunas_nucleo(perfil: dict) -> list[str]:
    return list((perfil.get("comunas") or {}).get("nucleo") or [])


def comunas_vecinas(perfil: dict) -> list[str]:
    return list((perfil.get("comunas") or {}).get("vecinas") or [])


def valor_uf(entorno: dict[str, str] | None = None) -> float:
    """El valor de la UF a usar para convertir cánones publicados en UF.

    Sale de la variable de entorno VALOR_UF cuando está, y del valor por
    omisión del parser cuando no. No se consulta a ninguna API: una
    dependencia de red más es una forma más de que la corrida falle entera, y
    para decidir si un arriendo está cerca de 1,6 millones un error de 1% en
    la UF no cambia ninguna decisión.
    """
    from .parse import VALOR_UF_DEFECTO

    env = os.environ if entorno is None else entorno
    crudo = env.get("VALOR_UF", "").strip()
    if not crudo:
        return VALOR_UF_DEFECTO
    try:
        v = float(crudo.replace(".", "").replace(",", "."))
    except ValueError:
        return VALOR_UF_DEFECTO
    # Una UF fuera de este rango es un error de tipeo, y usarla convertiría
    # todos los cánones en UF en basura.
    return v if 20_000 <= v <= 100_000 else VALOR_UF_DEFECTO
