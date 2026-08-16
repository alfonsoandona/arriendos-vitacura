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
    assert req["arriendo_clp"]["max"] == 1_600_000


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
