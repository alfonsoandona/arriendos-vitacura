"""Tests del extractor genérico contra HTML escrito como lo escriben los portales.

Las tres pasadas —JSON-LD, estado embebido y tarjetas— tienen cada una su
fixture. Los fixtures incluyen a propósito el ruido real de un portal: el menú
de navegación, el panel de filtros, el botón de "publica tu propiedad" metido
dentro de la tarjeta, y avisos de venta y de temporada mezclados en la misma
grilla.
"""

from pathlib import Path

import pytest

from arriendo.sources.base import FuenteConfig
from arriendo.sources.generic import extraer, enlaces_de_detalle

FIXTURES = Path(__file__).parent / "fixtures"


def html(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


@pytest.fixture
def fuente():
    return FuenteConfig(id="ejemplo", nombre="Ejemplo", urls=["https://ejemplo.cl/arriendo"])


# ---------------------------------------------------------------------------
# Pasada 1: JSON-LD
# ---------------------------------------------------------------------------

def test_jsonld_extrae_los_dos_avisos(fuente):
    avisos = extraer(html("portal_jsonld.html"), "https://ejemplo.cl/arriendo", fuente)
    assert len(avisos) == 2
    assert all(a.extras["via"] == "json-ld" for a in avisos)


def test_jsonld_lee_los_campos_estructurados(fuente):
    avisos = extraer(html("portal_jsonld.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if "4200" in x.direccion)

    assert a.comuna == "Vitacura"
    assert a.m2_totales == 134
    assert a.dormitorios == 3
    assert a.banos == 3
    assert a.arriendo_clp == 1_550_000
    assert a.ano_construccion == 2018
    assert (a.lat, a.lon) == (-33.3830, -70.5650)


def test_jsonld_saca_los_gastos_comunes_de_la_descripcion(fuente):
    """El dato con el que se decide y que schema.org no tiene dónde poner."""
    avisos = extraer(html("portal_jsonld.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if "4200" in x.direccion)
    assert a.gastos_comunes_clp == 220_000
    assert a.costo_mensual == 1_770_000


def test_jsonld_no_confunde_el_valor_uf_del_encabezado(fuente):
    """El header trae "Valor UF hoy: 40.844,79" y no es el precio de nada."""
    avisos = extraer(html("portal_jsonld.html"), "https://ejemplo.cl/arriendo", fuente)
    assert all(a.arriendo_uf is None for a in avisos)


def test_jsonld_lee_estacionamientos_y_orientacion(fuente):
    avisos = extraer(html("portal_jsonld.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if "4200" in x.direccion)
    assert a.estacionamientos == 2
    assert a.bodega is True
    assert a.orientacion == "nororiente"


# ---------------------------------------------------------------------------
# Pasada 2: estado embebido
# ---------------------------------------------------------------------------

def test_spa_lee_el_estado_embebido(fuente):
    """El HTML solo trae un spinner: sin esta pasada el portal da cero."""
    avisos = extraer(html("portal_spa.html"), "https://ejemplo.cl/arriendo", fuente)
    assert len(avisos) == 2
    assert all(a.extras["via"] == "estado-embebido" for a in avisos)


def test_spa_arma_la_url_absoluta(fuente):
    avisos = extraer(html("portal_spa.html"), "https://ejemplo.cl/arriendo", fuente)
    assert any(a.url == "https://ejemplo.cl/propiedad/77120" for a in avisos)


def test_spa_lee_los_campos_en_castellano(fuente):
    avisos = extraer(html("portal_spa.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if x.comuna == "Vitacura")

    assert a.arriendo_clp == 1_520_000
    assert a.gastos_comunes_clp == 210_000
    assert a.m2_totales == 138
    assert a.m2_utiles == 120
    assert a.dormitorios == 3
    assert a.estacionamientos == 2
    assert a.ano_construccion == 2015


def test_spa_no_toma_la_configuracion_de_moneda_como_aviso(fuente):
    """`currencyConfig` tiene un campo `price` y no es una propiedad.

    Por eso `_parece_aviso` exige precio Y algo que ubique o mida: los
    payloads están llenos de nodos con precio que no son avisos.
    """
    avisos = extraer(html("portal_spa.html"), "https://ejemplo.cl/arriendo", fuente)
    assert len(avisos) == 2


def test_spa_valida_las_coordenadas(fuente):
    avisos = extraer(html("portal_spa.html"), "https://ejemplo.cl/arriendo", fuente)
    for a in avisos:
        assert -56 <= a.lat <= -17
        assert -76 <= a.lon <= -66


# ---------------------------------------------------------------------------
# Pasada 3: tarjetas
# ---------------------------------------------------------------------------

def test_tarjetas_encuentra_los_cinco_avisos(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    assert len(avisos) == 5, [a.title for a in avisos]
    assert all(a.extras["via"] == "tarjeta" for a in avisos)


def test_tarjetas_ignora_el_menu_y_el_pie(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    urls = {a.url for a in avisos}
    assert not any("/contacto" in u or "/venta" in u for u in urls)


def test_tarjetas_ignora_el_panel_de_filtros(fuente):
    """Un panel enumera rangos; un aviso nombra su precio una vez.

    Sin esta defensa cada faceta del buscador entra como un departamento
    distinto: son decenas de avisos fantasma que apuntan todos a un listado.
    """
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    assert not any("PriceRange" in a.url for a in avisos)
    assert not any("dormitorios/vitacura" in a.url for a in avisos)


def test_el_enlace_apunta_al_aviso_y_no_a_la_promo(fuente):
    """Varios portales meten un "Publica tu propiedad" DENTRO de cada tarjeta.

    Sin este cuidado la alerta llega apuntando a la página de planes del
    portal: el departamento estaba bien leído y el link no servía.
    """
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    assert not any("/planes" in a.url for a in avisos)
    assert any(a.url.endswith("/aviso/12001") for a in avisos)


def test_tarjetas_lee_precio_y_gastos_comunes(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if x.url.endswith("/aviso/12001"))
    assert a.arriendo_clp == 1_480_000
    assert a.gastos_comunes_clp == 195_000


def test_tarjetas_lee_las_dos_superficies(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if x.url.endswith("/aviso/12001"))
    assert a.m2_totales == 134
    assert a.m2_utiles == 118


def test_tarjetas_lee_el_programa_completo(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if x.url.endswith("/aviso/12001"))
    assert a.dormitorios == 3
    assert a.banos == 3
    assert a.estacionamientos == 2
    assert a.bodega is True
    assert a.ano_construccion == 2016
    assert a.piso == 11


def test_tarjetas_detecta_la_operacion(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    por_url = {a.url.rsplit("/", 1)[-1]: a for a in avisos}

    assert por_url["12001"].operacion == "arriendo"
    assert por_url["12003"].operacion == "temporada"
    assert por_url["12004"].operacion == "venta"


def test_tarjetas_extrae_la_direccion(fuente):
    """Es la pieza con la que se deduplica entre portales."""
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if x.url.endswith("/aviso/12001"))
    assert "Santa María 6800" in a.direccion


def test_la_unidad_entra_en_los_extras(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if x.url.endswith("/aviso/12001"))
    assert a.extras.get("unidad") == "1102"


def test_publicado_relativo(fuente):
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    a = next(x for x in avisos if x.url.endswith("/aviso/12001"))
    assert a.dias_publicado == 5


# ---------------------------------------------------------------------------
# Selector configurado
# ---------------------------------------------------------------------------

def test_el_selector_configurado_manda():
    """Lo que deja la calibración siempre es mejor que adivinar."""
    fuente = FuenteConfig(id="e", nombre="E", urls=["https://ejemplo.cl"],
                          selector_card="article.card")
    avisos = extraer(html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo", fuente)
    assert len(avisos) == 5


# ---------------------------------------------------------------------------
# Enlaces de detalle
# ---------------------------------------------------------------------------

def test_enlaces_de_detalle():
    enlaces = enlaces_de_detalle(
        html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo",
        r"/aviso/\d+")
    assert enlaces == [f"https://ejemplo.cl/aviso/1200{n}" for n in range(1, 6)]


def test_enlaces_de_detalle_respeta_el_tope():
    enlaces = enlaces_de_detalle(
        html("portal_tarjetas.html"), "https://ejemplo.cl/arriendo",
        r"/aviso/\d+", tope=2)
    assert len(enlaces) == 2


# ---------------------------------------------------------------------------
# Robustez
# ---------------------------------------------------------------------------

def test_html_vacio_no_revienta(fuente):
    assert extraer("", "https://ejemplo.cl", fuente) == []
    assert extraer("<html><body></body></html>", "https://ejemplo.cl", fuente) == []


def test_jsonld_roto_cae_a_la_pasada_siguiente(fuente):
    roto = """<html><head>
      <script type="application/ld+json">{ esto no es json </script>
      </head><body>
      <article><a href="/aviso/1">Depto Vitacura</a>
      <p>Alonso de Córdova 4200 · $1.500.000 · 134 m² · 3 dormitorios</p>
      </article></body></html>"""
    avisos = extraer(roto, "https://ejemplo.cl", fuente)
    assert len(avisos) == 1
    assert avisos[0].extras["via"] == "tarjeta"


# ---------------------------------------------------------------------------
# La dirección — la llave de la deduplicación
# ---------------------------------------------------------------------------

from arriendo.sources.generic import _direccion_desde  # noqa: E402


@pytest.mark.parametrize("texto,esperada", [
    # El tipo de vía cierra el nombre hacia atrás.
    ("Santa María de Manquehue Avenida Santa María 6800",
     "Avenida Santa María 6800"),
    # ...salvo cuando es parte del nombre: en Vitacura la calle se llama así.
    ("Departamento en Nueva Costanera 3600", "Nueva Costanera 3600"),
    # La comuna pegada adelante se saca entera, no a medias.
    ("Arriendo en Las Condes Isabel La Católica 4800",
     "Isabel La Católica 4800"),
    ("Vitacura Candelaria Goyenechea 3900", "Candelaria Goyenechea 3900"),
    # Los conectores son parte del nombre.
    ("Alonso de Córdova 4200", "Alonso de Córdova 4200"),
    # Una superficie tiene forma de altura y no lo es.
    ("Departamento de 134 m² en Luis Carrera 1200", "Luis Carrera 1200"),
    # El número que sigue a "Depto" es la unidad.
    ("Depto 802", ""),
    # Sin altura no hay dirección.
    ("Departamento luminoso en Vitacura", ""),
])
def test_direccion_desde(texto, esperada):
    assert _direccion_desde(texto, "") == esperada


def test_la_comuna_se_agrega_a_la_direccion():
    assert _direccion_desde("Alonso de Córdova 4200", "Vitacura") == \
        "Alonso de Córdova 4200, Vitacura"


def test_direcciones_de_distintos_portales_dedupican():
    """La prueba que importa: la misma calle escrita por cuatro portales."""
    from arriendo.models import clave_direccion

    variantes = [
        _direccion_desde("Santa María de Manquehue Avenida Santa María 6800", ""),
        _direccion_desde("Av. Santa María 6800, Vitacura", ""),
        _direccion_desde("Vitacura Avenida Santa María N° 6800", ""),
    ]
    claves = {clave_direccion(d, "Vitacura") for d in variantes}
    assert len(claves) == 1, f"{variantes} -> {claves}"
