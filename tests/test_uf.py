"""Tests del valor de la UF.

No es un dato de adorno: Yapo publica buena parte de su inventario de Vitacura
en UF, así que para esa fuente el precio en pesos lo calcula este radar, y de
ese cálculo depende si el aviso pasa o no el tope de presupuesto.
"""

import json
from datetime import date, timedelta

import pytest

from arriendo.parse import VALOR_UF_DEFECTO
from arriendo.uf import ARCHIVO_CACHE, valor_uf


class RespuestaFalsa:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class SesionFalsa:
    def __init__(self, respuesta=None, excepcion=None):
        self.respuesta, self.excepcion = respuesta, excepcion
        self.llamadas = 0

    def get(self, url, **kw):
        self.llamadas += 1
        if self.excepcion:
            raise self.excepcion
        return self.respuesta


def _serie(valor):
    return RespuestaFalsa({"serie": [{"fecha": "2026-08-16", "valor": valor}]})


# ---------------------------------------------------------------------------
# La cascada, escalón por escalón
# ---------------------------------------------------------------------------

def test_la_variable_del_entorno_gana_sobre_todo(tmp_path):
    """Una decisión explícita de una persona manda sobre la API."""
    sesion = SesionFalsa(_serie(41_000))
    v, origen = valor_uf(tmp_path, {"VALOR_UF": "39.500"}, sesion)
    assert v == 39_500
    assert "VALOR_UF" in origen
    assert sesion.llamadas == 0, "ni siquiera debería consultar la API"


def test_formato_chileno_en_la_variable(tmp_path):
    v, _ = valor_uf(tmp_path, {"VALOR_UF": "41.250,75"}, SesionFalsa(_serie(1)))
    assert v == 41_250.75


def test_usa_la_api_cuando_no_hay_variable(tmp_path):
    v, origen = valor_uf(tmp_path, {}, SesionFalsa(_serie(41_123.45)))
    assert v == 41_123.45
    assert "API" in origen


def test_la_api_se_guarda_en_cache(tmp_path):
    valor_uf(tmp_path, {}, SesionFalsa(_serie(41_123.45)))
    guardado = json.loads((tmp_path / ARCHIVO_CACHE).read_text(encoding="utf-8"))
    assert guardado["valor"] == 41_123.45
    assert guardado["cuando"] == date.today().isoformat()


def test_cae_a_la_cache_cuando_la_api_falla(tmp_path):
    """Un valor de hace dos días es muchísimo mejor que la constante.

    La UF se mueve unos pocos pesos al día; la constante del código lleva
    meses escrita.
    """
    ayer = (date.today() - timedelta(days=2)).isoformat()
    (tmp_path / ARCHIVO_CACHE).write_text(
        json.dumps({"valor": 41_050.0, "cuando": ayer}), encoding="utf-8")

    v, origen = valor_uf(tmp_path, {}, SesionFalsa(excepcion=OSError("sin red")))
    assert v == 41_050.0
    assert "caché" in origen
    assert "2 día" in origen


def test_cae_a_la_constante_sin_api_ni_cache(tmp_path):
    v, origen = valor_uf(tmp_path, {}, SesionFalsa(excepcion=OSError("sin red")))
    assert v == VALOR_UF_DEFECTO
    assert "constante" in origen


# ---------------------------------------------------------------------------
# Nada de esto puede tumbar la corrida
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("respuesta", [
    RespuestaFalsa({}, 500),                    # la API se cayó
    RespuestaFalsa({"serie": []}),              # respondió vacío
    RespuestaFalsa({"otra_cosa": 1}),           # cambió el formato
    RespuestaFalsa({"serie": [{"valor": None}]}),
    RespuestaFalsa({"serie": [{"valor": "no es un número"}]}),
])
def test_una_api_rota_no_levanta(tmp_path, respuesta):
    v, origen = valor_uf(tmp_path, {}, SesionFalsa(respuesta))
    assert v == VALOR_UF_DEFECTO
    assert "constante" in origen


@pytest.mark.parametrize("absurdo", [0, -1, 5, 1_000_000, 41])
def test_un_valor_absurdo_se_rechaza(tmp_path, absurdo):
    """Un typo convertiría todos los cánones en UF en basura."""
    v, _ = valor_uf(tmp_path, {}, SesionFalsa(_serie(absurdo)))
    assert v == VALOR_UF_DEFECTO


def test_una_variable_absurda_tambien_se_rechaza(tmp_path):
    v, _ = valor_uf(tmp_path, {"VALOR_UF": "41"}, SesionFalsa(_serie(41_000)))
    assert v == 41_000, "debería caer al escalón siguiente, no a la constante"


def test_una_cache_corrupta_no_revienta(tmp_path):
    (tmp_path / ARCHIVO_CACHE).write_text("{ esto no es json", encoding="utf-8")
    v, origen = valor_uf(tmp_path, {}, SesionFalsa(excepcion=OSError()))
    assert v == VALOR_UF_DEFECTO


def test_sin_state_dir_igual_funciona():
    v, _ = valor_uf(None, {}, SesionFalsa(_serie(41_000)))
    assert v == 41_000


# ---------------------------------------------------------------------------
# Por qué importa
# ---------------------------------------------------------------------------

def test_la_uf_decide_si_un_aviso_de_yapo_pasa_el_tope(tmp_path):
    """El caso concreto que motivó todo el módulo.

    UF 39 con la constante entra al presupuesto; con la UF real de $41.500 se
    pasa. El mismo departamento, dos veredictos.
    """
    from arriendo import scoring as S
    from arriendo.config import cargar_perfil
    from arriendo.parse import parse_montos

    perfil = cargar_perfil()
    tope, _ = S.tope_arriendo(perfil)

    con_constante = parse_montos("CLF 39.00", valor_uf=40_800)["arriendo_clp"]
    con_real = parse_montos("CLF 39.00", valor_uf=41_500)["arriendo_clp"]

    assert con_constante <= tope
    assert con_real > tope
