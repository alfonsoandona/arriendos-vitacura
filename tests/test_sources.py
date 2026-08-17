"""Tests del catálogo de fuentes y de lo que cada fuente le aporta al aviso.

Acá vive lo que depende de la CONFIGURACIÓN de la fuente y no del HTML: lo que
el listado ya sabe por venir filtrado, y que sus avisos por lo tanto no
repiten.
"""

from arriendo.sources.base import FuenteConfig
from arriendo.sources.generic import extraer


# ---------------------------------------------------------------------------
# La comuna del listado
# ---------------------------------------------------------------------------

def test_un_listado_filtrado_por_comuna_se_la_pone_a_sus_avisos():
    """Una tarjeta no repite lo que el usuario acaba de elegir en el filtro.

    Un aviso dentro de `/departamento/arriendo/comuna/306-vitacura` no dice
    "Vitacura" en ninguna parte, y sin ese dato pierde el multiplicador que
    hace cumplir "prioriza Vitacura comuna entera": se puntúa como si no se
    supiera dónde está y se va al fondo del tablero.
    """
    fuente = FuenteConfig(id="gp", nombre="GoPlaceIt", comuna_default="Vitacura",
                          urls=["https://gp.cl/arriendo/comuna/306-vitacura"])
    html = """<html><body><article>
        <a href="/aviso/1">Departamento 3D 3B, 134 m², $1.450.000</a>
      </article></body></html>"""

    avisos = extraer(html, "https://gp.cl/arriendo/comuna/306-vitacura", fuente)
    assert avisos and avisos[0].comuna == "Vitacura"


def test_la_comuna_del_aviso_le_gana_a_la_del_listado():
    """Un listado de Vitacura igual trae avisos colados de Las Condes.

    Pisarles la comuna sería inventar datos, que es peor que no tenerlos: un
    dato ausente se nota y alguien mira; uno falso no lo revisa nadie.
    """
    fuente = FuenteConfig(id="gp", nombre="GoPlaceIt", comuna_default="Vitacura",
                          urls=["https://gp.cl/x"])
    html = """<html><body><article>
        <a href="/aviso/2">Departamento en Las Condes, 3D, 120 m², $1.400.000</a>
      </article></body></html>"""

    avisos = extraer(html, "https://gp.cl/x", fuente)
    assert avisos and avisos[0].comuna == "Las Condes"


def test_la_comuna_del_listado_queda_marcada_como_deducida():
    """La marca es la que deja que las coordenadas la desmientan.

    `evaluar_zona` descarta un aviso cuya comuna se dedujo cuando las
    coordenadas dicen que está a 100 km. Sin la marca, un aviso colado de otra
    región entraría como Vitacura con datos que se contradicen entre sí.
    """
    fuente = FuenteConfig(id="gp", nombre="GoPlaceIt", comuna_default="Vitacura",
                          urls=["https://gp.cl/x"])
    html = """<html><body><article>
        <a href="/aviso/3">Departamento 3D, 130 m², $1.500.000</a>
      </article></body></html>"""

    a = extraer(html, "https://gp.cl/x", fuente)[0]
    assert a.extras.get("comuna_origen")


def test_sin_comuna_default_no_se_inventa_nada():
    fuente = FuenteConfig(id="x", nombre="X", urls=["https://x.cl/"])
    html = """<html><body><article>
        <a href="/aviso/4">Departamento 3D, 130 m², $1.500.000</a>
      </article></body></html>"""

    assert not extraer(html, "https://x.cl/", fuente)[0].comuna
