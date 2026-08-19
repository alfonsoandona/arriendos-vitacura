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


def test_el_numero_de_dormitorios_no_es_la_altura_de_la_calle():
    """Aviso real de Yapo que producía la dirección "Luis Carrera 3".

    La dirección es la llave con la que se deduplica entre portales, así que
    una inventada puede fusionar dos departamentos distintos o impedir que se
    junten dos copias del mismo.
    """
    assert _direccion_desde(
        "Departamento en Luis Carrera 3 Dormitorios por CLP 1600000.00", "") == ""


@pytest.mark.parametrize("texto", [
    "Depto 3 dormitorios",
    "Amplio depto 2 baños",
    "Con 2 estacionamientos",
    "Superficie 134 m²",
])
def test_una_cifra_de_programa_nunca_es_una_direccion(texto):
    assert _direccion_desde(texto, "") == ""


def test_la_altura_de_verdad_sobrevive_a_la_palabra_piso():
    """En "Luis Carrera 1200 piso 5" el 1200 sí es la altura."""
    assert _direccion_desde("Luis Carrera 1200 piso 5", "") == "Luis Carrera 1200"


def test_anadir_a_favoritos_no_es_parte_del_titulo():
    """"Añadir a favoritos Leticia Caceres Vitacura Departamento…" fue un
    título REAL enviado por Telegram: el botón de la tarjeta leído como si
    fuera el nombre del departamento."""
    from arriendo.sources.generic import _titulo_desde
    t = _titulo_desde("Añadir a favoritos Leticia Caceres Vitacura "
                      "Departamento en arriendo de 4 dorm. en Vitacura")
    assert not t.lower().startswith("añadir")


# ---------------------------------------------------------------------------
# Los fallos de la corrida del 17-08 21:07, aviso por aviso
#
# Cada texto de esta sección es LITERAL: es lo que un portal entregó y lo que
# llegó (mal) al teléfono. Si uno de estos tests se rompe, se rompió algo que
# ya estuvo roto a la vista del usuario.
# ---------------------------------------------------------------------------

# El widget de "Propiedades Destacadas" de propiedades.cl: TRES propiedades en
# un solo bloque. Alertó como un aviso con la dirección de la oficina, el
# canon de la oficina (UF 22) y dormitorios de la mezcla.
_WIDGET_DESTACADAS = (
    "Propiedades Destacadas Listado de propiedades "
    "COD: 50.444 Departamento en Santiago Tarapacá / Eleuterio Ramírez "
    "Venta: UF 1.700 1 Dorm. 1 baño Detalles "
    "COD: 50.888 Oficina en Las Condes MUT / Metro Tobalaba "
    "Arriendo: UF 22,04 2 baños 70 m 2 Superficies Detalles "
    "COD: 51.193 Casa en Ñuñoa Prof. Juan Gómez Millas "
    "Arriendo: $ 850.000 3 Dorm. 2 baños Detalles")


def test_el_widget_de_destacadas_no_es_una_tarjeta(fuente):
    doc = (f'<html><body><div class="lateral"><a href="/aviso/1">'
           f'{_WIDGET_DESTACADAS}</a></div></body></html>')
    assert extraer(doc, "https://ejemplo.cl/arriendo", fuente) == []


def test_varios_codigos_de_publicacion_no_son_un_aviso():
    """La enumeración delata al widget aunque cambie el encabezado."""
    from arriendo.sources.generic import _tiene_senal
    assert not _tiene_senal("COD: 50.444 Depto Venta: UF 1.700 "
                            "COD: 50.888 Oficina Arriendo: UF 22,04")
    # Un solo código es un aviso normal: chilepropiedades los pone en todos.
    assert _tiene_senal("Cod. 109.892 Departamento 4 dormitorios $1.000.000")


