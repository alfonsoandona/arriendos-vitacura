"""Tests del barrido en paralelo.

El paralelismo es la clase de cambio que "funciona" en la prueba manual y
falla en producción una vez cada veinte corridas. Los cuatro riesgos reales
son:

1. Que deje de ser educado con los portales (varios requests simultáneos al
   mismo sitio).
2. Que el resultado deje de ser determinista, porque la deduplicación se queda
   con el primer aviso de la lista y esa lista pasaría a depender de qué
   portal respondió más rápido hoy.
3. Que el estado compartido del Fetcher se pise entre hilos, y una fuente
   quede reportada con el error de otra.
4. Que el presupuesto de tiempo deje de cortar.

Cada uno tiene su test acá. Son tests con hilos de verdad, no con mocks del
pool: un candado mal puesto solo se nota corriendo.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest

from arriendo.models import Arriendo
from arriendo.sources import registry
from arriendo.sources.base import Fetcher, FuenteConfig, ResultadoFuente


def _fuentes(n: int) -> list[FuenteConfig]:
    return [FuenteConfig(id=f"f{i}", nombre=f"Fuente {i}",
                         urls=[f"https://sitio{i}.cl/arriendo"])
            for i in range(n)]


# ---------------------------------------------------------------------------
# Que efectivamente corra en paralelo
# ---------------------------------------------------------------------------

def test_las_fuentes_se_barren_a_la_vez(monkeypatch):
    """Ocho fuentes de 0,2s cada una: en serie 1,6s, con 4 hilos ~0,4s.

    Sin este test, `barrer_todas` podría estar corriendo en serie por un bug
    del pool y nadie lo notaría: el resultado sería idéntico, solo lento.
    """
    def lento(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        time.sleep(0.2)
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(registry, "barrer", lento)

    partida = time.monotonic()
    salida = registry.barrer_todas(_fuentes(8), Fetcher(delay=0), hilos=4)
    transcurrido = time.monotonic() - partida

    assert len(salida) == 8
    assert transcurrido < 1.0, f"parece estar corriendo en serie ({transcurrido:.2f}s)"


def test_hilos_uno_es_serie(monkeypatch):
    """`--hilos 1` tiene que ser el camino serial de verdad.

    Es la salida de emergencia si el paralelismo resulta problemático con
    algún portal, así que no puede ser "paralelo con un hilo".
    """
    simultaneos = []
    activos = 0
    candado = threading.Lock()

    def registrar(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        nonlocal activos
        with candado:
            activos += 1
            simultaneos.append(activos)
        time.sleep(0.02)
        with candado:
            activos -= 1
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(registry, "barrer", registrar)
    registry.barrer_todas(_fuentes(5), Fetcher(delay=0), hilos=1)

    assert max(simultaneos) == 1


# ---------------------------------------------------------------------------
# El orden, que es lo que hace determinista a la deduplicación
# ---------------------------------------------------------------------------

def test_el_resultado_va_en_orden_de_catalogo(monkeypatch):
    """Aunque las fuentes terminen al revés.

    Importa porque la deduplicación entre portales fusiona los avisos
    repetidos y el que sobrevive es el primero de la lista. Con orden de
    llegada, el link que aparece en el Telegram lo decidiría una carrera de
    red: la misma corrida dos veces daría fichas distintas.
    """
    def al_reves(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        # La primera del catálogo demora más que la última.
        time.sleep(0.05 * (5 - int(fuente.id[1:])))
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(registry, "barrer", al_reves)

    salida = registry.barrer_todas(_fuentes(5), Fetcher(delay=0), hilos=5)
    assert [f.id for f, _ in salida] == ["f0", "f1", "f2", "f3", "f4"]


def test_los_hallazgos_conservan_el_orden_del_catalogo(monkeypatch):
    """Lo mismo, visto desde los avisos: es lo que consume la deduplicación."""
    def con_aviso(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        time.sleep(0.05 * (3 - int(fuente.id[1:])))
        return ResultadoFuente(
            fuente_id=fuente.id, urls_ok=1,
            hallazgos=[Arriendo(source=fuente.id, url=f"https://{fuente.id}.cl/1",
                                title="Depto")])

    monkeypatch.setattr(registry, "barrer", con_aviso)

    salida = registry.barrer_todas(_fuentes(3), Fetcher(delay=0), hilos=3)
    avisos = [a for _f, r in salida for a in r.hallazgos]
    assert [a.source for a in avisos] == ["f0", "f1", "f2"]


# ---------------------------------------------------------------------------
# Que siga siendo educado
# ---------------------------------------------------------------------------

def test_un_mismo_host_sigue_recibiendo_un_request_a_la_vez():
    """La regla que no se puede romper por ir más rápido.

    El paralelismo es entre PORTALES. Sobre un mismo host el comportamiento
    tiene que seguir siendo el serial: uno a la vez y espaciado. Si esto se
    rompe, el radar pasa de ser un cliente educado a parecer un ataque, y el
    portal bloquea la IP de Actions.
    """
    fetcher = Fetcher(delay=0, respetar_robots=False)
    simultaneos, activos = [], 0
    candado = threading.Lock()

    class RespuestaFalsa:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html></html>"
        content = b"<html></html>"

    class SesionFalsa:
        def get(self, url, timeout=None):
            nonlocal activos
            with candado:
                activos += 1
                simultaneos.append(activos)
            time.sleep(0.03)
            with candado:
                activos -= 1
            return RespuestaFalsa()

    # La sesión es por hilo, así que se sustituye la propiedad de la clase.
    monkeypatch_sesion(fetcher, SesionFalsa())

    hilos = [threading.Thread(target=fetcher.get,
                              args=("https://mismositio.cl/pagina",))
             for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert len(simultaneos) == 6, "los seis requests tienen que haberse hecho"
    assert max(simultaneos) == 1, "dos requests a la vez al mismo host"


def test_hosts_distintos_no_se_bloquean_entre_si():
    """Y el reverso: el candado es por host, no global.

    Si fuera global, el paralelismo no serviría para nada: seis fuentes
    esperando el mismo candado son seis fuentes en serie con más código.
    """
    fetcher = Fetcher(delay=0, respetar_robots=False)
    maximo, activos = 0, 0
    candado = threading.Lock()

    class RespuestaFalsa:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html></html>"
        content = b"<html></html>"

    class SesionFalsa:
        def get(self, url, timeout=None):
            nonlocal activos, maximo
            with candado:
                activos += 1
                maximo = max(maximo, activos)
            time.sleep(0.05)
            with candado:
                activos -= 1
            return RespuestaFalsa()

    monkeypatch_sesion(fetcher, SesionFalsa())

    hilos = [threading.Thread(target=fetcher.get,
                              args=(f"https://sitio{i}.cl/pagina",))
             for i in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert maximo == 4


def test_el_delay_por_host_se_respeta_entre_hilos():
    """Dos hilos sobre el mismo host: el segundo espera el delay completo."""
    fetcher = Fetcher(delay=0.2, respetar_robots=False)

    class RespuestaFalsa:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html></html>"
        content = b"<html></html>"

    class SesionFalsa:
        def get(self, url, timeout=None):
            return RespuestaFalsa()

    monkeypatch_sesion(fetcher, SesionFalsa())

    partida = time.monotonic()
    hilos = [threading.Thread(target=fetcher.get,
                              args=("https://mismositio.cl/p",))
             for _ in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    # Tres requests con 0,2s de separación: al menos 0,4s.
    assert time.monotonic() - partida >= 0.35


# ---------------------------------------------------------------------------
# Estado del Fetcher que no se puede compartir entre hilos
# ---------------------------------------------------------------------------

def test_cada_hilo_tiene_su_sesion():
    """`requests.Session` no promete ser thread-safe.

    Y su forma de fallar sería silenciosa y grave: el HTML de un portal
    parseado como si fuera de otro.
    """
    fetcher = Fetcher(delay=0)
    sesiones = []

    def anotar():
        sesiones.append(id(fetcher.session))

    hilos = [threading.Thread(target=anotar) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert len(set(sesiones)) == 4
    # Y dentro de un mismo hilo es siempre la misma: si no, se perderían las
    # cookies y el pool de conexiones entre páginas de un mismo portal.
    assert id(fetcher.session) == id(fetcher.session)


def test_el_motivo_del_fallo_no_se_cruza_entre_hilos():
    """Sin esto, una fuente aparecería reportada con el error de otra.

    Es el bug que más caro sale en el reporte de calibración: manda a arreglar
    la URL equivocada.
    """
    fetcher = Fetcher(delay=0)
    visto: dict[str, str] = {}
    listos = threading.Barrier(2)

    def hilo(nombre: str, motivo: str):
        fetcher.ultimo_motivo = motivo
        listos.wait()          # los dos escriben antes de que ninguno lea
        visto[nombre] = fetcher.ultimo_motivo

    a = threading.Thread(target=hilo, args=("a", "HTTP 403"))
    b = threading.Thread(target=hilo, args=("b", "el dominio no existe"))
    a.start()
    b.start()
    a.join()
    b.join()

    assert visto == {"a": "HTTP 403", "b": "el dominio no existe"}


# ---------------------------------------------------------------------------
# Presupuesto de tiempo
# ---------------------------------------------------------------------------

def test_con_el_presupuesto_vencido_no_se_mira_ninguna(monkeypatch):
    def nunca(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        pytest.fail("no debería haberse barrido ninguna fuente")

    monkeypatch.setattr(registry, "barrer", nunca)

    salida = registry.barrer_todas(_fuentes(6), Fetcher(delay=0), hilos=4,
                                   limite=datetime.utcnow())
    assert all(not r.intentada for _f, r in salida)


def test_las_que_no_se_alcanzaron_a_mirar_se_distinguen_de_las_vacias(monkeypatch):
    """"No se miró" y "se miró y no había nada" piden cosas distintas.

    La primera no es una fuente caída y no puede contarse como tal: mañana
    hay que volver a mirarla, no hay que arreglarla.
    """
    limite = datetime.utcnow() + timedelta(seconds=0.15)

    def lento(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        time.sleep(0.1)
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(registry, "barrer", lento)

    salida = registry.barrer_todas(_fuentes(8), Fetcher(delay=0), hilos=2,
                                   limite=limite)
    intentadas = [r for _f, r in salida if r.intentada]
    saltadas = [r for _f, r in salida if not r.intentada]

    assert intentadas, "algunas alcanzaron a mirarse"
    assert saltadas, "y otras se saltaron por el presupuesto"
    assert len(intentadas) + len(saltadas) == 8


def test_barrer_corta_entre_paginas_con_el_presupuesto_vencido(monkeypatch):
    """Una sola fuente lenta no puede llevarse el presupuesto de la corrida.

    Con paginación y fichas, una fuente demora varios minutos sola. Sin este
    corte, la que arranca justo antes del límite lo pasa de largo entera.
    """
    fuente = FuenteConfig(id="lenta", nombre="Lenta",
                          urls=["https://lenta.cl/a"],
                          paginacion={"paginas": 5, "parametro": "page"})

    r = registry.barrer(fuente, Fetcher(delay=0), limite=datetime.utcnow())
    assert r.cortada_por_tiempo
    assert r.urls_ok == 0


# ---------------------------------------------------------------------------
# Robustez
# ---------------------------------------------------------------------------

def test_una_fuente_que_revienta_no_se_lleva_a_las_otras(monkeypatch):
    def a_veces(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        if fuente.id == "f2":
            raise RuntimeError("boom")
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(registry, "barrer", a_veces)

    salida = dict((f.id, r) for f, r in
                  registry.barrer_todas(_fuentes(5), Fetcher(delay=0), hilos=3))

    assert len(salida) == 5
    assert salida["f2"].reventada and "boom" in salida["f2"].error
    assert all(salida[f"f{i}"].ok for i in (0, 1, 3, 4))


def test_el_callback_se_llama_una_vez_por_fuente(monkeypatch):
    """Y serializado: las estadísticas de la corrida se escriben ahí."""
    def rapida(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(registry, "barrer", rapida)

    llamadas: list[str] = []
    contador = {"n": 0}

    def al_terminar(fuente, resultado):
        # Sin serializar, este patrón perdería incrementos.
        contador["n"] = contador["n"] + 1
        llamadas.append(fuente.id)

    registry.barrer_todas(_fuentes(20), Fetcher(delay=0), hilos=8,
                          al_terminar=al_terminar)

    assert sorted(llamadas) == sorted(f"f{i}" for i in range(20))
    assert contador["n"] == 20


def test_sin_fuentes_no_revienta():
    assert registry.barrer_todas([], Fetcher(delay=0), hilos=4) == []


def test_el_tope_de_navegadores_no_se_supera(monkeypatch):
    """Cada Chromium se come 300 MB: cuatro a la vez matan al runner.

    Y morir por falta de memoria es el peor final posible: mata el proceso,
    así que no se manda ninguna alerta ni se escribe la bitácora.
    """
    fetcher = Fetcher(delay=0, respetar_robots=False)
    maximo, activos = 0, 0
    candado = threading.Lock()

    def falso_navegador(url, acciones=None, timeout_ms=None):
        nonlocal activos, maximo
        with candado:
            activos += 1
            maximo = max(maximo, activos)
        time.sleep(0.05)
        with candado:
            activos -= 1
        return "<html></html>"

    import arriendo.sources.navegador as nav
    monkeypatch.setattr(nav, "bajar_con_navegador", falso_navegador)

    fuentes = [FuenteConfig(id=f"n{i}", nombre=f"Nav {i}", motor="navegador",
                            ignorar_robots=True,
                            urls=[f"https://nav{i}.cl/"])
               for i in range(6)]

    registry.barrer_todas(fuentes, fetcher, hilos=6)
    assert maximo <= registry.TOPE_NAVEGADOR


# ---------------------------------------------------------------------------
# Utilidad
# ---------------------------------------------------------------------------

def monkeypatch_sesion(fetcher: Fetcher, sesion) -> None:
    """Le pone la MISMA sesión falsa a todos los hilos.

    `Fetcher.session` es una propiedad con almacenamiento por hilo, así que no
    se puede asignar el atributo y ya. Y acá interesa justo lo contrario de lo
    que hace la propiedad: una sola sesión compartida, porque lo que se está
    midiendo es cuántos requests coinciden en el tiempo y hay que contarlos
    todos en un mismo lugar.

    Se logra cambiando la clase de la instancia por una subclase de un solo
    uso, que es lo mínimo que hay que tocar.
    """
    fetcher.__class__ = type("FetcherDePrueba", (Fetcher,),
                             {"session": property(lambda self: sesion)})
