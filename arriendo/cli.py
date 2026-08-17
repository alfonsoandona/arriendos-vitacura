"""Línea de comandos y orquestación de una corrida."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from . import parse as P
from . import scoring as S
from .alerts.telegram import Telegram, mensaje_bajas, mensaje_sobrantes
from .bitacora import escribir_bitacora
from .config import (ALERTAS_DIR, LOGS_DIR, STATE_DIR, PerfilInvalido,
                     cargar_perfil, dir_alertas, dir_estado, dir_logs)
from .uf import valor_uf as valor_uf_del_dia
from .fichas import escribir_ficha, escribir_tablero, url_ficha
from .historial import (a_markdown as historial_markdown,
                        anotar as anotar_historial, eventos_de_corrida,
                        gc_tipico, leer as leer_historial, resumen_mercado,
                        ya_visto)
from .models import Arriendo
from .sources.base import Fetcher
from .sources.registry import (FuentesInvalidas, barrer_todas,
                               cargar_fuentes, fuentes_activas)
from .store import Store, deduplicar, leer_tendencia
from .tiempo import ahora_utc

log = logging.getLogger("arriendo")


class ArchivoNoEncontrado(FileNotFoundError):
    """Un archivo que pidió el usuario y no está. Se reporta como config."""


def _deadline(tope_minutos: float):
    """Cuándo hay que dejar de barrer. None si no hay tope.

    Se calcula al empezar y no se recalcula: el punto es acotar la corrida
    entera, no cada fuente.
    """
    from datetime import timedelta

    if not tope_minutos or tope_minutos <= 0:
        return None
    return ahora_utc() + timedelta(minutes=float(tope_minutos))


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

    stats: dict = {"inicio": ahora_utc()}
    try:
        return _correr(args, perfil, fuentes, stats)
    except Exception as e:                                       # noqa: BLE001
        log.exception("La corrida falló")
        stats["error"] = f"{type(e).__name__}: {e}"
        stats["fin"] = ahora_utc()
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


# Motivos de descarte que sacan al aviso del mercado que se está midiendo.
#
# El historial existe para poder decir "un 3D de 120 m² en Vitacura se arrienda
# en $1.5M". Para que ese número signifique algo, tiene que calcularse sobre
# departamentos en arriendo de la zona y nada más: una casa en venta en Maipú
# que apareció en un metabuscador movería la mediana sin ser parte del mercado
# que se está mirando.
#
# Los descartes por precio, superficie o dormitorios NO están en esta lista, y
# es a propósito: esos avisos sí son parte del mercado, solo que no de esta
# búsqueda. Un 3D de 95 m² a $1.7M en Vitacura es exactamente lo que hay que
# contar para saber si el presupuesto es realista.
_FUERA_DEL_MERCADO = {"portal", "operacion", "tipo", "zona"}


def _es_del_mercado(a: Arriendo) -> bool:
    return a.clase_descarte not in _FUERA_DEL_MERCADO


def escribir_historial(destino: Path) -> None:
    """Rearma `alertas/historial.md` desde el log de eventos. Nunca levanta.

    Es una página aparte del tablero porque contesta otra pregunta: el tablero
    dice qué hay HOY, el historial dice cómo viene el mercado. Se abren en
    momentos distintos.
    """
    try:
        eventos = leer_historial(dir_estado())
        if not eventos:
            return
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "historial.md").write_text(historial_markdown(eventos),
                                              encoding="utf-8")
    except OSError as e:
        log.warning("No se pudo escribir alertas/historial.md: %s", e)


def _sin_adornos(url: str) -> str:
    """La URL reducida a lo que identifica al aviso, para poder compararlas."""
    u = (url or "").split("#")[0].split("?")[0].rstrip("/").lower()
    u = re.sub(r"^https?://", "", u)
    return u.removeprefix("www.")


def _no_contradice(a, c) -> bool:
    """¿Lo que dice el candidato es compatible con lo que el aviso ya sabe?"""
    if a.comuna and c.comuna and P.norm(a.comuna) != P.norm(c.comuna):
        return False
    for campo in ("dormitorios", "banos"):
        mio, suyo = getattr(a, campo), getattr(c, campo)
        if mio is not None and suyo is not None and mio != suyo:
            return False
    return True


def _candidatos_propios(a, candidatos: list) -> list:
    """Los candidatos extraídos de la ficha que describen AL aviso.

    Una ficha no trae solo su propiedad: trae el widget de "propiedades
    similares", y cada similar sale del extractor como un candidato más. La
    versión anterior los fusionaba TODOS, y así fue como un departamento de
    goplaceit cuya tarjeta solo decía el título alertó con los dormitorios y
    baños de un vecino del widget — dato inventado en el campo que más pesa.

    La regla: es propio lo que apunta a la MISMA URL del aviso. Si nada
    apunta y la ficha entregó UN solo candidato, se toma como la propiedad
    misma —el caso típico de un JSON-LD sin URL—, pero solo si no contradice
    nada de lo que el aviso ya sabe. Ante la duda, NADA: el aviso escueto ya
    era alertable, y engordarlo con datos ajenos es peor que dejarlo así.
    """
    propios = [c for c in candidatos
               if _sin_adornos(c.url) and _sin_adornos(c.url) == _sin_adornos(a.url)]
    if propios:
        return propios
    if len(candidatos) == 1 and _no_contradice(a, candidatos[0]):
        return candidatos
    return []


def _enriquecer_por_ficha(a_avisar: list, fuentes: list, fetcher,
                          uf: float, perfil: dict, store) -> list:
    """Completa cada alerta con los datos de su propia ficha. Ver paso 6b.

    Nunca levanta: una ficha caída deja el aviso como estaba, que ya era
    alertable. Y el precio NO se toca —_fusionar lo excluye a propósito—: el
    del listado es el vigente, y el de una ficha puede estar desactualizado.
    """
    from .sources.generic import extraer
    from .sources.registry import _bajar
    from .store import _fusionar

    por_id = {f.id: f for f in fuentes}
    salida = []
    for a, motivo in a_avisar:
        fuente = por_id.get(a.source)
        faltan = (a.antiguedad_anos is None or a.gastos_comunes_clp is None
                  or a.m2_totales is None or a.piso is None
                  or a.dormitorios is None or a.banos is None)
        if not (fuente and faltan) or a.extras.get("sin_link_directo"):
            salida.append((a, motivo))
            continue

        try:
            html = _bajar(fetcher, fuente, a.url)
        except Exception:                                        # noqa: BLE001
            html = None
        if not html:
            salida.append((a, motivo))
            continue

        propios = _candidatos_propios(a, extraer(html, a.url, fuente, uf))
        if not propios:
            # La ficha no se reconoció a sí misma (o era en verdad un
            # listado). El aviso sigue tal cual: escueto pero honesto.
            log.debug("Ficha de %s sin candidato propio; no se fusiona",
                      a.codigo)
            salida.append((a, motivo))
            continue
        for candidato in propios:
            _fusionar(a, candidato)
        a.extras["enriquecido_de_ficha"] = True
        S.evaluar(a, perfil)

        if a.descartado:
            # La ficha reveló un incumplimiento —"construido en 1985", 95 m²
            # de verdad— que la tarjeta del listado escondía. Esto es el
            # enriquecimiento haciendo SU mejor trabajo: la alerta que no
            # llegó. Queda registrado y en la bitácora, no en el teléfono.
            log.info("Enriquecido y descartado: %s (%s)", a.codigo,
                     a.motivo_descarte)
            store.registrar(a)
            continue
        salida.append((a, motivo))
    return salida


def _correr(args: argparse.Namespace, perfil: dict, fuentes: list,
            stats: dict) -> int:
    store = Store(dir_estado())
    fetcher = Fetcher(delay=args.delay, timeout=args.timeout)
    # El valor de la UF. No es un detalle: Yapo publica buena parte de su
    # inventario de Vitacura en UF, así que para esa fuente el precio en pesos
    # lo calcula este radar, y de ese cálculo depende si el aviso pasa o no el
    # tope de presupuesto. Ver arriendo/uf.py.
    uf, origen_uf = valor_uf_del_dia(dir_estado())
    stats["valor_uf"], stats["origen_uf"] = uf, origen_uf
    log.info("UF = $%s (%s)", f"{uf:,.2f}".replace(",", "."), origen_uf)

    stats.update({
        "fuentes_consultadas": len(fuentes),
        "fuentes_ok": 0,
        "por_fuente": {},
        "segundos_fuente": {},
        "errores_fuente": {},
        "fuentes_caidas": [],
        "fuentes_parciales": [],
        "fuentes_reventadas": [],
        "hilos": args.hilos,
    })

    # --- 1. barrer ---
    #
    # Con presupuesto de tiempo. Hace falta desde que el catálogo pasó de 20 a
    # 39 fuentes: en el caso normal la corrida toma unos 12 minutos, pero si
    # varias fuentes se cuelgan hasta su timeout el peor caso llega a 54 —
    # contra un job de GitHub Actions que corta a los 30.
    #
    # Y que lo corte Actions es el peor final posible: mata el proceso, así
    # que no se manda ninguna alerta, no se guarda el estado, no se escribe la
    # bitácora, y desde afuera solo se ve una X roja sin explicación. Las
    # fuentes que ya habían entregado se pierden junto con las que colgaron.
    #
    # Cortar nosotros es infinitamente mejor: se avisa lo que se alcanzó a
    # encontrar, se guarda el estado, y la bitácora dice qué quedó sin mirar
    # para que la corrida siguiente empiece por ahí.
    # Y en paralelo, que es lo que hace que el presupuesto alcance: la corrida
    # es casi toda espera de red, así que cuatro fuentes a la vez la bajan de
    # unos 12 minutos a unos 4. El límite de velocidad sigue siendo por host,
    # o sea que ningún portal recibe más carga que antes. Ver `barrer_todas`.
    limite = _deadline(args.tope_minutos)
    sin_mirar: list[str] = []

    def _anotar(fuente, resultado) -> None:
        """Se llama al terminar cada fuente, ya serializado entre hilos."""
        if not resultado.intentada:
            sin_mirar.append(fuente.nombre)
            return

        stats["por_fuente"][fuente.id] = len(resultado.hallazgos)
        stats["segundos_fuente"][fuente.id] = round(resultado.segundos, 1)
        if resultado.ok:
            stats["fuentes_ok"] += 1
        if resultado.error:
            # El motivo se guarda, no solo se loguea. Sin esto la bitácora
            # dice "17 fuentes en cero" y no dice cuáles murieron en un 403,
            # cuáles no resuelven el dominio y cuáles respondieron bien pero
            # con un HTML que el extractor no reconoció — que son tres
            # problemas con tres arreglos distintos.
            stats["errores_fuente"][fuente.id] = resultado.error
            log.warning("  %s: %s", fuente.id, resultado.error)
        if resultado.reventada:
            # Un bug nuestro, no un portal que no contesta. Va aparte porque
            # entre 19 fuentes sin calibrar que traen cero, un error de código
            # pasaría desapercibido para siempre.
            stats["fuentes_reventadas"].append(
                f"{fuente.nombre}: {resultado.error}")
        if resultado.cortada_por_tiempo:
            stats["fuentes_parciales"].append(fuente.nombre)
        log.info("  %s: %d avisos en %.0fs", fuente.id,
                 len(resultado.hallazgos), resultado.segundos)

        # Una fuente que venía entregando y hoy trae cero se rompió; una que
        # nunca entregó todavía no está calibrada. Son problemas distintos y
        # solo el primero merece interrumpir a alguien.
        if not resultado.hallazgos and not fuente.entrega_variable:
            if store.indice and any(e.get("source") == fuente.id
                                    for e in store.indice.values()):
                stats["fuentes_caidas"].append(f"{fuente.nombre}: 0 avisos")

    barridos = barrer_todas(fuentes, fetcher,
                            hilos=args.hilos,
                            seguir_detalles=not args.sin_detalles,
                            valor_uf=uf,
                            limite=limite,
                            al_terminar=_anotar)

    # Los hallazgos se juntan en el orden del catálogo, no en el de llegada:
    # la deduplicación se queda con el primero de la lista y ese ganador no
    # puede depender de qué portal respondió más rápido hoy.
    crudos: list[Arriendo] = []
    for _f, resultado in barridos:
        crudos.extend(resultado.hallazgos)

    if sin_mirar:
        log.warning("Presupuesto de %s min agotado: quedan %d fuentes sin "
                    "mirar (%s)", args.tope_minutos, len(sin_mirar),
                    ", ".join(sin_mirar[:5]) + ("…" if len(sin_mirar) > 5 else ""))
        stats["corte_por_tiempo"] = sin_mirar

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
        # La tendencia de precio se adjunta ANTES de evaluar: es lo que
        # convierte "bajó una vez" en "lleva bajando", y eso decide con qué
        # número se llama a negociar.
        if (historial := store.historial_precio(a)):
            a.extras["historial_precio"] = historial
            if (tendencia := leer_tendencia(historial)):
                a.extras["tendencia_precio"] = tendencia
        S.evaluar(a, perfil)

    candidatos = [a for a in unicos if not a.descartado]
    stats["candidatos"] = len(candidatos)
    log.info("Pasaron los filtros: %d", len(candidatos))

    # --- 5. anotar el historial de búsquedas ---
    #
    # Va ANTES de registrar en el estado, y ese orden es todo: los eventos se
    # calculan comparando lo de hoy contra lo que el estado recuerda, y una
    # vez registrado ya no hay contra qué comparar —todo se vería como si
    # siempre hubiera estado ahí—.
    #
    # El historial guarda lo que el estado olvida: el estado purga a los 120
    # días para no crecer sin fin, y justo lo más valioso —el departamento que
    # estuvo dos meses, bajó tres veces y desapareció— se iba sin dejar rastro.
    # Ver arriendo/historial.py.
    del_mercado = [a for a in unicos if _es_del_mercado(a)]
    eventos = eventos_de_corrida(
        del_mercado, store,
        {fid for fid, n in stats["por_fuente"].items() if n},
        todos=unicos)
    stats["eventos_historial"] = len(eventos)
    stats["nuevos"] = sum(1 for e in eventos if e["evento"] == "alta")
    stats["se_fueron"] = sum(1 for e in eventos if e["evento"] == "baja")

    # Y de vuelta al aviso: un departamento que ya estuvo publicado y volvió no
    # es una novedad, es una oferta que no se arrendó. Si además volvió más
    # barato, es la mejor señal de negociación que da este mercado, y el estado
    # no puede verla porque para entonces ya la olvidó.
    previos = leer_historial(dir_estado())
    for a in candidatos:
        if (antes := ya_visto(previos, a)):
            a.extras["ya_estuvo"] = antes

    # Gemelos: dos candidatos con el MISMO canon exacto, dormitorios, baños y
    # comuna que la deduplicación no logró juntar (uno sin dirección, por
    # ejemplo) son probablemente el mismo inmueble. No se fusionan —dos
    # unidades gemelas de la misma torre al mismo precio existen— pero se
    # ETIQUETAN con el código del otro, que es el distintivo que pidió el
    # usuario: si dos mensajes dicen "posible mismo que #X", se miran juntos.
    por_firma: dict[tuple, list[Arriendo]] = {}
    for a in candidatos:
        if a.arriendo_clp and a.dormitorios:
            firma = (P.norm(a.comuna), a.arriendo_clp, a.dormitorios, a.banos)
            por_firma.setdefault(firma, []).append(a)
    for grupo in por_firma.values():
        if len(grupo) > 1:
            for a in grupo:
                a.extras["gemelos"] = [x.codigo for x in grupo if x is not a]

    # --- 6. decidir a quién avisar ---
    a_avisar: list[tuple[Arriendo, str]] = []
    # Orden = (puntaje, confianza). El desempate por confianza es lo que
    # decide cuál de dos avisos igual de buenos se mira primero: el que
    # SABEMOS que es bueno le gana al que PARECE bueno. Ver `scoring.orden`.
    for a in sorted(candidatos, key=S.orden, reverse=True):
        if not S.debe_alertar(a, perfil):
            continue
        if store.es_nuevo(a) or store.envio_pendiente(a):
            # Nunca visto es LA noticia; y un envío que falló ayer es una
            # entrega pendiente, no noticia vieja: se reintenta.
            motivo = ""
        else:
            # Ya visto —avisado o no—: solo alerta si CAMBIÓ (baja de canon,
            # umbral de días publicado). Pedido del 18-08: "la corrida de
            # todos los días que sea solo de nuevos o modificaciones".
            #
            # Antes, lo visto-pero-no-avisado seguía en cola y cada corrida
            # mandaba los 8 siguientes del acumulado: cuatro días de avisos
            # viejos disfrazados de novedad. Un aviso que el radar conoce
            # hace tres corridas no es una novedad por no haber cabido en el
            # tope; si amerita mirarse, está en el tablero con su puntaje.
            motivo = store.cambio_relevante(a, perfil)
            if not motivo:
                continue
        a_avisar.append((a, motivo))

    tope = int((perfil.get("alertas") or {}).get("max_por_corrida", 8))
    sobrantes: list[Arriendo] = []
    if len(a_avisar) > tope:
        # Las de mayor puntaje primero; el resto queda en el tablero y alerta
        # en la corrida siguiente. Sin tope, la primera corrida contra
        # portales de arriendo manda cuarenta mensajes seguidos, porque trae
        # inventario acumulado y no novedades del día.
        #
        # Pero el recorte ya no es silencioso: los que no cupieron van en UN
        # mensaje índice (ver el paso 7). Si el noveno era justo el bueno, la
        # única forma de saberlo era abrir el tablero por iniciativa propia.
        log.info("%d avisos por mandar, tope %d: se posponen %d",
                 len(a_avisar), tope, len(a_avisar) - tope)
        sobrantes = [a for a, _m in a_avisar[tope:]]
        a_avisar = a_avisar[:tope]

    # --- 6b. enriquecer desde la ficha propia, ANTES de avisar ---
    #
    # La auditoría de las 16 alertas reales (18-08) midió el problema: 15 no
    # traían antigüedad —el criterio sí-o-sí del usuario—, la mayoría tampoco
    # GC ni m² totales… y 14 tenían ficha propia donde esos datos VIVEN. El
    # radar mandaba el link con la respuesta adentro sin leerla.
    #
    # Son a lo más `tope` fetches (8) por corrida, solo de los que van a
    # alertar: el costo es un minuto y el beneficio es doble. La alerta sale
    # completa, y el filtro duro trabaja con datos: si la ficha revela 40
    # años, el aviso se descarta acá en vez de llegar al teléfono.
    a_avisar = _enriquecer_por_ficha(a_avisar, fuentes, fetcher, uf,
                                     perfil, store)

    # --- 7. avisar ---
    # La mediana del mercado sale del historial de búsquedas y se le pasa al
    # canal para que el aviso pueda decir "12% bajo la mediana" y no solo el
    # precio. Es la diferencia entre un dato y un juicio: $1.490.000 no dice
    # si es caro; comparado contra lo que efectivamente se publica en la zona,
    # sí. Acotada a la comuna núcleo, que es donde el número significa algo.
    nucleo = ((perfil.get("comunas") or {}).get("nucleo") or [""])[0]
    mediana = resumen_mercado(previos, comuna=nucleo).get("precio_mediano") or 0
    stats["mediana_mercado"] = mediana

    telegram = Telegram(
        dry_run=args.dry_run,
        caminable_km=float((perfil.get("radio_km") or {}).get("preferente") or 0),
        ancla=(perfil.get("ancla") or {}).get("nombre", ""),
        tope_arriendo=S.tope_arriendo(perfil)[0] or 0.0,
        mediana_mercado=float(mediana),
        # El estimador de GC cierra sobre el historial ya leído: para cada
        # aviso sin gastos comunes, el mensaje puede decir el típico de la
        # zona según su superficie. Ver historial.gc_tipico.
        gc_tipico=lambda m2: gc_tipico(previos, m2),
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
            store.registrar(a, avisado=False, fallido=True)

    for a in unicos:
        if not any(a is x for x, _ in a_avisar):
            store.registrar(a)

    stats["avisados"] = enviados

    # El índice de los que calificaron y no cupieron. No los marca como
    # avisados a propósito: su aviso completo llega en las corridas
    # siguientes; esto solo evita que el tope sea un recorte invisible.
    if enviados and sobrantes:
        telegram.enviar(mensaje_sobrantes(sobrantes))

    # El cierre del ciclo: los departamentos AVISADOS que dejaron de
    # aparecer en todos los portales. De los que nunca se avisaron nadie
    # está esperando noticias, así que no se molesta por ellos.
    despedidas = [e for e in eventos
                  if e.get("evento") == "baja" and e.get("avisado")]
    if despedidas:
        telegram.enviar(mensaje_bajas(despedidas))

    telegram.resumen(stats, enviados, marca_dir=dir_estado())

    # --- 8. guardar ---
    #
    # El historial se escribe acá y no en el paso 5 por la misma razón que el
    # resto: una corrida en seco no puede dejar rastro. Los eventos ya estaban
    # calculados; lo único que se posterga es escribirlos.
    if not args.dry_run:
        anotar_historial(eventos, dir_estado())
        # El resumen del mercado se rearma sobre TODO el historial, no solo
        # sobre los eventos de hoy: es lo que convierte seis meses de corridas
        # en "el canon mediano de un 3D en Vitacura son $1.48M".
        escribir_historial(dir_alertas())
        store.purgar()
        store.guardar(unicos)
        escribir_tablero(unicos, dir_alertas(), perfil)

    stats["fin"] = ahora_utc()
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

    ruta = Path(args.archivo)
    if not ruta.exists():
        # `demo` es el comando con el que alguien prueba el radar por primera
        # vez, casi siempre escribiendo la ruta a mano. Un traceback acá es la
        # peor primera impresión posible y no dice qué hacer.
        raise ArchivoNoEncontrado(
            f"No existe el archivo: {ruta}\n\n"
            "  `demo` corre el filtrado sobre un HTML que ya tienes guardado.\n"
            "  Para conseguir uno: abre el portal en el navegador, guarda la\n"
            "  página (Ctrl+S) y pásale esa ruta.\n\n"
            "  Para probar sin bajar nada, el repositorio trae ejemplos:\n"
            "    python -m arriendo demo tests/fixtures/portal_tarjetas.html")

    html = ruta.read_text(encoding="utf-8", errors="replace")
    fuente = FuenteConfig(id="demo", nombre="Demo", urls=[args.url])

    hallazgos = deduplicar(extraer(html, args.url, fuente, valor_uf_del_dia(dir_estado())[0]))
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


def _pesos_tope() -> str:
    """El tope del perfil, en texto. Se lee del YAML y no se escribe a mano:
    un número hardcodeado en un mensaje de usuario se queda viejo en silencio
    la primera vez que alguien cambia el presupuesto."""
    try:
        tope = S.tope_arriendo(cargar_perfil())[0] or 0
    except Exception:                                            # noqa: BLE001
        return "tu tope"
    return f"${tope:,.0f}".replace(",", ".")


def probar_aviso(args: argparse.Namespace) -> int:
    """Manda un mensaje de prueba por Telegram y dice si llegó.

    Existe porque el modo más caro de fallar es encontrar el departamento y no
    conseguir avisar: desde afuera se ve igual que no haber encontrado nada.
    Esto cierra el asunto en dos segundos en vez de esperar una corrida.
    """
    from .alerts.telegram import VAR_CHAT_ID, VAR_GENERICA, VAR_TOKEN

    t = Telegram(dry_run=args.dry_run)
    if not t.configurado and not args.dry_run:
        print("✗ Telegram sin configurar.")
        print()
        print(f"  Faltan los secrets {VAR_TOKEN} y {VAR_CHAT_ID}.")
        print("  El paso a paso está en AVISOS.md y se hace desde el teléfono.")
        if os.environ.get(VAR_GENERICA):
            print()
            print(f"  Ojo: tienes {VAR_GENERICA} configurada, pero es la del")
            print("  radar de remates. Este radar usa un bot DISTINTO a")
            print("  propósito, así que no la reutiliza.")
        return 1

    ok = t.enviar(
        "✅ <b>Radar de Arriendos</b>\n\n"
        "Prueba de conexión. Si estás leyendo esto, las alertas van a llegar "
        "a esta conversación.\n\n"
        "Busca: departamentos en Vitacura o a 1,2 km del Sport Francés, más "
        f"de 100 m² totales, 3+ dormitorios, hasta {_pesos_tope()}."
    )
    print("✓ Mensaje entregado" if ok else "✗ Telegram no confirmó la entrega")
    return 0 if ok else 1


def historial(args: argparse.Namespace) -> int:
    """Qué dice el historial de búsquedas sobre el mercado.

    Existe como comando aparte porque la pregunta "¿cuánto vale de verdad un
    3D en Vitacura?" no se hace en el mismo momento que "¿qué salió hoy?", y
    porque la respuesta no depende de correr nada: sale de lo ya guardado.
    """
    from .historial import contar_por_mes, resumen_mercado

    eventos = leer_historial(dir_estado())
    if not eventos:
        print("Todavía no hay historial. Se llena solo con las corridas.")
        return 0

    r = resumen_mercado(eventos, dias=args.dias, comuna=args.comuna)
    donde = f" en {args.comuna}" if args.comuna else ""
    print(f"\nEl mercado{donde}, últimos {args.dias} días")
    print(f"  {len(eventos)} eventos guardados en total\n")
    print(f"  Departamentos nuevos      {r['nuevos']}")
    print(f"  Dejaron de publicarse     {r['se_fueron']}")
    print(f"  Cambios de precio         {r['cambios_de_precio']} "
          f"({r['rebajas']} a la baja)")

    # Los None se imprimen como un guión y no se omiten: que falte el dato es
    # información —significa que todavía no hay suficientes avisos— y ocultar
    # la línea haría pensar que el radar no lo mide.
    def _linea(etiqueta: str, valor, formato=str):
        print(f"  {etiqueta:<25} {formato(valor) if valor else '— (pocos datos)'}")

    _linea("Canon mediano", r["precio_mediano"],
           lambda v: f"${v:,.0f}".replace(",", "."))
    _linea("Canon mediano por m²", r["precio_m2_mediano"],
           lambda v: f"${v:,.0f}".replace(",", "."))
    _linea("Días antes de irse", r["dias_hasta_arrendarse"], lambda v: f"{v}")
    _linea("Rebaja mediana", r["rebaja_mediana_pct"], lambda v: f"{v}%")

    if (por_mes := contar_por_mes(eventos)):
        print("\n  Avisos nuevos por mes")
        for mes, n in list(por_mes.items())[-12:]:
            print(f"    {mes}  {'▪' * min(n, 40)} {n}")
    print()
    return 0


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
    fetcher = Fetcher(delay=args.delay, timeout=args.timeout)
    uf = valor_uf_del_dia(dir_estado())[0]

    destino = Path(args.fixtures)
    destino.mkdir(parents=True, exist_ok=True)

    nucleo = {c.lower() for c in (perfil.get("comunas") or {}).get("nucleo") or []}
    L: list[str] = ["# Calibración de fuentes", "",
                    f"Corrida: {ahora_utc():%d-%m-%Y %H:%M} UTC", "",
                    "| Fuente | URL | Estado | Avisos | En zona | Pasan filtros |",
                    "|---|---|---|---|---|---|"]
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
            avisos.extend(extraer(html, url, fuente, uf))

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
            L.append(f"| {fuente.nombre} | {_marca_url(fuente)} | {estado} | "
                     f"{len(avisos)} | {len(en_zona)} | {len(pasan)} |")
        else:
            estado = "⚠️ cero resultados" if fetcher.ultimo_motivo == "" else estado
            L.append(f"| {fuente.nombre} | {_marca_url(fuente)} | {estado} | 0 | 0 | 0 |")
            sin_motivo = "la página respondió pero no se reconoció ningún aviso"
            # Que la URL esté confirmada cambia el diagnóstico por completo, y
            # sin decirlo los dos casos se ven idénticos en el reporte.
            pista = (
                "la URL está confirmada, así que lo más probable es que el "
                "sitio arme la página con JavaScript o haya cambiado su HTML"
                if fuente.url_confirmada else
                "**la URL no está confirmada**: lo más probable es que esté "
                "mala. Ábrela en el teléfono y copia la que funciona")
            detalle.append(f"- **{fuente.nombre}** (`{fuente.id}`): "
                           f"{motivo or sin_motivo}. {pista}.")

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

    L.append("")
    L.append("`✔︎` = URL confirmada contra el sitio · `?` = ruta por calibrar, "
             "apunta a la raíz para dejar el HTML guardado.")
    L.append("")

    reporte = Path(args.reporte)
    reporte.write_text("\n".join(L), encoding="utf-8")
    print(f"\nReporte en {reporte}")
    return 0


def _marca_url(fuente) -> str:
    """Si la URL de esta fuente estaba confirmada o era una apuesta.

    Va en el reporte porque cambia el diagnóstico por completo: una fuente
    CONFIRMADA que entrega cero se rompió o cambió su HTML; una POR CALIBRAR
    que entrega cero lo más probable es que tenga la ruta mala. Sin la
    columna, las dos filas se ven idénticas.
    """
    return "✔︎" if fuente.url_confirmada else "?"


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
    c.add_argument("--tope-minutos", type=float, default=18.0,
                   help="presupuesto de tiempo para barrer las fuentes. Al "
                        "agotarse se sigue con lo encontrado en vez de dejar "
                        "que Actions mate el job. 0 = sin tope")
    c.add_argument("--timeout", type=int, default=20,
                   help="segundos de espera por petición")
    c.add_argument("--hilos", type=int, default=4,
                   help="cuántas fuentes barrer a la vez. El límite de "
                        "velocidad sigue siendo por sitio, así que subirlo no "
                        "carga más a ningún portal. 1 = en serie")
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
    c.add_argument("--timeout", type=int, default=20)
    c.set_defaults(func=calibrar)

    c = sub.add_parser("historial", parents=[comun],
                       help="qué dice el historial sobre el mercado")
    c.add_argument("--dias", type=int, default=90,
                   help="ventana a resumir")
    c.add_argument("--comuna", default="",
                   help="acotar a una comuna, p. ej. Vitacura")
    c.set_defaults(func=historial)

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
    except (PerfilInvalido, FuentesInvalidas, ArchivoNoEncontrado) as e:
        # Errores de configuración: el mensaje es para una persona que está
        # editando un YAML desde el teléfono, no un stack trace.
        print(f"\n✗ {e}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
