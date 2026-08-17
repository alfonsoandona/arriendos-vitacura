"""Diagnóstico de cobertura: qué publica cada página vs qué extrae el radar.

La pregunta que responde es la del usuario mirando su teléfono: "siento que
faltan datos". Para responderla no basta mirar lo que el radar guardó —eso
solo muestra lo que YA extrae—: hay que bajar la página real y poner lado a
lado lo que la página publica con lo que el extractor le sacó. La diferencia
es la lista de tareas.

Corre en el runner de Actions, que es donde hay red hacia los portales (el
resto del desarrollo pasa por un proxy que los bloquea). Es una lupa, no una
corrida: no toca el estado, no manda avisos, no escribe bitácora. Todo lo
que produce va a stdout —pensado para leerse desde el log del workflow— y
las páginas crudas quedan en diagnostico/ para el artifact.

    python -m arriendo.diagnostico              # todas las fuentes activas
    python -m arriendo.diagnostico --fuente goplaceit
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from . import parse as P
from .sources.base import Fetcher
from .sources.generic import extraer
from .sources.registry import _bajar, cargar_fuentes, fuentes_activas

# Los campos con los que se decide, en el orden del perfil. La cobertura se
# mide sobre estos: un aviso sin foto da lo mismo, uno sin precio no.
CAMPOS = ["arriendo_clp", "gastos_comunes_clp", "m2_totales", "dormitorios",
          "banos", "antiguedad_anos", "piso", "estacionamientos",
          "direccion"]

# Lo que parece un dato en el texto visible de una ficha. Sirve para listar
# lo que la página MUESTRA: si una línea de estas existe y el campo salió
# vacío, el hueco es del extractor, no del portal.
_LINEA_CON_DATO = re.compile(
    r"dormitorio|habitaci|ba[ñn]o|superficie|m2|m²|[uú]til|total|terraza"
    r"|gasto|com[uú]n|estacionamiento|bodega|piso\b|orientaci|a[ñn]o"
    r"|antig[uü]edad|amoblado|mascota|garant[ií]a|precio|arriendo|\buf\b"
    r"|construc|expensas", re.I)

# Llaves interesantes dentro de un JSON embebido (NEXT_DATA, NUXT, etc.).
_LLAVE_UTIL = re.compile(
    r"price|precio|valor|rent|dorm|bed|bath|ban[oi]|habitacion|superficie"
    r"|surface|area|m2|gasto|common|expens|year|ano|antiguedad|floor|piso"
    r"|parking|estacion|bodega|orientaci|address|direccion|comuna", re.I)

_DIR_SALIDA = Path("diagnostico")


def _texto_visible(html: str) -> list[str]:
    """Las líneas del texto visible de la página que parecen datos."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lineas = []
    for cruda in soup.get_text("\n").splitlines():
        linea = re.sub(r"\s+", " ", cruda).strip()
        if 3 <= len(linea) <= 160 and _LINEA_CON_DATO.search(linea):
            lineas.append(linea)
    # Únicas conservando el orden: los sitios repiten el bloque de specs.
    vistas: set[str] = set()
    salida = []
    for l in lineas:
        if l.lower() not in vistas:
            vistas.add(l.lower())
            salida.append(l)
    return salida


def _rutas_de_json(nodo, ruta="", salida=None, tope=60):
    """Las rutas llave=valor de un JSON embebido que huelen a dato útil."""
    if salida is None:
        salida = []
    if len(salida) >= tope:
        return salida
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            _rutas_de_json(v, f"{ruta}.{k}" if ruta else str(k), salida, tope)
    elif isinstance(nodo, list):
        # Con el primer elemento alcanza: los demás son más de lo mismo.
        if nodo:
            _rutas_de_json(nodo[0], ruta + "[]", salida, tope)
    else:
        llave = ruta.rsplit(".", 1)[-1]
        if _LLAVE_UTIL.search(llave) and nodo not in (None, "", [], {}):
            valor = str(nodo)
            salida.append(f"{ruta} = {valor[:80]}")
    return salida


