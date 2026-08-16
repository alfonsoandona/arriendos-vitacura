"""Tests del estado persistente y de la deduplicación entre portales.

La deduplicación es la operación que hace usable este radar: el mismo
departamento está publicado en cuatro portales a la vez, y sin colapsarlo son
cuatro mensajes de Telegram del mismo departamento.
"""

from datetime import date, timedelta

import pytest

from arriendo.models import Arriendo
from arriendo.store import Store, deduplicar


def aviso(**kw) -> Arriendo:
    base = dict(
        source="toctoc",
        url="https://toctoc.com/aviso/1",
        title="Departamento en Vitacura",
        direccion="Alonso de Córdova 4200",
        comuna="Vitacura",
        tipo="departamento",
        m2_totales=134.0,
        dormitorios=3,
        arriendo_clp=1_500_000,
    )
    base.update(kw)
    return Arriendo(**base)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


# ---------------------------------------------------------------------------
# Identidad
# ---------------------------------------------------------------------------

def test_la_misma_direccion_escrita_distinto_es_el_mismo_departamento():
    """Cuatro portales, cuatro formas de escribir la misma dirección."""
    formas = [
        "Alonso de Córdova 4200",
        "Alonso de Córdova Nº 4200",
        "Calle Alonso de Cordova 4200",
        "Alonso De Cordova Vitacura 4200",
        "Alonso de Córdova 4200, Vitacura, Región Metropolitana",
    ]
    fps = {aviso(direccion=d).fingerprint for d in formas}
    assert len(fps) == 1, f"quedaron {len(fps)} identidades: {formas}"


def test_unidades_distintas_del_mismo_edificio_no_se_colapsan():
    """En un edificio de Vitacura se arriendan varias unidades a la vez.

    Colapsarlas haría perder todas menos una, que es peor que duplicar.
    """
    a = aviso(extras={"unidad": "802"})
    b = aviso(extras={"unidad": "1204"})
    assert a.fingerprint != b.fingerprint


def test_sin_direccion_la_url_identifica():
    a = aviso(direccion="", comuna="", url="https://x.cl/1", title="Depto A")
    b = aviso(direccion="", comuna="", url="https://x.cl/1", title="Depto B")
    c = aviso(direccion="", comuna="", url="https://x.cl/1", title="Depto A")
    assert a.fingerprint != b.fingerprint
    assert a.fingerprint == c.fingerprint


# ---------------------------------------------------------------------------
# Deduplicación entre portales
# ---------------------------------------------------------------------------

def test_cuatro_portales_un_solo_aviso():
    copias = [
        aviso(source="toctoc", url="https://toctoc.com/1"),
        aviso(source="yapo", url="https://yapo.cl/2",
              direccion="Alonso de Córdova Nº 4200"),
        aviso(source="chilepropiedades", url="https://cp.cl/3",
              direccion="Calle Alonso de Cordova 4200"),
        aviso(source="corredora", url="https://corredora.cl/4"),
    ]
    assert len(deduplicar(copias)) == 1


def test_la_fusion_sabe_mas_que_cualquier_copia():
    """Cada portal publica un subconjunto distinto de los datos.

    Este es el beneficio real de deduplicar: no es solo dejar de repetir, es
    que el aviso resultante está más completo que ninguno de los originales.
    """
    copias = [
        aviso(source="a", url="https://a.cl/1", m2_totales=134.0,
              antiguedad_anos=None, gastos_comunes_clp=None),
        aviso(source="b", url="https://b.cl/1", m2_totales=None,
              antiguedad_anos=8, gastos_comunes_clp=None),
        aviso(source="c", url="https://c.cl/1", m2_totales=None,
              antiguedad_anos=None, gastos_comunes_clp=190_000),
    ]
    (fusionado,) = deduplicar(copias)
    assert fusionado.m2_totales == 134.0
    assert fusionado.antiguedad_anos == 8
    assert fusionado.gastos_comunes_clp == 190_000


def test_la_fusion_no_pisa_un_dato_existente():
    copias = [
        aviso(source="a", url="https://a.cl/1", m2_totales=134.0),
        aviso(source="b", url="https://b.cl/1", m2_totales=999.0),
    ]
    (fusionado,) = deduplicar(copias)
    assert fusionado.m2_totales == 134.0


def test_se_guardan_los_otros_enlaces():
    """A veces otra copia trae mejores fotos o el teléfono directo."""
    copias = [
        aviso(source="a", url="https://a.cl/1", score=80),
        aviso(source="b", url="https://b.cl/2", score=70),
    ]
    (fusionado,) = deduplicar(copias)
    assert fusionado.extras["tambien_en"] == ["b|https://b.cl/2"]


def test_departamentos_distintos_no_se_fusionan():
    distintos = [
        aviso(direccion="Alonso de Córdova 4200"),
        aviso(direccion="Nueva Costanera 3600", url="https://x.cl/2"),
    ]
    assert len(deduplicar(distintos)) == 2


# ---------------------------------------------------------------------------
# Memoria entre corridas
# ---------------------------------------------------------------------------

def test_lo_avisado_no_vuelve_a_ser_nuevo(store):
    a = aviso()
    assert store.es_nuevo(a)
    store.registrar(a, avisado=True)
    assert not store.es_nuevo(a)
    assert store.ya_avisado(a)


def test_el_estado_sobrevive_a_la_corrida(store, tmp_path):
    store.registrar(aviso(), avisado=True)
    store.guardar()
    assert Store(tmp_path).ya_avisado(aviso())


def test_un_estado_corrupto_no_voltea_la_corrida(tmp_path):
    (tmp_path / "vistos.json").write_text("{ esto no es json", encoding="utf-8")
    assert Store(tmp_path).indice == {}


