"""Infraestructura común de las fuentes: HTTP educado y contrato de scraping."""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from ..models import Arriendo

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (compatible; radar-arriendos/1.0; uso personal, busqueda de "
    "arriendo; +https://github.com/alfonsoandona/arriendos-vitacura)"
)


@dataclass
class FuenteConfig:
    """Definición declarativa de una fuente.

    Se mantiene como datos y no como subclases porque la mayoría de las
    fuentes solo difieren en URLs: el parseo lo hace el extractor genérico.
    Agregar un portal tiene que ser editar un YAML desde el teléfono, no
    escribir una clase.
    """

    id: str
    nombre: str
    urls: list[str]
    # Qué publica esta fuente cuando el aviso no lo dice. Un listado ya
    # filtrado a arriendo no repite la palabra en cada tarjeta.
    operacion_default: str = "arriendo"
    # "requests" (un GET simple) o "navegador" (Chromium ejecutando el JS).
    # El navegador es mucho más caro: solo donde el sitio lo exige.
    motor: str = "requests"
    # Pasos a ejecutar en el navegador antes de leer: elegir una comuna,
    # apretar un botón, esperar un elemento.
    acciones: list = field(default_factory=list)
    # Cómo avanzar de página, si el sitio pagina.
    paginacion: dict = field(default_factory=dict)
    # Fichas a seguir después de leer el listado, cuando el detalle vive en su
    # propia página. `patron` reconoce el enlace y `max` topea por corrida.
    #
    # En arriendo rinde más que en remates: los gastos comunes, la superficie
    # total y el año casi nunca están en la tarjeta del listado y casi siempre
    # están en la ficha. Sin seguirlas, el radar puntúa a ciegas.
    detalle: dict = field(default_factory=dict)
    # Selector CSS de la tarjeta de resultado. Opcional: si está vacío se usa
    # la detección heurística. Se completa al calibrar contra HTML real.
    selector_card: str = ""
    selector_link: str = "a"
    activa: bool = True
    # Saltarse robots.txt EN ESTA FUENTE. Por defecto no: la regla es del
    # sitio y normalmente hay que respetarla.
    ignorar_robots: bool = False
    # Fuentes cuyo inventario legítimamente se vacía. Sin la marca, su 0 se ve
    # igual que el de una fuente que se cayó, y eso hace perder la tarde
    # revisando lo que está bien mientras lo que sí se rompió pasa
    # desapercibido.
    entrega_variable: bool = False
    # Si la URL salió de ver el sitio (aunque sea por buscador) o es una
    # suposición del patrón que usa el portal.
    #
    # No cambia el comportamiento: cambia qué dice el reporte de calibración.
    # Una fuente confirmada que entrega cero está ROTA; una sin confirmar que
    # entrega cero probablemente solo tiene la URL mal, y son dos problemas
    # con arreglos distintos.
    url_confirmada: bool = False
    notas: str = ""