def test_el_catalogo_completo_no_es_el_link_del_aviso(fuente):
    """El "link al aviso" de la quimera era el catálogo entero del portal."""
    doc = ('<html><body><div>'
           '<a href="/Todos_los_tipos/Venta_y_Arriendo/Todas_las_comunas">'
           'Departamento en Vitacura $1.000.000 3 dormitorios 2 baños'
           '</a></div></body></html>')
    assert extraer(doc, "https://ejemplo.cl/arriendo", fuente) == []


def test_el_titulo_hueco_cae_al_slug_de_la_url(fuente):
    """"$ 770.000 Arriendo Ver más Contactar" ALERTÓ con 87 puntos: doomos
    escribe sus tarjetas así, con la descripción real en el slug."""
    doc = ('<html><body><article><a href='
           '"/de/1465886_arriendo-departamento-en-av-kennedy-vitacura.html">'
           '$ 770.000 Arriendo Ver más Contactar</a></article></body></html>')
    a = extraer(doc, "https://www.doomos.cl/departamentos-vitacura", fuente)[0]
    assert a.title == "Arriendo departamento en av kennedy vitacura"
    assert a.arriendo_clp == 770_000


def test_un_titulo_con_sustancia_no_se_toca(fuente):
    doc = ('<html><body><article><a href="/de/99_otra-cosa.html">'
           'Departamento Espoz 2620 $1.500.000 3 dormitorios</a>'
           '</article></body></html>')
    a = extraer(doc, "https://www.doomos.cl/departamentos-vitacura", fuente)[0]
    assert a.title.startswith("Departamento Espoz")


def test_el_lastre_numerico_de_remax_no_es_el_titulo():
    """"1/28 1.250.000 $ Mensual 30,60 UF 2 2 5 103 Departamento…": el
    paginador del carrusel, el precio dos veces y la fila de iconos, todo
    antes de la primera palabra con sustancia."""
    from arriendo.sources.generic import _titulo_desde
    t = _titulo_desde("1/28 1.250.000 $ Mensual 30,60 UF 2 2 5 103 "
                      "Departamento Vitacura, Santiago, Metropolitana De "
                      "Santiago, Chile Usada")
    assert t.startswith("Departamento Vitacura")


def test_un_titulo_que_es_puro_numero_queda_como_estaba():
    """El recorte del lastre no puede dejar el título vacío."""
    from arriendo.sources.generic import _titulo_desde
    assert _titulo_desde("$1.500.000 UF 38") == "$1.500.000 UF 38"


@pytest.mark.parametrize("texto", [
    # "Mensual 30, Vitacura": el rótulo del precio como calle y la parte
    # entera de "30,60 UF" como altura.
    "1/28 1.250.000 $ Mensual 30,60 UF 2 2 5 103 Departamento Vitacura",
    # "UF29 38, Vitacura": la UF con decimales, partida por la coma.
    "DPTO AMOBLADO EXCELENTE UBICACION ... UF29,38 3 2 2 Compara",
    # "UF38 00": lo mismo, con la parte decimal "00" como altura.
    "140m2 Vitacura Departamento ... UF38,00 -5% 4 1 3 Compara",
    # Direcciones REALES del diagnóstico: el título tragado como calle, con
    # la UF de altura. La palabra de precio EN MEDIO delata al nombre entero.
    "HEY! CASA COMERCIAL EN ARRIENDO! UF 75",
    "Local San Sebastián Arriendo: UF 90 Providencia",
    # "Sólo 3, Vitacura" ALERTÓ el 18-08: el marketing con el 3 de altura.
    "Departamento nuevo Sólo 3 disponibles en Vitacura",
    "Quedan 2 unidades con terraza",
])
def test_un_precio_partido_por_la_coma_no_es_direccion(texto):
    assert _direccion_desde(texto, "") == ""


def test_la_basura_inicial_no_esconde_la_direccion_real():
    """Descartar "Baños: 3" como calle no puede rendirse: la dirección de
    verdad puede venir después en el mismo texto."""
    assert _direccion_desde(
        "Baños: 3 Departamento en Avenida Santa María 6800", "") == \
        "Avenida Santa María 6800"


