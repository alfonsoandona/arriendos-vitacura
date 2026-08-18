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
