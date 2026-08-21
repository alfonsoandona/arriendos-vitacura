"""Tests de la carga y validación de perfil.yml y fuentes.yml.

Los dos archivos los edita una persona desde el navegador del teléfono. Cada
validación de acá corresponde a un error que dejaría el radar CORRIENDO pero
mintiendo, que es peor que uno que lo detiene: un `max` menor que un `min` no
rompe nada, descarta todo el inventario en silencio.
"""

import pytest

from arriendo.config import (PerfilInvalido, cargar_perfil, comunas_nucleo,
                             comunas_vecinas, valor_uf, validar_perfil)
from arriendo.sources.registry import (FuentesInvalidas, cargar_fuentes,
                                       fuentes_activas)


# ---------------------------------------------------------------------------
# El perfil de verdad
# ---------------------------------------------------------------------------

def test_el_perfil_del_repo_es_valido():
    perfil = cargar_perfil()
    assert comunas_nucleo(perfil) == ["Vitacura"]
    assert set(comunas_vecinas(perfil)) == {"Las Condes", "Lo Barnechea"}


def test_el_perfil_dice_lo_que_se_pidio():
    """Los tres requisitos obligatorios, verificados contra el YAML.

    Si alguien edita el perfil y rompe uno sin darse cuenta, este test lo
    dice. Son las tres cosas que el pedido puso como "sí o sí".
    """
    req = cargar_perfil()["requisitos"]
    assert req["tipo"] == ["departamento"]
    assert req["m2_totales"]["min"] == 100
    assert req["m2_totales"]["estricto"] is True
    assert req["dormitorios"]["min"] == 3
    # Subido de 1,6 a 1,7 millones el 17-08-2026, a pedido. El test se
    # actualiza junto con el perfil a propósito: si el tope cambia sin querer
    # —un dedo en el YAML— esto lo dice antes de que empiece a llegar ruido.
    assert req["arriendo_clp"]["max"] == 1_700_000
    assert req["arriendo_clp"]["holgura_pct"] == 12
    # Y el techo negociable, que es el número que de verdad descarta.
    from arriendo import scoring as S
    assert round(S.tope_arriendo(cargar_perfil())[1]) == 1_904_000


def test_la_zona_es_la_pedida():
    perfil = cargar_perfil()
    assert perfil["radio_km"]["anillo"] == 1.2
    assert perfil["ancla"]["nombre"] == "Sport Francés"
    # Vitacura tiene que valer más que cualquier vecina, o "prioriza Vitacura
    # comuna entera" queda escrito y desmentido por el tablero.
    prioridad = perfil["prioridad_comuna"]
    assert prioridad["Vitacura"] > max(v for k, v in prioridad.items()
                                       if k != "Vitacura")


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------

def _base() -> dict:
    return {
        "requisitos": {"tipo": ["departamento"],
                       "m2_totales": {"min": 100},
                       "dormitorios": {"min": 3},
                       "arriendo_clp": {"max": 1_600_000}},
        "comunas": {"nucleo": ["Vitacura"]},
    }


def test_falta_requisitos():
    with pytest.raises(PerfilInvalido, match="requisitos"):
        validar_perfil({"comunas": {"nucleo": ["Vitacura"]}})


def test_tipo_vacio():
    perfil = _base()
    perfil["requisitos"]["tipo"] = []
    with pytest.raises(PerfilInvalido, match="tipo"):
        validar_perfil(perfil)


def test_min_mayor_que_max():
    """No rompe nada: descarta todo el inventario en silencio."""
    perfil = _base()
    perfil["requisitos"]["m2_totales"] = {"min": 200, "max": 100}
    with pytest.raises(PerfilInvalido, match="min .* mayor que max"):
        validar_perfil(perfil)


def test_tope_negativo():
    perfil = _base()
    perfil["requisitos"]["arriendo_clp"] = {"max": -1}
    with pytest.raises(PerfilInvalido, match="positivo"):
        validar_perfil(perfil)


def test_comparar_invalido():
    perfil = _base()
    perfil["requisitos"]["comparar"] = "canon"
    with pytest.raises(PerfilInvalido, match="arriendo.*total"):
        validar_perfil(perfil)


