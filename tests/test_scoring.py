"""Tests del scoring y de los filtros duros.

Los filtros duros son los tres requisitos que el pedido dice como
obligatorios ("debe ser sí o sí más de 100 m² totales, 3 piezas mínimo, y
cerca de 1.6 millones máximo") más la zona. Cada uno tiene acá su test de que
descarta cuando debe, y —más importante— su test de que NO descarta cuando el
dato falta.
"""

from datetime import date, timedelta

import pytest

from arriendo import scoring as S
from arriendo.config import cargar_perfil
from arriendo.models import Arriendo


@pytest.fixture(scope="module")
def perfil():
    return cargar_perfil()


def depto(**kw) -> Arriendo:
    """Un arriendo que cumple todo. Cada test rompe solo lo que quiere probar."""
    base = dict(
        source="test",
        url="https://ejemplo.cl/aviso/1",
        title="Departamento en Vitacura",
        direccion="Alonso de Córdova 4200",
        comuna="Vitacura",
        tipo="departamento",
        operacion="arriendo",
        m2_totales=134.0,
        dormitorios=3,
        banos=3,
        arriendo_clp=1_500_000,
        gastos_comunes_clp=180_000,
        antiguedad_anos=8,
    )
    base.update(kw)
    return Arriendo(**base)


# ---------------------------------------------------------------------------
# El caso feliz
# ---------------------------------------------------------------------------

def test_el_departamento_ideal_puntua_alto(perfil):
    l = S.evaluar(depto(), perfil)
    assert not l.descartado
    assert l.score >= 75, f"puntuó {l.score}: {l.razones}"


def test_alerta_el_departamento_ideal(perfil):
    assert S.debe_alertar(S.evaluar(depto(), perfil), perfil) is True


# ---------------------------------------------------------------------------
# Filtro duro: superficie
# ---------------------------------------------------------------------------

def test_descarta_bajo_100_m2(perfil):
    l = S.evaluar(depto(m2_totales=85.0), perfil)
    assert l.descartado
    assert l.clase_descarte == "superficie"


def test_exactamente_100_no_es_mas_de_100(perfil):
    """El pedido dice "más de 100 m²", no "100 o más".

    Con `estricto: true` en el perfil, 100,0 exactos no cumplen. Es una
    diferencia de un metro que cambia el veredicto, así que tiene que estar
    escrita y probada en vez de quedar a criterio del que lea el YAML.
    """
    assert S.evaluar(depto(m2_totales=100.0), perfil).descartado is True
    assert S.evaluar(depto(m2_totales=100.5), perfil).descartado is False


def test_superficie_ausente_no_descarta(perfil):
    """Descartar por dato faltante es el error que no se ve."""
    l = S.evaluar(depto(m2_totales=None, m2_utiles=None), perfil)
    assert not l.descartado


def test_util_sobre_el_minimo_sirve_de_garantia(perfil):
    """La total nunca es menor que la útil.

    Una útil de 118 m² garantiza una total de al menos 118, así que el filtro
    puede usarla sin riesgo de dejar entrar algo que no cumple.
    """
    l = S.evaluar(depto(m2_totales=None, m2_utiles=118.0), perfil)
    assert not l.descartado
    assert l.score > 0


def test_util_bajo_el_minimo_no_alcanza_para_descartar(perfil):
    """Una útil de 92 m² no dice nada sobre la total.

    Puede ser un departamento de 92 útiles + 15 de terraza = 107 totales, que
    sí cumple. Descartarlo sería botar por un dato que no se tiene.
    """
    l = S.evaluar(depto(m2_totales=None, m2_utiles=92.0), perfil)
    assert not l.descartado


def test_total_declarada_bajo_el_minimo_si_descarta(perfil):
    """La otra mitad de la asimetría.

    Con la TOTAL publicada el filtro decide en los dos sentidos: 96 m²
    totales no cumplen "más de 100" y no hay tolerancia que los salve. Es lo
    que pidió el "sí o sí".
    """
    l = S.evaluar(depto(m2_totales=96.0), perfil)
    assert l.descartado
    assert l.clase_descarte == "superficie"


def test_util_alta_con_total_baja_manda_la_total(perfil):
    """Si vienen las dos, decide la total. La útil no la puede sobrescribir."""
    l = S.evaluar(depto(m2_totales=98.0, m2_utiles=118.0), perfil)
    assert l.descartado


