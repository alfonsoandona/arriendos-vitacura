"""Tests del mensaje de Telegram y de la ficha.

El mensaje se lee en la pantalla de bloqueo, así que lo que se prueba acá es
sobre todo qué NO entra: cada línea de más empuja el link fuera de la pantalla
y convierte un aviso en un documento.
"""

import re
from datetime import date, timedelta

import pytest

from arriendo import scoring as S
from arriendo.alerts.telegram import (Telegram, _falta, _latido, _mensaje,
                                      _que_se_rompio, titulo_corto)
from arriendo.config import cargar_perfil
from arriendo.fichas import (escribir_ficha, escribir_tablero, nombre_archivo,
                             url_ficha)
from arriendo.models import Arriendo


@pytest.fixture(scope="module")
def perfil():
    return cargar_perfil()


def depto(**kw) -> Arriendo:
    base = dict(
        source="toctoc",
        url="https://toctoc.com/aviso/1",
        title="Espectacular departamento con vista panorámica",
        direccion="Alonso de Córdova 4200, Vitacura",
        comuna="Vitacura",
        tipo="departamento",
        m2_totales=134.0,
        dormitorios=3,
        banos=3,
        arriendo_clp=1_500_000,
        gastos_comunes_clp=180_000,
        antiguedad_anos=8,
        lat=-33.3800,
        lon=-70.5610,
    )
    base.update(kw)
    return Arriendo(**base)


# ---------------------------------------------------------------------------
# El mensaje
# ---------------------------------------------------------------------------

def test_el_mensaje_cabe_en_una_pantalla(perfil):
    """Ocho líneas y un link. Más que eso deja de ser un aviso."""
    a = S.evaluar(depto(), perfil)
    lineas = [l for l in _mensaje(a, "", 0.9, "Sport Francés").split("\n") if l]
    assert len(lineas) <= 9, "\n".join(lineas)


def test_el_mensaje_muestra_el_costo_total(perfil):
    """El aviso publica $1.500.000 y lo que se paga son $1.680.000.

    Sin los dos números juntos, comparar dos departamentos obliga a abrir los
    dos y a hacer la suma a mano.
    """
    a = S.evaluar(depto(), perfil)
    texto = _mensaje(a, "", 0.9, "Sport Francés")
    assert "$1.500.000" in texto
    assert "$180.000" in texto
    assert "$1.680.000" in texto


def test_avisa_cuando_no_se_publicaron_los_gastos_comunes(perfil):
    """Un costo total sin gastos comunes no es un costo total."""
    a = S.evaluar(depto(gastos_comunes_clp=None), perfil)
    assert "GC no publicados" in _mensaje(a, "", 0.9, "")


def test_la_distancia_se_dice_en_minutos_caminando(perfil):
    """'0,25 km' obliga a hacer la cuenta; '3 min caminando' ya la tiene hecha."""
    a = S.evaluar(depto(), perfil)
    assert "min caminando" in _mensaje(a, "", 0.9, "Sport Francés")


def test_fuera_de_lo_caminable_se_dice_la_distancia(perfil):
    a = S.evaluar(depto(lat=-33.3560, lon=-70.5700), perfil)
    texto = _mensaje(a, "", 0.9, "Sport Francés")
    assert "min caminando" not in texto
    assert "km del Sport Francés" in texto


def test_el_titulo_prefiere_la_direccion_al_marketing(perfil):
    """"Espectacular departamento con vista panorámica" no identifica nada."""
    a = depto()
    assert titulo_corto(a).startswith("Alonso de Córdova 4200")
    assert "Espectacular" not in titulo_corto(a)


def test_el_titulo_incluye_la_unidad(perfil):
    a = depto(extras={"unidad": "802"})
    assert "depto 802" in titulo_corto(a)


def test_la_comuna_no_se_repite_en_el_titulo(perfil):
    """Va en su propia línea; repetida se come el ancho de la pantalla."""
    assert titulo_corto(depto()) == "Alonso de Córdova 4200"


def test_dice_que_falta(perfil):
    a = S.evaluar(depto(antiguedad_anos=None, gastos_comunes_clp=None), perfil)
    falta = _falta(a)
    assert "antigüedad" in falta
    assert "gastos comunes" in falta
    assert "Preguntar:" in _mensaje(a, "", 0.9, "")


def test_el_veredicto_dice_sobre_cuanto_se_midio(perfil):
    """Un puntaje bajo por falta de datos no es lo mismo que uno bajo por malo."""
    a = S.evaluar(depto(antiguedad_anos=None), perfil)
    assert "% de los datos" in _mensaje(a, "", 0.9, "")


def test_el_veredicto_no_aclara_nada_cuando_se_midio_todo(perfil):
    a = S.evaluar(depto(), perfil)
    assert "% de los datos" not in _mensaje(a, "", 0.9, "")