def test_coordenadas_fuera_de_rango():
    perfil = _base()
    perfil["ancla"] = {"lat": -933.38, "lon": -70.56}
    with pytest.raises(PerfilInvalido, match="fuera de rango"):
        validar_perfil(perfil)


def test_lo_caminable_tiene_que_caber_en_el_anillo():
    perfil = _base()
    perfil["radio_km"] = {"preferente": 3.0, "anillo": 1.2}
    with pytest.raises(PerfilInvalido, match="caber dentro"):
        validar_perfil(perfil)


def test_los_tramos_de_antiguedad_van_en_orden():
    perfil = _base()
    perfil["antiguedad"] = {"ideal_max": 30, "bueno_max": 10}
    with pytest.raises(PerfilInvalido, match="menor a mayor"):
        validar_perfil(perfil)


def test_comunas_nucleo_vacio():
    perfil = _base()
    perfil["comunas"] = {"nucleo": []}
    with pytest.raises(PerfilInvalido, match="nucleo"):
        validar_perfil(perfil)


def test_perfil_inexistente():
    with pytest.raises(PerfilInvalido, match="No existe"):
        cargar_perfil("/no/existe/perfil.yml")


# ---------------------------------------------------------------------------
# Valor de la UF
# ---------------------------------------------------------------------------

def test_valor_uf_por_omision():
    assert valor_uf({}) > 20_000


def test_valor_uf_del_entorno():
    assert valor_uf({"VALOR_UF": "41.250,5"}) == 41_250.5


def test_valor_uf_absurdo_se_ignora():
    """Un typo convertiría todos los cánones en UF en basura."""
    por_omision = valor_uf({})
    assert valor_uf({"VALOR_UF": "41"}) == por_omision
    assert valor_uf({"VALOR_UF": "no es un número"}) == por_omision


# ---------------------------------------------------------------------------
# El catálogo de fuentes
# ---------------------------------------------------------------------------

def test_el_catalogo_del_repo_es_valido():
    fuentes = cargar_fuentes()
    assert len(fuentes) >= 15


def test_portal_inmobiliario_esta_apagado_a_proposito():
    """Es la premisa del proyecto: complementar, no duplicar.

    Está registrado —para que la omisión sea visible— y desactivado.
    """
    fuentes = {f.id: f for f in cargar_fuentes()}
    assert "portalinmobiliario" in fuentes
    assert fuentes["portalinmobiliario"].activa is False
    assert fuentes["portalinmobiliario"] not in fuentes_activas(
        list(fuentes.values()))


def test_las_fuentes_activas_no_incluyen_las_apagadas():
    fuentes = cargar_fuentes()
    activas = fuentes_activas(fuentes)
    assert all(f.activa for f in activas)
    assert len(activas) < len(fuentes)


def test_acotar_a_una_fuente():
    fuentes = cargar_fuentes()
    solo = fuentes_activas(fuentes, "toctoc")
    assert [f.id for f in solo] == ["toctoc"]


def test_acotar_a_una_fuente_apagada_igual_funciona():
    """Sirve para depurar una fuente antes de encenderla."""
    solo = fuentes_activas(cargar_fuentes(), "portalinmobiliario")
    assert [f.id for f in solo] == ["portalinmobiliario"]


def test_fuente_inexistente_lista_las_que_hay():
    with pytest.raises(FuentesInvalidas, match="Disponibles"):
        fuentes_activas(cargar_fuentes(), "no-existe")


def test_fuente_sin_id(tmp_path):
    yml = tmp_path / "f.yml"
    yml.write_text("fuentes:\n  - nombre: X\n    urls: ['https://x.cl']\n",
                   encoding="utf-8")
    with pytest.raises(FuentesInvalidas, match="no tiene 'id'"):
        cargar_fuentes(yml)


def test_fuente_sin_urls(tmp_path):
    yml = tmp_path / "f.yml"
    yml.write_text("fuentes:\n  - id: x\n    nombre: X\n", encoding="utf-8")
    with pytest.raises(FuentesInvalidas, match="no tiene 'urls'"):
        cargar_fuentes(yml)