# La fila de iconos: los números pelados con que Yapo cierra cada tarjeta.
# El 77% de sus avisos quedaba sin dormitorios teniendo el dato a la vista.

from arriendo.sources.generic import _programa_de_iconos  # noqa: E402


@pytest.mark.parametrize("texto,esperado", [
    # Texto real: "Arrienda Depto 2D2B1E1B" confirma d=2, e=1, b=2.
    ("Si buscas un arriendo de departamento en Vitacura ... $1.250.000 2 1 2",
     {"dormitorios": 2, "estacionamientos": 1, "banos": 2}),
    # Texto real houm: el "m 2" de la superficie pegado por delante.
    ("Entérate de todos los beneficios ... $1.350.000 75 m 2 2 1 2",
     {"dormitorios": 2, "estacionamientos": 1, "banos": 2}),
    # Texto real: "4 dormitorios y 3 baños" confirma d=4, b=3.
    ("Con una ubicación inmejorable ... $1.000.000 120 m 2 4 1 3",
     {"dormitorios": 4, "estacionamientos": 1, "banos": 3}),
    # La racha final es corta ("hace 3 días"): no alcanza y no inventa.
    ("Departamento $1.500.000 publicado hace 3", {}),
])
def test_fila_de_iconos_yapo(texto, esperado):
    assert _programa_de_iconos(texto, "d e b") == esperado


def test_fila_de_iconos_icasas():
    """Texto real: "...exclusiva zona... 65m2 2 2" — 2D 2B."""
    assert _programa_de_iconos(
        "modernas y elegantes terminaciones ... 65m2 2 2", "d b") == \
        {"dormitorios": 2, "banos": 2}


def test_los_iconos_no_pisan_el_programa_rotulado(fuente):
    """"3 dormitorios" escrito le gana al icono, siempre."""
    from dataclasses import replace
    f = replace(fuente, fila_iconos="d e b")
    doc = ('<html><body><article><a href="/aviso/9">Departamento 3 '
           'dormitorios en Vitacura $1.500.000 9 9 9</a></article>'
           '</body></html>')
    a = extraer(doc, "https://ejemplo.cl/arriendo", f)[0]
    assert a.dormitorios == 3, "el texto rotulado manda"
    assert a.estacionamientos == 9 and a.banos == 9, \
        "los huecos sí se rellenan con el icono"


def test_sin_fila_configurada_no_se_adivina(fuente):
    doc = ('<html><body><article><a href="/aviso/9">Departamento en '
           'Vitacura $1.500.000 2 1 2</a></article></body></html>')
    a = extraer(doc, "https://ejemplo.cl/arriendo", fuente)[0]
    assert a.dormitorios is None and a.banos is None


# El patrón de los metabuscadores, medido contra sus páginas reales: el
# JSON-LD trae todo menos el precio (nuroa: 100% dormitorios, 88% m², 12%
# precio), y el precio vive solo en la tarjeta visible. Como la pasada
# JSON-LD gana, la tarjeta se descartaba entera.

def test_el_precio_de_la_tarjeta_completa_al_jsonld(fuente):
    import json as _json
    nodo = {"@type": "Apartment", "name": "Departamento 3D en Vitacura",
            "url": "https://ejemplo.cl/aviso/9",
            "numberOfBedrooms": 3, "numberOfBathroomsTotal": 2,
            "floorSize": {"@type": "QuantitativeValue", "value": 120},
            "address": {"streetAddress": "Espoz 3400",
                        "addressLocality": "Vitacura"}}
    doc = (f'<html><body><script type="application/ld+json">'
           f'{_json.dumps(nodo, ensure_ascii=False)}</script>'
           f'<article><a href="/aviso/9">Departamento 3D en Vitacura '
           f'$1.550.000 3 dormitorios</a></article></body></html>')
    avisos = extraer(doc, "https://ejemplo.cl/arriendo", fuente)
    assert len(avisos) == 1, "completar no puede duplicar el aviso"
    a = avisos[0]
    assert a.extras["via"] == "json-ld"
    assert a.dormitorios == 3 and a.m2_totales == 120
    assert a.arriendo_clp == 1_550_000, "el precio vivía solo en la tarjeta"


