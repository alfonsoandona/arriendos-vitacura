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
from arriendo.sources.generic import extraer

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
    for nombre in ("ARRIENDO_STATE_DIR", "ARRIENDO_LOGS_DIR", "ARRIENDO_ALERTAS_DIR"):
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
    monkeypatch.setattr(cli, "_deadline", lambda t: datetime.utcnow())

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
