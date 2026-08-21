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


# ---------------------------------------------------------------------------
# El rescate anti-bot
# ---------------------------------------------------------------------------

def test_un_403_se_reintenta_con_navegador(monkeypatch):
    """economicos.cl entregó 51 avisos un día y respondió 403 al siguiente.

    Sin el rescate, una de las fuentes más productivas queda muerta hasta que
    alguien la mire a mano. El 401/403 significa "no me gustan los clientes
    que no son un navegador" — así que se le manda un navegador.
    """
    from arriendo.sources import registry

    class FetcherFalso:
        ultimo_motivo = ""
        def get(self, url, reintentos=3, ignorar_robots=False):
            self.ultimo_motivo = ("HTTP 403 — el sitio rechaza clientes "
                                  "que no son un navegador")
            return None

    import arriendo.sources.navegador as nav
    monkeypatch.setattr(nav, "bajar_con_navegador",
                        lambda url, acciones=None, timeout_ms=None: "<html>ok</html>")

    fuente = FuenteConfig(id="x", nombre="X", urls=["https://x.cl/"])
    f = FetcherFalso()
    assert registry._bajar(f, fuente, "https://x.cl/") == "<html>ok</html>"
    assert f.ultimo_motivo == "", "el rescate limpió el motivo del fallo"


def test_un_timeout_no_gasta_chromium(monkeypatch):
    """Un sitio que no responde tampoco va a responder con navegador."""
    from arriendo.sources import registry

    class FetcherFalso:
        ultimo_motivo = ""
        def get(self, url, reintentos=3, ignorar_robots=False):
            self.ultimo_motivo = "el sitio no respondió a tiempo"
            return None

    import arriendo.sources.navegador as nav
    def nunca(*a, **k):
        raise AssertionError("no debería haberse levantado Chromium")
    monkeypatch.setattr(nav, "bajar_con_navegador", nunca)

    fuente = FuenteConfig(id="x", nombre="X", urls=["https://x.cl/"])
    assert registry._bajar(FetcherFalso(), fuente, "https://x.cl/") is None


def test_si_el_rescate_tambien_falla_queda_el_motivo_original(monkeypatch):
    """El 403 es el dato accionable; el fallo del rescate es secundario."""
    from arriendo.sources import registry

    class FetcherFalso:
        ultimo_motivo = ""
        def get(self, url, reintentos=3, ignorar_robots=False):
            self.ultimo_motivo = ("HTTP 403 — el sitio rechaza clientes "
                                  "que no son un navegador")
            return None

    import arriendo.sources.navegador as nav
    def revienta(*a, **k):
        raise RuntimeError("Chromium no está")
    monkeypatch.setattr(nav, "bajar_con_navegador", revienta)

    fuente = FuenteConfig(id="x", nombre="X", urls=["https://x.cl/"])
    f = FetcherFalso()
    assert registry._bajar(f, fuente, "https://x.cl/") is None
    assert "rechaza clientes" in f.ultimo_motivo


def test_la_suite_no_duerme_esperando_reintentos_imposibles():
    """La espera entre reintentos (2s, 4s, 8s) le da al sitio tiempo de
    recuperarse, y en producción hay que dejarla en paz. Acá no: la suite
    corta la red por contrato, así que todo reintento está condenado desde
    antes de empezar.

    La cuenta de no anularla, medida en el runner del 21-08: 353 segundos
    de suite con 6 de CPU — cinco minutos y medio de cada corrida, tres
    veces al día, esperando conexiones que no iban a existir. Este test
    guarda la fixture que lo arregla, porque el síntoma solo se ve en CI y
    ahí nadie lo mira hasta que las alertas llegan tarde.
    """
    from arriendo.sources import base
    assert base.espera_de_reintento(0) == 0
    assert base.espera_de_reintento(5) == 0
