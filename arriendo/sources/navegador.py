"""Motor de navegador, para los portales que arman la página con JavaScript.

La mayoría de los portales chilenos de arriendo son aplicaciones de React o
Nuxt: el HTML que entrega el servidor trae un spinner y nada más. Con un GET
simple esas fuentes dan cero, y lo peor es que dan cero SIN ERROR, que se ve
igual que "hoy no hay inventario".

Este módulo es caro —levanta Chromium— así que se usa solo donde el sitio lo
exige, marcado fuente por fuente con `motor: navegador` en fuentes.yml.

Antes de llegar acá, el extractor ya intentó leer el estado embebido
(`__NEXT_DATA__` y compañía), que resuelve varios de estos sitios sin
navegador. Esto es para los que ni eso tienen.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Cuánto esperar a que el sitio termine de armarse. Generoso porque estos
# portales cargan el listado con una segunda llamada después de pintar.
# 45 segundos y no 30, medido: la corrida del 21-08 perdió DOS de las tres
# búsquedas de toctoc con "Timeout 30000ms exceeded" y la fuente entregó 318
# avisos en vez de ~650. Toctoc es un tercio del inventario del radar, así
# que su timeout arrastra la corrida entera: de 1.400 avisos crudos a 918.
TIMEOUT_MS = 45_000
ESPERA_RED_MS = 6_000

# Bajo esto, lo que quedó en la página es un cascarón y no un listado.
MINIMO_UTIL = 2_000


class NavegadorNoDisponible(RuntimeError):
    """Playwright o Chromium no están instalados."""


def bajar_con_navegador(url: str, acciones: list[dict] | None = None,
                        timeout_ms: int = TIMEOUT_MS) -> str | None:
    """Abre la página en Chromium, ejecuta las acciones y devuelve el HTML.

    `acciones` son los pasos que haría una persona antes de poder leer el
    listado: abrir el panel de filtros, elegir la comuna, esperar. Van
    declaradas en fuentes.yml para no tener que escribir código por sitio.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise NavegadorNoDisponible(
            "playwright no está instalado: pip install -r requirements.txt "
            "&& python -m playwright install chromium") from e

    from .base import UA

    with sync_playwright() as p:
        try:
            navegador = p.chromium.launch(headless=True)
        except Exception as e:                                   # noqa: BLE001
            raise NavegadorNoDisponible(
                f"no se pudo levantar Chromium: {e}. Falta "
                "`python -m playwright install chromium`") from e

        try:
            contexto = navegador.new_context(
                user_agent=UA,
                locale="es-CL",
                viewport={"width": 1400, "height": 2000},
            )
            pagina = contexto.new_page()
            try:
                pagina.goto(url, timeout=timeout_ms,
                            wait_until="domcontentloaded")
            except Exception as e:                               # noqa: BLE001
                # Que `goto` se pase del tiempo NO significa que la página
                # esté vacía: casi siempre el HTML ya llegó y lo que quedó
                # colgado es un rastreador, un anuncio o una fuente de
                # letra. Antes eso costaba la página entera —y con toctoc,
                # un tercio del inventario de la corrida—. Ahora se lee lo
                # que haya y se decide por el tamaño, que es el único juez
                # honesto acá.
                log.warning("Se pasó del tiempo al abrir %s (%s); "
                            "se lee lo que alcanzó a llegar", url, type(e).__name__)
                try:
                    parcial = pagina.content()
                except Exception:                                # noqa: BLE001
                    return None
                if len(parcial) < MINIMO_UTIL:
                    return None
                for accion in acciones or []:
                    _ejecutar(pagina, accion)
                return pagina.content()

            # `networkidle` es lo que espera a la segunda llamada que trae el
            # listado. Si no llega —hay sitios con polling que nunca quedan
            # en silencio— se sigue igual con lo que haya: media página es
            # mucho mejor que ninguna.
            try:
                pagina.wait_for_load_state("networkidle", timeout=ESPERA_RED_MS)
            except Exception:                                    # noqa: BLE001
                log.debug("La red no quedó en silencio en %s; se lee igual", url)

            for accion in acciones or []:
                _ejecutar(pagina, accion)

            return pagina.content()
        finally:
            navegador.close()


def _ejecutar(pagina: Any, accion: dict) -> None:
    """Ejecuta un paso declarado en fuentes.yml.

    Ninguna acción que falle detiene la corrida. Un filtro que no se pudo
    aplicar deja una página con más resultados de los buscados, y eso el
    scoring lo resuelve; una excepción, en cambio, deja la fuente en cero.
    """
    tipo = accion.get("tipo")
    selector = accion.get("selector", "")
    valor = accion.get("valor")

    try:
        if tipo == "click":
            pagina.click(selector, timeout=10_000)
        elif tipo == "esperar":
            pagina.wait_for_selector(selector, timeout=15_000)
        elif tipo == "esperar_ms":
            pagina.wait_for_timeout(int(valor or 1000))
        elif tipo == "seleccionar":
            pagina.select_option(selector, str(valor), timeout=10_000)
        elif tipo == "escribir":
            pagina.fill(selector, str(valor), timeout=10_000)
        elif tipo == "scroll":
            # Varios portales cargan más resultados al bajar (scroll infinito).
            for _ in range(int(valor or 3)):
                pagina.mouse.wheel(0, 4000)
                pagina.wait_for_timeout(1200)
        else:
            log.warning("Acción desconocida en fuentes.yml: %r", tipo)
    except Exception as e:                                       # noqa: BLE001
        log.warning("La acción %s(%s) no se pudo ejecutar: %s",
                    tipo, selector or valor, str(e)[:120])