def test_sin_urls_el_precio_calza_por_titulo_unico(fuente):
    """El caso mitula, medido contra su nodo real: el JSON-LD no declara url
    y la tarjeta no tiene href. El título exacto y único identifica igual."""
    import json as _json
    nodo = {"@type": "Apartment", "name": "FERNANDO DE ARGUELLO / PADRE HURTADO",
            "description": "IMPECABLE DUPLEX 3 dormitorios 3 baños",
            "address": {"streetAddress": "Fernando de Arguello 8399",
                        "addressLocality": "Vitacura"}}
    doc = (f'<html><body><script type="application/ld+json">'
           f'{_json.dumps(nodo, ensure_ascii=False)}</script>'
           f'<div><a href="#"><span>x</span></a>'
           f'<article>FERNANDO DE ARGUELLO / PADRE HURTADO '
           f'$1.650.000 3 dormitorios 120 m2 <a href="#">ver</a></article>'
           f'</div></body></html>')
    a = extraer(doc, "https://casas.mitula.cl/arriendo-vitacura", fuente)[0]
    assert a.arriendo_clp == 1_650_000, \
        "el precio vivía solo en la tarjeta sin link"


def test_dos_tarjetas_del_mismo_titulo_no_completan_nada(fuente):
    import json as _json
    nodo = {"@type": "Apartment", "name": "Departamento en Vitacura centro",
            "address": {"streetAddress": "Espoz 3400",
                        "addressLocality": "Vitacura"}}
    doc = (f'<html><body><script type="application/ld+json">'
           f'{_json.dumps(nodo, ensure_ascii=False)}</script>'
           f'<article>Departamento en Vitacura centro $1.500.000 '
           f'3 dormitorios <a href="#">v</a></article>'
           f'<article>Departamento en Vitacura centro $2.900.000 '
           f'2 dormitorios <a href="#">v</a></article>'
           f'</body></html>')
    a = extraer(doc, "https://casas.mitula.cl/arriendo-vitacura", fuente)[0]
    assert a.arriendo_clp is None, \
        "con título repetido, cualquiera de los dos precios sería inventado"


def test_la_tarjeta_de_otro_aviso_no_completa_nada(fuente):
    import json as _json
    nodo = {"@type": "Apartment", "name": "Departamento 3D en Vitacura",
            "url": "https://ejemplo.cl/aviso/9", "numberOfBedrooms": 3,
            "address": {"streetAddress": "Espoz 3400",
                        "addressLocality": "Vitacura"}}
    doc = (f'<html><body><script type="application/ld+json">'
           f'{_json.dumps(nodo, ensure_ascii=False)}</script>'
           f'<article><a href="/aviso/77">Otro departamento $2.990.000 '
           f'2 dormitorios</a></article></body></html>')
    a = extraer(doc, "https://ejemplo.cl/arriendo", fuente)[0]
    assert a.arriendo_clp is None, "sin calce de URL no se cree nada"


# La tercera vía: calce por ORDEN con anclas. El caso real de mitula del
# 18-08: 40 de 49 avisos sin precio porque media página se titula
# "Departamento en arriendo en VITACURA" y el calce por título único se
# rinde — con el precio a la vista en cada tarjeta ("90 UF").

