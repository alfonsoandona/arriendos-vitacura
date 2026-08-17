"""Tests del historial de búsquedas.

Lo que se está protegiendo acá es la memoria del mercado: los datos con los
que, dentro de tres meses, se va a poder decir "un 3D de 120 m² en Vitacura se
arrienda en $1,48M y se demora 25 días". Si estos eventos se anotan mal, el
error no se nota en la corrida —todo funciona igual— y se descubre recién
cuando el número resultante es una mentira.

El riesgo grande tiene nombre: dar por arrendado un departamento porque el
portal que lo publicaba se cayó un martes.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from arriendo import historial as H
from arriendo.models import Arriendo
from arriendo.store import Store
from arriendo.tiempo import ahora_utc


def _aviso(**kw) -> Arriendo:
    base = dict(source="toctoc", url="https://toctoc.com/aviso/1",
                title="Departamento en Vitacura", direccion="Alonso de Córdova 4200",
                comuna="Vitacura", arriendo_clp=1_500_000, m2_totales=130,
                dormitorios=3)
    base.update(kw)
    return Arriendo(**base)


def _hace(dias: int) -> str:
    return (date.today() - timedelta(days=dias)).isoformat()


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------

def test_un_departamento_nuevo_es_un_alta(tmp_path):
    store = Store(tmp_path)
    eventos = H.eventos_de_corrida([_aviso()], store, {"toctoc"})

    assert len(eventos) == 1
    assert eventos[0]["evento"] == "alta"
    assert eventos[0]["clp"] == 1_500_000
    assert eventos[0]["comuna"] == "Vitacura"


def test_uno_ya_conocido_no_genera_alta(tmp_path):
    store = Store(tmp_path)
    a = _aviso()
    store.registrar(a)

    assert H.eventos_de_corrida([a], store, {"toctoc"}) == []


def test_un_cambio_de_precio_queda_anotado(tmp_path):
    store = Store(tmp_path)
    store.registrar(_aviso())

    eventos = H.eventos_de_corrida([_aviso(arriendo_clp=1_400_000)], store,
                                   {"toctoc"})
    assert len(eventos) == 1
    assert eventos[0] == {
        "cuando": date.today().isoformat(), "evento": "precio",
        "fp": _aviso().fingerprint, "clp": 1_400_000, "antes": 1_500_000,
        "direccion": "Alonso de Córdova 4200",
    }


def test_el_alta_guarda_lo_que_hace_falta_para_las_medianas(tmp_path):
    """Sin superficie no hay precio por m², que es la cifra que más sirve."""
    store = Store(tmp_path)
    e = H.eventos_de_corrida([_aviso()], store, {"toctoc"})[0]

    for campo in ("clp", "m2", "dorm", "comuna", "cuando"):
        assert e.get(campo), f"falta {campo} en el evento de alta"


# ---------------------------------------------------------------------------
# Bajas: el riesgo grande
# ---------------------------------------------------------------------------

def test_una_ausencia_suelta_no_da_por_arrendado(tmp_path):
    """Un portal se cae todo el tiempo. Una corrida sin verlo no prueba nada."""
    store = Store(tmp_path)
    store.registrar(_aviso())

    assert H.eventos_de_corrida([], store, {"toctoc"}) == []


def test_a_las_tres_ausencias_si(tmp_path):
    store = Store(tmp_path)
    store.registrar(_aviso())

    for _ in range(H.AUSENCIAS_PARA_BAJA - 1):
        assert H.eventos_de_corrida([], store, {"toctoc"}) == []

    eventos = H.eventos_de_corrida([], store, {"toctoc"})
    assert len(eventos) == 1
    assert eventos[0]["evento"] == "baja"
    assert eventos[0]["direccion"] == "Alonso de Córdova 4200"


def test_la_baja_se_avisa_una_sola_vez(tmp_path):
    """Con `>=` en vez de `==` se repetiría en cada corrida hasta la purga."""
    store = Store(tmp_path)
    store.registrar(_aviso())

    bajas = 0
    for _ in range(8):
        bajas += sum(1 for e in H.eventos_de_corrida([], store, {"toctoc"})
                     if e["evento"] == "baja")
    assert bajas == 1


def test_un_portal_caido_no_arrienda_sus_departamentos(tmp_path):
    """El bug que este módulo tiene que no cometer.

    Sin la condición de "su fuente sí entregó", un martes con TocToc caído
    daría por arrendados a sus cuarenta departamentos, y esos cuarenta
    entrarían a la mediana de "días hasta arrendarse" con un número inventado.
    """
    store = Store(tmp_path)
    store.registrar(_aviso())

    for _ in range(10):
        # La fuente no entregó en ninguna de las diez corridas.
        assert H.eventos_de_corrida([], store, set()) == []

    # Y cuando vuelve a entregar, la cuenta parte de cero.
    for _ in range(H.AUSENCIAS_PARA_BAJA - 1):
        assert H.eventos_de_corrida([], store, {"toctoc"}) == []
    assert any(e["evento"] == "baja"
               for e in H.eventos_de_corrida([], store, {"toctoc"}))


def test_reaparecer_borra_las_ausencias(tmp_path):
    """Un aviso que falta dos corridas y vuelve no está de salida."""
    store = Store(tmp_path)
    a = _aviso()
    store.registrar(a)

    H.eventos_de_corrida([], store, {"toctoc"})
    H.eventos_de_corrida([], store, {"toctoc"})
    H.eventos_de_corrida([a], store, {"toctoc"})     # volvió

    # Y desde acá vuelve a hacer falta el ciclo completo.
    for _ in range(H.AUSENCIAS_PARA_BAJA - 1):
        assert H.eventos_de_corrida([], store, {"toctoc"}) == []
    assert any(e["evento"] == "baja"
               for e in H.eventos_de_corrida([], store, {"toctoc"}))


def test_la_baja_dice_cuantos_dias_estuvo(tmp_path):
    store = Store(tmp_path)
    store.registrar(_aviso())
    entrada = store.indice[_aviso().fingerprint]
    entrada["primera_vez"] = (ahora_utc() - timedelta(days=47)).isoformat()

    for _ in range(H.AUSENCIAS_PARA_BAJA - 1):
        H.eventos_de_corrida([], store, {"toctoc"})
    baja = H.eventos_de_corrida([], store, {"toctoc"})[0]

    assert baja["dias"] == 47


def test_lo_que_sigue_publicado_pero_salio_del_mercado_no_se_da_de_baja(tmp_path):
    """Un aviso que hoy resultó ser una venta sigue estando ahí.

    Las cifras se calculan sobre `del_mercado` —departamentos en arriendo de la
    zona— pero la PRESENCIA se mide contra todo lo que se vio. Sin esa
    distinción, un aviso que un día se reclasifica como venta empieza a contar
    ausencias y a las tres corridas se anota como arrendado: un evento que
    nunca ocurrió, metido en la mediana de "días hasta arrendarse".
    """
    store = Store(tmp_path)
    a = _aviso()
    store.registrar(a)

    for _ in range(6):
        # No está en el mercado medido, pero sí en lo que se vio.
        assert H.eventos_de_corrida([], store, {"toctoc"}, todos=[a]) == []


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------

def test_anotar_y_leer(tmp_path):
    H.anotar([{"cuando": "2026-01-01", "evento": "alta", "fp": "x"}], tmp_path)
    H.anotar([{"cuando": "2026-01-02", "evento": "baja", "fp": "x"}], tmp_path)

    eventos = H.leer(tmp_path)
    assert [e["evento"] for e in eventos] == ["alta", "baja"]


def test_una_linea_corrupta_no_pierde_el_resto(tmp_path):
    """Un job matado a mitad de escritura deja media línea.

    Perder por eso seis meses de historial sería mucho peor que saltarse un
    evento.
    """
    ruta = tmp_path / H.ARCHIVO
    ruta.write_text(
        json.dumps({"cuando": "2026-01-01", "evento": "alta"}) + "\n"
        '{"cuando": "2026-01-02", "eve\n'
        + json.dumps({"cuando": "2026-01-03", "evento": "baja"}) + "\n",
        encoding="utf-8")

    eventos = H.leer(tmp_path)
    assert [e["evento"] for e in eventos] == ["alta", "baja"]


def test_el_archivo_no_crece_sin_fin(tmp_path):
    H.anotar([{"cuando": "2026-01-01", "evento": "alta", "fp": str(i)}
              for i in range(H.MAX_EVENTOS + 500)], tmp_path)
    assert len(H.leer(tmp_path)) == H.MAX_EVENTOS


def test_anotar_nada_no_crea_el_archivo(tmp_path):
    H.anotar([], tmp_path)
    assert not (tmp_path / H.ARCHIVO).exists()


def test_leer_sin_archivo_devuelve_vacio(tmp_path):
    assert H.leer(tmp_path) == []


# ---------------------------------------------------------------------------
# Lo que el historial responde
# ---------------------------------------------------------------------------

def _altas(precios, m2=120, cuando=None):
    return [{"cuando": cuando or _hace(10), "evento": "alta", "fp": str(i),
             "comuna": "Vitacura", "clp": p, "m2": m2}
            for i, p in enumerate(precios)]


def test_la_mediana_necesita_datos_suficientes():
    """Una mediana sobre tres avisos es peor que no tener mediana.

    Peor porque parece un dato: se ve igual que una calculada sobre cuarenta,
    y sobre ella se decide cuánto ofrecer.
    """
    assert H.resumen_mercado(_altas([1_000_000, 2_000_000, 3_000_000]))["precio_mediano"] is None
    r = H.resumen_mercado(_altas([1_400_000, 1_500_000, 1_600_000, 1_700_000]))
    assert r["precio_mediano"] == 1_550_000


def test_el_precio_por_m2():
    r = H.resumen_mercado(_altas([1_200_000] * 4, m2=100))
    assert r["precio_m2_mediano"] == 12_000


def test_lo_viejo_no_entra_en_el_resumen():
    eventos = _altas([1_400_000, 1_500_000, 1_600_000, 1_700_000],
                     cuando=_hace(200))
    assert H.resumen_mercado(eventos, dias=90)["nuevos"] == 0


def test_se_puede_acotar_a_una_comuna():
    eventos = _altas([1_500_000] * 4)
    eventos += [{"cuando": _hace(5), "evento": "alta", "fp": "z",
                 "comuna": "Las Condes", "clp": 900_000, "m2": 80}]

    assert H.resumen_mercado(eventos)["nuevos"] == 5
    assert H.resumen_mercado(eventos, comuna="Vitacura")["nuevos"] == 4


def test_los_dias_hasta_arrendarse():
    eventos = [{"cuando": _hace(3), "evento": "baja", "fp": str(i),
                "comuna": "Vitacura", "clp": 1_500_000, "dias": d}
               for i, d in enumerate((10, 20, 30, 40))]
    assert H.resumen_mercado(eventos)["dias_hasta_arrendarse"] == 25


def test_la_rebaja_mediana():
    eventos = [{"cuando": _hace(3), "evento": "precio", "fp": str(i),
                "clp": 900_000, "antes": 1_000_000} for i in range(4)]
    r = H.resumen_mercado(eventos)
    assert r["rebajas"] == 4
    assert r["rebaja_mediana_pct"] == 10


def test_una_subida_no_cuenta_como_rebaja():
    eventos = [{"cuando": _hace(3), "evento": "precio", "fp": "a",
                "clp": 1_100_000, "antes": 1_000_000}]
    assert H.resumen_mercado(eventos)["rebajas"] == 0


def test_contar_por_mes():
    eventos = [{"cuando": "2026-03-04", "evento": "alta"},
               {"cuando": "2026-03-19", "evento": "alta"},
               {"cuando": "2026-04-01", "evento": "alta"},
               {"cuando": "2026-04-02", "evento": "baja"}]
    assert H.contar_por_mes(eventos) == {"2026-03": 2, "2026-04": 1}


def test_resumen_vacio_no_revienta():
    r = H.resumen_mercado([])
    assert r["nuevos"] == 0 and r["precio_mediano"] is None


# ---------------------------------------------------------------------------
# "Este ya lo vi"
# ---------------------------------------------------------------------------

def test_reconoce_un_departamento_que_vuelve_por_otro_portal():
    """El caso que el fingerprint no puede atrapar.

    Mismo departamento, otro portal, otra URL, tres meses después. Para el
    estado es un aviso nuevo; para quien busca es la misma oferta que ya no se
    arrendó, y eso es exactamente lo que hay que saber antes de llamar.
    """
    eventos = [{"cuando": _hace(120), "evento": "alta", "fp": "viejo",
                "direccion": "Alonso de Córdova 4200", "comuna": "Vitacura",
                "clp": 1_750_000}]
    ahora = _aviso(source="yapo", url="https://yapo.cl/otro",
                   arriendo_clp=1_550_000)

    previo = H.ya_visto(eventos, ahora)
    assert previo and previo["clp"] == 1_750_000


def test_no_se_reconoce_a_si_mismo():
    a = _aviso()
    eventos = [{"cuando": _hace(120), "evento": "alta", "fp": a.fingerprint,
                "direccion": a.direccion, "comuna": "Vitacura",
                "clp": 1_500_000}]
    assert H.ya_visto(eventos, a) is None


def test_sin_direccion_no_se_arriesga():
    """Cruzar por título juntaría departamentos distintos del mismo edificio."""
    assert H.ya_visto([], _aviso(direccion="", comuna="")) is None


# ---------------------------------------------------------------------------
# La página
# ---------------------------------------------------------------------------

def test_el_markdown_se_arma():
    eventos = _altas([1_400_000, 1_500_000, 1_600_000, 1_700_000])
    texto = H.a_markdown(eventos)

    assert "# Historial de búsquedas" in texto
    assert "$1.550.000" in texto
    assert "Últimos movimientos" in texto


def test_el_markdown_dice_cuando_no_hay_datos_suficientes():
    """En vez de mostrar una mediana de dos avisos como si fuera un dato."""
    texto = H.a_markdown(_altas([1_500_000]))
    assert "no hay suficientes avisos" in texto


def test_el_markdown_con_historial_vacio_no_revienta():
    assert "Historial de búsquedas" in H.a_markdown([])

# ---------------------------------------------------------------------------
# Los gastos comunes típicos
# ---------------------------------------------------------------------------

def _altas_con_gc(n=5, gc=180_000, m2=120):
    return [{"cuando": _hace(10), "evento": "alta", "fp": str(i),
             "comuna": "Vitacura", "clp": 1_500_000, "gc": gc, "m2": m2}
            for i in range(n)]


def test_gc_tipico_por_m2():
    """$1.500/m² sobre 120 m² del historial → ≈$150.000 para uno de 100 m²."""
    assert H.gc_tipico(_altas_con_gc(gc=180_000, m2=120), m2=100) == 150_000


def test_gc_tipico_sin_superficie_usa_la_mediana_a_secas():
    assert H.gc_tipico(_altas_con_gc(gc=180_000), m2=None) == 180_000


def test_gc_tipico_con_pocos_datos_no_inventa():
    """Una estimación sacada de dos avisos parece un dato y no lo es."""
    assert H.gc_tipico(_altas_con_gc(n=3), m2=100) is None


def test_gc_tipico_se_redondea_a_diez_mil():
    """Precisión de pesos exactos en una estimación es mentira tipográfica."""
    eventos = _altas_con_gc(gc=183_456, m2=120)
    assert H.gc_tipico(eventos, m2=120) % 10_000 == 0

