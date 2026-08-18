"""Tests de la gestión personal (gestion.yml).

Es la pieza que resuelve el problema del usuario con poco tiempo: cada día
llegan avisos nuevos, algunos se alcanzan a mirar y otros no, y el radar
tiene que recordar cuáles ya no valen la pena y qué se averiguó de los que
sí. Un descarte tuyo que se olvida —o un dato tuyo que no entra al puntaje—
convierte el medio minuto que invertiste en trabajo perdido.
"""

from pathlib import Path

import pytest
import yaml

from arriendo.config import cargar_perfil
from arriendo.gestion import GestionInvalida, aplicar, cargar
from arriendo.models import Arriendo
from arriendo import scoring as S


def _escribir(tmp_path: Path, datos: dict) -> Path:
    ruta = tmp_path / "gestion.yml"
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True),
                    encoding="utf-8")
    return ruta


def _aviso(**kw):
    base = dict(source="f1", url="https://f1.cl/aviso/1", title="Depto",
                direccion="Espoz 2620", comuna="Vitacura",
                dormitorios=3, banos=2, m2_totales=120,
                arriendo_clp=1_500_000)
    base.update(kw)
    return Arriendo(**base)


# ---------------------------------------------------------------------------
# Cargar
# ---------------------------------------------------------------------------

def test_sin_archivo_no_hay_gestion(tmp_path):
    assert cargar(tmp_path / "no-existe.yml") == {}


def test_carga_y_normaliza_el_codigo(tmp_path):
    ruta = _escribir(tmp_path, {"departamentos": [
        {"codigo": "#abc12", "estado": "visita", "nota": "ok"}]})
    g = cargar(ruta)
    assert g == {"ABC12": {"estado": "visita", "nota": "ok"}}


def test_estado_desconocido_falla_con_el_codigo(tmp_path):
    ruta = _escribir(tmp_path, {"departamentos": [
        {"codigo": "ABC12", "estado": "pendiente"}]})
    with pytest.raises(GestionInvalida, match="ABC12"):
        cargar(ruta)


def test_campo_desconocido_falla(tmp_path):
    """Un typo en silencio es una gestión que no hace lo que su YAML dice."""
    ruta = _escribir(tmp_path, {"departamentos": [
        {"codigo": "ABC12", "gastos_communes": 250000}]})
    with pytest.raises(GestionInvalida, match="gastos_communes"):
        cargar(ruta)


def test_valor_fuera_de_rango_falla(tmp_path):
    ruta = _escribir(tmp_path, {"departamentos": [
        {"codigo": "ABC12", "piso": 400}]})
    with pytest.raises(GestionInvalida, match="piso"):
        cargar(ruta)


# ---------------------------------------------------------------------------
# Aplicar
# ---------------------------------------------------------------------------

def test_descartado_por_ti_sale_y_no_alerta():
    a = _aviso()
    perfil = cargar_perfil()
    S.evaluar(a, perfil)
    assert not a.descartado

    aplicar([a], {a.codigo: {"estado": "descartado",
                             "nota": "muy oscuro"}}, perfil)
    assert a.descartado
    assert a.clase_descarte == "gestion"
    assert "muy oscuro" in a.motivo_descarte
    assert not S.debe_alertar(a, perfil), \
        "un descarte tuyo apaga la alerta para siempre"


def test_tus_datos_pisan_al_aviso_y_recalculan():
    perfil = cargar_perfil()
    a = _aviso(gastos_comunes_clp=None)
    S.evaluar(a, perfil)
    sin_gc = a.score

    aplicar([a], {a.codigo: {"estado": "", "nota": "",
                             "gastos_comunes_clp": 180_000,
                             "ano_construccion": 2018}}, perfil)
    assert a.gastos_comunes_clp == 180_000
    assert a.ano_construccion == 2018
    assert a.antiguedad_anos is not None
    assert "gastos_comunes_clp" in a.extras["datos_tuyos"]
    assert a.score != sin_gc, "el puntaje se recalcula con tus datos"


def test_el_estado_viaja_a_extras():
    perfil = cargar_perfil()
    a = _aviso()
    S.evaluar(a, perfil)
    aplicar([a], {a.codigo: {"estado": "visita", "nota": "martes 10:00"}},
            perfil)
    assert a.extras["gestion"] == {"estado": "visita", "nota": "martes 10:00"}
    assert not a.descartado


def test_un_codigo_que_no_esta_no_toca_nada():
    perfil = cargar_perfil()
    a = _aviso()
    S.evaluar(a, perfil)
    antes = a.score
    n = aplicar([a], {"ZZZZ9": {"estado": "descartado", "nota": ""}}, perfil)
    assert n == 0 and a.score == antes and not a.descartado


def test_tu_dato_puede_descartar_por_el_filtro():
    """La otra cara de completar datos: si averiguas que tiene 40 años, el
    requisito duro hace su trabajo — mejor saberlo por ti que visitarlo."""
    perfil = cargar_perfil()
    a = _aviso(antiguedad_anos=None)
    S.evaluar(a, perfil)
    assert not a.descartado
    aplicar([a], {a.codigo: {"estado": "", "nota": "",
                             "ano_construccion": 1985}}, perfil)
    assert a.descartado
    assert a.clase_descarte == "antiguedad"