def _pagina_mitula(orden_tarjetas):
    import json as _json
    nodos = [
        {"@type": "Apartment", "name": "PENTHOUSE LO CURRO ESPECIAL",
         "address": {"streetAddress": "Vía Aurora 9260",
                     "addressLocality": "Vitacura"}},
        {"@type": "Apartment", "name": "Departamento en arriendo en VITACURA",
         "description": "Avenida Juan XXIII 6699",
         "address": {"streetAddress": "Avenida Juan XXIII 6699",
                     "addressLocality": "Vitacura"}},
        {"@type": "Apartment", "name": "Departamento en arriendo en VITACURA",
         "address": {"streetAddress": "Mar Jónico 5900",
                     "addressLocality": "Vitacura"}},
        {"@type": "Apartment", "name": "FERNANDO DE ARGUELLO / PADRE HURTADO",
         "address": {"streetAddress": "Fernando de Arguello 8399",
                     "addressLocality": "Vitacura"}},
    ]
    tarjetas = {
        "A": ('<article>PENTHOUSE LO CURRO ESPECIAL $2.500.000 '
              '3 dormitorios <a href="#">v</a></article>'),
        "B": ('<article>Departamento en arriendo en VITACURA 90 UF '
              '3 dormitorios 270 m2 <a href="#">v</a></article>'),
        "C": ('<article>Departamento en arriendo en VITACURA $1.650.000 '
              '3 dormitorios 93 m2 <a href="#">v</a></article>'),
        "D": ('<article>FERNANDO DE ARGUELLO / PADRE HURTADO $1.800.000 '
              '3 dormitorios <a href="#">v</a></article>'),
    }
    cuerpo = "".join(tarjetas[k] for k in orden_tarjetas)
    return (f'<html><body><script type="application/ld+json">'
            f'{_json.dumps(nodos, ensure_ascii=False)}</script>'
            f"{cuerpo}</body></html>")


def test_titulos_repetidos_calzan_por_orden_con_anclas(fuente):
    doc = _pagina_mitula("ABCD")
    avisos = extraer(doc, "https://casas.mitula.cl/arriendo-vitacura",
                     fuente, valor_uf=40_000.0)
    assert len(avisos) == 4
    por_dir = {(a.direccion or "").split(",")[0]: a for a in avisos}
    juan = por_dir["Avenida Juan XXIII 6699"]
    assert juan.arriendo_uf == 90, "el 90 UF de la tarjeta es SU precio"
    assert juan.arriendo_clp == 90 * 40_000
    assert por_dir["Mar Jónico 5900"].arriendo_clp == 1_650_000


def test_orden_roto_ninguna_ancla_miente(fuente):
    """Si una ancla no está en SU tarjeta, el orden no es el que creemos:
    un precio del vecino es peor que ningún precio."""
    doc = _pagina_mitula("ADBC")   # FERNANDO quedó en la posición de Juan
    avisos = extraer(doc, "https://casas.mitula.cl/arriendo-vitacura",
                     fuente, valor_uf=40_000.0)
    por_dir = {(a.direccion or "").split(",")[0]: a for a in avisos}
    assert por_dir["Avenida Juan XXIII 6699"].arriendo_clp is None
    assert por_dir["Mar Jónico 5900"].arriendo_clp is None


def test_con_tarjetas_de_mas_no_se_alinea(fuente):
    """Una tarjeta extra (publicidad) corre todos los índices: sin el mismo
    largo en ambas listas no hay hipótesis de orden que verificar."""
    doc = _pagina_mitula("ABCD").replace(
        "<article>PENTHOUSE",
        '<article>Casa en venta en Chicureo $350.000.000 5 dormitorios '
        '250 m2 <a href="#">v</a></article>'
        "<article>PENTHOUSE")
    avisos = extraer(doc, "https://casas.mitula.cl/arriendo-vitacura",
                     fuente, valor_uf=40_000.0)
    por_dir = {(a.direccion or "").split(",")[0]: a for a in avisos}
    assert por_dir["Avenida Juan XXIII 6699"].arriendo_clp is None