def test_fuente_duplicada(tmp_path):
    yml = tmp_path / "f.yml"
    yml.write_text(
        "fuentes:\n"
        "  - {id: x, nombre: X, urls: ['https://x.cl']}\n"
        "  - {id: x, nombre: Y, urls: ['https://y.cl']}\n", encoding="utf-8")
    with pytest.raises(FuentesInvalidas, match="duplicada"):
        cargar_fuentes(yml)


def test_campo_inventado_es_un_typo(tmp_path):
    """Un campo que no existe casi siempre es un typo.

    En silencio se traduce en una fuente que no hace lo que su YAML dice, y
    eso es muy difícil de notar mirando el resultado.
    """
    yml = tmp_path / "f.yml"
    yml.write_text(
        "fuentes:\n  - {id: x, nombre: X, urls: ['https://x.cl'], motorr: navegador}\n",
        encoding="utf-8")
    with pytest.raises(FuentesInvalidas, match="motorr"):
        cargar_fuentes(yml)


def test_catalogo_vacio(tmp_path):
    yml = tmp_path / "f.yml"
    yml.write_text("fuentes: []\n", encoding="utf-8")
    with pytest.raises(FuentesInvalidas, match="ninguna fuente"):
        cargar_fuentes(yml)


# ---------------------------------------------------------------------------
# La línea de comandos
# ---------------------------------------------------------------------------

def test_las_opciones_comunes_van_a_los_dos_lados():
    """`arriendo run --fuentes f.yml` es lo que sale natural escribir.

    Con argparse, una opción declarada solo en el parser principal falla con
    "unrecognized arguments" si se escribe después del subcomando.
    """
    from arriendo.cli import _con_defaults, construir_parser

    p = construir_parser()
    antes = _con_defaults(p.parse_args(["--fuentes", "f.yml", "run", "--dry-run"]))
    despues = _con_defaults(p.parse_args(["run", "--fuentes", "f.yml", "--dry-run"]))

    assert antes.fuentes == despues.fuentes == "f.yml"
    assert antes.dry_run is despues.dry_run is True


def test_las_opciones_comunes_tienen_default():
    from arriendo.cli import _con_defaults, construir_parser

    args = _con_defaults(construir_parser().parse_args(["run"]))
    assert args.perfil is None
    assert args.fuentes is None
    assert args.verbose is False


def test_verbose_a_los_dos_lados():
    from arriendo.cli import _con_defaults, construir_parser

    p = construir_parser()
    assert _con_defaults(p.parse_args(["-v", "run"])).verbose is True
    assert _con_defaults(p.parse_args(["run", "-v"])).verbose is True


# ---------------------------------------------------------------------------
# Paginación
#
# Sin esto el radar ve solo la primera página de cada portal —unos 20 avisos—
# y se pierde el resto en silencio.
# ---------------------------------------------------------------------------

def test_sin_paginacion_es_una_sola_url():
    from arriendo.sources.registry import urls_paginadas

    assert urls_paginadas("https://x.cl/arriendo", {}) == ["https://x.cl/arriendo"]
    assert urls_paginadas("https://x.cl/a", {"paginas": 1}) == ["https://x.cl/a"]


def test_paginacion_por_parametro():
    from arriendo.sources.registry import urls_paginadas

    urls = urls_paginadas("https://x.cl/arriendo",
                          {"paginas": 3, "parametro": "page"})
    assert urls == ["https://x.cl/arriendo",
                    "https://x.cl/arriendo?page=2",
                    "https://x.cl/arriendo?page=3"]


def test_la_primera_pagina_va_sin_el_parametro():
    """Varios portales devuelven otro listado (o un 404) ante ?page=1."""
    from arriendo.sources.registry import urls_paginadas

    urls = urls_paginadas("https://x.cl/a", {"paginas": 2, "parametro": "page"})
    assert "page=" not in urls[0]