# ---------------------------------------------------------------------------
# Filtro duro: dormitorios
# ---------------------------------------------------------------------------

def test_descarta_bajo_3_dormitorios(perfil):
    l = S.evaluar(depto(dormitorios=2), perfil)
    assert l.descartado
    assert l.clase_descarte == "dormitorios"


def test_tres_dormitorios_pasa(perfil):
    assert S.evaluar(depto(dormitorios=3), perfil).descartado is False


def test_dormitorios_ausentes_no_descartan(perfil):
    assert S.evaluar(depto(dormitorios=None), perfil).descartado is False


# ---------------------------------------------------------------------------
# Filtro duro: precio, y el "cerca de" del pedido
# ---------------------------------------------------------------------------

def test_dentro_del_presupuesto(perfil):
    assert S.evaluar(depto(arriendo_clp=1_500_000), perfil).descartado is False


def test_apenas_sobre_el_tope_entra_penalizado(perfil):
    """"Cerca de 1.6 millones máximo" es parte del pedido.

    $1.650.000 se negocia. Descartarlo en silencio sería perder un
    departamento que cumple todo lo demás por un 3% de diferencia.
    """
    dentro = S.evaluar(depto(arriendo_clp=1_550_000), perfil)
    apenas = S.evaluar(depto(arriendo_clp=1_650_000), perfil)
    assert not apenas.descartado
    assert apenas.score < dentro.score, "pasarse del tope tiene que costar puntos"


def test_muy_sobre_el_tope_se_descarta(perfil):
    """No hay negociación que baje $2.500.000 a $1.600.000."""
    l = S.evaluar(depto(arriendo_clp=2_500_000), perfil)
    assert l.descartado
    assert l.clase_descarte == "precio"


def test_precio_ausente_no_descarta(perfil):
    assert S.evaluar(depto(arriendo_clp=None), perfil).descartado is False


def test_gastos_comunes_bajos_ganan(perfil):
    """El número con el que se decide es el costo total, no el canon.

    Dos departamentos idénticos salvo los gastos comunes tienen que quedar en
    ese orden, y sin este criterio quedarían empatados.
    """
    barato = S.evaluar(depto(arriendo_clp=1_500_000, gastos_comunes_clp=120_000), perfil)
    caro = S.evaluar(depto(arriendo_clp=1_500_000, gastos_comunes_clp=420_000), perfil)
    assert barato.score > caro.score


def test_comparar_total_cambia_el_veredicto(perfil):
    """Con `comparar: total`, el tope aplica sobre canon + gastos comunes."""
    por_total = dict(perfil)
    por_total["requisitos"] = {**perfil["requisitos"], "comparar": "total"}

    l = depto(arriendo_clp=1_580_000, gastos_comunes_clp=400_000)
    assert S.evaluar(depto(**_campos(l)), perfil).descartado is False
    assert S.evaluar(depto(**_campos(l)), por_total).descartado is True


def _campos(l: Arriendo) -> dict:
    return {"arriendo_clp": l.arriendo_clp, "gastos_comunes_clp": l.gastos_comunes_clp}


# ---------------------------------------------------------------------------
# Filtro duro: zona
# ---------------------------------------------------------------------------

def test_vitacura_entera_entra_sin_mirar_distancia(perfil):
    """"Prioriza Vitacura comuna entera".

    Este departamento está a 3 km del Sport Francés, muy fuera del anillo de
    1,2 km, y tiene que entrar igual porque es Vitacura. Es la regla que
    distingue este perfil de uno que solo mira el radio.
    """
    l = S.evaluar(depto(comuna="Vitacura", lat=-33.3560, lon=-70.5700), perfil)
    assert not l.descartado
    assert l.distancia_km > 1.2


def test_las_condes_dentro_del_anillo_entra(perfil):
    """El borde de Las Condes pegado al club es lo que el anillo deja entrar."""
    l = S.evaluar(depto(comuna="Las Condes", lat=-33.3850, lon=-70.5630), perfil)
    assert not l.descartado
    assert l.distancia_km <= 1.2


def test_las_condes_fuera_del_anillo_se_descarta(perfil):
    """El Golf está en Las Condes y a 5 km: fuera."""
    l = S.evaluar(depto(comuna="Las Condes", lat=-33.4160, lon=-70.5980), perfil)
    assert l.descartado
    assert l.clase_descarte == "zona"