# El estado de impresiones del buscador (mitula): medido contra la página
# real del 19-08, el HTML del servidor no trae NINGUNA tarjeta con precio
# —se pintan con JavaScript— y el precio viaja en
# window.serpSectionImpressionData.listings, con posición, moneda (CLP o
# CLF=UF), programa y el listingId del link directo.

def _pagina_serp(dorm_blob=(3, 4)):
    import json as _json
    nodos = [
        {"@type": "Apartment", "name": "Departamento en arriendo en VITACURA",
         "numberOfBedrooms": 3, "numberOfBathroomsTotal": 2,
         "address": {"streetAddress": "Avenida Juan XXIII 6699",
                     "addressLocality": "Vitacura"}},
        {"@type": "Apartment", "name": "Departamento en arriendo en VITACURA",
         "numberOfBedrooms": 4, "numberOfBathroomsTotal": 3,
         "address": {"streetAddress": "Mar Jónico 5900",
                     "addressLocality": "Vitacura"}},
    ]
    serp = {"totalResults": 2316, "page": 1, "listings": [
        {"listingId": "24301-256-7c5d-db3e427b7870-9818-19e6ebd-b6e8",
         "position": 0, "numberOfBedrooms": dorm_blob[0],
         "numberOfBathrooms": 2, "floorArea": 270,
         "operations": [{"operationType": "RENT",
                         "price": {"value": 90, "currency": "CLF"}}]},
        {"listingId": "24301-256-aaaa-bbbbbbbbbbbb-cccc-1234567-dddd",
         "position": 1, "numberOfBedrooms": dorm_blob[1],
         "numberOfBathrooms": 3, "floorArea": 93,
         "operations": [{"operationType": "RENT",
                         "price": {"value": 1650000, "currency": "CLP"}}]},
    ]}
    return (f'<html><body><script type="application/ld+json">'
            f'{_json.dumps(nodos, ensure_ascii=False)}</script>'
            f"<script>window.serpSectionImpressionData = "
            f"{_json.dumps(serp)}</script></body></html>")


def test_el_serp_de_mitula_trae_precio_y_link_directo(fuente):
    avisos = extraer(_pagina_serp(),
                     "https://casas.mitula.cl/casas/arriendo-vitacura",
                     fuente, valor_uf=40_800.0)
    assert len(avisos) == 2
    juan = next(a for a in avisos if "Juan XXIII" in (a.direccion or ""))
    assert juan.arriendo_uf == 90, "el 90 UF que el usuario veía en la tarjeta"
    assert juan.arriendo_clp == round(90 * 40_800)
    assert juan.url == ("https://casas.mitula.cl/adform/"
                        "24301-256-7c5d-db3e427b7870-9818-19e6ebd-b6e8"), \
        "el listingId es el link directo al aviso"
    assert "sin_link_directo" not in juan.extras
    otro = next(a for a in avisos if "Mar Jónico" in (a.direccion or ""))
    assert otro.arriendo_clp == 1_650_000


def test_un_programa_discrepante_aborta_el_serp(fuente):
    """Dormitorios del blob ≠ dormitorios del JSON-LD: el orden no es el que
    creemos, y un precio del vecino es peor que ningún precio."""
    avisos = extraer(_pagina_serp(dorm_blob=(4, 3)),
                     "https://casas.mitula.cl/casas/arriendo-vitacura",
                     fuente, valor_uf=40_800.0)
    assert all(a.arriendo_clp is None and a.arriendo_uf is None
               for a in avisos)


# El candidato de texto de ficha: goplaceit e iCasas no ponen JSON-LD de la
# propiedad en su ficha (las tres pasadas extraen CERO) y el texto visible lo
# dice todo. Las líneas del fixture son las REALES del diagnóstico del 17-08.