def test_la_paginacion_conserva_los_filtros_de_la_url():
    """La URL ya viene filtrada a Vitacura: perder eso sería barrer todo Chile."""
    from arriendo.sources.registry import urls_paginadas

    urls = urls_paginadas("https://x.cl/a?comuna=vitacura&tipo=depto",
                          {"paginas": 2, "parametro": "page"})
    assert "comuna=vitacura" in urls[1]
    assert "tipo=depto" in urls[1]
    assert "page=2" in urls[1]


def test_la_paginacion_no_duplica_el_parametro():
    from arriendo.sources.registry import urls_paginadas

    urls = urls_paginadas("https://x.cl/a?page=7",
                          {"paginas": 2, "parametro": "page"})
    assert urls[1].count("page=") == 1
    assert "page=2" in urls[1]


def test_paginacion_por_plantilla():
    """Los portales que paginan en la ruta y no en el query."""
    from arriendo.sources.registry import urls_paginadas

    urls = urls_paginadas("https://x.cl/arriendo/",
                          {"paginas": 3, "plantilla": "{url}/pagina-{n}"})
    assert urls == ["https://x.cl/arriendo/",
                    "https://x.cl/arriendo/pagina-2",
                    "https://x.cl/arriendo/pagina-3"]


def test_paginacion_mal_configurada_no_revienta():
    """Sin 'parametro' ni 'plantilla' no se puede paginar: se usa la página 1."""
    from arriendo.sources.registry import urls_paginadas

    assert urls_paginadas("https://x.cl/a", {"paginas": 5}) == ["https://x.cl/a"]


# ---------------------------------------------------------------------------
# El valor de la UF llega hasta donde se usa
# ---------------------------------------------------------------------------

def test_el_valor_uf_configurado_llega_al_extractor(monkeypatch):
    """La variable VALOR_UF está documentada en el workflow: tiene que servir.

    Estaba definida y no se usaba en ninguna parte: el extractor convertía
    siempre con el valor por omisión.
    """
    from arriendo.sources.base import FuenteConfig
    from arriendo.sources.generic import extraer

    html = """<html><body><article>
        <a href="/aviso/1">Departamento en arriendo</a>
        <p>Luis Carrera 1200, Vitacura</p>
        <p>Arriendo UF 38 mensuales</p>
        <p>134 m² totales · 3 dormitorios</p>
        </article></body></html>"""
    fuente = FuenteConfig(id="x", nombre="X", urls=["https://x.cl"])

    (a,) = extraer(html, "https://x.cl/arriendo", fuente, valor_uf=50_000)
    assert a.arriendo_uf == 38
    assert a.arriendo_clp == 1_900_000

    (b,) = extraer(html, "https://x.cl/arriendo", fuente, valor_uf=40_000)
    assert b.arriendo_clp == 1_520_000


def test_el_tope_de_fichas_es_de_la_fuente_y_no_de_cada_pagina():
    """La paginación no puede multiplicar el costo de seguir fichas.

    Con el tope aplicado por página, TocToc con 3 páginas y `max: 12` pedía
    39 cargas de navegador —unos diez minutos para una sola fuente— y entre
    dos fuentes así se acababa el presupuesto de 30 minutos del job antes de
    llegar a las otras quince.
    """
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.sources.registry import barrer

    listado = "".join(
        f'<article><a href="/aviso/{n}">Depto en arriendo</a>'
        f'<p>Luis Carrera {1000 + n}, Vitacura</p><p>$1.450.000</p>'
        f'<p>134 m² totales · 3 dormitorios</p></article>'
        for n in range(30))
    html = f"<html><body>{listado}</body></html>"

    pedidas: list[str] = []

    class FetcherFalso(Fetcher):
        def __init__(self):
            super().__init__(delay=0)

        def get(self, url, reintentos=3, ignorar_robots=False):
            pedidas.append(url)
            return html

    fuente = FuenteConfig(
        id="x", nombre="X", urls=["https://x.cl/arriendo"],
        paginacion={"paginas": 3, "parametro": "page"},
        detalle={"patron": r"/aviso/\d+", "max": 5})

    barrer(fuente, FetcherFalso())

    fichas = [u for u in pedidas if "/aviso/" in u]
    assert len(fichas) == 5, f"se pidieron {len(fichas)} fichas, el tope era 5"
    assert len([u for u in pedidas if "/aviso/" not in u]) == 3


