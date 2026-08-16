"""Test de la corrida completa, de punta a punta, sin tocar la red.

Se sustituye el `Fetcher` por uno que devuelve fixtures y el canal de Telegram
por uno que junta los mensajes en una lista. Todo lo demás —extracción,
deduplicación, scoring, estado, fichas, tablero, bitácora— corre de verdad.

Es el test que atrapa los errores que ningún test unitario ve: el orden de los
pasos. Que la ficha se escriba ANTES de mandar el mensaje que la enlaza, que
el estado se complete ANTES de evaluar, que un envío fallido no se marque como
avisado.
"""

from pathlib import Path

import pytest

from arriendo import cli
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
    def barrer(fuente, fetcher, seguir_detalles=True):
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
    monkeypatch.setattr(cli, "barrer", _fuente_falsa("portal_tarjetas.html"))

    assert cli.correr(ArgsFalsos(fuentes=una_fuente)) == 0

    # De los cinco avisos del fixture, solo uno pasa todos los filtros: los
    # otros son venta, temporada, sobre presupuesto y bajo 100 m².
    assert len(mensajes) == 1
    assert mensajes[0].endswith("/aviso/12001")


def test_no_se_avisa_dos_veces(entorno, mensajes, una_fuente, monkeypatch):
    """La corrida siguiente no repite lo ya avisado."""
    monkeypatch.setattr(cli, "barrer", _fuente_falsa("portal_tarjetas.html"))

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
    monkeypatch.setattr(cli, "barrer", _fuente_falsa("portal_tarjetas.html"))
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
    monkeypatch.setattr(cli, "barrer", _fuente_falsa("portal_tarjetas.html"))

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
    monkeypatch.setattr(cli, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente, dry_run=True))

    assert not (entorno / "state" / "vistos.json").exists()


def test_el_tablero_se_escribe(entorno, mensajes, una_fuente, monkeypatch):
    monkeypatch.setattr(cli, "barrer", _fuente_falsa("portal_tarjetas.html"))
    cli.correr(ArgsFalsos(fuentes=una_fuente))

    tablero = entorno / "alertas" / "README.md"
    assert tablero.exists()
    texto = tablero.read_text(encoding="utf-8")
    assert "Tablero de arriendos" in texto
    assert "Descartados" in texto


def test_la_bitacora_se_escribe(entorno, mensajes, una_fuente, monkeypatch):
    monkeypatch.setattr(cli, "barrer", _fuente_falsa("portal_tarjetas.html"))
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

    def barrer(fuente, fetcher, seguir_detalles=True):
        return ResultadoFuente(
            fuente_id=fuente.id,
            hallazgos=extraer(paginas[fuente.id], fuente.urls[0], fuente),
            urls_ok=1)

    monkeypatch.setattr(cli, "barrer", barrer)
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

    def barrer(fuente, fetcher, seguir_detalles=True):
        if fuente.id == "rota":
            return ResultadoFuente(fuente_id=fuente.id, urls_fallidas=1,
                                   error="HTTP 403")
        return _fuente_falsa("portal_tarjetas.html")(fuente, fetcher)

    monkeypatch.setattr(cli, "barrer", barrer)
    assert cli.correr(ArgsFalsos(fuentes=str(yml))) == 0
    assert len(mensajes) == 1


def test_sin_resultados_no_revienta(entorno, mensajes, una_fuente, monkeypatch):
    def barrer(fuente, fetcher, seguir_detalles=True):
        return ResultadoFuente(fuente_id=fuente.id, urls_ok=1)

    monkeypatch.setattr(cli, "barrer", barrer)
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

    def barrer(fuente, fetcher, seguir_detalles=True):
        html = f"<html><body>{tarjetas}</body></html>"
        return ResultadoFuente(fuente_id=fuente.id,
                               hallazgos=extraer(html, fuente.urls[0], fuente),
                               urls_ok=1)

    monkeypatch.setattr(cli, "barrer", barrer)

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
    """
    def barrer_roto(fuente, fetcher, seguir_detalles=True):
        raise RuntimeError("el navegador no arrancó")

    monkeypatch.setattr(cli, "barrer", barrer_roto)

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