_FICHA_ICASAS = """<html><body>
  <header>iCasas — Habitacionales en Venta · Habitacionales en Arriendo</header>
  <h1>Arriendo Departamento amoblado</h1>
  <div>Departamento en arriendo Fernando De Arguello 6699, Vitacura, Chile</div>
  <ul><li>UF 49</li><li>140m2</li><li>2 Dormitorios</li><li>3 Baños</li></ul>
  <div>Gastos comunes: UF 15,78</div>
  <div>Año de construcción: 2.018</div>
  <section>Propiedades similares: Departamento en arriendo de 5 dorm. en
  Vitacura $3.200.000 · Departamento 6 dormitorios 7 baños</section>
</body></html>"""


def test_candidato_de_texto_lee_la_ficha_de_icasas(fuente):
    from arriendo.sources.generic import candidato_de_texto
    c = candidato_de_texto(_FICHA_ICASAS, "https://icasas.cl/prop/1", fuente,
                           valor_uf=40_854,
                           titulo="Arriendo Departamento amoblado")
    assert c is not None and c.extras["texto_anclado"]
    assert c.dormitorios == 2 and c.banos == 3
    assert c.gastos_comunes_clp == round(15.78 * 40_854)
    assert c.ano_construccion == 2018
    assert c.direccion == "" and c.comuna == "", \
        "la identidad nunca se toma del texto suelto"


def test_candidato_de_texto_sin_ancla_queda_marcado(fuente):
    from arriendo.sources.generic import candidato_de_texto
    c = candidato_de_texto(_FICHA_ICASAS, "https://icasas.cl/prop/1", fuente,
                           titulo="Un título que no aparece en la página")
    assert c is not None and not c.extras["texto_anclado"]


def test_candidato_de_texto_no_toma_numeros_sueltos_como_canon(fuente):
    """"UF $ 40.855,33" (el valor UF del día) y los promedios del sector
    andan sueltos por la ficha; sin rótulo no son el canon."""
    from arriendo.sources.generic import candidato_de_texto
    doc = """<html><body><h1>Departamento en arriendo en Vitacura</h1>
      <div>UF $ 40.855,33</div><div>promedio del sector $ 2.400.000</div>
      <div>Precio convertido: $1.817.308</div></body></html>"""
    c = candidato_de_texto(doc, "https://x.cl/1", fuente,
                           titulo="Departamento en arriendo en Vitacura")
    assert c.arriendo_clp == 1_817_308


# Los mapeos que salieron de los volcados del diagnóstico v2: los nodos son
# los REALES de cada portal, recortados.

def test_toctoc_publica_el_precio_como_lista_de_monedas(fuente):
    """TocToc entero daba CERO avisos con el inventario a la vista: su
    NEXT_DATA usa `precios: [{prefix, value}]` y `urlFicha`."""
    import json as _json
    nodo = {"props": {"pageProps": {"propiedades": {"results": [{
        "titulo": "Amplio depto Vitacura, muy iluminado, vista despejada",
        "comuna": "Vitacura", "region": "Metropolitana",
        "urlFicha": "https://www.toctoc.com/propiedades/arriendocorredorasr/"
                    "departamento/vitacura/amplio-depto/57d38326",
        "tipoPropiedad": "Departamento", "tipoOperacion": "Arriendo",
        "precios": [{"order": 0, "prefix": "UF", "value": "49"},
                    {"order": 1, "prefix": "$", "value": "2.001.911"}],
        "superficie": ["140", "140"], "dormitorios": ["3"]}]}}}}
    doc = (f'<html><body><script id="__NEXT_DATA__" type="application/json">'
           f'{_json.dumps(nodo, ensure_ascii=False)}</script></body></html>')
    avisos = extraer(doc, "https://www.toctoc.com/arriendo/departamento",
                     fuente)
    assert len(avisos) == 1
    a = avisos[0]
    assert a.url.endswith("57d38326")
    assert a.arriendo_clp == 2_001_911 and a.arriendo_uf == 49
    assert a.m2_totales == 140
    assert a.dormitorios == 3
    assert a.comuna == "Vitacura"


