"""Historial de las búsquedas: qué había en el mercado, corrida tras corrida.

Por qué existe
--------------
El estado (`store.py`) responde una sola pregunta: *¿ya avisé esto?*. Y para
no crecer sin fin, olvida todo lo que lleva 120 días sin aparecer. O sea que
justo lo más valioso —el departamento que estuvo publicado dos meses, bajó el
precio tres veces y después desapareció— se borra sin dejar rastro.

Este módulo guarda ese rastro. No es un log de diagnóstico: es la memoria del
mercado, y sirve para tres cosas que ninguna corrida suelta puede contestar:

  1. **¿Este ya lo vi?** Un aviso que reaparece en otro portal tres meses
     después, $150.000 más barato, es la misma oferta y hay que saberlo.
  2. **¿Cuánto vale de verdad un 3D de 120 m² en Vitacura?** Con seis meses de
     avisos, la mediana la dice el historial y no el vendedor.
  3. **¿Cuánto se demora en arrendarse?** Lo que desaparece de todos los
     portales se arrendó. Saber que la mediana son 25 días —y a qué precio—
     cambia por completo cuánto se puede negociar y cuánto se puede esperar.

Cómo se guarda
--------------
Como un log de EVENTOS y no como una foto por corrida. Una foto por corrida
serían 180 avisos × 2 corridas al día × 365 días = 130.000 líneas al año para
repetir casi siempre lo mismo. Los eventos son tres y son pocos:

    alta    apareció un departamento que no se había visto nunca
    precio  cambió el canon de uno que ya estaba
    baja    dejó de aparecer en todos los portales: probablemente se arrendó

Con eso, el archivo crece unas pocas líneas por corrida y contiene todo lo que
hubo. Es JSONL para poder agregarle una línea sin releer el archivo, y vive
versionado en el repo como todo lo demás.

La regla de las bajas
---------------------
Un departamento que no aparece hoy NO se dio de baja: lo más probable es que
el portal se haya caído. Por eso una baja pide dos cosas a la vez: que el
aviso falte en varias corridas seguidas, y que la fuente que lo publicaba SÍ
haya entregado en esas corridas. Sin la segunda condición, un portal caído un
martes daría por arrendados a sus cuarenta departamentos.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .models import Arriendo
from .tiempo import ahora_utc

log = logging.getLogger(__name__)

ARCHIVO = "inventario.jsonl"

# Cuántos eventos conservar. Con unas 10 líneas por corrida y dos corridas
# diarias, 8.000 son más de un año: de sobra para lo que se busca —una
# temporada de arriendo dura meses, no años— y sigue siendo un archivo que
# abre en el teléfono.
MAX_EVENTOS = 8_000

# Corridas seguidas sin aparecer antes de dar por arrendado un departamento.
#
# Tres y no una: los portales fallan. Con dos corridas al día son un día y
# medio de ausencia, que es poco para un arriendo (nada se arrienda y se
# desmarca en 36 horas por casualidad) y suficiente para no confundirse con
# un sitio caído una tarde.
AUSENCIAS_PARA_BAJA = 3


def anotar(eventos: list[dict], directorio: Path) -> None:
    """Agrega eventos al historial. Nunca levanta.

    Igual que la bitácora: esto es memoria, no operación. Un fallo escribiendo
    el historial no puede voltear una corrida que sí encontró departamentos y
    sí mandó los avisos.
    """
    if not eventos:
        return
    try:
        directorio.mkdir(parents=True, exist_ok=True)
        ruta = directorio / ARCHIVO
        lineas = ruta.read_text(encoding="utf-8").splitlines() if ruta.exists() else []
        lineas.extend(json.dumps(e, ensure_ascii=False) for e in eventos)
        ruta.write_text("\n".join(lineas[-MAX_EVENTOS:]) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("No se pudo escribir el historial de búsquedas: %s", e)


def leer(directorio: Path) -> list[dict]:
    """Todos los eventos guardados. Las líneas rotas se saltan."""
    ruta = Path(directorio) / ARCHIVO
    if not ruta.exists():
        return []

    salida = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            salida.append(json.loads(linea))
        except json.JSONDecodeError:
            # Una línea corrupta —un job matado a mitad de escritura— no puede
            # hacer perder el resto del historial.
            continue
    return salida


# ---------------------------------------------------------------------------
# Construcción de los eventos de una corrida
# ---------------------------------------------------------------------------

def eventos_de_corrida(del_mercado: list[Arriendo], store,
                       fuentes_con_entrega: set[str],
                       todos: Iterable[Arriendo] | None = None) -> list[dict]:
    """Qué cambió en el mercado en esta corrida.

    `fuentes_con_entrega` son las fuentes que hoy sí trajeron avisos. Es lo que
    permite distinguir "este departamento desapareció" de "el portal que lo
    publicaba se cayó", que sin este dato se ven idénticos y llevan a dar por
    arrendado un inventario que sigue ahí.

    `del_mercado` son los avisos que cuentan para las cifras —departamentos en
    arriendo de la zona— y `todos` es TODO lo que se vio, incluido lo que se
    descartó por ser una venta o estar en otra comuna. Los dos hacen falta y
    son distintos: las altas y los cambios de precio salen del primero, pero
    la presencia se mide contra el segundo. Si no, un aviso que hoy resultó ser
    una venta contaría como ausente y a las tres corridas se anotaría como
    arrendado, que es un evento que nunca ocurrió.
    """
    hoy = date.today().isoformat()
    eventos: list[dict] = []

    for a in del_mercado:
        prev = store.indice.get(a.fingerprint)
        if prev is None:
            eventos.append(_alta(a, hoy))
            continue

        antes = prev.get("arriendo_clp")
        if a.arriendo_clp is not None and antes is not None and a.arriendo_clp != antes:
            eventos.append({
                "cuando": hoy, "evento": "precio", "fp": a.fingerprint,
                "clp": a.arriendo_clp, "antes": antes,
                "direccion": a.direccion or a.title[:60],
            })

    presentes = {a.fingerprint for a in (del_mercado if todos is None else todos)}
    eventos.extend(_bajas(store, presentes, fuentes_con_entrega, hoy))
    return eventos


def _alta(a: Arriendo, hoy: str) -> dict:
    """El evento de un departamento nuevo, con lo que lo hace comparable.

    Se guardan los campos con los que después se calculan las medianas —canon,
    superficie, dormitorios, comuna— y no el aviso completo: el historial no
    reemplaza a la ficha, y una copia entera por evento lo volvería ilegible.
    """
    return {
        "cuando": hoy,
        "evento": "alta",
        "fp": a.fingerprint,
        "fuente": a.source,
        "direccion": a.direccion or a.title[:60],
        "comuna": a.comuna,
        "clp": a.arriendo_clp,
        "gc": a.gastos_comunes_clp,
        "m2": a.m2_totales,
        "dorm": a.dormitorios,
        "anos": a.antiguedad_anos,
        "score": a.score,
        "url": a.url,
    }


def _bajas(store, vistos_hoy: set[str], fuentes_con_entrega: set[str],
           hoy: str) -> list[dict]:
    """Los que dejaron de aparecer lo suficiente como para darlos por idos.

    Muta el índice del store: la cuenta de ausencias vive ahí, porque es lo
    único que persiste entre corridas.
    """
    eventos = []

    for fp, e in store.indice.items():
        if fp in vistos_hoy:
            e["ausencias"] = 0
            continue

        # La fuente que lo publicaba no entregó hoy: no se sabe nada de este
        # aviso, así que no se cuenta la ausencia. Contarla igual sería
        # arrendar departamentos por decreto cada vez que un portal se cae.
        if e.get("source") not in fuentes_con_entrega:
            continue

        e["ausencias"] = int(e.get("ausencias", 0)) + 1
        if e["ausencias"] != AUSENCIAS_PARA_BAJA:
            # `!=` y no `>=`: el evento se emite UNA vez, en la corrida exacta
            # en que se cruza el umbral. Con `>=` se repetiría en cada corrida
            # hasta que la purga lo borre.
            continue

        eventos.append({
            "cuando": hoy,
            "evento": "baja",
            "fp": fp,
            "direccion": e.get("direccion") or e.get("titulo", "")[:60],
            "comuna": e.get("comuna"),
            "clp": e.get("arriendo_clp"),
            "m2": e.get("m2_totales"),
            "dias": _dias_publicado(e),
            "avisado": bool(e.get("avisado")),
        })

    return eventos


def _dias_publicado(entrada: dict) -> int | None:
    """Cuántos días estuvo visible, de la primera vez que se vio a hoy.

    Es un piso, no el dato exacto: el aviso pudo estar publicado antes de que
    el radar existiera o antes de que su portal entrara al catálogo. Igual es
    la única medida propia de cuánto se demora en arrendarse algo así, y no
    depende de que el portal publique su fecha.
    """
    try:
        desde = datetime.fromisoformat(entrada["primera_vez"]).date()
    except (KeyError, ValueError, TypeError):
        return None
    return (date.today() - desde).days


# ---------------------------------------------------------------------------
# Lo que el historial responde
# ---------------------------------------------------------------------------

def resumen_mercado(eventos: list[dict], dias: int = 90,
                    comuna: str = "") -> dict:
    """Las cifras del mercado según lo que el radar vio.

    Es el único número honesto que se puede tener del arriendo en Vitacura: no
    sale de un índice publicado ni del precio que pide un corredor, sale de los
    avisos que efectivamente aparecieron y de los que efectivamente
    desaparecieron.

    Devuelve un diccionario con vacíos (None) donde no hay dato suficiente. Es
    a propósito: una mediana calculada sobre tres avisos es peor que no tener
    mediana, porque parece un dato.
    """
    corte = _hace(dias)
    altas = [e for e in eventos
             if e.get("evento") == "alta" and _cuando(e) >= corte
             and _de_comuna(e, comuna)]
    bajas = [e for e in eventos
             if e.get("evento") == "baja" and _cuando(e) >= corte
             and _de_comuna(e, comuna)]
    cambios = [e for e in eventos
               if e.get("evento") == "precio" and _cuando(e) >= corte]

    precios = [e["clp"] for e in altas if e.get("clp")]
    por_m2 = [e["clp"] / e["m2"] for e in altas
              if e.get("clp") and e.get("m2") and e["m2"] > 0]
    dias_hasta_baja = [e["dias"] for e in bajas if e.get("dias")]
    rebajas = [e for e in cambios if e.get("antes") and e["clp"] < e["antes"]]

    return {
        "dias": dias,
        "comuna": comuna,
        "nuevos": len(altas),
        "se_fueron": len(bajas),
        "cambios_de_precio": len(cambios),
        "rebajas": len(rebajas),
        # El umbral de 4 es el mismo criterio de todo el módulo: con menos, un
        # solo aviso raro mueve la mediana y el número engaña.
        "precio_mediano": round(median(precios)) if len(precios) >= 4 else None,
        "precio_m2_mediano": round(median(por_m2)) if len(por_m2) >= 4 else None,
        "dias_hasta_arrendarse": (round(median(dias_hasta_baja))
                                  if len(dias_hasta_baja) >= 4 else None),
        "rebaja_mediana_pct": (
            round(median([100 * (e["antes"] - e["clp"]) / e["antes"]
                          for e in rebajas]))
            if len(rebajas) >= 4 else None),
    }


def gc_tipico(eventos: list[dict], m2: float | None = None,
              dias: int = 180) -> int | None:
    """Los gastos comunes típicos de la zona, según lo que el radar ha visto.

    Es la respuesta al aviso que no publica los GC, que en la corrida real fue
    la MAYORÍA. Hoy el mensaje dice "GC no publicados" y el costo mensual
    queda incompleto — honesto, pero deja la comparación coja: no se puede
    poner al lado de uno que sí los publica.

    Con historial se puede estimar sin inventar: la mediana de $/m² de los
    avisos que SÍ publicaron gastos comunes y superficie. Con la superficie
    del aviso se convierte a pesos; sin ella, la mediana de GC a secas.

    Devuelve None con menos de cuatro datos — el mismo umbral de todas las
    medianas del módulo: una estimación sacada de dos avisos parece un dato y
    no lo es. Y quien la muestre tiene que decir que es estimación: un dato
    deducido presentado como publicado es peor que uno ausente.
    """
    corte = _hace(dias)
    altas = [e for e in eventos
             if e.get("evento") == "alta" and _cuando(e) >= corte and e.get("gc")]

    if m2:
        por_m2 = [e["gc"] / e["m2"] for e in altas
                  if e.get("m2") and e["m2"] > 0]
        if len(por_m2) >= 4:
            # Redondeado a los $10.000: la precisión de pesos exactos en una
            # estimación es una mentira tipográfica.
            return int(round(median(por_m2) * m2, -4))

    absolutos = [e["gc"] for e in altas]
    if len(absolutos) >= 4:
        return int(round(median(absolutos), -4))
    return None


def contar_por_mes(eventos: list[dict], evento: str = "alta") -> dict[str, int]:
    """Cuántos avisos por mes. Sirve para ver la estacionalidad.

    En Chile el arriendo tiene temporada —enero y marzo mueven mucho más que
    julio— y saber en qué mes está el mercado dice si conviene apurarse o
    esperar dos semanas a que salga más inventario.
    """
    salida: dict[str, int] = {}
    for e in eventos:
        if e.get("evento") != evento:
            continue
        mes = str(e.get("cuando", ""))[:7]
        if len(mes) == 7:
            salida[mes] = salida.get(mes, 0) + 1
    return dict(sorted(salida.items()))


def ya_visto(eventos: list[dict], a: Arriendo) -> dict | None:
    """El alta anterior de este mismo departamento, si la hay.

    Reconoce por dirección y comuna, no por fingerprint: el caso que importa
    es justo el que el fingerprint no atrapa —el mismo departamento que vuelve
    a publicarse meses después, en otro portal y con otra URL—.

    Un aviso que ya estuvo y volvió no es una novedad: es una oferta que no se
    arrendó, y eso es información al momento de negociar.
    """
    if not a.direccion or not a.comuna:
        return None

    from .models import clave_direccion

    clave = clave_direccion(a.direccion, a.comuna)
    if not clave:
        return None

    previos = [e for e in eventos
               if e.get("evento") == "alta"
               and e.get("fp") != a.fingerprint
               and clave_direccion(e.get("direccion") or "",
                                   e.get("comuna") or "") == clave]
    return previos[-1] if previos else None


def a_markdown(eventos: list[dict], dias: int = 90) -> str:
    """El historial en una página que se lee en el teléfono.

    Se escribe junto al tablero. Va en orden inverso —lo último arriba— porque
    la pregunta con la que se abre este archivo es casi siempre "¿qué pasó
    estos días?" y no "¿qué pasó en marzo?".
    """
    r = resumen_mercado(eventos, dias)
    L = ["# Historial de búsquedas", "",
         f"_Actualizado {ahora_utc():%d-%m-%Y %H:%M} UTC · "
         f"{len(eventos)} eventos guardados_", "",
         f"## El mercado, últimos {dias} días", "",
         "| | |", "|---|---|",
         f"| Departamentos nuevos | {r['nuevos']} |",
         f"| Dejaron de publicarse | {r['se_fueron']} |",
         f"| Cambios de precio | {r['cambios_de_precio']} "
         f"({r['rebajas']} a la baja) |"]

    if r["precio_mediano"]:
        L.append(f"| Canon mediano | ${r['precio_mediano']:,.0f} |"
                 .replace(",", "."))
    if r["precio_m2_mediano"]:
        L.append(f"| Canon mediano por m² | ${r['precio_m2_mediano']:,.0f} |"
                 .replace(",", "."))
    if r["dias_hasta_arrendarse"]:
        L.append(f"| Días publicado antes de irse | "
                 f"{r['dias_hasta_arrendarse']} |")
    if r["rebaja_mediana_pct"]:
        L.append(f"| Rebaja mediana | {r['rebaja_mediana_pct']}% |")
    L.append("")

    # Los números con pocos datos no se muestran: ver `resumen_mercado`.
    if not r["precio_mediano"]:
        L.append("_Todavía no hay suficientes avisos para calcular medianas "
                 "confiables. Se van llenando solas con las corridas._")
        L.append("")

    if (por_mes := contar_por_mes(eventos)):
        L.append("## Avisos nuevos por mes")
        L.append("")
        L.append("| Mes | Nuevos |")
        L.append("|---|---|")
        for mes, n in list(por_mes.items())[-12:]:
            L.append(f"| {mes} | {'▪' * min(n, 30)} {n} |")
        L.append("")

    L.append("## Últimos movimientos")
    L.append("")
    L.append("| Fecha | | Departamento | Canon |")
    L.append("|---|---|---|---|")
    for e in list(eventos)[-60:][::-1]:
        L.append(f"| {e.get('cuando', '')} | {_icono(e)} | "
                 f"{_texto(e)} | {_plata(e.get('clp'))} |")
    L.append("")

    return "\n".join(L)


_ICONOS = {"alta": "🆕", "precio": "💲", "baja": "📤"}


def _icono(e: dict) -> str:
    if e.get("evento") == "precio" and e.get("antes") and e.get("clp"):
        return "📉" if e["clp"] < e["antes"] else "📈"
    return _ICONOS.get(str(e.get("evento")), "·")


def _texto(e: dict) -> str:
    base = str(e.get("direccion") or "—")[:52]
    if e.get("comuna"):
        base += f" · {e['comuna']}"
    if e.get("evento") == "baja" and e.get("dias"):
        base += f" — {e['dias']} días publicado"
    if e.get("evento") == "precio" and e.get("antes"):
        base += f" — antes {_plata(e['antes'])}"
    return base


def _plata(v: Any) -> str:
    return f"${v:,.0f}".replace(",", ".") if v else "—"


def _cuando(e: dict) -> str:
    return str(e.get("cuando", ""))[:10]


def _hace(dias: int) -> str:
    from datetime import timedelta
    return (date.today() - timedelta(days=dias)).isoformat()


def _de_comuna(e: dict, comuna: str) -> bool:
    if not comuna:
        return True
    return str(e.get("comuna", "")).strip().lower() == comuna.strip().lower()
