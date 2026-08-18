"""Test de la corrida completa, de punta a punta, sin tocar la red.

Se sustituye el `Fetcher` por uno que devuelve fixtures y el canal de Telegram
por uno que junta los mensajes en una lista. Todo lo demás —extracción,
deduplicación, scoring, estado, fichas, tablero, bitácora— corre de verdad.

Es el test que atrapa los errores que ningún test unitario ve: el orden de los
pasos. Que la ficha se escriba ANTES de mandar el mensaje que la enlaza, que
el estado se complete ANTES de evaluar, que un envío fallido no se marque como
avisado.
"""

from datetime import datetime
from pathlib import Path

import pytest

from arriendo import cli
from arriendo.sources import registry
from arriendo.sources.base import ResultadoFuente
from arriendo.historial import leer as leer_historial
from arriendo.sources.generic import extraer
from arriendo.tiempo import ahora_utc

FIXTURES = Path(__file__).parent / "fixtures"


class ArgsFalsos:
    """Los argumentos que `correr` espera, con los valores de una corrida real."""

    def __init__(self, **kw):
        self.perfil = None
        self.fuentes = None
        self.verbose = False
        self.dry_run = False
        self.fuente = ""
        self.delay = 0.0
        self.sin_detalles = True
        self.tope_minutos = 0.0
        self.timeout = 20
        # Los tests corren en paralelo de verdad: es el camino de producción,
        # y una carrera que solo aparece con hilos no se descubre corriendo en
        # serie. El orden del resultado no depende de eso —`barrer_todas`
        # devuelve en orden de catálogo— así que las aserciones siguen siendo
        # deterministas.
        self.hilos = 4
        self.__dict__.update(kw)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Aísla estado, logs y alertas en un directorio temporal.

    Sin esto un test escribiría en el state/ versionado del repo y le borraría
    al radar el recuerdo de lo que ya avisó.
    """
    # DOCS también: la fuga fue real — los tests del pipeline escribieron
    # docs/index.html en el repo (con datos de fixture) y el commit del
    # dashboard se los llevó. Exactamente la clase de accidente que esta
    # fixture existe para impedir.
    for nombre in ("ARRIENDO_STATE_DIR", "ARRIENDO_LOGS_DIR",
                   "ARRIENDO_ALERTAS_DIR", "ARRIENDO_DOCS_DIR"):
        destino = tmp_path / nombre.split("_")[1].lower()
        destino.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(nombre, str(destino))
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    return tmp_path


@pytest.fixture
def mensajes(monkeypatch):
    """Captura lo que se habría mandado por Telegram."""
    enviados: list[str] = []

    class TelegramFalso:
        def __init__(self, *a, **kw):
            self.caminable_km = kw.get("caminable_km", 0)
            self.ancla = kw.get("ancla", "")
            self.fallar = False

        def alertar(self, aviso, motivo=""):
            if self.fallar:
                return False
            enviados.append(aviso.url)
            return True

        def resumen(self, *a, **kw):
            pass

    monkeypatch.setattr(cli, "Telegram", TelegramFalso)
    return enviados


def _fuente_falsa(fixture: str):
    """Un `barrer` que lee de un fixture en vez de la red."""
    def barrer(fuente, fetcher, seguir_detalles=True, valor_uf=None,
               limite=None):
        html = (FIXTURES / fixture).read_text(encoding="utf-8")
        url = fuente.urls[0]
        return ResultadoFuente(fuente_id=fuente.id,
                               hallazgos=extraer(html, url, fuente),
                               urls_ok=1)
    return barrer


@pytest.fixture
def una_fuente(monkeypatch, tmp_path):
    """Reduce el catálogo a una sola fuente, para que el test sea legible."""
    yml = tmp_path / "fuentes.yml"
    yml.write_text(
        "fuentes:\n"
        "  - id: portal\n"
        "    nombre: Portal de prueba\n"
        "    urls: ['https://ejemplo.cl/arriendo']\n",
        encoding="utf-8")
    return str(yml)


# ---------------------------------------------------------------------------
# La corrida completa
# ---------------------------------------------------------------------------

def test_corrida_completa(entorno, mensajes, una_fuente, monkeypatch):
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))

    assert cli.correr(ArgsFalsos(fuentes=una_fuente)) == 0

    # De los cinco avisos del fixture, solo uno pasa todos los filtros: los
    # otros son venta, temporada, sobre presupuesto y bajo 100 m².
    assert len(mensajes) == 1
    assert mensajes[0].endswith("/aviso/12001")


def test_no_se_avisa_dos_veces(entorno, mensajes, una_fuente, monkeypatch):
    """La corrida siguiente no repite lo ya avisado."""
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))

    cli.correr(ArgsFalsos(fuentes=una_fuente))
    assert len(mensajes) == 1

    cli.correr(ArgsFalsos(fuentes=una_fuente))
    assert len(mensajes) == 1, "el segundo aviso es un duplicado"


def test_la_ficha_existe_antes_de_que_llegue_el_mensaje(entorno, mensajes,
                                                        una_fuente, monkeypatch):
    """El mensaje lleva el link de la ficha adentro.

    Escribir la ficha después de mandar el aviso garantiza un 404 en el
    momento exacto en que alguien hace click.
    """
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    casos = list((entorno / "state").parent.glob("alertas/casos/*.md"))
    assert len(casos) == 1
    assert "De dónde sale el puntaje" in casos[0].read_text(encoding="utf-8")


def test_un_envio_fallido_no_se_marca_como_avisado(entorno, una_fuente,
                                                   monkeypatch):
    """La peor forma de fallar: perder el departamento en silencio.

    Si Telegram rechaza el mensaje y el radar lo marca como avisado igual,
    ese departamento no se vuelve a intentar nunca.
    """
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))

    enviados: list[str] = []

    class TelegramCaido:
        def __init__(self, *a, **kw):
            pass

        def alertar(self, aviso, motivo=""):
            return False

        def resumen(self, *a, **kw):
            pass

    monkeypatch.setattr(cli, "Telegram", TelegramCaido)
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    # Segunda corrida, ahora con Telegram funcionando: tiene que reintentar.
    class TelegramSano(TelegramCaido):
        def alertar(self, aviso, motivo=""):
            enviados.append(aviso.url)
            return True

    monkeypatch.setattr(cli, "Telegram", TelegramSano)
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    assert len(enviados) == 1, "el aviso que falló tiene que reintentarse"


def test_dry_run_no_escribe_estado(entorno, mensajes, una_fuente, monkeypatch):
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente, dry_run=True))

    assert not (entorno / "state" / "vistos.json").exists()


def test_el_tablero_se_escribe(entorno, mensajes, una_fuente, monkeypatch):
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    tablero = entorno / "alertas" / "README.md"
    assert tablero.exists()
    texto = tablero.read_text(encoding="utf-8")
    assert "Tablero de arriendos" in texto
    assert "Descartados" in texto


def test_la_bitacora_se_escribe(entorno, mensajes, una_fuente, monkeypatch):
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    bitacora = entorno / "logs" / "ultima-corrida.md"
    assert bitacora.exists()
    texto = bitacora.read_text(encoding="utf-8")
    assert "Avisos leídos" in texto
    assert "Pasaron los filtros" in texto

    historial = entorno / "logs" / "historial.jsonl"
    assert historial.exists()
    assert historial.read_text(encoding="utf-8").count("\n") == 1


# ---------------------------------------------------------------------------
# Deduplicación entre fuentes, en la corrida de verdad
# ---------------------------------------------------------------------------

def test_el_mismo_departamento_en_dos_portales_avisa_una_vez(
        entorno, mensajes, tmp_path, monkeypatch):
    """El caso que hace o rompe este radar."""
    yml = tmp_path / "dos.yml"
    yml.write_text(
        "fuentes:\n"
        "  - id: portal_a\n"
        "    nombre: Portal A\n"
        "    urls: ['https://a.cl/arriendo']\n"
        "  - id: portal_b\n"
        "    nombre: Portal B\n"
        "    urls: ['https://b.cl/arriendo']\n",
        encoding="utf-8")

    # Los dos portales publican el mismo departamento, escrito distinto.
    paginas = {
        "portal_a": """<html><body><article>
            <a href="/aviso/1">Departamento en arriendo</a>
            <p>Avenida Santa María 6800, Depto 1102, Vitacura</p>
            <p>$1.480.000 + G.C. $195.000</p>
            <p>134 m² totales · 3 dormitorios · 3 baños</p>
            </article></body></html>""",
        "portal_b": """<html><body><article>
            <a href="/propiedad/xyz">Depto en arriendo Vitacura</a>
            <p>Vitacura Av. Santa María N° 6800 depto 1102</p>
            <p>Arriendo $1.480.000</p>
            <p>134 m2 · 3D/3B · Año de construcción: 2016</p>
            </article></body></html>""",
    }

    def barrer(fuente, fetcher, seguir_detalles=True, valor_uf=None,
               limite=None):
        return ResultadoFuente(
            fuente_id=fuente.id,
            hallazgos=extraer(paginas[fuente.id], fuente.urls[0], fuente),
            urls_ok=1)

    monkeypatch.setattr(registry, "barrer", barrer)
    cli.correr(ArgsFalsos(fuentes=str(yml)))

    assert len(mensajes) == 1, "el mismo departamento alertó dos veces"

    # Y la copia que quedó sabe lo que sabía cada portal por separado.
    casos = list((entorno / "alertas" / "casos").glob("*.md"))
    assert len(casos) == 1
    ficha = casos[0].read_text(encoding="utf-8")
    assert "$195.000" in ficha, "se perdió el gasto común del portal A"
    assert "2016" in ficha, "se perdió el año del portal B"


# ---------------------------------------------------------------------------
# Robustez
# ---------------------------------------------------------------------------

def test_una_fuente_caida_no_voltea_la_corrida(entorno, mensajes, tmp_path,
                                               monkeypatch):
    yml = tmp_path / "dos.yml"
    yml.write_text(
        "fuentes:\n"
        "  - id: rota\n"
        "    nombre: Rota\n"
        "    urls: ['https://rota.cl/']\n"
        "  - id: sana\n"
        "    nombre: Sana\n"
        "    urls: ['https://ejemplo.cl/arriendo']\n",
        encoding="utf-8")

    def barrer(fuente, fetcher, seguir_detalles=True, valor_uf=None,
               limite=None):
        if fuente.id == "rota":
            return ResultadoFuente(fuente_id=fuente.id, urls_fallidas=1,
                                   error="HTTP 403")
        return _fuente_falsa("portal_tarjetas.html")(fuente, fetcher, valor_uf=valor_uf)

    monkeypatch.setattr(registry, "barrer", barrer)
    assert cli.correr(ArgsFalsos(fuentes=str(yml))) == 0
    assert len(mensajes) == 1


def test_sin_resultados_no_revienta(entorno, mensajes, una_fuente, monkeypatch):
    def barrer(fuente, fetcher, seguir_detalles=True, valor_uf=None,
               limite=None):
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(registry, "barrer", barrer)
    assert cli.correr(ArgsFalsos(fuentes=una_fuente)) == 0
    assert mensajes == []


def test_el_tope_por_corrida_se_respeta(entorno, mensajes, una_fuente,
                                        tmp_path, monkeypatch):
    """La primera corrida trae inventario acumulado, no novedades del día.

    Sin tope serían cuarenta mensajes seguidos a las ocho de la mañana.
    """
    tarjetas = "".join(
        f"""<article><a href="/aviso/{n}">Departamento en arriendo</a>
        <p>Luis Carrera {1000 + n}, Vitacura</p>
        <p>$1.450.000 + G.C. $180.000</p>
        <p>134 m² totales · 3 dormitorios · 3 baños</p></article>"""
        for n in range(20))

    def barrer(fuente, fetcher, seguir_detalles=True, valor_uf=None,
               limite=None):
        html = f"<html><body>{tarjetas}</body></html>"
        return ResultadoFuente(fuente_id=fuente.id,
                               hallazgos=extraer(html, fuente.urls[0], fuente),
                               urls_ok=1)

    monkeypatch.setattr(registry, "barrer", barrer)

    perfil = tmp_path / "perfil.yml"
    original = Path("perfil.yml").read_text(encoding="utf-8")
    perfil.write_text(original.replace("max_por_corrida: 8",
                                       "max_por_corrida: 3"), encoding="utf-8")

    cli.correr(ArgsFalsos(fuentes=una_fuente, perfil=str(perfil)))
    assert len(mensajes) == 3


# ---------------------------------------------------------------------------
# Fallar sin quedarse callado
# ---------------------------------------------------------------------------

def test_una_corrida_que_revienta_deja_rastro_y_avisa(entorno, una_fuente,
                                                      monkeypatch):
    """El modo de fallar más caro que tiene este radar.

    Desde el lado del usuario, una corrida que se cayó a la mitad se ve
    exactamente igual que una que no encontró ningún departamento. Sin este
    camino se pueden pasar dos semanas sin radar sin que nadie lo note.

    El fallo se simula en la deduplicación y no en el barrido a propósito: una
    fuente que revienta ya NO voltea la corrida (la atrapa `barrer_todas`, ver
    el test siguiente), así que para probar este camino hace falta reventar en
    una parte del pipeline que sí es fatal.
    """
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))

    def deduplicar_roto(avisos):
        raise RuntimeError("el navegador no arrancó")

    monkeypatch.setattr(cli, "deduplicar", deduplicar_roto)

    avisos: list[str] = []

    class TelegramEspia:
        def __init__(self, *a, **kw):
            pass

        def alertar(self, aviso, motivo=""):
            return True

        def resumen(self, stats, alertas, marca_dir=None):
            if stats.get("error"):
                avisos.append(stats["error"])

    monkeypatch.setattr(cli, "Telegram", TelegramEspia)

    assert cli.correr(ArgsFalsos(fuentes=una_fuente)) == 1

    # Avisó por Telegram...
    assert len(avisos) == 1
    assert "el navegador no arrancó" in avisos[0]

    # ...y dejó la bitácora, que es donde se lee el detalle.
    bitacora = (entorno / "logs" / "ultima-corrida.md").read_text(encoding="utf-8")
    assert "La corrida falló" in bitacora
    assert "el navegador no arrancó" in bitacora


def test_una_fuente_que_revienta_no_voltea_la_corrida(entorno, mensajes,
                                                      tmp_path, monkeypatch):
    """Con 39 fuentes, que una se lleve las otras 38 es inaceptable.

    `barrer` promete no levantar, pero esa promesa la cumple capturando por
    dentro: una excepción en un lugar que no previó —Chromium que no arranca,
    un bug en el extractor— se escapaba igual y volteaba la corrida completa.
    Ahora la atrapa el barrido paralelo, la corrida sigue, y el bug queda
    escrito aparte en la bitácora para que no se pierda entre las fuentes que
    traen cero por tener la URL mala.
    """
    yml = tmp_path / "dos.yml"
    yml.write_text(
        "fuentes:\n"
        "  - {id: rota, nombre: Fuente Rota, urls: ['https://rota.cl/']}\n"
        "  - {id: portal, nombre: Portal de prueba, "
        "urls: ['https://ejemplo.cl/arriendo']}\n",
        encoding="utf-8")

    sana = _fuente_falsa("portal_tarjetas.html")

    def barrer(fuente, fetcher, seguir_detalles=True, valor_uf=None, limite=None):
        if fuente.id == "rota":
            raise RuntimeError("el navegador no arrancó")
        return sana(fuente, fetcher)

    monkeypatch.setattr(registry, "barrer", barrer)

    assert cli.correr(ArgsFalsos(fuentes=str(yml))) == 0
    assert len(mensajes) == 1, "la fuente sana tiene que haber avisado igual"

    bitacora = (entorno / "logs" / "ultima-corrida.md").read_text(encoding="utf-8")
    assert "Fuentes que reventaron" in bitacora
    assert "Fuente Rota" in bitacora
    assert "el navegador no arrancó" in bitacora


def test_un_error_de_configuracion_no_se_reporta_como_caida(entorno, tmp_path):
    """Un YAML mal escrito no es "el radar se cayó": es "arregla el YAML".

    Se distinguen porque piden cosas distintas, y confundirlos manda a buscar
    el problema al lado equivocado.
    """
    yml = tmp_path / "malo.yml"
    yml.write_text("fuentes:\n  - nombre: sin id\n", encoding="utf-8")

    assert cli.main(["run", "--fuentes", str(yml), "--dry-run"]) == 2
    # Y da igual de qué lado del subcomando vaya la opción.
    assert cli.main(["--fuentes", str(yml), "run", "--dry-run"]) == 2


# ---------------------------------------------------------------------------
# Presupuesto de tiempo
# ---------------------------------------------------------------------------

def test_la_corrida_se_corta_antes_de_que_actions_la_mate(entorno, mensajes,
                                                          tmp_path, monkeypatch):
    """El peor final posible es que GitHub Actions mate el job.

    Ahí no se manda ninguna alerta, no se guarda el estado, no se escribe la
    bitácora, y desde afuera solo se ve una X roja. Cortar nosotros conserva
    todo lo que ya se había encontrado.
    """
    yml = tmp_path / "muchas.yml"
    yml.write_text(
        "fuentes:\n" + "".join(
            f"  - {{id: f{n}, nombre: Fuente {n}, urls: ['https://f{n}.cl/']}}\n"
            for n in range(10)), encoding="utf-8")

    vistas: list[str] = []

    def barrer_lento(fuente, fetcher, seguir_detalles=True, valor_uf=None,
                     limite=None):
        vistas.append(fuente.id)
        return _fuente_falsa("portal_tarjetas.html")(fuente, fetcher)

    monkeypatch.setattr(registry, "barrer", barrer_lento)
    # El presupuesto ya vencido: se corta antes de la primera fuente.
    monkeypatch.setattr(cli, "_deadline", lambda t: ahora_utc())

    assert cli.correr(ArgsFalsos(fuentes=str(yml), tope_minutos=18)) == 0
    assert vistas == [], "no debería haber barrido ninguna con el tope vencido"

    bitacora = (entorno / "logs" / "ultima-corrida.md").read_text(encoding="utf-8")
    assert "se cortó por tiempo" in bitacora
    assert "Fuente 0" in bitacora


def test_sin_tope_se_barren_todas(entorno, mensajes, una_fuente, monkeypatch):
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    assert cli.correr(ArgsFalsos(fuentes=una_fuente, tope_minutos=0)) == 0
    assert len(mensajes) == 1


def test_el_corte_por_tiempo_se_avisa_por_telegram():
    """Cortar en silencio sería el mismo error que fallar en silencio."""
    from arriendo.alerts.telegram import _que_se_rompio

    texto = _que_se_rompio({"fuentes_consultadas": 39, "fuentes_ok": 20,
                            "corte_por_tiempo": [f"F{n}" for n in range(19)]})
    assert "cortó por tiempo" in texto
    assert "19 fuentes" in texto

# ---------------------------------------------------------------------------
# Historial de búsquedas
# ---------------------------------------------------------------------------

def test_la_corrida_deja_el_historial_de_busquedas(entorno, mensajes,
                                                   una_fuente, monkeypatch):
    """Lo que el estado olvida a los 120 días, el historial lo conserva."""
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    eventos = leer_historial(entorno / "state")
    assert eventos, "la primera corrida tiene que anotar altas"
    assert all(e["evento"] == "alta" for e in eventos)

    # Y la página que se lee desde el teléfono.
    pagina = (entorno / "alertas" / "historial.md").read_text(encoding="utf-8")
    assert "Historial de búsquedas" in pagina


def test_el_historial_no_cuenta_lo_que_no_es_de_este_mercado(entorno, mensajes,
                                                             una_fuente,
                                                             monkeypatch):
    """Las ventas y lo de otras comunas moverían las medianas sin ser el mercado.

    El fixture trae cinco avisos: uno pasa todos los filtros, y de los otros
    cuatro hay una venta y un arriendo por temporada que no son parte del
    mercado que se está midiendo.
    """
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    eventos = leer_historial(entorno / "state")
    assert 0 < len(eventos) < 5


def test_la_segunda_corrida_no_repite_las_altas(entorno, mensajes, una_fuente,
                                                monkeypatch):
    """Si repitiera, el historial diría que salen el doble de departamentos."""
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente))
    primera = len(leer_historial(entorno / "state"))

    cli.correr(ArgsFalsos(fuentes=una_fuente))
    assert len(leer_historial(entorno / "state")) == primera


def test_una_corrida_en_seco_no_deja_historial(entorno, mensajes, una_fuente,
                                               monkeypatch):
    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente, dry_run=True))

    assert leer_historial(entorno / "state") == []
    assert not (entorno / "alertas" / "historial.md").exists()




def test_lo_visto_sin_avisar_no_realerta_sin_cambio(entorno, mensajes,
                                                    una_fuente, tmp_path,
                                                    monkeypatch):
    """"La corrida de todos los días que sea solo de nuevos o modificaciones."

    Antes, lo visto-pero-no-avisado seguía en cola y cada corrida mandaba los
    8 siguientes del acumulado: días de avisos viejos disfrazados de novedad.
    Ahora lo ya visto solo alerta si CAMBIÓ. El envío fallido es la excepción
    —es una entrega pendiente, no noticia vieja— y conserva su propio test.
    """
    import yaml
    from arriendo.config import cargar_perfil

    monkeypatch.setattr(registry, "barrer", _fuente_falsa("portal_tarjetas.html"))

    # Primera corrida con tope 0: todo queda VISTO, nada avisado.
    perfil = cargar_perfil()
    perfil["alertas"]["max_por_corrida"] = 0
    p0 = tmp_path / "tope-cero.yml"
    p0.write_text(yaml.safe_dump(perfil, allow_unicode=True), encoding="utf-8")
    cli.correr(ArgsFalsos(fuentes=una_fuente, perfil=str(p0)))
    assert mensajes == []

    # Segunda corrida con el perfil normal: lo visto sin cambio NO alerta.
    cli.correr(ArgsFalsos(fuentes=una_fuente))
    assert mensajes == [], "visto sin avisar y sin cambio = noticia vieja"


def test_la_ficha_propia_completa_el_aviso_antes_de_mandarlo(monkeypatch):
    """15 de las 16 alertas reales salieron sin antigüedad —el criterio
    sí-o-sí— teniendo 14 de ellas ficha propia donde el dato vive. El radar
    mandaba el link con la respuesta adentro sin leerla."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/9", title="Depto",
                 direccion="Espoz 2620", comuna="Vitacura",
                 m2_totales=None, dormitorios=3, arriendo_clp=1_500_000)
    perfil = cargar_perfil()
    S.evaluar(a, perfil)
    assert a.antiguedad_anos is None

    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body><article><a href="/aviso/9">Departamento Espoz 2620,
      3 dormitorios, 134 m2 totales, construido en 2018, gastos comunes
      $180.000, arriendo $1.500.000</a></article></body></html>""")

    class StoreFalso:
        def registrar(self, *a, **k): pass

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    assert len(salida) == 1
    listo = salida[0][0]
    assert listo.m2_totales == 134
    assert listo.antiguedad_anos is not None
    assert listo.gastos_comunes_clp == 180_000
    assert listo.extras.get("enriquecido_de_ficha")


def test_si_la_ficha_revela_mas_de_30_anos_no_se_alerta(monkeypatch):
    """El enriquecimiento haciendo su mejor trabajo: la alerta que NO llegó."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/7", title="Depto",
                 direccion="Espoz 2620", comuna="Vitacura",
                 m2_totales=120, dormitorios=3, arriendo_clp=1_400_000)
    perfil = cargar_perfil()
    S.evaluar(a, perfil)

    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body><article><a href="/aviso/7">Departamento Espoz 2620, 3
      dormitorios, 120 m2, construido en 1985, $1.400.000</a></article>
      </body></html>""")

    registrados = []
    class StoreFalso:
        def registrar(self, x, **k): registrados.append(x)

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    assert salida == [], "la ficha reveló 40 años: no llega al teléfono"
    assert registrados and registrados[0].clase_descarte == "antiguedad"


def test_los_similares_de_la_ficha_no_engordan_el_aviso(monkeypatch):
    """El programa fantasma de la corrida del 17-08: un goplaceit cuya
    tarjeta solo decía el título alertó con 4 dormitorios y 5 baños. Salían
    del widget de "propiedades similares" de su propia ficha: el
    enriquecimiento fusionaba TODOS los candidatos de la página, vecinos
    incluidos. Un dato inventado en el campo que más pesa."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/5",
                 title="Arriendo dpto sector exclusivo Vitacura",
                 comuna="Vitacura", arriendo_clp=1_500_000)
    perfil = cargar_perfil()
    S.evaluar(a, perfil)

    # La ficha solo entregó el widget de similares: dos vecinos, ninguno es
    # este aviso.
    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body>
      <article><a href="/aviso/101">Departamento Kennedy 4801, 4 dormitorios,
      5 baños, $2.500.000</a></article>
      <article><a href="/aviso/102">Departamento Padre Hurtado 1200, 3
      dormitorios, 2 baños, $1.900.000</a></article>
      </body></html>""")

    class StoreFalso:
        def registrar(self, *x, **k): pass

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    assert len(salida) == 1, "el aviso sigue alertable, escueto pero honesto"
    listo = salida[0][0]
    assert listo.dormitorios is None and listo.banos is None
    assert not listo.extras.get("enriquecido_de_ficha")


def test_de_la_ficha_se_fusiona_la_propiedad_y_no_el_vecino(monkeypatch):
    """Cuando la ficha trae a la propiedad Y al widget, se fusiona solo lo
    que apunta a la URL del aviso."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/5", title="Depto",
                 comuna="Vitacura", arriendo_clp=1_500_000)
    perfil = cargar_perfil()
    S.evaluar(a, perfil)

    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body>
      <article><a href="/aviso/5">Departamento Espoz 2620, 3 dormitorios, 2
      baños, 134 m2 totales, $1.500.000</a></article>
      <article><a href="/aviso/101">Departamento Kennedy 4801, 6 dormitorios,
      7 baños, $2.500.000</a></article>
      </body></html>""")

    class StoreFalso:
        def registrar(self, *x, **k): pass

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    listo = salida[0][0]
    assert (listo.dormitorios, listo.banos) == (3, 2), \
        "los 6D/7B del vecino no son de este departamento"
    assert listo.extras.get("enriquecido_de_ficha")


def test_el_precio_de_la_ficha_rescata_al_aviso_sin_precio(monkeypatch):
    """goplaceit publica sus tarjetas sin precio —el 100% del tablero real—
    y el aviso quedaba comprimido bajo el techo de los sin-precio teniendo
    el dato a un click, en su propia ficha."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/5", title="Depto",
                 comuna="Vitacura", dormitorios=3, banos=2, m2_totales=120)
    perfil = cargar_perfil()
    S.evaluar(a, perfil)
    assert a.extras.get("sin_precio")

    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body><article><a href="/aviso/5">Departamento Espoz 2620, 3
      dormitorios, 2 baños, 120 m2, arriendo $1.500.000, gastos comunes
      $180.000</a></article></body></html>""")

    class StoreFalso:
        def registrar(self, *x, **k): pass

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    listo = salida[0][0]
    assert listo.arriendo_clp == 1_500_000
    assert not listo.extras.get("sin_precio"), \
        "con el precio puesto, el marcador tiene que desaparecer"


def test_el_precio_del_listado_no_se_pisa_con_el_de_la_ficha(monkeypatch):
    """Cuando el listado SÍ publica precio, ese manda: el de una ficha puede
    estar desactualizado."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/5", title="Depto",
                 comuna="Vitacura", dormitorios=3, arriendo_clp=1_500_000)
    perfil = cargar_perfil()
    S.evaluar(a, perfil)

    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body><article><a href="/aviso/5">Departamento Espoz 2620, 3
      dormitorios, 2 baños, arriendo $1.400.000</a></article></body>
      </html>""")

    class StoreFalso:
        def registrar(self, *x, **k): pass

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    assert salida[0][0].arriendo_clp == 1_500_000


def test_si_la_ficha_revela_un_precio_sobre_el_tope_no_se_alerta(monkeypatch):
    """La otra cara del rescate: el precio recién sabido también filtra."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/5", title="Depto",
                 comuna="Vitacura", dormitorios=3, banos=2, m2_totales=120)
    perfil = cargar_perfil()
    S.evaluar(a, perfil)

    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body><article><a href="/aviso/5">Departamento Espoz 2620, 3
      dormitorios, 2 baños, 120 m2, arriendo $4.500.000</a></article>
      </body></html>""")

    registrados = []
    class StoreFalso:
        def registrar(self, x, **k): registrados.append(x)

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    assert salida == [], "$4,5 millones no es un aviso para este perfil"
    assert registrados and registrados[0].descartado


def test_la_ficha_sin_candidatos_pero_con_texto_enriquece_igual(monkeypatch):
    """El caso goplaceit del diagnóstico: la ficha no trae JSON-LD de la
    propiedad (cero candidatos) y el texto visible lo dice todo. Se cree lo
    rotulado, anclado al título del aviso."""
    from arriendo.config import cargar_perfil
    from arriendo.sources import registry
    from arriendo.sources.base import Fetcher, FuenteConfig
    from arriendo.models import Arriendo
    from arriendo import cli as C, scoring as S

    a = Arriendo(source="f1", url="https://f1.cl/aviso/5",
                 title="Departamento en Arriendo en Tabancura - 4D/3B",
                 comuna="Vitacura")
    perfil = cargar_perfil()
    S.evaluar(a, perfil)

    # Sin <a> por ninguna parte: las tres pasadas extraen cero, como en la
    # ficha real. Las líneas son las del diagnóstico del 17-08.
    monkeypatch.setattr(registry, "_bajar", lambda f, fu, u: """
      <html><body>
      <h1>Departamento en Arriendo en Tabancura - 4D/3B</h1>
      <div>Precio convertido: $1.817.308</div>
      <div>4 Habitaciones / 3 Baños / 127M² útiles</div>
      <div>Superficie 142 m2 totales / 127 m2 útiles</div>
      <div>2 Estacionamientos</div>
      <div>Orientación: Sur-Oriente</div>
      <div>+250.000CLP de gastos comunes</div>
      </body></html>""")

    class StoreFalso:
        def registrar(self, *x, **k): pass

    fuente = FuenteConfig(id="f1", nombre="F1", urls=["https://f1.cl/"])
    salida = C._enriquecer_por_ficha([(a, "")], [fuente], Fetcher(delay=0),
                                     40_854, perfil, StoreFalso())
    listo = salida[0][0]
    assert (listo.dormitorios, listo.banos) == (4, 3)
    assert listo.m2_totales == 142
    assert listo.gastos_comunes_clp == 250_000
    assert listo.arriendo_clp == 1_817_308, \
        "el 'Precio convertido' rotulado es el canon"
    assert listo.orientacion


def test_candidatos_propios_ignora_www_y_parametros():
    from arriendo.cli import _candidatos_propios
    from arriendo.models import Arriendo

    a = Arriendo(source="s", url="https://www.goplaceit.com/cl/propiedad/12054265-dpto")
    propio = Arriendo(source="s",
                      url="https://goplaceit.com/cl/propiedad/12054265-dpto?utm_source=x")
    vecino = Arriendo(source="s", url="https://www.goplaceit.com/cl/propiedad/99-otro")
    assert _candidatos_propios(a, [vecino, propio]) == [propio]


def test_un_candidato_unico_coherente_es_la_propiedad():
    """El JSON-LD de una ficha a veces no declara URL: si es lo único que la
    página entregó y no contradice nada, es la propiedad misma."""
    from arriendo.cli import _candidatos_propios
    from arriendo.models import Arriendo

    a = Arriendo(source="s", url="https://f1.cl/a", comuna="Vitacura")
    c = Arriendo(source="s", url="", dormitorios=4, comuna="Vitacura")
    assert _candidatos_propios(a, [c]) == [c]


def test_un_candidato_unico_que_contradice_no_es_la_propiedad():
    from arriendo.cli import _candidatos_propios
    from arriendo.models import Arriendo

    a = Arriendo(source="s", url="https://f1.cl/a", dormitorios=3)
    c = Arriendo(source="s", url="https://f1.cl/otro", dormitorios=4)
    assert _candidatos_propios(a, [c]) == []