def test_demo_con_archivo_inexistente_no_escupe_traceback():
    """`demo` es el comando con el que alguien prueba el radar por primera vez.

    Casi siempre escribiendo la ruta a mano, así que equivocarse es lo normal.
    Un traceback ahí es la peor primera impresión posible y no dice qué hacer.
    """
    from arriendo import cli

    assert cli.main(["demo", "/no/existe.html"]) == 2


def test_demo_funciona_con_los_ejemplos_del_repo():
    """El mensaje de error ofrece este comando: tiene que andar."""
    from arriendo import cli

    assert cli.main(["demo", "tests/fixtures/portal_tarjetas.html"]) == 0


# ---------------------------------------------------------------------------
# Las fuentes por calibrar
# ---------------------------------------------------------------------------

def test_property_partners_esta_en_el_catalogo():
    """Pedida explícitamente."""
    ids = {f.id for f in cargar_fuentes()}
    assert "propertypartners" in ids


def test_hay_dos_grupos_de_fuentes_y_se_distinguen():
    """La marca es lo que separa "se rompió" de "la URL está mala".

    Sin ella, una fuente confirmada que entrega cero y una sin confirmar que
    entrega cero se ven idénticas en el reporte, y piden arreglos distintos.
    """
    activas = fuentes_activas(cargar_fuentes())
    confirmadas = [f for f in activas if f.url_confirmada]
    por_calibrar = [f for f in activas if not f.url_confirmada]

    # Los dos grupos existen y la marca los separa. El test NO fija un
    # número: apagar una fuente muerta con su motivo medido es progreso, y
    # un umbral que baja solo obliga a editar el test cada vez que el
    # catálogo mejora. Lo que sí tiene que seguir siendo cierto es que la
    # distinción sirva para algo — que haya de las dos.
    assert confirmadas and por_calibrar
    assert len(confirmadas) + len(por_calibrar) == len(activas)
    assert all(f.urls for f in activas), \
        "una fuente activa sin URL no se puede consultar ni calibrar"


def test_las_fuentes_por_calibrar_apuntan_a_la_raiz():
    """Una ruta inventada da 404, que en el reporte se ve como sitio caído.

    La raíz siempre existe y deja el HTML guardado, que es con lo que después
    se escribe la ruta buena.
    """
    from urllib.parse import urlparse

    for f in fuentes_activas(cargar_fuentes()):
        # `ruta_candidata` es el tercer estado: la raíz YA se midió y resultó
        # ser la portada del sitio, así que insistir en ella es garantizar el
        # cero. La candidata sale del patrón confirmado de las fichas del
        # propio portal, y la corrida siguiente dice si sirvió.
        if f.url_confirmada or f.ruta_candidata:
            continue
        for u in f.urls:
            ruta = urlparse(u).path
            assert ruta in ("", "/"), f"{f.id} apunta a {ruta}, no a la raíz"


def test_ningun_dominio_se_repite_entre_fuentes():
    """Dos fuentes con el mismo dominio son la misma: se pisan y duplican."""
    from urllib.parse import urlparse

    vistos: dict[str, str] = {}
    for f in cargar_fuentes():
        host = (urlparse(f.urls[0]).hostname or "").replace("www.", "")
        assert host not in vistos, f"{f.id} repite el dominio de {vistos.get(host)}"
        vistos[host] = f.id


def test_la_ruta_candidata_es_un_tercer_estado_y_no_una_excusa():
    """Una ruta candidata solo se justifica cuando la raíz YA se midió y
    resultó ser la portada. Si además estuviera "confirmada" el catálogo
    estaría diciendo dos cosas a la vez, y el reporte de calibración
    —que separa "está rota" de "tiene la URL mala"— dejaría de servir.
    """
    from urllib.parse import urlparse

    for f in cargar_fuentes():
        if not f.ruta_candidata:
            continue
        assert not f.url_confirmada, \
            f"{f.id}: o la URL está confirmada, o es candidata; no las dos"
        assert any(urlparse(u).path not in ("", "/") for u in f.urls), \
            f"{f.id} se declara candidata pero apunta a la raíz"