def test_el_motivo_de_reaviso_aparece(perfil):
    a = S.evaluar(depto(), perfil)
    assert "Bajó 9%" in _mensaje(a, "Bajó 9%: de $1.650.000 a $1.500.000", 0.9, "")


def test_avisa_cuando_esta_en_varios_portales(perfil):
    """Un departamento en cuatro portales lleva rato dando vueltas."""
    a = S.evaluar(depto(extras={"tambien_en": ["yapo|u1", "goplaceit|u2"]}), perfil)
    texto = _mensaje(a, "", 0.9, "")
    # Los otros portales van como links con nombre, no como un conteo:
    # "También en 2 portal(es) más" obligaba a ir a buscarlos a mano.
    assert "También en" in texto
    assert "Yapo" in texto and "Goplaceit" in texto


def test_el_html_se_escapa(perfil):
    """Un & en el título rompe el parse_mode HTML y Telegram rechaza el mensaje."""
    a = S.evaluar(depto(direccion="Perez & Cia 100, Vitacura"), perfil)
    assert "&amp;" in _mensaje(a, "", 0.9, "")
    assert "& Cia" not in _mensaje(a, "", 0.9, "")


def test_siempre_hay_un_link(perfil):
    """Un mensaje sin ningún link no se puede seguir."""
    a = S.evaluar(depto(), perfil)
    assert "https://toctoc.com/aviso/1" in _mensaje(a, "", 0.9, "")


def test_prefiere_el_link_a_la_ficha(perfil):
    a = S.evaluar(depto(extras={"ficha_url": "https://github.com/x/y/ficha.md"}),
                  perfil)
    texto = _mensaje(a, "", 0.9, "")
    assert "Ficha completa" in texto
    # El portal va nombrado en el link: ayuda a decidir si vale la pena
    # abrirlo, porque no todos los portales publican lo mismo.
    assert "Ver en toctoc" in texto


def test_publicado_hace_rato_se_dice(perfil):
    a = S.evaluar(depto(publicado_el=date.today() - timedelta(days=60)), perfil)
    assert "se negocia" in _mensaje(a, "", 0.9, "")


# ---------------------------------------------------------------------------
# El canal
# ---------------------------------------------------------------------------