class Fetcher:
    """Cliente HTTP con reintentos, límite de velocidad y respeto por robots.txt.

    Es seguro compartir UNA instancia entre varios hilos, y esa es la forma en
    que se usa: la corrida barre varias fuentes en paralelo (ver
    `registry.barrer_todas`) con un solo Fetcher.

    Compartirlo no es un detalle de implementación, es lo que hace que el
    paralelismo siga siendo educado. Las tres cosas que este cliente cuida
    —el espaciado entre requests, la caché de robots.txt y el motivo del
    último fallo— dejarían de funcionar si cada hilo tuviera su propio
    cliente: dos hilos que caen sobre el mismo host pedirían al mismo tiempo
    sin respetar el delay, y robots.txt se bajaría una vez por hilo.

    Cómo se logra, en tres piezas:

    1. Un candado POR HOST (reentrante). Se toma durante todo el request, así
       que sobre un mismo host el comportamiento es exactamente el serial de
       antes: uno a la vez y con `delay` segundos entre medio. El paralelismo
       ocurre entre hosts distintos, que es donde no molesta a nadie.
    2. Una sesión de `requests` POR HILO. `requests.Session` no promete ser
       thread-safe, y el modo en que falla —respuestas cruzadas entre hilos—
       sería un desastre silencioso: el HTML de un portal parseado como si
       fuera de otro.
    3. `ultimo_motivo` POR HILO. Es un dato del request recién hecho, y con un
       solo atributo compartido el reporte de calibración le atribuiría a una
       fuente el error de otra.
    """

    def __init__(self, delay: float = 2.0, timeout: int = 30,
                 respetar_robots: bool = True):
        self.delay = delay
        self.timeout = timeout
        self.respetar_robots = respetar_robots
        self._ultimo_por_host: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._candados: dict[str, threading.RLock] = {}
        self._candado_mapa = threading.Lock()
        self._hilo = threading.local()

    # -- estado por hilo --------------------------------------------------
    @property
    def session(self) -> requests.Session:
        """La sesión de ESTE hilo, creada al primer uso.

        Se comparte el Fetcher pero no la sesión: ver el docstring de la clase.
        """
        s = getattr(self._hilo, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({
                "User-Agent": UA,
                "Accept-Language": "es-CL,es;q=0.9",
            })
            self._hilo.session = s
        return s

    @property
    def ultimo_motivo(self) -> str:
        return getattr(self._hilo, "motivo", "")

    @ultimo_motivo.setter
    def ultimo_motivo(self, valor: str) -> None:
        self._hilo.motivo = valor

    def _candado_host(self, host: str) -> threading.RLock:
        """El candado de un host, creado una sola vez.

        Reentrante a propósito: `_pedir` lo toma y adentro llama a
        `_puede_fetch`, que lo vuelve a tomar para bajar robots.txt.
        """
        with self._candado_mapa:
            candado = self._candados.get(host)
            if candado is None:
                candado = self._candados[host] = threading.RLock()
            return candado

    # -- robots -----------------------------------------------------------
    def _puede_fetch(self, url: str, ignorar: bool = False) -> bool:
        if ignorar or not self.respetar_robots:
            return True

        host = urlparse(url).netloc
        with self._candado_host(host):
            return self._robots_permite(host, url)

    def _robots_permite(self, host: str, url: str) -> bool:
        if host not in self._robots:
            rp = robotparser.RobotFileParser()
            rp.set_url(urljoin(f"https://{host}", "/robots.txt"))
            try:
                # No se usa rp.read(): urllib no pasa por la sesión ni respeta
                # timeouts, y un robots.txt colgado bloquearía la corrida.
                r = self.session.get(urljoin(f"https://{host}", "/robots.txt"),
                                     timeout=10)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp = None  # sin robots.txt legible, se asume permitido
            except requests.RequestException:
                rp = None
            self._robots[host] = rp

        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(UA, url)

    def puede_fetch(self, url: str, ignorar: bool = False) -> bool:
        """Chequeo de robots.txt reutilizable fuera del camino HTTP.

        El motor de navegador no pasa por `_pedir`, así que sin esto se
        saltaría la regla del sitio solo por usar otro cliente.
        """
        return self._puede_fetch(url, ignorar)

    # -- fetch ------------------------------------------------------------
    def _pedir(self, url: str, reintentos: int,
               ignorar_robots: bool = False) -> requests.Response | None:
        # El motivo del fallo cambia qué hay que hacer: un 403 se arregla
        # distinto que un DNS muerto o que un robots.txt que prohíbe.
        self.ultimo_motivo = ""
        host = urlparse(url).netloc

        # Todo el request va bajo el candado del host, incluidos los
        # reintentos y sus esperas. Es lo que mantiene el trato con el sitio
        # igual al de la versión serial: sobre un mismo host, un request a la
        # vez y con `delay` segundos entre medio, aunque afuera haya cuatro
        # hilos barriendo otros portales.
        with self._candado_host(host):
            if not self._puede_fetch(url, ignorar_robots):
                log.warning("robots.txt no permite %s — se omite", url)
                self.ultimo_motivo = "robots.txt del sitio no permite este acceso"
                return None

            espera = self.delay - (time.time() - self._ultimo_por_host.get(host, 0))
            if espera > 0:
                time.sleep(espera)

            for intento in range(reintentos):
                try:
                    self._ultimo_por_host[host] = time.time()
                    r = self.session.get(url, timeout=self.timeout)

                    if r.status_code == 200:
                        return r
                    if r.status_code in (429, 503):
                        # Backoff exponencial: el sitio pide explícitamente calma.
                        pausa = 2 ** (intento + 2)
                        log.warning("%s respondió %s — esperando %ss",
                                    host, r.status_code, pausa)
                        time.sleep(pausa)
                        continue
                    log.warning("%s respondió %s", url, r.status_code)
                    self.ultimo_motivo = (
                        f"HTTP {r.status_code}"
                        + (" — el sitio rechaza clientes que no son un navegador"
                           if r.status_code in (401, 403) else "")
                    )
                    return None

                except requests.RequestException as e:
                    log.warning("Error en %s (intento %d/%d): %s",
                                url, intento + 1, reintentos, e)
                    self.ultimo_motivo = _explicar(e)
                    if intento < reintentos - 1:
                        time.sleep(2 ** (intento + 1))

        return None

    def get(self, url: str, reintentos: int = 3,
            ignorar_robots: bool = False) -> str | None:
        r = self._pedir(url, reintentos, ignorar_robots)
        if r is None:
            return None
        if (codificacion := _codificacion(r)):
            r.encoding = codificacion
        return r.text


_CHARSET_META = re.compile(rb"""<meta[^>]+?charset=["']?\s*([\w-]+)""", re.I)


def _codificacion(r: Any) -> str | None:
    """Con qué codificación leer la respuesta, cuando el servidor no lo dice.

    `requests` supone ISO-8859-1 para cualquier text/* sin charset en la
    cabecera, y muchos sitios chilenos son UTF-8 y no lo declaran.

    No es cosmético. Con los bytes mal leídos, "Ñuñoa" y "Peñalolén" dejan de
    calzar con la lista de comunas, y "Gerónimo de Alderete" deja de calzar
    consigo misma entre dos portales: el departamento se duplica o se pierde.

    Devuelve None cuando el servidor SÍ declaró charset —ahí manda él— y en
    caso contrario prefiere el <meta> del HTML por sobre adivinar.
    """
    if "charset=" in r.headers.get("content-type", "").lower():
        return None

    if (m := _CHARSET_META.search(r.content[:4096])):
        return m.group(1).decode("ascii", "ignore")

    # Último recurso: mirar los bytes. Es una suposición, pero una mucho mejor
    # que "latin-1 porque sí".
    return r.apparent_encoding


def _explicar(e: Exception) -> str:
    """Traduce un error de red a algo accionable."""
    texto = str(e)
    if "NameResolutionError" in texto or "Name or service not known" in texto:
        return ("el dominio no existe (DNS no resuelve) — la URL está mala o "
                "el sitio se dio de baja")
    if "RemoteDisconnected" in texto or "Connection aborted" in texto:
        return "el servidor cortó la conexión sin responder — suele ser bloqueo anti-bot"
    if "SSLError" in texto or "CertificateError" in texto:
        return "problema de certificado TLS"
    if "timed out" in texto.lower():
        return "el sitio no respondió a tiempo"
    return texto[:120]


@dataclass
class ResultadoFuente:
    fuente_id: str
    hallazgos: list[Arriendo] = field(default_factory=list)
    urls_ok: int = 0
    urls_fallidas: int = 0
    error: str = ""
    # Si la fuente alcanzó a consultarse. Con presupuesto de tiempo hay
    # fuentes que ni se intentan, y "no se miró" no es lo mismo que "se miró
    # y no había nada": la primera no puede contarse como fuente caída ni
    # dejar de mirarse mañana.
    intentada: bool = True
    # Se empezó a mirar pero se cortó a mitad por el presupuesto de tiempo:
    # lo que trae es parcial.
    cortada_por_tiempo: bool = False
    # Cuánto demoró. Es el dato que dice qué fuente conviene sacar o mandar a
    # otra corrida cuando el presupuesto empieza a quedar chico.
    segundos: float = 0.0
    # Se levantó una excepción que `barrer` no esperaba. Es distinto de
    # `error`: un 403 o un DNS muerto son el portal, esto es un bug nuestro y
    # se reporta aparte para que no se pierda entre las 19 fuentes sin
    # calibrar que igual traen cero.
    reventada: bool = False

    @property
    def ok(self) -> bool:
        return self.urls_ok > 0 and not self.error
