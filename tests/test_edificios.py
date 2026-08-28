"""La libreta de edificios: el año es del edificio, no del aviso."""

from __future__ import annotations

from datetime import date

import pytest

from arriendo.edificios import Libreta, aplicar
from arriendo.models import Arriendo


def _aviso(direccion="Espoz 4200", ano=None, source="toctoc", url="https://x/1"):
    a = Arriendo(source=source, url=url, title="Depto", comuna="Vitacura")
    a.direccion = direccion
    a.ano_construccion = ano
    return a


@pytest.fixture
def libreta(tmp_path):
    return Libreta(tmp_path)


def test_lo_que_un_aviso_ensena_le_sirve_al_vecino(libreta):
    """Dos departamentos en Espoz 4200 se construyeron el mismo año, los
    publique quien los publique. Es el criterio SÍ O SÍ del perfil y el
    dato más escaso de todos: en la corrida del 21-08 lo traía el 6%."""
    sabe = _aviso(ano=2010, source="goplaceit")
    no_sabe = _aviso(source="mitula", url="https://y/2")
    assert aplicar([sabe, no_sabe], libreta) == 1
    assert no_sabe.ano_construccion == 2010
    assert no_sabe.antiguedad_anos == date.today().year - 2010
    assert no_sabe.extras["ano_de_libreta"] is True, \
        "tiene que quedar dicho que el año no lo publicó este aviso"


def test_el_que_ya_sabe_su_ano_no_se_toca(libreta):
    propio = _aviso(ano=1998)
    otro = _aviso(ano=2010, url="https://y/2")
    aplicar([otro, propio], libreta)
    assert propio.ano_construccion == 1998
    assert "ano_de_libreta" not in propio.extras


def test_una_calle_sin_altura_no_es_un_edificio(libreta):
    """"Candelaria Goyenechea, Lo Castillo" es una calle entera con
    edificios de distintas décadas."""
    sabe = _aviso(direccion="Candelaria Goyenechea, Lo Castillo", ano=2007)
    no_sabe = _aviso(direccion="Candelaria Goyenechea, Lo Castillo",
                     url="https://y/2")
    assert aplicar([sabe, no_sabe], libreta) == 0
    assert no_sabe.ano_construccion is None


def test_un_desacuerdo_anula_la_entrada_en_vez_de_elegir(libreta):
    """Un año equivocado acá no es un dato feo: es un descarte falso de un
    departamento que servía, o una alerta de uno que no."""
    aplicar([_aviso(ano=2010), _aviso(ano=1975, url="https://y/2")], libreta)
    huerfano = _aviso(url="https://z/3")
    assert aplicar([huerfano], libreta) == 0
    assert huerfano.ano_construccion is None


def test_el_desacuerdo_queda_anulado_para_siempre(libreta):
    """Quien escribió el año mal una vez lo volvería a escribir igual."""
    aplicar([_aviso(ano=2010), _aviso(ano=1975, url="https://y/2")], libreta)
    aplicar([_aviso(ano=2010, url="https://w/4")], libreta)
    assert libreta.ano_de(_aviso()) is None


def test_un_ano_imposible_no_entra_a_la_libreta(libreta):
    aplicar([_aviso(ano=1830), _aviso(ano=299, url="https://y/2")], libreta)
    assert libreta.datos == {}


def test_ensena_antes_de_responder_en_la_misma_corrida(libreta):
    """Los duplicados de un mismo edificio llegan juntos, desde portales
    distintos, uno con año y otro sin. Si respondiera antes de aprender,
    el orden de la lista decidiría el resultado."""
    sin_ano = _aviso(source="mitula", url="https://y/2")
    con_ano = _aviso(ano=2015, source="toctoc")
    assert aplicar([sin_ano, con_ano], libreta) == 1
    assert sin_ano.ano_construccion == 2015


def test_la_libreta_sobrevive_a_la_corrida(tmp_path):
    """Los runners de GitHub Actions arrancan limpios: sin persistir, cada
    corrida volvería a empezar de cero."""
    primera = Libreta(tmp_path)
    aplicar([_aviso(ano=2010)], primera)
    primera.guardar()

    segunda = Libreta(tmp_path)
    huerfano = _aviso(url="https://y/2")
    assert aplicar([huerfano], segunda) == 1
    assert huerfano.ano_construccion == 2010


def test_sin_direccion_no_hay_nada_que_anotar(libreta):
    aplicar([_aviso(direccion="", ano=2010)], libreta)
    assert libreta.datos == {}


def test_la_direccion_basura_no_llega_a_la_libreta(libreta):
    """"Vitacura 3" no identifica un edificio: `clave_direccion` la vacía y
    la libreta hereda ese guardia entero."""
    aplicar([_aviso(direccion="Vitacura 3, Vitacura", ano=2010)], libreta)
    assert libreta.datos == {}


def test_la_libreta_se_limpia_sola_al_abrirla(tmp_path):
    """Lo que aprendió con un extractor viejo puede tener llaves que hoy no
    serían direcciones ("id 44348 las condes", "312 metropolitana juan
    xxiii 6859 301"). Esas entradas ya son inalcanzables —nadie va a volver
    a producir esa llave— y solo engordan el archivo."""
    import json
    (tmp_path / "edificios.json").write_text(json.dumps({
        "id 44348 las condes": {"ano": 1991},
        "edificio de 18": {"ano": 2003},
        "candelaria goyenechea sin altura": {"ano": 2007},
        "espoz 4200": {"ano": 2010},
    }), encoding="utf-8")

    libreta = Libreta(tmp_path)
    assert set(libreta.datos) == {"espoz 4200"}
    assert libreta.sucia, "el olvido tiene que llegar al archivo"


def test_una_direccion_que_hoy_no_es_direccion_no_entra(libreta):
    """El guardia vive en `clave_direccion`, así que la libreta lo hereda
    junto con la deduplicación y la memoria del store."""
    aplicar([_aviso(direccion="Edificio de 18, Vitacura", ano=2010)], libreta)
    aplicar([_aviso(direccion="ID 44348, Las Condes", ano=1991)], libreta)
    assert libreta.datos == {}


def test_las_llaves_largas_del_extractor_viejo_se_purgan(tmp_path):
    """"espoz 3276 vitacura santiago metropolitana de santiago" es la forma
    que escribió el extractor de ANTES del recorte de colas, y fragmentaba
    la libreta: el mismo edificio dos veces, una bajo una llave que las
    consultas de hoy ya nunca producen. La medición del 28-08 encontró
    cuatro pares así en 30 entradas."""
    import json
    (tmp_path / "edificios.json").write_text(json.dumps({
        "espoz 3276": {"ano": 2007},
        "espoz 3276 vitacura santiago metropolitana de santiago": {"ano": 2007},
        "312 metropolitana juan xxiii 6859 301": {"ano": 1983},
        "vitacura 9976": {"ano": 2015},
    }), encoding="utf-8")
    libreta = Libreta(tmp_path)
    assert set(libreta.datos) == {"espoz 3276", "vitacura 9976"}, \
        "la comuna como nombre de calle (Vitacura 9976) tiene que sobrevivir"