def test_otra_comuna_se_descarta(perfil):
    l = S.evaluar(depto(comuna="Ñuñoa", direccion="Irarrázaval 3000"), perfil)
    assert l.descartado
    assert l.clase_descarte == "zona"


def test_vitacura_gana_a_las_condes_mas_cerca(perfil):
    """La instrucción, medida.

    Un departamento de Vitacura en la periferia de la comuna tiene que ir por
    delante de uno idéntico de Las Condes pegado al club. Sin el multiplicador
    por comuna el orden sale al revés, porque el ancla está a menos de 400 m
    del límite entre las dos.
    """
    vitacura_lejos = S.evaluar(
        depto(comuna="Vitacura", lat=-33.3600, lon=-70.5750), perfil)
    condes_cerca = S.evaluar(
        depto(comuna="Las Condes", lat=-33.3840, lon=-70.5625), perfil)

    assert vitacura_lejos.distancia_km > condes_cerca.distancia_km
    assert vitacura_lejos.score > condes_cerca.score, (
        f"Vitacura a {vitacura_lejos.distancia_km} km puntuó "
        f"{vitacura_lejos.score}; Las Condes a {condes_cerca.distancia_km} km "
        f"puntuó {condes_cerca.score}")


def test_sin_coordenadas_la_comuna_salva(perfil):
    l = S.evaluar(depto(lat=None, lon=None), perfil)
    assert not l.descartado
    assert l.distancia_km is None


# ---------------------------------------------------------------------------
# Filtro duro: operación y tipo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operacion,clase", [
    ("venta", "operacion"),
    ("temporada", "operacion"),
    ("pieza", "operacion"),
])
def test_descarta_lo_que_no_es_arriendo(perfil, operacion, clase):
    l = S.evaluar(depto(operacion=operacion), perfil)
    assert l.descartado
    assert l.clase_descarte == clase


def test_descarta_casa(perfil):
    """El pedido dice "arriendos de departamento"."""
    l = S.evaluar(depto(tipo="casa"), perfil)
    assert l.descartado
    assert l.clase_descarte == "tipo"


def test_tipo_ausente_no_descarta(perfil):
    assert S.evaluar(depto(tipo=""), perfil).descartado is False


# ---------------------------------------------------------------------------
# "Ideal más nuevo"
# ---------------------------------------------------------------------------

def test_mas_nuevo_puntua_mas(perfil):
    """El criterio de orden que se pidió, medido de punta a punta."""
    puntajes = [S.evaluar(depto(antiguedad_anos=a), perfil).score
                for a in (3, 12, 25, 45)]
    assert puntajes == sorted(puntajes, reverse=True), puntajes


def test_antiguo_no_se_descarta(perfil):
    """Un edificio de los ochenta puntúa poco, pero es una decisión del usuario."""
    l = S.evaluar(depto(antiguedad_anos=45), perfil)
    assert not l.descartado
    assert l.score > 0


def test_a_estrenar_puntua_sin_publicar_el_ano(perfil):
    """El techo declarado es un dato de verdad y aparece más que el año."""
    sin_dato = S.evaluar(depto(antiguedad_anos=None), perfil)
    a_estrenar = S.evaluar(
        depto(antiguedad_anos=None, title="Departamento a estrenar en Vitacura"),
        perfil)
    assert a_estrenar.score > sin_dato.score


def test_ano_de_construccion_deriva_la_antiguedad(perfil):
    l = S.evaluar(depto(antiguedad_anos=None, ano_construccion=2020), perfil)
    assert l.antiguedad_anos == S.P.hoy().year - 2020


# ---------------------------------------------------------------------------
# Normalización: no cobrar lo que no se pudo medir
# ---------------------------------------------------------------------------

def test_no_se_castiga_por_datos_que_el_portal_no_publica(perfil):
    """Un departamento perfecto sin año publicado no puede quedar en 55.

    La antigüedad pesa 24 puntos y los portales de arriendo la publican poco.
    Cobrarla como 0 dejaría a este departamento debajo de uno peor que sí
    publicó el año, y el puntaje perdería su única función: ordenar.
    """
    completo = S.evaluar(depto(), perfil)
    sin_ano = S.evaluar(depto(antiguedad_anos=None), perfil)
    assert sin_ano.score >= completo.score - 12, (
        f"completo={completo.score} sin_ano={sin_ano.score}")