# ---------------------------------------------------------------------------
# La baja de canon — la señal del mercado de arriendo
# ---------------------------------------------------------------------------

@pytest.fixture
def perfil_reaviso():
    return {"alertas": {"reavisar": {"baja_precio_pct": 4,
                                     "dias_publicado_aviso": 45}}}


def test_la_baja_de_canon_vuelve_a_avisar(store, perfil_reaviso):
    store.registrar(aviso(arriendo_clp=1_600_000), avisado=True)
    motivo = store.cambio_relevante(aviso(arriendo_clp=1_450_000), perfil_reaviso)
    assert "Bajó" in motivo
    assert "9%" in motivo


def test_una_baja_minima_no_molesta(store, perfil_reaviso):
    """Un 1% es redondeo del portal, no una negociación."""
    store.registrar(aviso(arriendo_clp=1_600_000), avisado=True)
    assert store.cambio_relevante(aviso(arriendo_clp=1_585_000),
                                  perfil_reaviso) == ""


def test_una_subida_no_avisa(store, perfil_reaviso):
    store.registrar(aviso(arriendo_clp=1_500_000), avisado=True)
    assert store.cambio_relevante(aviso(arriendo_clp=1_600_000),
                                  perfil_reaviso) == ""


def test_lleva_mucho_publicado_avisa_una_sola_vez(store, perfil_reaviso):
    viejo = aviso(publicado_el=date.today() - timedelta(days=60))
    store.registrar(viejo, avisado=True)

    motivo = store.cambio_relevante(viejo, perfil_reaviso)
    assert "60 días publicado" in motivo

    # Al registrarlo con ese motivo queda marcado y no se repite.
    store.registrar(viejo, avisado=True, motivo=motivo)
    assert store.cambio_relevante(viejo, perfil_reaviso) == ""


# ---------------------------------------------------------------------------
# Aprender entre corridas
# ---------------------------------------------------------------------------

def test_hereda_lo_que_costo_averiguar(store):
    """Si TocToc publicó la superficie y Yapo no, no hay que volver a buscarla."""
    store.registrar(aviso(source="toctoc", m2_totales=134.0,
                          antiguedad_anos=8, lat=-33.38, lon=-70.56))

    flaco = aviso(source="yapo", url="https://yapo.cl/9",
                  m2_totales=None, antiguedad_anos=None)
    recuperados = store.completar(flaco)

    assert flaco.m2_totales == 134.0
    assert flaco.antiguedad_anos == 8
    assert flaco.lat == -33.38
    assert set(recuperados) >= {"m2_totales", "antiguedad_anos", "lat"}


def test_nunca_pisa_un_dato_fresco(store):
    store.registrar(aviso(m2_totales=134.0))
    fresco = aviso(m2_totales=140.0)
    store.completar(fresco)
    assert fresco.m2_totales == 140.0


def test_el_precio_no_se_hereda(store):
    """Heredarlo escondería la baja de canon, que es la señal que interesa."""
    store.registrar(aviso(arriendo_clp=1_600_000))
    sin_precio = aviso(arriendo_clp=None)
    store.completar(sin_precio)
    assert sin_precio.arriendo_clp is None


def test_cruza_por_direccion_cuando_el_fingerprint_no_alcanza(store):
    """Un portal publica la unidad y otro no: caen en fingerprints distintos."""
    store.registrar(aviso(source="toctoc", m2_totales=134.0))

    con_unidad = aviso(source="yapo", url="https://yapo.cl/9",
                       extras={"unidad": "802"}, m2_totales=None)
    assert con_unidad.fingerprint != aviso().fingerprint
    store.completar(con_unidad)
    assert con_unidad.m2_totales == 134.0


def test_no_hereda_cuando_hay_ambiguedad(store):
    """Dos unidades del mismo edificio: heredar le pegaría los datos de otra."""
    store.registrar(aviso(source="a", url="https://a.cl/1",
                          extras={"unidad": "802"}, m2_totales=134.0))
    store.registrar(aviso(source="b", url="https://b.cl/2",
                          extras={"unidad": "1204"}, m2_totales=180.0))

    tercero = aviso(source="c", url="https://c.cl/3", m2_totales=None)
    store.completar(tercero)
    assert tercero.m2_totales is None


# ---------------------------------------------------------------------------
# Mantenimiento
# ---------------------------------------------------------------------------

def test_purga_lo_viejo(store):
    store.registrar(aviso())
    fp = aviso().fingerprint
    store.indice[fp]["ultima_vez"] = "2020-01-01T00:00:00"
    assert store.purgar(dias=120) == 1
    assert fp not in store.indice


def test_la_baja_se_mide_contra_el_precio_avisado(store, perfil_reaviso):
    """Una baja gradual tiene que acumularse.

    Un canon que baja 2% por corrida durante tres corridas cayó 6% en total y
    nunca dispararía un umbral del 4% comparando contra la corrida anterior:
    cada paso individual se queda corto. Contra el precio con el que se avisó,
    la baja se acumula y se detecta.
    """
    store.registrar(aviso(arriendo_clp=1_600_000), avisado=True)

    # Dos corridas de bajas chicas: ninguna sola alcanza el umbral.
    for precio in (1_570_000, 1_540_000):
        motivo = store.cambio_relevante(aviso(arriendo_clp=precio), perfil_reaviso)
        store.registrar(aviso(arriendo_clp=precio), avisado=bool(motivo))

    # Acumuladas son 6,25%, y eso sí se avisa.
    motivo = store.cambio_relevante(aviso(arriendo_clp=1_500_000), perfil_reaviso)
    assert "Bajó" in motivo
    assert "1.600.000" in motivo, "la baja se mide desde el precio que el usuario vio"
