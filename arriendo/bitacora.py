"""Bitácora: qué pasó en cada corrida, versionada en el repo.

Se escribe al repositorio y no solo al log de Actions porque los logs de
Actions expiran y hay que pedirlos por API. La bitácora se lee desde el
navegador del teléfono, que es donde se opera este radar.

Se escribe TAMBIÉN cuando la corrida falla, que es justo cuando sirve: una
corrida que revienta y no deja rastro en el repo se ve, desde afuera, igual
que una que no encontró nada.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from .tiempo import ahora_utc

log = logging.getLogger(__name__)

ULTIMA = "ultima-corrida.md"
HISTORIAL = "historial.jsonl"

# Cuántas corridas conservar en el historial. Con dos corridas al día, 400
# entradas son unos siete meses: suficiente para ver una tendencia y poco
# suficiente para que el archivo siga abriéndose en el teléfono.
MAX_HISTORIAL = 400


def escribir_bitacora(stats: dict, directorio: Path) -> None:
    """Escribe el resumen de la corrida. Nunca levanta.

    Que no levante es deliberado: la bitácora es diagnóstico, y un fallo
    escribiéndola no puede voltear una corrida que sí encontró departamentos
    y sí mandó los avisos.
    """
    try:
        directorio.mkdir(parents=True, exist_ok=True)
        (directorio / ULTIMA).write_text(_resumen(stats), encoding="utf-8")
        _anotar_historial(stats, directorio / HISTORIAL)
    except OSError as e:
        log.warning("No se pudo escribir la bitácora: %s", e)


def _duracion(stats: dict) -> str:
    inicio, fin = stats.get("inicio"), stats.get("fin")
    if not isinstance(inicio, datetime) or not isinstance(fin, datetime):
        return "—"
    return f"{(fin - inicio).total_seconds():.0f}s"


def _resumen(stats: dict) -> str:
    cuando = stats.get("inicio")
    cuando = cuando if isinstance(cuando, datetime) else ahora_utc()

    L = [f"# Última corrida — {cuando:%d-%m-%Y %H:%M} UTC", ""]

    if stats.get("error"):
        L.append("## ❌ La corrida falló")
        L.append("")
        L.append("```")
        L.append(str(stats["error"])[:1500])
        L.append("```")
        L.append("")

    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Duración | {_duracion(stats)} |")
    L.append(f"| Fuentes consultadas | {stats.get('fuentes_consultadas', 0)} |")
    L.append(f"| Fuentes que entregaron | {stats.get('fuentes_ok', 0)} |")
    L.append(f"| Avisos leídos | {stats.get('total', 0)} |")
    L.append(f"| Después de deduplicar | {stats.get('unicos', 0)} |")
    L.append(f"| Pasaron los filtros | {stats.get('candidatos', 0)} |")
    L.append(f"| Avisados por Telegram | {stats.get('avisados', 0)} |")
    if stats.get("nuevos") is not None:
        # El movimiento del mercado, que es distinto de lo que se avisó: un día
        # con 6 altas y 0 avisos significa que salió inventario y no calificó,
        # y eso se lee muy distinto de un día sin inventario nuevo.
        L.append(f"| Aparecieron | {stats.get('nuevos', 0)} |")
        L.append(f"| Dejaron de publicarse | {stats.get('se_fueron', 0)} |")
    if stats.get("valor_uf"):
        # De dónde salió la UF explica por qué un aviso publicado en UF quedó
        # justo a un lado u otro del tope de presupuesto.
        uf = f"{stats['valor_uf']:,.2f}".replace(",", ".")
        L.append(f"| Valor UF usado | ${uf} · {stats.get('origen_uf', '?')} |")
    L.append("")

    por_fuente = stats.get("por_fuente") or {}
    if por_fuente:
        # El tiempo por fuente es lo que se mira cuando la corrida se cortó:
        # dice cuál se colgó, que es distinto de cuál no entregó.
        segundos = stats.get("segundos_fuente") or {}
        errores = stats.get("errores_fuente") or {}
        L.append("## Qué entregó cada fuente")
        L.append("")
        L.append("| Fuente | Avisos | Tiempo | Qué pasó |")
        L.append("|---|---|---|---|")
        for fid, n in sorted(por_fuente.items(), key=lambda kv: -kv[1]):
            marca = "" if n else " ⚠️"
            t = segundos.get(fid)
            # El motivo del cero es lo único accionable de esta tabla: separa
            # "el sitio no contesta" de "contestó y no entendimos su HTML".
            que = errores.get(fid) or ("" if n else
                                       "respondió, pero no se reconoció ningún aviso")
            L.append(f"| {fid}{marca} | {n} | {f'{t:.0f}s' if t is not None else '—'} "
                     f"| {que} |")
        L.append("")

    if (reventadas := stats.get("fuentes_reventadas")):
        # Arriba de todo lo demás: es lo único de esta lista que es un bug y
        # no una condición normal del mundo.
        L.append("## 🐞 Fuentes que reventaron")
        L.append("")
        for r in reventadas:
            L.append(f"- {r}")
        L.append("")
        L.append("Una excepción que el barrido no esperaba. No es el portal "
                 "caído ni la URL mala: es un error de código, y la corrida "
                 "siguió sin esa fuente en vez de voltearse entera.")
        L.append("")

    if (parciales := stats.get("fuentes_parciales")):
        L.append("## ⏱ Fuentes que quedaron a medias")
        L.append("")
        L.append(", ".join(parciales))
        L.append("")
        L.append("Se alcanzaron a mirar, pero se les acabó el presupuesto de "
                 "tiempo antes de terminar sus páginas: lo que trajeron es "
                 "parcial.")
        L.append("")

    if (pendientes := stats.get("corte_por_tiempo")):
        L.append("## ⏱ La corrida se cortó por tiempo")
        L.append("")
        L.append(f"Se agotó el presupuesto y quedaron **{len(pendientes)} "
                 f"fuentes sin mirar**:")
        L.append("")
        for n in pendientes:
            L.append(f"- {n}")
        L.append("")
        L.append("No es un error: es preferible avisar lo que se alcanzó a "
                 "encontrar antes de que GitHub Actions mate el job, que "
                 "perdería la corrida entera. Si se repite, lo más probable "
                 "es que una fuente esté colgándose hasta el timeout — la "
                 "tabla de arriba dice cuál trajo cero.")
        L.append("")

    if (caidas := stats.get("fuentes_caidas")):
        L.append("## ⚠️ Fuentes que dejaron de entregar")
        L.append("")
        for c in caidas:
            L.append(f"- {c}")
        L.append("")
        L.append("Venían trayendo avisos y hoy no trajeron ninguno. Puede ser "
                 "falta de inventario nuevo, o que el sitio haya cambiado. "
                 "`Actions → Calibrar fuentes` lo distingue.")
        L.append("")

    # Una fuente que nunca ha entregado no está rota: está sin calibrar, y son
    # cosas distintas que piden acciones distintas.
    sin_calibrar = [fid for fid, n in por_fuente.items() if n == 0]
    if sin_calibrar and not stats.get("fuentes_caidas"):
        L.append("## Fuentes en cero")
        L.append("")
        L.append(", ".join(f"`{f}`" for f in sorted(sin_calibrar)))
        L.append("")
        L.append("Si nunca han entregado, lo más probable es que su URL esté "
                 "mal: corre `Actions → Calibrar fuentes` y arréglalas "
                 "copiando la URL desde el navegador.")
        L.append("")

    return "\n".join(L)


def _anotar_historial(stats: dict, ruta: Path) -> None:
    """Una línea JSON por corrida. Sirve para ver tendencias sin parsear prosa."""
    entrada = {
        "cuando": (stats.get("inicio") or ahora_utc()).isoformat(
            timespec="seconds"),
        "duracion_s": None,
        "fuentes_ok": stats.get("fuentes_ok", 0),
        "fuentes_consultadas": stats.get("fuentes_consultadas", 0),
        "total": stats.get("total", 0),
        "unicos": stats.get("unicos", 0),
        "candidatos": stats.get("candidatos", 0),
        "avisados": stats.get("avisados", 0),
        "error": str(stats.get("error", ""))[:200] or None,
        "corte_por_tiempo": len(stats.get("corte_por_tiempo") or []) or None,
        "reventadas": len(stats.get("fuentes_reventadas") or []) or None,
        "hilos": stats.get("hilos"),
    }
    inicio, fin = stats.get("inicio"), stats.get("fin")
    if isinstance(inicio, datetime) and isinstance(fin, datetime):
        entrada["duracion_s"] = round((fin - inicio).total_seconds())

    lineas = []
    if ruta.exists():
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    lineas.append(json.dumps(entrada, ensure_ascii=False))
    ruta.write_text("\n".join(lineas[-MAX_HISTORIAL:]) + "\n", encoding="utf-8")