def test_houm_publica_el_canon_dentro_de_potentialaction(fuente):
    """El nodo real de houm: ApartmentComplex → RentAction →
    PriceSpecification. Además el aviso es de LAS CONDES dentro del listado
    de Vitacura: la comuna sale de la ruta, no del listado."""
    import json as _json
    from dataclasses import replace
    nodo = {"@type": "ApartmentComplex", "name": "Avenida Presidente Kennedy",
            "url": "https://www.houm.com/cl/arriendo-departamento-region-"
                   "metropolitana/las-condes/170742",
            "potentialAction": {"@type": "RentAction", "priceSpecification": {
                "@type": "PriceSpecification", "price": 2200000,
                "priceCurrency": "CLP"}},
            "address": {"@type": "PostalAddress",
                        "addressLocality": "Avenida Presidente Kennedy",
                        "addressRegion": "Region Metropolitana"}}
    doc = (f'<html><body><script type="application/ld+json">'
           f'{_json.dumps(nodo, ensure_ascii=False)}</script></body></html>')
    f = replace(fuente, comuna_default="Vitacura")
    a = extraer(doc, "https://houm.com/cl/arriendo-vitacura", f)[0]
    assert a.arriendo_clp == 2_200_000
    assert a.comuna == "Las Condes", \
        "la ruta del aviso le gana a la comuna del listado"


def test_un_nodo_con_puro_nombre_no_es_un_aviso(fuente):
    """propertypartners producía cuatro "avisos" que eran Place
    {name: "Región Metropolitana"}: cascarones sin un solo dato."""
    doc = ('<html><body><script type="application/ld+json">'
           '[{"@type": "Place", "name": "Región Metropolitana"},'
           ' {"@type": "Place", "name": "Vitacura"}]'
           '</script></body></html>')
    assert extraer(doc, "https://ejemplo.cl/arriendo", fuente) == []


@pytest.mark.parametrize("url,esperada", [
    ("https://www.houm.com/cl/arriendo-departamento-region-metropolitana/"
     "las-condes/170742", "Las Condes"),
    # La ruta va de lo grande a lo chico: gana la última.
    ("https://r.cl/metropolitana-de-santiago/santiago/vitacura/dep-123",
     "Vitacura"),
    ("https://www.goplaceit.com/cl/propiedad/arriendo/departamento/"
     "vitacura/12054265-arriendo-dpto", "Vitacura"),
    ("https://portal.cl/aviso/98765", ""),
])
def test_parse_comuna_de_url(url, esperada):
    from arriendo import parse as P
    assert P.parse_comuna_de_url(url) == esperada


def test_direccion_jsonld_sin_comuna_repetida(fuente):
    """El streetAddress de un metabuscador ya trae comuna, región y país;
    pegarle addressLocality y addressRegion producía "Vitacura" dos veces."""
    import json as _json
    nodo = {"@type": "Apartment", "name": "Departamento 4D en Lo Castillo",
            "url": "https://ejemplo.cl/aviso/77",
            "address": {
                "streetAddress": "Los Acantos 1234, Lo Castillo, Vitacura, "
                                 "Región Metropolitana de Santiago, Chile",
                "addressLocality": "Vitacura",
                "addressRegion": "Metropolitana de Santiago"}}
    doc = ('<html><body><script type="application/ld+json">'
           f'{_json.dumps(nodo, ensure_ascii=False)}</script></body></html>')
    a = extraer(doc, "https://ejemplo.cl/arriendo", fuente)[0]
    assert a.direccion.lower().count("vitacura") == 1
    assert a.comuna == "Vitacura"


def test_banos_3_no_es_una_direccion():
    """"Baños: 3" salió como dirección real — y por lo tanto como llave de
    deduplicación y título del mensaje."""
    from arriendo.sources.generic import _direccion_desde
    assert _direccion_desde("Departamento con Baños: 3 y cocina equipada",
                            "Vitacura") in ("", "Vitacura")
