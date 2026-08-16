"""Línea de comandos y orquestación de una corrida."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import scoring as S
from .alerts.telegram import Telegram
from .bitacora import escribir_bitacora
from .config import (ALERTAS_DIR, LOGS_DIR, STATE_DIR, PerfilInvalido,
                     cargar_perfil, dir_alertas, dir_estado, dir_logs,
                     valor_uf)
from .fichas import escribir_ficha, escribir_tablero, url_ficha
from .models import Arriendo
from .sources.base import Fetcher
from .sources.registry import (FuentesInvalidas, barrer, cargar_fuentes,
                               fuentes_activas)
from .store import Store, deduplicar

log = logging.getLogger("arriendo")


def _configurar_log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Ruidosas y sin nada que aportar a la bitácora.
    for ruidosa in ("urllib3", "requests", "asyncio"):
        logging.getLogger(ruidosa).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# La corrida
# ---------------------------------------------------------------------------

def correr(args: argparse.Namespace) -> int:
    """Corrida completa. Nunca deja el fallo en silencio.

    La carga del perfil y de las fuentes va FUERA del try: son errores de
    configuración, tienen su propio mensaje en `main` y no son "el radar se
    cayó" sino "el YAML está mal".

    Todo lo demás va adentro, porque el modo de fallar más caro que tiene este
    radar es reventar sin decir nada: desde el lado del usuario, una corrida
    que se cayó a la mitad se ve exactamente igual que una que no encontró
    ningún departamento.
    """
    perfil = cargar_perfil(args.perfil)
    fuentes = fuentes_activas(cargar_fuentes(args.fuentes), args.fuente)

    stats: dict = {"inicio": datetime.utcnow()}
    try:
        return _correr(args, perfil, fuentes, stats)
    except Exception as e:                                       # noqa: BLE001
        log.exception("La corrida falló")
        stats["error"] = f"{type(e).__name__}: {e}"
        stats["fin"] = datetime.utcnow()
        escribir_bitacora(stats, dir_logs())
        # El aviso de que el radar se cayó es lo único que no se puede perder:
        # es lo que separa "no hay nada nuevo" de "llevas dos semanas sin
        # radar y no te has dado cuenta".
        try:
            Telegram(dry_run=args.dry_run).resumen(stats, alertas=0,
                                                   marca_dir=dir_estado())
        except Exception:                                        # noqa: BLE001
            log.error("Tampoco se pudo avisar que la corrida falló")
        return 1


def _correr(args: argparse.Namespace, perfil: dict, fuentes: list,
            stats: dict) -> int:
    store = Store(dir_estado())
    fetcher = Fetcher(delay=args.delay)

    stats.update({
        "fuentes_consultadas": len(fuentes),
        "fuentes_ok": 0,
        "por_fuente": {},
        "fuentes_caidas": [],
    })

    # --- 1. barrer ---
    crudos: list[Arriendo] = []
    for fuente in fuentes:
        log.info("Barriendo %s (%s)…", fuente.nombre, fuente.id)
        resultado = barrer(fuente, fetcher,
                           seguir_detalles=not args.sin_detalles)
        crudos.extend(resultado.hallazgos)
        stats["por_fuente"][fuente.id] = len(resultado.hallazgos)
        if resultado.ok:
            stats["fuentes_ok"] += 1
        if resultado.error:
            log.warning("  %s: %s", fuente.id, resultado.error)
        log.info("  %s: %d avisos", fuente.id, len(resultado.hallazgos))

        # Una fuente que venía entregando y hoy trae cero se rompió; una que
        # nunca entregó todavía no está calibrada. Son problemas distintos y
        # solo el primero merece interrumpir a alguien.
        if not resultado.hallazgos and not fuente.entrega_variable:
            if store.indice and any(e.get("source") == fuente.id
                                    for e in store.indice.values()):
                stats["fuentes_caidas"].append(f"{fuente.nombre}: 0 avisos")

    stats["total"] = len(crudos)
    log.info("Total crudo: %d avisos", len(crudos))

    # --- 2. completar con lo que ya se sabía ---
    #
    # Va ANTES de evaluar: un dato heredado puede ser justo el que decide si
    # el departamento pasa el filtro de superficie.
    for a in crudos:
        store.completar(a)

    # --- 3. deduplicar entre portales ---
    unicos = deduplicar(crudos)
    stats["unicos"] = len(unicos)
    log.info("Después de deduplicar: %d", len(unicos))

    # --- 4. evaluar ---
    for a in unicos:
        S.evaluar(a, perfil)

    candidatos = [a for a in unicos if not a.descartado]
    stats["candidatos"] = len(candidatos)
    log.info("Pasaron los filtros: %d", len(candidatos))

    # --- 5. decidir a quién avisar ---
    a_avisar: list[tuple[Arriendo, str]] = []
    for a in sorted(candidatos, key=lambda x: -x.score):
        if not S.debe_alertar(a, perfil):
            continue
        if store.ya_avisado(a):
            motivo = store.cambio_relevante(a, perfil)
            if not motivo:
                continue
        else:
            motivo = ""
        a_avisar.append((a, motivo))

    tope = int((perfil.get("alertas") or {}).get("max_por_corrida", 8))
    if len(a_avisar) > tope:
        # Las de mayor puntaje primero; el resto queda en el tablero y alerta
        # en la corrida siguiente. Sin tope, la primera corrida contra
        # portales de arriendo manda cuarenta mensajes seguidos, porque trae
        # inventario acumulado y no novedades del día.
        log.info("%d avisos por mandar, tope %d: se posponen %d",
                 len(a_avisar), tope, len(a_avisar) - tope)
        a_avisar = a_avisar[:tope]

    # --- 6. avisar ---
    telegram = Telegram(
        dry_run=args.dry_run,
        caminable_km=float((perfil.get("radio_km") or {}).get("preferente") or 0),
        ancla=(perfil.get("ancla") or {}).get("nombre", ""),
    )

    enviados = 0
    for a, motivo in a_avisar:
        # La ficha se escribe ANTES de mandar el mensaje: el mensaje lleva su
        # link adentro, y mandarlo primero es garantizar un 404.
        escribir_ficha(a, dir_alertas() / "casos", perfil, motivo)
        if (url := url_ficha(a)):
            a.extras["ficha_url"] = url

        if telegram.alertar(a, motivo):
            enviados += 1
            store.registrar(a, avisado=True, motivo=motivo)
        else:
            # No se marca como avisado: si el envío falló, la corrida
            # siguiente tiene que volver a intentarlo. Marcarlo igual haría
            # perder el departamento en silencio, que es la peor forma de
            # fallar que tiene este radar.
            log.error("No se pudo avisar %s", a.url)
            store.registrar(a, avisado=False)

    for a in unicos:
        if not any(a is x for x, _ in a_avisar):
            store.registrar(a)

    stats["avisados"] = enviados
    telegram.resumen(stats, enviados, marca_dir=dir_estado())

    # --- 7. guardar ---
    if not args.dry_run:
        store.purgar()
        store.guardar(unicos)
        escribir_tablero(unicos, dir_alertas(), perfil)

    stats["fin"] = datetime.utcnow()
    escribir_bitacora(stats, dir_logs())

    log.info("Listo: %d avisados de %d candidatos", enviados, len(candidatos))
    return 0


# ---------------------------------------------------------------------------
# Comandos auxiliares
# ---------------------------------------------------------------------------

def demo(args: argparse.Namespace) -> int:
    """Corre el filtrado contra HTML local, sin tocar la red.

    Es la forma de ver qué haría el radar con una página concreta: se guarda
    el HTML del portal desde el navegador y se corre esto encima.
    """
    from .sources.base import FuenteConfig
    from .sources.generic import extraer

    perfil = cargar_perfil(args.perfil)
    html = Path(args.archivo).read_text(encoding="utf-8", errors="replace")
    fuente = FuenteConfig(id="demo", nombre="Demo", urls=[args.url])

    hallazgos = deduplicar(extraer(html, args.url, fuente))
    for a in hallazgos:
        S.evaluar(a, perfil)

    hallazgos.sort(key=lambda x: (x.descartado, -x.score))
    print(f"\n{len(hallazgos)} avisos leídos de {args.archivo}\n")
    for a in hallazgos:
        marca = "✗" if a.descartado else "✓"
        print(f"{marca} [{a.score:3d}] {(a.direccion or a.title)[:52]:52s} "
              f"{str(a.comuna)[:12]:12s} "
              f"{_corto_pesos(a.arriendo_clp):>12s} "
              f"{_corto_m2(a):>9s} "
              f"{str(a.dormitorios or '—'):>2s}D")
        if a.descartado:
            print(f"    └─ {a.motivo_descarte}")
    print()
    return 0


def _corto_pesos(v: float | None) -> str:
    return "—" if v is None else "$" + f"{v:,.0f}".replace(",", ".")


def _corto_m2(a: Arriendo) -> str:
    m2 = a.m2_referencia
    return "—" if m2 is None else f"{m2:g} m²"


def probar_aviso(args: argparse.Namespace) -> int:
    """Manda un mensaje de prueba por Telegram y dice si llegó.

    Existe porque el modo más caro de fallar es encontrar el departamento y no
    conseguir avisar: desde afuera se ve igual que no haber encontrado nada.
    Esto cierra el asunto en dos segundos en vez de esperar una corrida.
    """
    t = Telegram(dry_run=args.dry_run)
    if not t.configurado and not args.dry_run:
        print("✗ Telegram sin configurar.")
        print()
        print("  Faltan los secrets TELEGRAM_TOKEN y TELEGRAM_CHAT_ID.")
        print("  El paso a paso está en AVISOS.md y se hace desde el teléfono.")
        return 1

    ok = t.enviar(
        "✅ <b>Radar de Arriendos</b>\n\n"
        "Prueba de conexión. Si estás leyendo esto, las alertas van a llegar "
        "a esta conversación.\n\n"
        "Busca: departamentos en Vitacura o a 1,2 km del Sport Francés, más "
        "de 100 m² totales, 3+ dormitorios, hasta $1.600.000."
    )
    print("✓ Mensaje entregado" if ok else "✗ Telegram no confirmó la entrega")
    return 0 if ok else 1


def calibrar(args: argparse.Namespace) -> int:
    """Descarga cada fuente, corre el extractor y reporta qué encontró.

    Es el primer paso obligatorio del proyecto. Las URLs de fuentes.yml están
    armadas con el patrón de cada portal pero nunca se pudieron abrir —el
    entorno donde se escribió esto tiene la red bloqueada—, así que varias van
    a estar mal. Esto dice cuáles.

    Corre en GitHub Actions, que sí tiene internet abierto, y guarda el HTML
    crudo para poder escribir los selectores que falten.
    """
    from .sources.registry import _bajar
    from .sources.generic import extraer

    perfil = cargar_perfil(args.perfil)
    fuentes = fuentes_activas(cargar_fuentes(args.fuentes), args.fuente)
    fetcher = Fetcher(delay=args.delay)

    destino = Path(args.fixtures)
    destino.mkdir(parents=True, exist_ok=True)

    nucleo = {c.lower() for c in (perfil.get("comunas") or {}).get("nucleo") or []}
    L: list[str] = ["# Calibración de fuentes", "",
                    f"Corrida: {datetime.utcnow():%d-%m-%Y %H:%M} UTC", "",
                    "| Fuente | Estado | Avisos | En zona | Pasan filtros |",
                    "|---|---|---|---|---|"]
    detalle: list[str] = []

    for fuente in fuentes:
        print(f"\n{fuente.nombre}  [{fuente.id}]")
        avisos: list[Arriendo] = []
        estado = "❌ sin respuesta"
        motivo = ""

        for i, url in enumerate(fuente.urls):
            print(f"  GET {url}")
            try:
                html = _bajar(fetcher, fuente, url)
            except Exception as e:                               # noqa: BLE001
                html, motivo = None, str(e)[:150]

            if not html:
                motivo = motivo or fetcher.ultimo_motivo or "sin respuesta"
                print(f"  ❌ {motivo}")
                continue

            archivo = destino / f"{fuente.id}_{i}.html"
            archivo.write_text(html, encoding="utf-8")
            print(f"  ✅ {len(html):,} bytes → {archivo}".replace(",", "."))
            avisos.extend(extraer(html, url, fuente))

        if avisos:
            for a in avisos:
                S.evaluar(a, perfil)
            en_zona = [a for a in avisos if (a.comuna or "").lower() in nucleo]
            pasan = [a for a in avisos if not a.descartado]
            estado = "✅ entrega"
            print(f"  → {len(avisos)} avisos, {len(en_zona)} en zona, "
                  f"{len(pasan)} pasan los filtros")
            for a in sorted(pasan, key=lambda x: -x.score)[:3]:
                print(f"     · [{a.score}] {(a.direccion or a.title)[:60]}")
            L.append(f"| {fuente.nombre} | {estado} | {len(avisos)} | "
                     f"{len(en_zona)} | {len(pasan)} |")
        else:
            estado = "⚠️ cero resultados" if fetcher.ultimo_motivo == "" else estado
            L.append(f"| {fuente.nombre} | {estado} | 0 | 0 | 0 |")
            sin_motivo = "la página respondió pero no se reconoció ningún aviso"
            detalle.append(f"- **{fuente.nombre}** (`{fuente.id}`): "
                           f"{motivo or sin_motivo}")

    L.append("")
    if detalle:
        L.append("## Fuentes que no entregaron")
        L.append("")
        L += detalle
        L.append("")
        L.append("Qué hacer con cada una:")
        L.append("")
        L.append("- **cero resultados con HTML guardado**: abre el archivo en "
                 "`fixtures/`. Si trae los avisos, agrégale `selector_card` a "
                 "esa fuente en `fuentes.yml`. Si viene vacío, el sitio arma "
                 "la página con JavaScript: ponle `motor: navegador`.")
        L.append("- **HTTP 403**: el sitio rechaza clientes que no son un "
                 "navegador. `motor: navegador` suele resolverlo.")
        L.append("- **DNS no resuelve**: la URL está mala. Ábrela en el "
                 "teléfono y copia la que funciona.")
        L.append("")

    reporte = Path(args.reporte)
    reporte.write_text("\n".join(L), encoding="utf-8")
    print(f"\nReporte en {reporte}")
    return 0


def geocode(args: argparse.Namespace) -> int:
    """Resuelve las coordenadas del ancla y las verifica contra el perfil."""
    from .geo import geocode as resolver, haversine_km

    perfil = cargar_perfil(args.perfil)
    ancla = perfil.get("ancla") or {}
    direccion = ancla.get("direccion", "")

    print(f"Ancla del perfil: {ancla.get('nombre')}")
    print(f"  {direccion}")
    print(f"  Coordenadas anotadas: {ancla.get('lat')}, {ancla.get('lon')}")

    coords = resolver(direccion)
    if not coords:
        print("  ⚠️ No se pudo resolver en el mapa (sin red o dirección no "
              "encontrada). Las coordenadas anotadas a mano siguen valiendo.")
        return 1

    print(f"  Coordenadas del mapa:  {coords[0]}, {coords[1]}")
    if ancla.get("lat") is not None:
        d = haversine_km(ancla["lat"], ancla["lon"], *coords)
        print(f"  Diferencia: {d * 1000:.0f} m")
        if d > 0.5:
            print("  ⚠️ Más de 500 m de diferencia: vale la pena revisar cuál "
                  "de las dos es la correcta antes de confiar en el anillo.")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    # Las opciones comunes se aceptan a los DOS lados del subcomando: tanto
    # `arriendo --fuentes f.yml run` como `arriendo run --fuentes f.yml`.
    #
    # Escribirlas después del subcomando es lo que sale natural, y con argparse
    # eso falla con "unrecognized arguments" si solo están en el parser
    # principal. `SUPPRESS` es lo que hace que convivan: sin él, la copia del
    # subcomando pisaría con su default lo que se pasó antes del subcomando.
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--perfil", default=argparse.SUPPRESS,
                       help="ruta a perfil.yml")
    comun.add_argument("--fuentes", default=argparse.SUPPRESS,
                       help="ruta a fuentes.yml")
    comun.add_argument("-v", "--verbose", action="store_true",
                       default=argparse.SUPPRESS)

    p = argparse.ArgumentParser(
        prog="python -m arriendo",
        parents=[comun],
        description="Radar de arriendos: Vitacura y el anillo del Sport Francés.",
    )
    # OJO: acá NO va `p.set_defaults(perfil=None, ...)`.
    #
    # `parents=` no copia las acciones, las COMPARTE, y `set_defaults` le pisa
    # el default a la acción compartida. Con eso el SUPPRESS de `comun`
    # desaparecía y el subcomando volvía a escribir `fuentes=None` encima de
    # lo que se hubiera pasado antes del subcomando.
    #
    # El síntoma era feo y silencioso: `arriendo --fuentes f.yml run` ignoraba
    # el archivo y corría contra el catálogo de verdad. Los defaults se
    # aplican después de parsear, en `_con_defaults`.
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("run", parents=[comun],
                       help="corrida completa: barrer, filtrar y avisar")
    c.add_argument("--dry-run", action="store_true",
                   help="imprime los avisos en vez de mandarlos")
    c.add_argument("--fuente", default="", help="limitar a una sola fuente")
    c.add_argument("--delay", type=float, default=2.0,
                   help="segundos entre requests al mismo sitio")
    c.add_argument("--sin-detalles", action="store_true",
                   help="no seguir las fichas de detalle (más rápido, "
                        "menos datos)")
    c.set_defaults(func=correr)

    c = sub.add_parser("demo", parents=[comun], help="probar el filtrado con HTML local")
    c.add_argument("archivo")
    c.add_argument("--url", default="https://ejemplo.cl/",
                   help="URL base para resolver los enlaces relativos")
    c.set_defaults(func=demo)

    c = sub.add_parser("calibrar", parents=[comun],
                       help="descargar cada fuente y reportar qué entrega")
    c.add_argument("--fuente", default="", help="limitar a una sola fuente")
    c.add_argument("--reporte", default="calibracion.md")
    c.add_argument("--fixtures", default="fixtures")
    c.add_argument("--delay", type=float, default=2.0)
    c.set_defaults(func=calibrar)

    c = sub.add_parser("probar-aviso", parents=[comun], help="mandar un mensaje de prueba")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=probar_aviso)

    c = sub.add_parser("geocode", parents=[comun], help="verificar las coordenadas del ancla")
    c.set_defaults(func=geocode)

    return p


# Los defaults de las opciones comunes. Se aplican después de parsear porque
# en el parser van con `SUPPRESS`: ver el comentario en `construir_parser`.
_DEFAULTS_COMUNES = {"perfil": None, "fuentes": None, "verbose": False}


def _con_defaults(args: argparse.Namespace) -> argparse.Namespace:
    for campo, valor in _DEFAULTS_COMUNES.items():
        if not hasattr(args, campo):
            setattr(args, campo, valor)
    return args


def main(argv: list[str] | None = None) -> int:
    args = _con_defaults(construir_parser().parse_args(argv))
    _configurar_log(args.verbose)

    try:
        return args.func(args)
    except (PerfilInvalido, FuentesInvalidas) as e:
        # Errores de configuración: el mensaje es para una persona que está
        # editando un YAML desde el teléfono, no un stack trace.
        print(f"\n✗ {e}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
