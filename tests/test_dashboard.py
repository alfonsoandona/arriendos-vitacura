"""Tests de los dashboards HTML y del geocoding por corrida.

Los dashboards son el pedido del 18-08: docs/index.html con todo el mercado
y docs/hoy.html con las últimas 24 horas, pisados en cada corrida. El
geocoding es lo que les da mapa: sin él, cero candidatos tienen coordenadas.
"""

from datetime import timedelta

import pytest

from arriendo import scoring as S
from arriendo.config import cargar_perfil
from arriendo.dashboard import escribir_dashboards
from arriendo.models import Arriendo
from arriendo.tiempo import ahora_utc


@pytest.fixture
def perfil():
    return cargar_perfil()


def depto(**kw):
    base = dict(source="f1", url="https://f1.cl/aviso/1",
                title="Departamento en Vitacura",
                direccion="Alonso de Córdova 4200", comuna="Vitacura",
                dormitorios=3, banos=2, m2_totales=120,
                arriendo_clp=1_500_000.0)
    base.update(kw)
    return Arriendo(**base)


def test_escribe_los_dos_archivos(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    indice, hoy = escribir_dashboards([a], tmp_path, perfil)
    assert indice.name == "index.html" and hoy.name == "hoy.html"
    texto = indice.read_text(encoding="utf-8")
    assert "candidatos vivos" in texto
    assert "se pisa en cada corrida" in texto
    assert "Alonso de Córdova 4200" in texto


def test_hoy_filtra_a_las_ultimas_24_horas(tmp_path, perfil):
    ahora = ahora_utc()
    nuevo = S.evaluar(depto(url="https://f1.cl/n",
                            direccion="Espoz 2620"), perfil)
    nuevo.extras["primera_vez"] = ahora.isoformat()
    viejo = S.evaluar(depto(url="https://f1.cl/v"), perfil)
    viejo.extras["primera_vez"] = (ahora - timedelta(days=3)).isoformat()

    _, hoy = escribir_dashboards([nuevo, viejo], tmp_path, perfil)
    texto = hoy.read_text(encoding="utf-8")
    assert "Espoz 2620" in texto
    assert "Alonso de Córdova" not in texto


def test_hoy_vacio_lo_dice_sin_drama(tmp_path, perfil):
    ahora = ahora_utc()
    viejo = S.evaluar(depto(), perfil)
    viejo.extras["primera_vez"] = (ahora - timedelta(days=3)).isoformat()
    _, hoy = escribir_dashboards([viejo], tmp_path, perfil)
    assert "Sin novedades en las últimas 24 horas" in \
        hoy.read_text(encoding="utf-8")


def test_el_mapa_aparece_con_coordenadas(tmp_path, perfil):
    a = S.evaluar(depto(lat=-33.3830, lon=-70.5650), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert "El barrio" in texto
    assert "<svg" in texto and 'class="punto' in texto
    assert "Sport Francés" in texto
    assert "google.com/maps" in texto, "cada punto abre Maps al tocarlo"


def test_sin_coordenadas_el_mapa_se_explica(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert "El mapa se está llenando" in texto


def test_el_histograma_marca_el_tope(tmp_path, perfil):
    avisos = [S.evaluar(depto(url=f"https://f1.cl/{i}",
                              direccion=f"Espoz {2600 + i}",
                              arriendo_clp=1_200_000.0 + i * 90_000), perfil)
              for i in range(6)]
    texto = escribir_dashboards(avisos, tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert "Dónde están los precios" in texto
    assert "tope $1.700.000" in texto
    assert "mediana" in texto


def test_la_tabla_es_ordenable_y_filtrable(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert 'id="tabla"' in texto and "data-col" in texto
    assert 'id="filtro"' in texto
    assert f"#{a.codigo}" in texto


def test_una_ficha_no_sale_dos_veces(tmp_path, perfil):
    a = S.evaluar(depto(url="https://f1.cl/1"), perfil)
    b = S.evaluar(depto(url="https://f1.cl/2",
                        arriendo_clp=1_600_000.0), perfil)
    texto = escribir_dashboards([a, b], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert texto.count("Alonso de Córdova 4200 <") <= 1 or \
        texto.count("<code>#") >= 1  # una fila, no dos
    filas = texto.count('<td><code>#')
    assert filas == 1


# ---------------------------------------------------------------------------
# El mapa interactivo y la capa de UX (pedido del 18-08, segunda ronda:
# "que el mapa sea interactivo, que si selecciono sepa qué inmueble es")
# ---------------------------------------------------------------------------

def _json_embebido(texto: str) -> list:
    import json as J
    cuerpo = texto.split('<script type="application/json" id="datos">')[1]
    return J.loads(cuerpo.split("</script>")[0])


def test_leaflet_va_vendored_no_de_un_cdn(tmp_path, perfil):
    a = S.evaluar(depto(lat=-33.3830, lon=-70.5650), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert (tmp_path / "lib" / "leaflet.js").exists()
    assert (tmp_path / "lib" / "leaflet.css").exists()
    assert (tmp_path / "lib" / "LICENSE").exists(), "BSD-2 viaja con el código"
    assert 'src="lib/leaflet.js"' in texto
    assert 'href="lib/leaflet.css"' in texto
    assert "unpkg.com" not in texto and "jsdelivr" not in texto


def test_los_datos_viajan_como_json_embebido(tmp_path, perfil):
    a = S.evaluar(depto(lat=-33.3830, lon=-70.5650), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    datos = _json_embebido(texto)
    assert len(datos) == 1
    d = datos[0]
    assert d["cod"] == a.codigo
    assert d["lat"] == pytest.approx(-33.3830)
    assert "Alonso de Córdova" in d["dir"]
    assert d["ptxt"].startswith("$")
    assert d["maps"], "el popup del mapa lleva su link a Google Maps"


def test_un_titulo_malicioso_no_cierra_el_script(tmp_path, perfil):
    a = S.evaluar(depto(direccion=None,
                        title="</script><script>alert(1)</script>"), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    crudo = texto.split('<script type="application/json" id="datos">')[1]
    crudo = crudo.split('<script src="lib/leaflet.js">')[0]
    assert crudo.count("</script>") == 1, \
        "dentro del bloque de datos solo puede cerrar el bloque mismo"
    datos = _json_embebido(texto)
    assert "alert(1)" in datos[0]["dir"], "el texto sobrevive, el tag no"


def test_el_mapa_leaflet_queda_cableado_con_respaldo(tmp_path, perfil):
    a = S.evaluar(depto(lat=-33.3830, lon=-70.5650), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert 'id="mapa"' in texto and 'data-lat=' in texto
    assert 'data-rpref' in texto, "los radios del perfil viajan al mapa"
    assert 'id="mapa-svg"' in texto and "<svg" in texto, \
        "sin Leaflet, el SVG geométrico sigue contando la historia"
    assert "tile.openstreetmap.org" in texto
    assert "openstreetmap.org/copyright" in texto, "atribución obligatoria"


def test_la_barra_de_filtros_y_el_orden_estan(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    for modo in ("todos", "nuevos", "bajaron", "mapa", "gestion"):
        assert f'data-modo="{modo}"' in texto
    assert 'id="orden"' in texto and 'id="cuenta"' in texto
    assert 'id="tema"' in texto, "el botón de tema claro/oscuro"
    assert 'data-l="$/mes"' in texto, "las tarjetas del teléfono llevan rótulo"
    assert f'id="r-{a.codigo}"' in texto, "cada fila es ancla del mapa"


def test_el_kpi_es_un_filtro_tocable(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_dashboards([a], tmp_path, perfil)[0].read_text(
        encoding="utf-8")
    assert 'data-modo="nuevos" role="button"' in texto


def test_hoy_tambien_lleva_mapa_y_datos(tmp_path, perfil):
    ahora = ahora_utc()
    a = S.evaluar(depto(lat=-33.3830, lon=-70.5650), perfil)
    a.extras["primera_vez"] = ahora.isoformat()
    _, hoy = escribir_dashboards([a], tmp_path, perfil)
    texto = hoy.read_text(encoding="utf-8")
    assert 'id="mapa"' in texto
    assert _json_embebido(texto)[0]["nuevo"] is True


# ---------------------------------------------------------------------------
# Geocoding por corrida
# ---------------------------------------------------------------------------

def test_geocodifica_reevalua_y_cachea(tmp_path, perfil, monkeypatch):
    from arriendo import cli as C, geo

    monkeypatch.setenv("ARRIENDO_STATE_DIR", str(tmp_path))
    llamadas = []

    def falso_geocode(consulta, session=None, timeout=20):
        llamadas.append(consulta)
        return (-33.3830, -70.5650)   # a ~1 km del Sport Francés

    monkeypatch.setattr(geo, "geocode", falso_geocode)

    a = S.evaluar(depto(), perfil)
    assert a.distancia_km is None
    stats: dict = {}
    C._geocodificar([a], perfil, stats)
    assert a.lat is not None
    assert a.distancia_km is not None, "con coordenadas hay distancia"
    assert stats["geocodificados"] == 1
    primeras = len(llamadas)

    # Segunda corrida: la caché responde, Nominatim no se molesta.
    b = S.evaluar(depto(), perfil)
    C._geocodificar([b], perfil, {})
    assert b.lat is not None
    assert len(llamadas) == primeras, "la dirección se paga UNA vez"


def test_un_resultado_implausible_se_descarta(tmp_path, perfil, monkeypatch):
    """Una calle homónima en otra ciudad no es la propiedad: peor que no
    tener coordenadas es tener las de otro lugar."""
    from arriendo import cli as C, geo

    monkeypatch.setenv("ARRIENDO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(geo, "geocode",
                        lambda *a, **k: (-33.04, -71.62))   # Valparaíso
    a = S.evaluar(depto(), perfil)
    C._geocodificar([a], perfil, {})
    assert a.lat is None


def test_el_tope_de_consultas_se_respeta(tmp_path, perfil, monkeypatch):
    from arriendo import cli as C, geo

    monkeypatch.setenv("ARRIENDO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(C, "TOPE_GEOCODE_POR_CORRIDA", 3)
    llamadas = []
    monkeypatch.setattr(geo, "geocode",
                        lambda c, **k: llamadas.append(c) or None)

    avisos = [S.evaluar(depto(url=f"https://f1.cl/{i}",
                              direccion=f"Espoz {2600 + i}"), perfil)
              for i in range(10)]
    C._geocodificar(avisos, perfil, {})
    assert len(llamadas) == 3


# ---------------------------------------------------------------------------
# El pin del mapa de la ficha y la geocodificación INVERSA (20-08).
#
# El usuario trajo un aviso real de yapo: "Dirección exacta: ¡Pregunta al
# anunciante!" y, al lado, el iframe de Google Maps con las coordenadas
# exactas. Sin dirección ese departamento era un aviso distinto del mismo
# departamento en mitula, y llegaron dos mensajes.
# ---------------------------------------------------------------------------

def test_el_pin_del_mapa_se_lee_en_sus_tres_formas():
    from arriendo.sources.generic import coords_de_mapa

    yapo = ('<iframe src="https://www.google.com/maps/embed/v1/place'
            '?key=AIza123&q=-33.397451500000000,-70.584671500000000"></iframe>')
    clasico = ('<iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12'
               '!2d-70.5846715!3d-33.3974515!2m3"></iframe>')
    escapado = '<iframe src="https://maps.google.com/maps?q=-33.3974%2C-70.5846">'
    for nombre, doc in (("yapo", yapo), ("pb", clasico), ("escapado", escapado)):
        pin = coords_de_mapa(doc)
        assert pin is not None, nombre
        assert -33.5 < pin[0] < -33.3 and -70.7 < pin[1] < -70.5, nombre


def test_un_mapa_fuera_de_chile_no_ubica_nada():
    from arriendo.sources.generic import coords_de_mapa

    assert coords_de_mapa('<iframe src="...?q=40.4168,-3.7038">') is None
    assert coords_de_mapa("<p>sin mapa</p>") is None


def test_la_inversa_llena_la_direccion_que_el_portal_esconde(
        tmp_path, perfil, monkeypatch):
    from arriendo import cli as C, geo

    monkeypatch.setenv("ARRIENDO_STATE_DIR", str(tmp_path))
    llamadas = []

    def falso_reverse(lat, lon, **kw):
        llamadas.append((lat, lon))
        return "Agustín del Castillo 1420"

    monkeypatch.setattr(geo, "reverse", falso_reverse)

    a = S.evaluar(depto(direccion=None, lat=-33.3974515, lon=-70.5846715),
                  perfil)
    assert not a.direccion
    C._geocodificar([a], perfil, {})
    assert a.direccion == "Agustín del Castillo 1420, Vitacura"
    assert a.extras["dir_origen"] == "del pin del mapa del aviso"

    # Segunda corrida: la caché responde, Nominatim no se molesta.
    b = S.evaluar(depto(url="https://f1.cl/otro", direccion=None,
                        lat=-33.3974515, lon=-70.5846715), perfil)
    C._geocodificar([b], perfil, {})
    assert b.direccion == "Agustín del Castillo 1420, Vitacura"
    assert len(llamadas) == 1, "el punto se paga UNA vez"


def test_con_la_calle_recuperada_los_dos_portales_se_funden(perfil):
    """El desenlace del caso real: yapo sin dirección y mitula con ella eran
    dos avisos y dos mensajes. Con la calle del pin, son uno."""
    from arriendo.store import deduplicar

    de_yapo = S.evaluar(depto(url="https://yapo.cl/aviso/32868761",
                              source="yapo",
                              direccion="Agustín del Castillo 1420",
                              arriendo_clp=1_634_318.0), perfil)
    de_mitula = S.evaluar(depto(url="https://casas.mitula.cl/adform/abc",
                                source="mitula",
                                direccion="Agustín del Castillo 1420",
                                arriendo_clp=1_634_318.0), perfil)
    assert len(deduplicar([de_yapo, de_mitula])) == 1