def _blobs_embebidos(html: str) -> list[str]:
    """Qué JSON estructurado trae la página, y qué hay adentro."""
    hallazgos: list[str] = []
    soup = BeautifulSoup(html, "lxml")

    nodos_ld = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        nodos_ld.append(data)
    if nodos_ld:
        tipos = Counter()

        def _tipos(n):
            if isinstance(n, list):
                for x in n:
                    _tipos(x)
            elif isinstance(n, dict):
                t = n.get("@type")
                if t:
                    tipos[str(t)] += 1
                for v in n.values():
                    _tipos(v)

        _tipos(nodos_ld)
        hallazgos.append(f"ld+json: {dict(tipos)}")
        for ruta in _rutas_de_json(nodos_ld, "ld", tope=25):
            hallazgos.append(f"  {ruta}")

    for nombre, patron in [
        ("NEXT_DATA", r'id="__NEXT_DATA__"[^>]*>(.*?)</script>'),
        ("NUXT", r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>"),
        ("INITIAL_STATE",
         r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>"),
    ]:
        m = re.search(patron, html, re.S)
        if not m:
            continue
        hallazgos.append(f"{nombre}: presente ({len(m.group(1)):,} bytes)")
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            hallazgos.append("  (no parsea como JSON)")
            continue
        for ruta in _rutas_de_json(data, nombre, tope=40):
            hallazgos.append(f"  {ruta}")

    # Otros <script> con JSON grande que no calzan con los patrones de
    # arriba: el estado de la SPA con otro nombre.
    for tag in soup.find_all("script"):
        contenido = (tag.string or "").strip()
        if len(contenido) > 5_000 and contenido[:1] in "{[" and \
                not tag.get("type", "").endswith("ld+json"):
            hallazgos.append(
                f"script JSON sin nombre: {len(contenido):,} bytes, "
                f"empieza: {contenido[:100]!r}")
    return hallazgos


def _resumen_aviso(a) -> str:
    partes = []
    for campo in CAMPOS:
        v = getattr(a, campo)
        partes.append(f"{campo.split('_')[0]}={'—' if v in (None, '') else v}")
    return " ".join(partes)


def _guardar(nombre: str, html: str) -> None:
    _DIR_SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = _DIR_SALIDA / f"{nombre}.html.gz"
    with gzip.open(ruta, "wt", encoding="utf-8") as f:
        f.write(html)


def _cobertura(avisos) -> str:
    n = len(avisos)
    if not n:
        return "0 avisos"
    partes = [f"{n} avisos"]
    for campo in CAMPOS:
        con = sum(1 for a in avisos if getattr(a, campo) not in (None, ""))
        partes.append(f"{campo.split('_')[0]}:{con * 100 // n}%")
    return " ".join(partes)


def _diagnosticar_fuente(fuente, fetcher, uf, fichas_por_fuente: int) -> None:
    print(f"\n{'=' * 74}\n== {fuente.id} ({fuente.urls[0]})\n{'=' * 74}")
    try:
        html = _bajar(fetcher, fuente, fuente.urls[0])
    except Exception as e:                                       # noqa: BLE001
        print(f"LISTADO: reventó al bajar: {e!r}")
        return
    if not html:
        print(f"LISTADO: no bajó ({fetcher.ultimo_motivo or 'sin motivo'})")
        return

    _guardar(f"{fuente.id}-listado", html)
    avisos = extraer(html, fuente.urls[0], fuente, uf)
    via = avisos[0].extras.get("via", "?") if avisos else "—"
    print(f"LISTADO: {len(html):,} bytes | pasada: {via} | {_cobertura(avisos)}")

    # Si el listado extrae poco precio, el problema puede estar en un blob
    # que no estamos leyendo: se muestra lo que la página trae embebido.
    sin_precio = [a for a in avisos if a.arriendo_clp is None]
    if not avisos or len(sin_precio) > len(avisos) / 2:
        print("\n-- blobs embebidos del LISTADO --")
        for linea in _blobs_embebidos(html) or ["(ninguno)"]:
            print(f"   {linea}")
        if not avisos:
            print("\n-- muestra del texto visible del LISTADO --")
            for linea in _texto_visible(html)[:15]:
                print(f"   | {linea}")

    # Las fichas: la página del aviso, que es donde viven los datos que la
    # tarjeta no trae. Solo avisos con link propio.
    con_link = [a for a in avisos
                if a.url and a.url != fuente.urls[0]
                and not a.extras.get("sin_link_directo")]
    for i, a in enumerate(con_link[:fichas_por_fuente], 1):
        print(f"\n-- FICHA {i}: {a.url}")
        try:
            ficha = _bajar(fetcher, fuente, a.url)
        except Exception as e:                                   # noqa: BLE001
            print(f"   reventó al bajar: {e!r}")
            continue
        if not ficha:
            print(f"   no bajó ({fetcher.ultimo_motivo or 'sin motivo'})")
            continue
        _guardar(f"{fuente.id}-ficha-{i}", ficha)

        candidatos = extraer(ficha, a.url, fuente, uf)
        print(f"   {len(ficha):,} bytes | candidatos extraídos: "
              f"{len(candidatos)}")
        propios = [c for c in candidatos if c.url == a.url] or candidatos[:1]
        for c in propios[:1]:
            print(f"   extraído:  {_resumen_aviso(c)}")
        print(f"   tarjeta:   {_resumen_aviso(a)}")

        for linea in _blobs_embebidos(ficha):
            print(f"   {linea}")
        print("   -- texto visible con pinta de dato --")
        for linea in _texto_visible(ficha)[:25]:
            print(f"   | {linea}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fuente", default="",
                    help="diagnosticar solo esta fuente (id de fuentes.yml)")
    ap.add_argument("--fichas", type=int, default=2,
                    help="fichas de detalle a bajar por fuente")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args(argv)

    fuentes = fuentes_activas(cargar_fuentes(), args.fuente)
    fetcher = Fetcher(delay=args.delay, timeout=30)
    uf = P.VALOR_UF_DEFECTO

    print(f"Diagnóstico de {len(fuentes)} fuentes; "
          f"{args.fichas} fichas por fuente.")
    for fuente in fuentes:
        try:
            _diagnosticar_fuente(fuente, fetcher, uf, args.fichas)
        except Exception as e:                                   # noqa: BLE001
            # Una fuente rota no puede matar el diagnóstico de las demás.
            print(f"[{fuente.id}] el diagnóstico reventó: {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