def test_con_muy_pocos_datos_no_se_normaliza(perfil):
    """Un 100% sacado de un solo dato es peor que un número bajo y honesto."""
    casi_nada = S.evaluar(
        Arriendo(source="t", url="u", comuna="Vitacura", tipo="departamento"),
        perfil)
    assert casi_nada.score < 60


def test_techo_alcanzable_separa_malo_de_desconocido(perfil):
    l = S.evaluar(depto(antiguedad_anos=None, m2_totales=None, m2_utiles=None),
                  perfil)
    assert S.techo_alcanzable(l) > l.score


def test_alerta_incompleto_si_puede_llegar_al_umbral(perfil):
    """El mercado de arriendo se mueve en días."""
    l = S.evaluar(depto(antiguedad_anos=None, banos=None), perfil)
    assert S.debe_alertar(l, perfil) is True


def test_no_alerta_lo_descartado(perfil):
    l = S.evaluar(depto(m2_totales=60.0), perfil)
    assert S.debe_alertar(l, perfil) is False


# ---------------------------------------------------------------------------
# Preferencias
# ---------------------------------------------------------------------------

def test_piso_alto_suma_y_primer_piso_resta(perfil):
    alto = S.evaluar(depto(piso=9), perfil)
    bajo = S.evaluar(depto(piso=1), perfil)
    assert alto.score > bajo.score


def test_penthouse_se_penaliza(perfil):
    """Vista sin la terraza expuesta ni los problemas de techo."""
    normal = S.evaluar(depto(piso=9), perfil)
    ph = S.evaluar(depto(piso=9, ultimo_piso=True), perfil)
    assert ph.score < normal.score


def test_holgura_por_dormitorio(perfil):
    """Un 130 m² de 3 dormitorios no es un 130 m² picado en 5 piezas."""
    amplio = S.evaluar(depto(m2_totales=134.0, dormitorios=3), perfil)
    picado = S.evaluar(depto(m2_totales=134.0, dormitorios=5), perfil)
    assert amplio.score > picado.score


def test_estacionamientos_suman(perfil):
    con = S.evaluar(depto(estacionamientos=2), perfil)
    sin = S.evaluar(depto(estacionamientos=0), perfil)
    assert con.score > sin.score


def test_publicado_hace_rato_suma(perfil):
    """Es la palanca de negociación, no un defecto."""
    viejo = S.evaluar(
        depto(publicado_el=date.today() - timedelta(days=60)), perfil)
    nuevo = S.evaluar(
        depto(publicado_el=date.today() - timedelta(days=2)), perfil)
    assert viejo.score > nuevo.score


def test_las_preferencias_no_saturan_el_puntaje(perfil):
    """Van fuera de la normalización para que sigan desempatando."""
    l = S.evaluar(depto(piso=12, orientacion="nororiente", estacionamientos=3,
                        bodega=True, title="Departamento nuevo a estrenar, "
                                           "termopanel, calefacción central"),
                  perfil)
    assert l.score <= 100


# ---------------------------------------------------------------------------
# Desglose
# ---------------------------------------------------------------------------

def test_desglose_suma_lo_que_dice(perfil):
    l = S.evaluar(depto(), perfil)
    rubros = S.desglose(l)
    assert len(rubros) == 5
    assert all(r.obtenido <= r.peso for r in rubros)
    assert sum(r.peso for r in rubros) == S.RUBRO_COMPLETO


def test_el_rubro_no_medido_dice_que_falta(perfil):
    l = S.evaluar(depto(arriendo_clp=None), perfil)
    precio = next(r for r in S.desglose(l) if r.nombre == "Precio")
    assert not precio.medido
    assert precio.falta


# ---------------------------------------------------------------------------
# Comuna desconocida — el descarte silencioso que hay que no cometer
# ---------------------------------------------------------------------------

def test_sin_comuna_el_anillo_no_descarta(perfil):
    """El bug del tipo peor: silencioso.

    Un departamento de Vitacura cuya comuna no se alcanzó a leer —pasa cuando
    el aviso la nombra solo en la ficha de detalle— quedaba descartado por
    estar a 2,9 km del club. Pero Vitacura entera es zona válida y se extiende
    mucho más allá del anillo, así que ese descarte perdía departamentos
    buenos sin dejar rastro.

    Con la comuna en blanco no se sabe si el anillo aplica, así que no se usa.
    """
    l = S.evaluar(depto(comuna="", lat=-33.3560, lon=-70.5700), perfil)
    assert not l.descartado
    assert l.distancia_km > 1.2