def test_sin_configurar_no_manda_nada(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert Telegram().configurado is False
    assert Telegram().enviar("hola") is False


def test_dry_run_no_toca_la_red(capsys):
    assert Telegram(dry_run=True).enviar("hola") is True
    assert "hola" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# El latido y los avisos de que algo se rompió
# ---------------------------------------------------------------------------

def test_el_radar_ciego_se_avisa():
    """Cuando fallan todas a la vez no es falta de inventario: es la corrida."""
    texto = _que_se_rompio({"fuentes_consultadas": 17, "fuentes_ok": 0})
    assert "quedó ciego" in texto


def test_una_fuente_caida_se_avisa():
    texto = _que_se_rompio({"fuentes_consultadas": 17, "fuentes_ok": 16,
                            "fuentes_caidas": ["TocToc: 0 avisos"]})
    assert "TocToc" in texto


def test_una_corrida_sana_no_avisa_nada():
    assert _que_se_rompio({"fuentes_consultadas": 17, "fuentes_ok": 17}) == ""


def test_el_latido_dice_numeros_de_verdad():
    """Lo único que aporta el latido es que se le pueda creer."""
    texto = _latido({"total": 143, "candidatos": 4, "fuentes_ok": 15})
    assert "143" in texto
    assert "4" in texto
    assert "15" in texto


def test_no_manda_latido_si_hubo_alertas(tmp_path):
    t = Telegram(dry_run=True)
    enviados = []
    t.enviar = lambda texto: enviados.append(texto) or True
    t.resumen({"fuentes_consultadas": 5, "fuentes_ok": 5}, alertas=2,
              marca_dir=tmp_path)
    assert enviados == []


# ---------------------------------------------------------------------------
# La ficha
# ---------------------------------------------------------------------------

def test_el_nombre_de_la_ficha_es_estable():
    """Si cambiara, el link del mensaje anterior apuntaría a un 404."""
    a, b = depto(), depto(arriendo_clp=1_400_000, score=99)
    assert nombre_archivo(a) == nombre_archivo(b)


def test_el_nombre_distingue_unidades_del_mismo_edificio():
    a = depto(extras={"unidad": "802"})
    b = depto(extras={"unidad": "1204"})
    assert nombre_archivo(a) != nombre_archivo(b)


def test_el_nombre_no_lleva_tildes_ni_signos():
    nombre = nombre_archivo(depto(direccion="Escrivá de Balaguer #5.500, Vitacura"))
    assert nombre.replace("-", "").replace(".md", "").isalnum()


def test_la_ficha_abre_el_puntaje(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_ficha(a, tmp_path, perfil).read_text(encoding="utf-8")

    assert "De dónde sale el puntaje" in texto
    for rubro in ("Ubicación", "Antigüedad", "Precio", "Superficie", "Programa"):
        assert rubro in texto


def test_la_ficha_dice_el_techo_cuando_faltan_datos(tmp_path, perfil):
    """La diferencia entre "no sirve" y "no sabemos si sirve"."""
    a = S.evaluar(depto(antiguedad_anos=None, gastos_comunes_clp=None), perfil)
    texto = escribir_ficha(a, tmp_path, perfil).read_text(encoding="utf-8")
    assert "podría llegar a" in texto


def test_la_ficha_pregunta_por_lo_que_falta(tmp_path, perfil):
    """La checklist se arma según este aviso, no como una lista fija."""
    sin_gc = S.evaluar(depto(gastos_comunes_clp=None), perfil)
    con_gc = S.evaluar(depto(gastos_comunes_clp=180_000), perfil)

    t1 = escribir_ficha(sin_gc, tmp_path / "a", perfil).read_text(encoding="utf-8")
    t2 = escribir_ficha(con_gc, tmp_path / "b", perfil).read_text(encoding="utf-8")

    assert "¿Cuánto son los gastos comunes?" in t1
    assert "¿Cuánto son los gastos comunes?" not in t2


def test_la_ficha_calcula_el_precio_por_m2(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_ficha(a, tmp_path, perfil).read_text(encoding="utf-8")
    assert "Por m²" in texto
    assert "$11.194 / m²" in texto      # 1.500.000 / 134


def test_la_ficha_usa_formato_chileno(tmp_path, perfil):
    """Coma decimal y punto de miles: es un documento en castellano de Chile."""
    a = S.evaluar(depto(), perfil)
    texto = escribir_ficha(a, tmp_path, perfil).read_text(encoding="utf-8")
    assert "12,0% del canon" in texto
    assert "12.0% del canon" not in texto


def test_la_ficha_lista_los_otros_portales(tmp_path, perfil):
    a = S.evaluar(
        depto(extras={"tambien_en": ["yapo|https://yapo.cl/1",
                                     "goplaceit|https://gpi.cl/2"]}), perfil)
    texto = escribir_ficha(a, tmp_path, perfil).read_text(encoding="utf-8")
    assert "https://yapo.cl/1" in texto
    assert "https://gpi.cl/2" in texto


def test_url_de_la_ficha(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "alfonsoandona/arriendos-vitacura")
    url = url_ficha(depto())
    assert url.startswith("https://github.com/alfonsoandona/arriendos-vitacura")
    assert url.endswith(nombre_archivo(depto()))


def test_sin_repositorio_no_hay_url_de_ficha(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert url_ficha(depto()) == ""


# ---------------------------------------------------------------------------
# El tablero
# ---------------------------------------------------------------------------

def test_el_tablero_ordena_por_puntaje(tmp_path, perfil):
    avisos = [
        S.evaluar(depto(url="https://x.cl/1", antiguedad_anos=40), perfil),
        S.evaluar(depto(url="https://x.cl/2", direccion="Luis Carrera 1200, Vitacura",
                        antiguedad_anos=3), perfil),
    ]
    texto = escribir_tablero(avisos, tmp_path, perfil).read_text(encoding="utf-8")
    assert texto.index("Luis Carrera") < texto.index("Alonso de Córdova")


def test_el_tablero_marca_el_costo_incompleto(tmp_path, perfil):
    a = S.evaluar(depto(gastos_comunes_clp=None), perfil)
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    assert "*" in texto
    assert "sin gastos comunes" in texto


def test_el_tablero_explica_los_descartes(tmp_path, perfil):
    a = S.evaluar(depto(m2_totales=60.0), perfil)
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    assert "Descartados" in texto
    assert "no llega a más de 100" in texto


def test_el_pipe_no_rompe_la_tabla(tmp_path, perfil):
    a = S.evaluar(depto(direccion="Calle A | Calle B 100, Vitacura"), perfil)
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    fila = next(l for l in texto.splitlines() if "Calle A" in l)
    assert fila.count("|") == 9      # 8 columnas + los bordes


# ---------------------------------------------------------------------------
# Independencia del bot de remates
#
# Decisión explícita: otro bot, otro token. Estos tests existen para que la
# decisión no se deshaga sin querer en una refactorización.
# ---------------------------------------------------------------------------

def test_usa_secrets_propios(monkeypatch):
    from arriendo.alerts.telegram import VAR_CHAT_ID, VAR_TOKEN

    monkeypatch.setenv(VAR_TOKEN, "123:abc")
    monkeypatch.setenv(VAR_CHAT_ID, "987")
    assert Telegram().configurado is True


def test_no_cae_al_token_del_radar_de_remates(monkeypatch):
    """El fallo bueno es ruidoso; el malo se ve igual que funcionar bien.

    Si este radar cayera al token genérico, los avisos de arriendo saldrían
    por el bot de remates sin que nada lo dijera.
    """
    from arriendo.alerts.telegram import VAR_CHAT_ID, VAR_TOKEN

    monkeypatch.delenv(VAR_TOKEN, raising=False)
    monkeypatch.delenv(VAR_CHAT_ID, raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:del-radar-de-remates")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987")

    t = Telegram()
    assert t.configurado is False
    assert t.token == ""
    assert t.enviar("hola") is False


def test_avisa_cuando_se_configuro_el_secret_equivocado(monkeypatch, caplog):
    """Es el error más probable y el más difícil de ver solo."""
    import logging

    from arriendo.alerts.telegram import VAR_CHAT_ID, VAR_TOKEN

    monkeypatch.delenv(VAR_TOKEN, raising=False)
    monkeypatch.delenv(VAR_CHAT_ID, raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:del-radar-de-remates")

    with caplog.at_level(logging.WARNING):
        Telegram().enviar("hola")

    assert "NO la usa" in caplog.text
    assert VAR_TOKEN in caplog.text


def test_los_nombres_llevan_sufijo_propio():
    from arriendo.alerts.telegram import VAR_CHAT_ID, VAR_TOKEN

    assert VAR_TOKEN.endswith("_ARRIENDOS")
    assert VAR_CHAT_ID.endswith("_ARRIENDOS")


# ---------------------------------------------------------------------------
# El aviso que se pasa del presupuesto
# ---------------------------------------------------------------------------

def test_avisa_cuando_se_pasa_del_tope(perfil):
    """Entra a propósito —"cerca de 1,6 millones"— pero tiene que decirlo.

    Sin esta línea, $1.690.000 se ve idéntico a $1.450.000 en la pantalla de
    bloqueo y el usuario descubre que se pasó del presupuesto recién al
    abrirlo.
    """
    a = S.evaluar(depto(arriendo_clp=1_690_000), perfil)
    texto = _mensaje(a, "", 0.9, "Sport Francés", tope_arriendo=1_600_000)
    assert "sobre tu tope" in texto
    assert "$90.000" in texto


def test_no_molesta_cuando_cabe_en_el_presupuesto(perfil):
    a = S.evaluar(depto(arriendo_clp=1_450_000), perfil)
    texto = _mensaje(a, "", 0.9, "Sport Francés", tope_arriendo=1_600_000)
    assert "sobre tu tope" not in texto


def test_sin_tope_configurado_no_inventa_la_advertencia(perfil):
    a = S.evaluar(depto(arriendo_clp=1_690_000), perfil)
    assert "sobre tu tope" not in _mensaje(a, "", 0.9, "")


def test_el_tablero_no_repite_la_comuna(tmp_path, perfil):
    """La comuna del núcleo no ocupa espacio (el 97% de los candidatos
    reales son de Vitacura y en un teléfono cada columna cuesta); una comuna
    DISTINTA sí se dice, en la celda de la dirección."""
    def _visible(fila: str) -> str:
        # Las URLs (el link a Maps lleva la comuna adentro) no son texto
        # visible: lo que se lee en el teléfono es lo que está fuera de ().
        return re.sub(r"\((?:casos/|https?://)[^)]*\)", "", fila)

    a = S.evaluar(depto(), perfil)
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    fila = next(l for l in texto.splitlines() if "Alonso" in l)
    assert "[Alonso de Córdova 4200]" in fila
    assert "Vitacura" not in _visible(fila)

    b = S.evaluar(depto(url="https://x.cl/lc", comuna="Las Condes",
                        direccion="Napoleón 3037, Las Condes"), perfil)
    texto = escribir_tablero([a, b], tmp_path, perfil).read_text(encoding="utf-8")
    fila_lc = next(l for l in texto.splitlines() if "Napole" in l)
    assert "Las Condes" in _visible(fila_lc)


def test_el_tablero_separa_lo_nuevo_del_stock(tmp_path, perfil):
    """El rediseño de triage: si solo hay tiempo para una sección, es la de
    los nuevos — lo demás ya estaba ayer."""
    nuevo = S.evaluar(depto(url="https://x.cl/n",
                            direccion="Luis Carrera 1200, Vitacura"), perfil)
    nuevo.extras["nuevo_en_corrida"] = True
    viejo = S.evaluar(depto(url="https://x.cl/v"), perfil)
    viejo.extras["nuevo_en_corrida"] = False
    texto = escribir_tablero([viejo, nuevo], tmp_path, perfil).read_text(
        encoding="utf-8")
    assert "🆕 Nuevos en esta corrida" in texto
    seccion = texto.split("🆕 Nuevos en esta corrida")[1].split("##")[0]
    assert "Luis Carrera" in seccion
    assert "Alonso" not in seccion
    assert "1 nuevos" in texto or "**1 nuevos**" in texto


def test_el_tablero_muestra_los_que_bajaron_de_precio(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    a.extras["historial_precio"] = [
        {"cuando": "2026-08-01", "clp": 1_650_000},
        {"cuando": "2026-08-15", "clp": 1_490_000},
    ]
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    assert "📉 Bajaron de precio" in texto
    assert "$1.650.000" in texto and "$1.490.000" in texto


def test_el_tablero_pone_tu_lista_corta_primero(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    a.extras["gestion"] = {"estado": "visita", "nota": "martes"}
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    assert "⭐ Tu lista corta" in texto
    assert texto.index("Tu lista corta") < texto.index("Todos los candidatos")


def test_el_tablero_no_repite_la_misma_ficha(tmp_path, perfil):
    """Dos registros del mismo departamento (misma ficha) aparecían como dos
    filas compitiendo entre sí — pasó con toctoc en el tablero real."""
    a = S.evaluar(depto(url="https://x.cl/1"), perfil)
    b = S.evaluar(depto(url="https://x.cl/2", arriendo_clp=1_600_000.0), perfil)
    texto = escribir_tablero([a, b], tmp_path, perfil).read_text(encoding="utf-8")
    filas = [l for l in texto.splitlines()
             if "[Alonso de Córdova 4200]" in l]
    assert len(filas) == 1


def test_el_tablero_separa_tus_descartes_de_los_del_filtro(tmp_path, perfil):
    del_filtro = S.evaluar(depto(url="https://x.cl/f", m2_totales=60.0), perfil)
    tuyo = S.evaluar(depto(url="https://x.cl/t",
                           direccion="Espoz 2620, Vitacura"), perfil)
    tuyo.descartado = True
    tuyo.clase_descarte = "gestion"
    tuyo.motivo_descarte = "lo descartaste tú: muy oscuro"
    tuyo.extras["gestion"] = {"estado": "descartado", "nota": "muy oscuro"}
    texto = escribir_tablero([del_filtro, tuyo], tmp_path, perfil).read_text(
        encoding="utf-8")
    assert "Los que descartaste tú (1)" in texto
    assert "muy oscuro" in texto
    assert "Descartados por el filtro" in texto


def test_el_tablero_explica_como_anotar(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    assert "gestion.yml" in texto
    assert f"codigo: {a.codigo}" in texto, \
        "el ejemplo lleva un código real, listo para copiar"


def test_el_mensaje_lleva_el_link_al_panel(perfil, monkeypatch):
    """La app de GitHub muestra el HTML como código fuente: el único camino
    al dashboard desde el teléfono es la URL de Pages."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "alfonsoandona/arriendos-vitacura")
    a = S.evaluar(depto(), perfil)
    texto = _mensaje(a, "", 0.9, "Sport Francés")
    assert "https://alfonsoandona.github.io/arriendos-vitacura/" in texto
    assert "📊 Panel" in texto


def test_sin_repo_no_se_inventa_panel(perfil, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    a = S.evaluar(depto(), perfil)
    assert "github.io" not in _mensaje(a, "", 0.9, "Sport Francés")


def test_el_mensaje_lleva_el_link_a_google_maps(perfil):
    a = S.evaluar(depto(), perfil)
    texto = _mensaje(a, "", 0.9, "Sport Francés")
    assert "google.com/maps/search" in texto
    assert "📍 Maps" in texto


def test_sin_direccion_el_mensaje_no_lleva_maps(perfil):
    a = S.evaluar(depto(direccion=""), perfil)
    assert "google.com/maps" not in _mensaje(a, "", 0.9, "Sport Francés")


def test_el_tablero_lleva_maps_por_fila(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    fila = next(l for l in texto.splitlines() if "Alonso" in l)
    assert "google.com/maps/search" in fila


def test_la_ficha_lleva_codigo_y_google_maps(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    escribir_ficha(a, tmp_path, perfil)
    texto = (tmp_path / nombre_archivo(a)).read_text(encoding="utf-8")
    assert f"`#{a.codigo}`" in texto
    assert "google.com/maps/search" in texto
    assert f"codigo: {a.codigo}" in texto, "el bloque para anotar viene listo"


def test_la_ficha_sin_direccion_no_inventa_mapa(tmp_path, perfil):
    a = S.evaluar(depto(direccion=""), perfil)
    escribir_ficha(a, tmp_path, perfil)
    texto = (tmp_path / nombre_archivo(a)).read_text(encoding="utf-8")
    assert "google.com/maps" not in texto


def test_la_ficha_muestra_tu_gestion(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    a.extras["gestion"] = {"estado": "contactado", "nota": "sin mascotas"}
    a.extras["datos_tuyos"] = ["gastos_comunes_clp"]
    escribir_ficha(a, tmp_path, perfil)
    texto = (tmp_path / nombre_archivo(a)).read_text(encoding="utf-8")
    assert "contactado" in texto and "sin mascotas" in texto
    assert "Datos completados por ti" in texto


def test_el_tablero_marca_la_superficie_util(tmp_path, perfil):
    """118 sin aclarar se lee como si ya hubiera pasado el filtro de 100 totales."""
    a = S.evaluar(depto(m2_totales=None, m2_utiles=118.0), perfil)
    texto = escribir_tablero([a], tmp_path, perfil).read_text(encoding="utf-8")
    assert "118 út." in texto
    assert "superficie útil" in texto


def test_el_portal_se_nombra_como_lo_conoce_una_persona(perfil):
    """'Ver en TocToc', no 'Ver en toctoc'."""
    a = S.evaluar(depto(extras={"portal": "TocToc"}), perfil)
    assert "Ver en TocToc" in _mensaje(a, "", 0.9, "")


# ---------------------------------------------------------------------------
# El título cuando el aviso no publica dirección
# ---------------------------------------------------------------------------

def test_el_titulo_se_limpia_del_precio_y_la_coletilla():
    """Título real de Yapo, que sin limpiar ocupa la línea entera.

    El precio ya va en su propia línea del mensaje: repetido en el título es
    ruido que empuja todo lo demás fuera de la pantalla.
    """
    a = Arriendo(source="yapo", url="https://yapo.cl/1",
                 title="Departamento en Luis Carrera 3 Dormitorios por CLP "
                       "1600000.00 Arriendo de Departamentos en Vitacura",
                 comuna="Vitacura", tipo="departamento")
    assert titulo_corto(a) == "Departamento en Luis Carrera 3 Dormitorios"


def test_el_titulo_limpio_no_queda_vacio():
    """Si limpiar se lleva todo, es mejor el título crudo que nada."""
    a = Arriendo(source="yapo", url="https://yapo.cl/1", title="por CLP 1600000")
    assert titulo_corto(a)


def test_la_direccion_le_gana_al_titulo():
    a = Arriendo(source="yapo", url="https://yapo.cl/1",
                 title="Departamento por CLP 1600000.00",
                 direccion="Luis Carrera 1200, Vitacura", comuna="Vitacura")
    assert titulo_corto(a) == "Luis Carrera 1200"


def test_la_tendencia_va_pegada_al_precio(perfil):
    """"2 bajas en 62 días" dice algo que el precio de hoy no puede decir."""
    a = S.evaluar(depto(extras={"tendencia_precio":
                                "2 bajas en 62 días: -10% desde $1.650.000"}),
                  perfil)
    texto = _mensaje(a, "", 0.9, "Sport Francés", 1_600_000)
    lineas = texto.split("\n")
    i_precio = next(n for n, l in enumerate(lineas) if l.startswith("💰"))
    assert lineas[i_precio + 1].strip().startswith("📉")
    assert "2 bajas" in lineas[i_precio + 1]


def test_la_ficha_muestra_el_historial(tmp_path, perfil):
    a = S.evaluar(depto(extras={
        "historial_precio": [
            {"clp": 1_650_000, "cuando": "2026-06-15"},
            {"clp": 1_500_000, "cuando": "2026-08-10"}],
        "tendencia_precio": "1 baja en 56 días: -9% desde $1.650.000"}), perfil)
    texto = escribir_ficha(a, tmp_path, perfil).read_text(encoding="utf-8")

    assert "Cómo se movió el precio" in texto
    assert "2026-06-15" in texto
    assert "$1.650.000" in texto
    assert "(-9%)" in texto


def test_sin_historial_la_ficha_no_muestra_la_seccion(tmp_path, perfil):
    a = S.evaluar(depto(), perfil)
    texto = escribir_ficha(a, tmp_path, perfil).read_text(encoding="utf-8")
    assert "Cómo se movió el precio" not in texto


# ---------------------------------------------------------------------------
# El orden del mensaje
#
# No es cosmético: la notificación de Telegram muestra las dos o tres primeras
# líneas en la pantalla de bloqueo, y con eso se decide si abrirlo.
# ---------------------------------------------------------------------------

def _completo(**kw):
    base = dict(source="toctoc", url="https://toctoc.com/aviso/1",
                title="Departamento", direccion="Alonso de Córdova 4200",
                comuna="Vitacura", m2_totales=134, dormitorios=3, banos=3,
                antiguedad_anos=8, arriendo_clp=1_490_000,
                gastos_comunes_clp=180_000)
    base.update(kw)
    a = Arriendo(**base)
    S.evaluar(a, cargar_perfil())
    return a


def test_el_veredicto_va_en_la_primera_linea():
    """Antes iba abajo, después de siete líneas de datos.

    O sea que lo único que resume todo lo demás quedaba justo fuera de lo que
    se alcanza a ver en la notificación.
    """
    primera = _mensaje(_completo(), "", 0.9, "").splitlines()[0]
    assert "Anda a verlo" in primera or "Muy bueno" in primera
    assert "<b>" in primera, "el puntaje va destacado"


def test_la_banda_le_da_significado_al_numero():
    """"88" no dice nada sin otro con qué compararlo; "88 · Muy bueno" sí."""
    assert S.banda(95)[1] == "Anda a verlo"
    assert S.banda(85)[1] == "Anda a verlo"
    assert S.banda(84)[1] == "Muy bueno"
    assert S.banda(70)[1] == "Muy bueno"
    assert S.banda(55)[1] == "Sirve"
    assert S.banda(40)[1] == "Dudoso"
    assert S.banda(0)[1] == "Al fondo"


def test_la_confianza_se_muestra_solo_cuando_falta_algo():
    """Decir "(100% de los datos)" en cada mensaje es ruido."""
    completo = _completo(piso=5, orientacion="norte", estacionamientos=1)
    assert "% de los datos" not in _mensaje(completo, "", 0.9, "")

    incompleto = _completo(antiguedad_anos=None)
    assert "% de los datos" in _mensaje(incompleto, "", 0.9, "")


# ---------------------------------------------------------------------------
# La comparación con el mercado
# ---------------------------------------------------------------------------

def test_el_precio_se_compara_contra_la_mediana():
    """Es lo que convierte un dato en un juicio.

    $1.490.000 no dice si es caro. Comparado contra lo que efectivamente se
    publica en la zona, sí — y es un número propio, sacado de los avisos que
    este radar vio, no de un índice publicado.
    """
    texto = _mensaje(_completo(), "", 0.9, "", 0, mediana_mercado=1_690_000)
    assert "12% bajo" in texto

    caro = _mensaje(_completo(arriendo_clp=1_850_000), "", 0.9, "", 0,
                    mediana_mercado=1_690_000)
    assert "9% sobre" in caro


def test_una_diferencia_chica_con_la_mediana_no_se_reporta_como_dato():
    """"1% sobre la mediana" es ruido con aspecto de dato."""
    texto = _mensaje(_completo(arriendo_clp=1_700_000), "", 0.9, "", 0,
                     mediana_mercado=1_690_000)
    assert "en la mediana" in texto


def test_sin_historial_no_se_inventa_la_comparacion():
    assert "mediana" not in _mensaje(_completo(), "", 0.9, "", 0,
                                     mediana_mercado=0)


# ---------------------------------------------------------------------------
# Por qué este
# ---------------------------------------------------------------------------

def test_dice_en_que_criterios_destaca_y_en_cuales_flojea():
    """El puntaje dice cuánto; esto dice en qué."""
    # 28 años: flojea en antigüedad sin cruzar el máximo duro de 30.
    malo = _completo(antiguedad_anos=28, arriendo_clp=1_880_000)
    texto = _mensaje(malo, "", 0.9, "")
    assert "Flojo en" in texto


def test_no_repite_lo_que_ya_esta_arriba():
    """La primera versión decía "Lo mejor: Vitacura · a 0,2 km · 8 años".

    Palabra por palabra las líneas 📍 y 🏗 que van justo encima. Una línea que
    repite lo de arriba enseña a saltarse el bloque entero, así que ahora se
    nombran los criterios y no sus detalles.
    """
    texto = _mensaje(_completo(), "", 0.9, "")
    resumen = [l for l in texto.splitlines() if l.startswith(("✅", "⚠️ Flojo"))]
    for linea in resumen:
        assert "km" not in linea and "años" not in linea


# ---------------------------------------------------------------------------
# Los otros portales
# ---------------------------------------------------------------------------

def test_los_otros_portales_van_como_links():
    """"También en 2 portal(es) más" obligaba a ir a buscarlos a mano."""
    a = _completo()
    a.extras["tambien_en"] = ["yapo|https://yapo.cl/1",
                              "chilepropiedades|https://cp.cl/2"]
    texto = _mensaje(a, "", 0.9, "")
    assert 'href="https://yapo.cl/1"' in texto
    assert "Chilepropiedades" in texto


def test_con_muchos_portales_se_resumen():
    a = _completo()
    a.extras["tambien_en"] = [f"p{i}|https://p{i}.cl" for i in range(6)]
    texto = _mensaje(a, "", 0.9, "")
    assert "y 3 más" in texto

# ---------------------------------------------------------------------------
# El cierre del ciclo y el recorte que dejó de ser invisible
# ---------------------------------------------------------------------------

def test_las_despedidas_dicen_cuanto_duro_publicado():
    """Con unas cuantas se aprende a qué velocidad se mueve el rango."""
    from arriendo.alerts.telegram import mensaje_bajas
    texto = mensaje_bajas([
        {"direccion": "Alonso de Córdova 4200", "clp": 1_490_000,
         "dias": 23, "avisado": True},
        {"direccion": "Espoz 2620", "clp": 1_600_000, "dias": 41,
         "avisado": True},
    ])
    assert "Se fueron del mercado" in texto
    assert "23 días publicado" in texto
    assert "$1.490.000" in texto


def test_muchas_despedidas_se_resumen():
    from arriendo.alerts.telegram import mensaje_bajas
    texto = mensaje_bajas([{"direccion": f"Calle {i}", "clp": 1_000_000,
                            "dias": 10} for i in range(9)])
    assert "y 3 más" in texto


def test_sin_bajas_no_hay_mensaje():
    from arriendo.alerts.telegram import mensaje_bajas
    assert mensaje_bajas([]) == ""


def test_los_sobrantes_son_un_click_no_una_lista(monkeypatch):
    """Pedido del 18-08: el índice de líneas truncadas "no tiene nada de
    info" — mejor un 'para más avisos haz click acá' que lleve a la lista
    completa en git."""
    from arriendo.alerts.telegram import mensaje_sobrantes
    monkeypatch.setenv("GITHUB_REPOSITORY", "alfonsoandona/arriendos-vitacura")
    monkeypatch.setenv("GITHUB_REF_NAME", "rama-x")
    avisos = []
    for i in range(12):
        a = Arriendo(source="x", url=f"https://x.cl/{i}", title="Depto",
                     direccion=f"Calle {i} 100", comuna="Vitacura",
                     m2_totales=120, dormitorios=3, arriendo_clp=1_400_000)
        S.evaluar(a, cargar_perfil())
        avisos.append(a)
    texto = mensaje_sobrantes(avisos)
    assert "calificaron 12 más" in texto
    assert ("https://github.com/alfonsoandona/arriendos-vitacura/blob/"
            "rama-x/alertas/README.md") in texto
    assert "Ver la lista completa" in texto
    assert "Calle 3 100" not in texto, "sin líneas de relleno"
    assert texto.count("\n") <= 4, "es un click, no una lista"


def test_sobrantes_vacios_no_mandan_nada():
    from arriendo.alerts.telegram import mensaje_sobrantes
    assert mensaje_sobrantes([]) == ""


def test_los_gc_estimados_van_marcados_como_estimacion():
    """Un dato deducido presentado como publicado es peor que uno ausente."""
    a = _completo(gastos_comunes_clp=None)
    texto = _mensaje(a, "", 0.9, "", 0, 0, gc_tipico=lambda m2: 180_000)
    assert "típico en la zona" in texto
    assert "≈$180.000" in texto


def test_sin_estimador_los_gc_dicen_no_publicados():
    a = _completo(gastos_comunes_clp=None)
    texto = _mensaje(a, "", 0.9, "", 0, 0, gc_tipico=None)
    assert "GC no publicados" in texto
    assert "típico" not in texto


def test_los_gc_publicados_nunca_se_reemplazan_por_la_estimacion():
    a = _completo()          # trae GC publicados
    texto = _mensaje(a, "", 0.9, "", 0, 0, gc_tipico=lambda m2: 999_999)
    assert "$180.000" in texto and "típico" not in texto



def test_el_codigo_identifica_al_departamento_entre_portales():
    """"Me gustó el K9D2M" en vez de "el tercero de ayer"."""
    a = _completo()
    b = _completo(source="yapo", url="https://yapo.cl/otro")   # misma dirección
    c = _completo(direccion="Otra Calle 123")
    assert a.codigo == b.codigo, "misma dirección → mismo código"
    assert a.codigo != c.codigo
    assert len(a.codigo) == 5
    assert f"#{a.codigo}" in _mensaje(a, "", 0.9, "")


def test_sin_link_directo_el_mensaje_lo_dice():
    """Tocar "Ver en Mitula" y caer en 2.277 resultados fue real (captura
    del 17-08). Si el link va al listado, el mensaje tiene que decirlo."""
    a = _completo()
    a.extras["sin_link_directo"] = True
    texto = _mensaje(a, "", 0.9, "")
    assert "Buscarlo en" in texto and "sin link directo" in texto


def test_la_ficha_apunta_a_la_rama_real(monkeypatch):
    """El link 404 que el usuario descubrió en su teléfono: url_ficha
    suponía "main" (herencia del radar de remates, donde la default SÍ es
    main). Acá la default es la rama de trabajo."""
    from arriendo.fichas import url_ficha
    monkeypatch.setenv("GITHUB_REPOSITORY", "alfonsoandona/arriendos-vitacura")
    monkeypatch.setenv("GITHUB_REF_NAME", "claude/telegram-vitacura-rentals-alert-48wnuy")
    url = url_ficha(_completo())
    assert "/blob/claude/telegram-vitacura-rentals-alert-48wnuy/" in url
