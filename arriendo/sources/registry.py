"""Carga del catálogo de fuentes y ejecución de una corrida contra ellas."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from ..config import FUENTES_DEFAULT
from ..models import Arriendo
from .base import Fetcher, FuenteConfig, ResultadoFuente
from .generic import extraer, enlaces_de_detalle

log = logging.getLogger(__name__)


class FuentesInvalidas(ValueError):
    pass


def cargar_fuentes(ruta: str | Path | None = None) -> list[FuenteConfig]:
    """Lee fuentes.yml y devuelve las fuentes, validadas.

    Falla temprano y con el id de la fuente en el mensaje: este archivo lo
    edita una persona desde el teléfono, y un YAML mal escrito que solo se
    note al correr deja al radar ciego sin decir por qué.
    """
    ruta = Path(ruta) if ruta else FUENTES_DEFAULT
    if not ruta.exists():
        raise FuentesInvalidas(f"No existe el catálogo de fuentes: {ruta}")

    with open(ruta, encoding="utf-8") as f:
        datos = yaml.safe_load(f) or {}

    crudas = datos.get("fuentes")
    if not crudas:
        raise FuentesInvalidas("fuentes.yml no define ninguna fuente")

    conocidos = set(FuenteConfig.__dataclass_fields__)
    vistos: set[str] = set()
    fuentes: list[FuenteConfig] = []

    for i, d in enumerate(crudas):
        if not isinstance(d, dict):
            raise FuentesInvalidas(f"La fuente #{i + 1} no es un bloque de campos")
        fid = d.get("id")
        if not fid:
            raise FuentesInvalidas(f"La fuente #{i + 1} no tiene 'id'")
        if fid in vistos:
            raise FuentesInvalidas(f"Fuente duplicada: '{fid}'")
        vistos.add(fid)
        if not d.get("urls"):
            raise FuentesInvalidas(f"La fuente '{fid}' no tiene 'urls'")

        # Un campo que no existe casi siempre es un typo, y en silencio se
        # traduce en una fuente que no hace lo que su YAML dice.
        if (sobran := set(d) - conocidos):
            raise FuentesInvalidas(
                f"La fuente '{fid}' tiene campos que no existen: "
                f"{', '.join(sorted(sobran))}")

        fuentes.append(FuenteConfig(**d))

    return fuentes


def fuentes_activas(fuentes: list[FuenteConfig],
                    solo: str = "") -> list[FuenteConfig]:
    """Las que se van a consultar.

    `solo` permite acotar a una fuente por id, que es como se depura una que
    está fallando sin esperar la corrida completa.
    """
    if solo:
        elegidas = [f for f in fuentes if f.id == solo]
        if not elegidas:
            disponibles = ", ".join(f.id for f in fuentes)
            raise FuentesInvalidas(
                f"No existe la fuente '{solo}'. Disponibles: {disponibles}")
        return elegidas
    return [f for f in fuentes if f.activa]


def _bajar(fetcher: Fetcher, fuente: FuenteConfig, url: str) -> str | None:
    """Baja una página con el motor que pida la fuente."""
    if fuente.motor != "navegador":
        return fetcher.get(url, ignorar_robots=fuente.ignorar_robots)

    if not fetcher.puede_fetch(url, fuente.ignorar_robots):
        # El navegador no pasa por el camino HTTP, así que sin este chequeo
        # se saltaría la regla del sitio solo por usar otro cliente.
        log.warning("robots.txt no permite %s — se omite", url)
        fetcher.ultimo_motivo = "robots.txt del sitio no permite este acceso"
        return None

    from .navegador import bajar_con_navegador

    return bajar_con_navegador(url, fuente.acciones)


def urls_paginadas(url: str, paginacion: dict) -> list[str]:
    """Las URLs de las páginas 1..N de un listado.

    Sin esto el radar ve solo la primera página de cada portal —unos 20
    avisos— y se pierde el resto en silencio, que para un portal con 60
    arriendos en Vitacura es perder dos tercios del inventario.

    Dos formas, porque los portales chilenos usan las dos:

        paginacion: {paginas: 3, parametro: page}     ...?page=2
        paginacion: {paginas: 3, plantilla: "{url}/pagina-{n}"}

    La página 1 siempre es la URL tal cual: varios portales devuelven un
    listado distinto (o un 404) cuando se les pide explícitamente `?page=1`.
    """
    paginas = int(paginacion.get("paginas") or 0)
    if paginas <= 1:
        return [url]

    desde = int(paginacion.get("desde", 1))
    salida = [url]

    for n in range(desde + 1, desde + paginas):
        if (plantilla := paginacion.get("plantilla")):
            salida.append(str(plantilla).format(url=url.rstrip("/"), n=n))
            continue

        parametro = paginacion.get("parametro")
        if not parametro:
            log.warning("paginacion sin 'parametro' ni 'plantilla': se ignora")
            return [url]

        partes = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                 if k != parametro]
        query.append((str(parametro), str(n)))
        salida.append(urlunsplit(partes._replace(query=urlencode(query))))

    return salida


def barrer(fuente: FuenteConfig, fetcher: Fetcher,
           seguir_detalles: bool = True,
           valor_uf: float | None = None) -> ResultadoFuente:
    """Consulta una fuente completa y devuelve lo que encontró.

    Nunca levanta: una fuente rota no puede voltear la corrida entera. El
    error se guarda en el resultado y la bitácora lo reporta, que es la única
    forma de que una fuente caída se note en vez de confundirse con "hoy no
    hay inventario".
    """
    resultado = ResultadoFuente(fuente_id=fuente.id)

    # El tope de fichas es de la FUENTE, no de cada página.
    #
    # Al principio se aplicaba por página y con la paginación eso se
    # multiplicaba: TocToc con 3 páginas y `max: 12` pedía 39 cargas de
    # navegador, unos diez minutos para una sola fuente, y entre dos fuentes
    # así se acababa el presupuesto de 30 minutos del job antes de llegar a
    # las otras quince.
    #
    # Como presupuesto de fuente, las fichas se gastan en las primeras
    # páginas, que además es donde están los avisos más nuevos.
    presupuesto = int(fuente.detalle.get("max", 10)) if fuente.detalle else 0

    for base in fuente.urls:
        for url in urls_paginadas(base, fuente.paginacion):
            try:
                html = _bajar(fetcher, fuente, url)
            except Exception as e:                               # noqa: BLE001
                # Un fallo del navegador (Chromium ausente, timeout del JS) es
                # una excepción y no un None, y sin capturarla se lleva la
                # corrida entera por una sola fuente rota.
                log.warning("[%s] %s falló: %s", fuente.id, url, e)
                resultado.urls_fallidas += 1
                resultado.error = resultado.error or str(e)[:160]
                break        # si falló una página, las siguientes también

            if not html:
                resultado.urls_fallidas += 1
                resultado.error = resultado.error or fetcher.ultimo_motivo
                break

            resultado.urls_ok += 1
            encontrados = extraer(html, url, fuente, valor_uf)
            resultado.hallazgos.extend(encontrados)

            if seguir_detalles and presupuesto > 0 and fuente.detalle.get("patron"):
                fichas = _seguir_fichas(fuente, fetcher, html, url,
                                        presupuesto, valor_uf)
                resultado.hallazgos.extend(fichas)
                presupuesto -= len(fichas)

            # Una página vacía significa que se acabó el inventario. Seguir
            # pidiendo páginas después de eso son requests al sitio que no
            # traen nada, y varios portales responden la última página una y
            # otra vez en vez de dar 404.
            if not encontrados:
                break

    return resultado


def _seguir_fichas(fuente: FuenteConfig, fetcher: Fetcher, html: str,
                   base_url: str, presupuesto: int,
                   valor_uf: float | None = None) -> list[Arriendo]:
    """Abre las fichas de detalle del listado y extrae lo que solo vive ahí.

    Es la pasada que más mejora el puntaje en arriendos. Los gastos comunes,
    la superficie total y el año de construcción casi nunca están en la
    tarjeta del listado y casi siempre están en la ficha, y son tres de los
    cinco rubros del puntaje.

    Lo que sale de acá NO reemplaza al aviso del listado: se agrega, y la
    deduplicación por dirección los fusiona después quedándose con lo mejor
    de cada uno.
    """
    enlaces = enlaces_de_detalle(html, base_url, fuente.detalle["patron"],
                                 presupuesto)

    salida: list[Arriendo] = []
    for enlace in enlaces:
        try:
            ficha = _bajar(fetcher, fuente, enlace)
        except Exception as e:                                   # noqa: BLE001
            log.debug("[%s] ficha %s falló: %s", fuente.id, enlace, e)
            continue
        if not ficha:
            continue
        for a in extraer(ficha, enlace, fuente, valor_uf):
            a.extras["desde_ficha"] = True
            salida.append(a)

    return salida