def test_sin_comuna_pero_lejisimos_si_se_descarta(perfil):
    """Lo que no cabe en ninguna comuna del perfil sí se puede botar.

    Viña del Mar está a 100 km: no hace falta saber la comuna para saber que
    no es la zona.
    """
    l = S.evaluar(depto(comuna="", direccion="Libertad 500",
                        title="Departamento en arriendo",
                        lat=-33.0245, lon=-71.5518), perfil)
    assert l.descartado
    assert l.clase_descarte == "zona"


def test_una_comuna_deducida_del_barrio_no_le_gana_a_las_coordenadas(perfil):
    """Una insinuación no puede más que un dato.

    "Alonso de Córdova" insinúa Vitacura, y hay una calle homónima en otras
    ciudades. Si las coordenadas ponen la propiedad a 100 km, mandan ellas:
    dejarla entrar como Vitacura sería meter al tablero un aviso cuyos
    propios datos se contradicen.
    """
    l = S.evaluar(depto(comuna="", direccion="Alonso de Córdova 4200",
                        lat=-33.0245, lon=-71.5518), perfil)
    assert l.descartado
    assert "desmienten" in l.motivo_descarte


def test_la_comuna_deducida_sirve_cuando_no_hay_coordenadas(perfil):
    """Sigue siendo mejor que nada: es la red de seguridad para el filtro."""
    l = S.evaluar(depto(comuna="", direccion="Alonso de Córdova 4200",
                        lat=None, lon=None), perfil)
    assert not l.descartado
    assert l.comuna == "Vitacura"


def test_una_vecina_lejos_si_se_descarta_por_el_anillo(perfil):
    """El anillo sigue siendo un límite duro para las comunas vecinas."""
    l = S.evaluar(depto(comuna="Las Condes", lat=-33.4160, lon=-70.5980), perfil)
    assert l.descartado


def test_una_comuna_ajena_se_descarta_aunque_este_cerca(perfil):
    """Providencia no está en el perfil, esté donde esté."""
    l = S.evaluar(depto(comuna="Providencia", lat=-33.3830, lon=-70.5640), perfil)
    assert l.descartado
    assert "fuera de la zona" in l.motivo_descarte


# ---------------------------------------------------------------------------
# Portales excluidos — la premisa del proyecto
# ---------------------------------------------------------------------------

def test_descarta_un_aviso_de_portal_inmobiliario(perfil):
    """Los metabuscadores arrastran los avisos de Portal Inmobiliario.

    Ya tienes alertas ahí; un aviso suyo reenviado por Trovit es exactamente
    el mensaje duplicado que este proyecto existe para no mandarte.
    """
    l = S.evaluar(depto(url="https://www.portalinmobiliario.com/MLC-123"), perfil)
    assert l.descartado
    assert l.clase_descarte == "portal"


def test_descarta_un_subdominio_del_portal_excluido(perfil):
    l = S.evaluar(depto(url="https://casa.mercadolibre.cl/MLC-9"), perfil)
    assert l.descartado


def test_un_metabuscador_no_se_descarta(perfil):
    """Trovit sí sirve: pesca corredoras chicas que no están en otra parte."""
    l = S.evaluar(depto(url="https://casas.trovit.cl/aviso/44"), perfil)
    assert not l.descartado


def test_el_dominio_en_el_query_no_descarta(perfil):
    """Un enlace de redirección todavía no es un aviso de ese portal.

    Comparar "está contenido en la URL" perdería este aviso sin motivo; se
    compara por sufijo de host.
    """
    l = S.evaluar(
        depto(url="https://casas.trovit.cl/r?to=portalinmobiliario.com%2F123"),
        perfil)
    assert not l.descartado


def test_un_dominio_parecido_no_calza(perfil):
    """'inmobiliario.com' no es 'portalinmobiliario.com'."""
    from arriendo.scoring import _dominio_excluido

    assert _dominio_excluido("https://inmobiliario.com/x",
                             ["portalinmobiliario.com"]) == ""
    assert _dominio_excluido("https://www.portalinmobiliario.com/x",
                             ["portalinmobiliario.com"]) == "portalinmobiliario.com"
