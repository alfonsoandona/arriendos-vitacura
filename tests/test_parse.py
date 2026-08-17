"""Tests del parser de texto libre.

Los casos no son inventados: están escritos como escriben los portales
chilenos de arriendo, que es lo único contra lo que este parser tiene que
funcionar. Cuando un caso viene de una forma concreta de publicar, se dice
cuál.
"""

from datetime import date

import pytest

from arriendo import parse as P


# ---------------------------------------------------------------------------
# Números
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("crudo,esperado", [
    ("1.550.000", 1_550_000),
    ("1550000", 1_550_000),
    ("1.550,25", 1550.25),
    ("1,6", 1.6),
    ("134,5", 134.5),
    ("118", 118),
    # Convención inglesa: dos grupos de comas o más, sin ambigüedad posible.
    ("1,550,000", 1_550_000),
    ("", None),
    ("abc", None),
    ("1.2.3", None),
])
def test_parse_numero(crudo, esperado):
    assert P.parse_numero(crudo) == esperado


def test_numero_ambiguo_se_lee_a_la_chilena():
    """'1,550' son mil quinientos cincuenta a la chilena, no 1550.

    Con una sola coma no hay forma de saberlo, y en un texto chileno la
    convención chilena es la apuesta correcta.
    """
    assert P.parse_numero("1,550") == 1.55


# ---------------------------------------------------------------------------
# Montos — el corazón del parser
# ---------------------------------------------------------------------------

def test_canon_y_gastos_comunes_rotulados():
    t = "Arriendo: $1.550.000. Gastos comunes: $220.000."
    m = P.parse_montos(t)
    assert m["arriendo_clp"] == 1_550_000
    assert m["gastos_comunes_clp"] == 220_000


def test_gastos_comunes_abreviados():
    """'G.C.' es como lo escribe la mitad de los avisos."""
    m = P.parse_montos("Valor $1.450.000 + G.C. $180.000")
    assert m["arriendo_clp"] == 1_450_000
    assert m["gastos_comunes_clp"] == 180_000


def test_gastos_comunes_pospuestos():
    """La forma invertida: el monto primero y la etiqueta después."""
    m = P.parse_montos("$1.600.000 mensuales, $250.000 de gastos comunes")
    assert m["arriendo_clp"] == 1_600_000
    assert m["gastos_comunes_clp"] == 250_000


def test_dos_montos_sin_rotular_el_mayor_es_el_canon():
    """Los gastos comunes son siempre una fracción del canon.

    Esta es la regla que salva los avisos que escriben los dos números
    sueltos, que son muchos.
    """
    m = P.parse_montos("Depto 3D 3B · $1.500.000 · $190.000")
    assert m["arriendo_clp"] == 1_500_000
    assert m["gastos_comunes_clp"] == 190_000


def test_no_confunde_gastos_comunes_con_el_canon():
    """El bug que ordena todo el módulo: quedarse con el primer monto.

    Acá el primero es el gasto común. Un parser que lee el primero y para
    reporta un arriendo de $220.000 en Vitacura, que además pasaría el filtro
    de presupuesto con holgura y alertaría.
    """
    t = "Gastos comunes aprox. $220.000. Arriendo mensual $1.550.000."
    m = P.parse_montos(t)
    assert m["arriendo_clp"] == 1_550_000
    assert m["gastos_comunes_clp"] == 220_000


# --- Rangos: una etiqueta que gobierna DOS montos ---------------------------
#
# Los cuatro textos de acá abajo son literales de la primera corrida real.
# Todos tienen la misma forma: los gastos comunes escritos como un par o un
# rango, con una sola etiqueta al principio. El segundo monto quedaba sin
# rotular, y de ahí pasaba a canon por ser el mayor de los sueltos.
#
# El resultado se veía bien y era falso: un departamento de 227 m² y 3
# dormitorios en Vitacura publicado a $490.000, primero del tablero. No existe
# ese arriendo — era el gasto común de invierno.

def test_un_par_de_gastos_comunes_no_inventa_un_canon():
    """"$400.000 en verano y $490.000 en invierno" son los dos el mismo gasto."""
    m = P.parse_montos(
        "*Gastos comunes: $400.000 en verano y $490.000 en invierno con "
        "calefacción aproximados")
    assert m.get("gastos_comunes_clp") == 400_000
    assert "arriendo_clp" not in m


def test_un_rango_con_guion_pegado_tampoco():
    m = P.parse_montos(
        "2 estacionamiento y 1 bodega Gastos comunes aprox. "
        "$250.000-$300.000.- por confirmar Calefacción central")
    assert m.get("gastos_comunes_clp") == 250_000
    assert "arriendo_clp" not in m


def test_un_parentesis_que_aclara_tampoco():
    m = P.parse_montos(
        "- 1 bodega - Gasto común: $280.000 (sube a $320.000 con calefacción)")
    assert m.get("gastos_comunes_clp") == 280_000
    assert "arriendo_clp" not in m


def test_el_guion_con_espacios_sigue_separando_dos_conceptos():
    """La corrección no puede tragarse el caso opuesto.

    Entre dos montos y sin nada más en medio, un guión es un rango. Con
    espacios alrededor es un separador de items, y ahí cada monto tiene su
    propia etiqueta. Los dos existen en los avisos reales.
    """
    m = P.parse_montos("Arriendo $1.500.000 - GC $200.000")
    assert m["arriendo_clp"] == 1_500_000
    assert m["gastos_comunes_clp"] == 200_000


def test_el_mas_sigue_separando_canon_de_gastos_comunes():
    """El caso más común de todos, que la herencia no puede romper."""
    m = P.parse_montos("Valor $1.450.000 + G.C. $180.000")
    assert m["arriendo_clp"] == 1_450_000
    assert m["gastos_comunes_clp"] == 180_000


def test_un_rango_de_arriendo_hereda_la_etiqueta_de_arriendo():
    """La herencia sirve para los dos lados: un edificio con varias unidades."""
    m = P.parse_montos("Arriendo desde $1.200.000 a $1.800.000")
    assert m["arriendo_clp"] == 1_200_000


def test_punto_separa_frases_y_corta_la_etiqueta():
    """'gastos comunes bajos. Arriendo $1.550.000' no es un gasto común.

    Sin el corte por puntuación, la ventana hacia atrás alcanza la frase
    anterior y el canon queda rotulado como gasto común.
    """
    m = P.parse_montos("Excelente ubicación, gastos comunes bajos. "
                       "Arriendo $1.550.000")
    assert m["arriendo_clp"] == 1_550_000
    assert "gastos_comunes_clp" not in m


def test_millones_en_texto():
    m = P.parse_montos("Se arrienda en 1,6 millones mensuales")
    assert m["arriendo_clp"] == 1_600_000


def test_garantia_no_se_confunde_con_el_canon():
    """La garantía es un múltiplo del canon y cae en la misma banda."""
    t = "Arriendo $1.500.000. Garantía: 2 meses. Comisión $750.000."
    m = P.parse_montos(t)
    assert m["arriendo_clp"] == 1_500_000
    assert m["garantia_meses"] == 2
    # Ni la comisión ni la garantía pueden terminar anotadas como gasto común.
    assert "gastos_comunes_clp" not in m


def test_garantia_en_letras():
    assert P.parse_garantia("Garantía equivalente a un mes de arriendo") == 1
    assert P.parse_garantia("Se pide garantía de dos meses") == 2


def test_precio_de_venta_no_es_arriendo():
    """Un aviso de venta que se cuela: $450.000.000 no es un canon."""
    m = P.parse_montos("Departamento en venta $450.000.000")
    assert "arriendo_clp" not in m
    assert m["precio_venta_clp"] == 450_000_000


def test_canon_en_uf_se_convierte():
    m = P.parse_montos("Arriendo UF 38 mensuales", valor_uf=40_000)
    assert m["arriendo_uf"] == 38
    assert m["arriendo_clp"] == 1_520_000


def test_valor_uf_del_dia_no_es_el_canon():
    """Los portales muestran la UF del día en el encabezado.

    Cae fuera de la banda de un canon en UF, pero además se descarta por su
    rótulo, que es la defensa que no depende de los números.
    """
    assert P.parse_arriendo_uf("Valor UF hoy: 40.844,79") is None


def test_pesos_gana_sobre_uf_cuando_vienen_los_dos():
    """El de pesos es el que el portal calculó y el que se paga."""
    m = P.parse_montos("Arriendo $1.550.000 (aprox. UF 38)")
    assert m["arriendo_clp"] == 1_550_000
    assert "arriendo_uf" not in m


def test_numero_pelado_con_separadores():
    """Sin signo peso, pero con forma de monto chileno."""
    m = P.parse_montos("Arriendo mensual 1.480.000 más gastos comunes")
    assert m["arriendo_clp"] == 1_480_000


def test_telefono_no_es_un_monto():
    """'+56 9 8765 4321' no tiene forma de monto chileno y no debe leerse."""
    m = P.parse_montos("Contacto +56 9 8765 4321 para visitas")
    assert "arriendo_clp" not in m


def test_sin_montos_devuelve_vacio():
    assert P.parse_montos("Departamento luminoso en Vitacura") == {}


# ---------------------------------------------------------------------------
# Superficies
# ---------------------------------------------------------------------------

def test_util_y_total_rotuladas():
    s = P.parse_superficies("Superficie útil 118 m², superficie total 134 m²")
    assert s["m2_utiles"] == 118
    assert s["m2_totales"] == 134


def test_util_mas_terraza_deduce_total():
    """Aritmética, no estimación: así definen la total los portales chilenos."""
    s = P.parse_superficies("118 m² útiles más 16 m² de terraza")
    assert s["m2_utiles"] == 118
    assert s["m2_terraza"] == 16
    assert s["m2_totales"] == 134


def test_dos_medidas_sin_rotular_la_mayor_es_la_total():
    s = P.parse_superficies("Departamento 134 m² / 118 m²")
    assert s["m2_totales"] == 134
    assert s["m2_utiles"] == 118


def test_una_medida_sola_se_anota_como_util():
    """La lectura conservadora.

    Anotarla como total sería inventar: si en realidad era la útil, el
    tablero mostraría 118 m² totales de un departamento que tiene 134, que es
    un dato falso donde antes había uno ausente.
    """
    s = P.parse_superficies("Departamento de 118 m² en Vitacura")
    assert s["m2_utiles"] == 118
    assert "m2_totales" not in s


def test_decimal_no_parte_la_medida():
    """'134,5 m²' es 134,5 y no 5.

    El delimitador de cláusulas no corta entre dígitos, porque ahí la coma es
    el decimal del formato chileno. Sin esa excepción, un departamento de
    134,5 m² se leía como uno de 5 y quedaba fuera del filtro.
    """
    s = P.parse_superficies("Superficie total 134,5 m²")
    assert s["m2_totales"] == 134.5


def test_terreno_no_contamina_la_superficie_del_departamento():
    s = P.parse_superficies("Casa de 134 m² en terreno de 400 m²")
    assert s["m2_terreno"] == 400
    assert s.get("m2_totales") != 400


def test_bodega_no_es_la_superficie():
    """Una medida de 6 m² cae bajo la banda y no se lee como superficie."""
    s = P.parse_superficies("134 m² totales, bodega de 6 m²")
    assert s["m2_totales"] == 134
    assert s.get("m2_utiles") != 6


def test_unidad_adelante():
    """Las grillas de atributos rotulan la columna y ponen el número después."""
    s = P.parse_superficies("Superficie mt2 118 Dormitorios 3")
    assert s["m2_utiles"] == 118


def test_total_menor_que_util_se_descarta():
    """Un error de lectura no debe quedar guardado como dato."""
    s = P.parse_superficies("Superficie total 90 m², superficie útil 118 m²")
    assert s["m2_utiles"] == 118
    assert "m2_totales" not in s


# ---------------------------------------------------------------------------
# Antigüedad
# ---------------------------------------------------------------------------

def test_ano_construccion():
    ano, antig = P.parse_antiguedad("Año de construcción: 2018",
                                    ref=date(2026, 8, 16))
    assert ano == 2018
    assert antig == 8


def test_antiguedad_declarada_deriva_el_ano():
    ano, antig = P.parse_antiguedad("Antigüedad: 12 años", ref=date(2026, 8, 16))
    assert antig == 12
    assert ano == 2014


def test_a_estrenar_es_un_techo_no_una_antiguedad():
    """"A estrenar" no dice que tenga 0 años: dice que tiene pocos."""
    assert P.techo_antiguedad("Departamento a estrenar") == P.TECHO_A_ESTRENAR
    assert P.techo_antiguedad("Edificio nuevo, primer arriendo") == P.TECHO_EDIFICIO_NUEVO
    assert P.techo_antiguedad("Departamento remodelado") is None
    # Y no se confunde con el dato real: son campos distintos.
    assert P.parse_antiguedad("Departamento a estrenar") == (None, None)


# ---------------------------------------------------------------------------
# Programa
# ---------------------------------------------------------------------------

def test_dormitorios_y_banos():
    p = P.parse_programa("3 dormitorios, 2 baños")
    assert p["dormitorios"] == 3
    assert p["banos"] == 2


def test_piezas_es_sinonimo_de_dormitorios():
    """El pedido dice "3 piezas mínimo" y así lo escriben muchos avisos."""
    assert P.parse_programa("4 piezas y 3 baños")["dormitorios"] == 4


def test_formato_compacto():
    p = P.parse_programa("Depto 3D/2B en Vitacura")
    assert p["dormitorios"] == 3
    assert p["banos"] == 2


def test_numeros_en_letras():
    p = P.parse_programa("Amplio departamento de tres dormitorios y dos baños")
    assert p["dormitorios"] == 3
    assert p["banos"] == 2


def test_pieza_de_servicio_no_suma_dormitorios():
    """Decisión de producto, no limitación técnica.

    "3 piezas mínimo" habla de dormitorios de la familia. Sumar la pieza de
    servicio dejaría entrar departamentos de 2D como si fueran de 3, que es
    exactamente el error que el filtro duro existe para evitar.
    """
    p = P.parse_programa("2 dormitorios más dormitorio de servicio, 3 baños")
    assert p["dormitorios"] == 2
    assert p["pieza_servicio"] is True


def test_estacionamientos():
    assert P.parse_programa("2 estacionamientos y bodega")["estacionamientos"] == 2
    assert P.parse_programa("Estacionamientos: 3")["estacionamientos"] == 3
    assert P.parse_programa("con estacionamiento y bodega")["estacionamientos"] == 1
    assert P.parse_programa("dos estacionamientos")["estacionamientos"] == 2


def test_bodega():
    assert P.parse_programa("con bodega")["bodega"] is True
    assert P.parse_programa("sin bodega")["bodega"] is False
    assert "bodega" not in P.parse_programa("3 dormitorios")


# ---------------------------------------------------------------------------
# Piso, orientación, unidad
# ---------------------------------------------------------------------------

def test_piso():
    assert P.parse_piso("Piso 12") == 12
    assert P.parse_piso("12° piso") == 12
    assert P.parse_piso("sexto piso") == 6
    assert P.parse_piso("décimo quinto piso") == 15
    assert P.parse_piso("sin datos") is None


def test_numero_de_departamento_no_es_el_piso():
    """Un 1502 suelto no debe leerse como piso 1502."""
    assert P.parse_piso("Departamento 1502") is None
    assert P.piso_desde_numero("Departamento 1502") == 15


def test_unidad_entra_en_la_identidad():
    assert P.parse_unidad("Depto 802, Alonso de Córdova 4200") == "802"
    assert P.parse_unidad("Alonso de Córdova 4200") == ""


def test_ultimo_piso():
    assert P.es_ultimo_piso("Penthouse con terraza") is True
    assert P.es_ultimo_piso("Piso 8 de 14") is False


def test_orientacion_compuesta_gana_a_la_simple():
    assert P.parse_orientacion("Orientación nororiente") == "nororiente"
    assert P.parse_orientacion("Vista al norte") == "norte"


# ---------------------------------------------------------------------------
# Amoblado y mascotas
# ---------------------------------------------------------------------------

def test_amoblado():
    assert P.parse_amoblado("Departamento amoblado") == "amoblado"
    assert P.parse_amoblado("Se arrienda sin amoblar") == "sin amoblar"
    assert P.parse_amoblado("Semi amoblado, con cocina equipada") == "semi amoblado"
    assert P.parse_amoblado("Departamento luminoso") == ""


def test_sin_amoblar_no_se_lee_como_amoblado():
    """'sin amoblar' contiene 'amoblar': el orden de las reglas es la defensa."""
    assert P.parse_amoblado("Amplio depto sin amoblar en Vitacura") == "sin amoblar"


def test_mascotas():
    assert P.parse_mascotas("No se aceptan mascotas") == "no acepta"
    assert P.parse_mascotas("Se aceptan mascotas") == "acepta"
    assert P.parse_mascotas("Pet friendly") == "acepta"
    assert P.parse_mascotas("Departamento luminoso") == ""


# ---------------------------------------------------------------------------
# Operación — el filtro más rentable
# ---------------------------------------------------------------------------

def test_operacion_arriendo():
    assert P.parse_operacion("Se arrienda departamento en Vitacura") == "arriendo"


def test_operacion_venta():
    assert P.parse_operacion("Departamento en venta, 12.000 UF") == "venta"


def test_pieza_se_detecta_antes_que_arriendo():
    """'Arriendo de pieza' contiene 'arriendo'."""
    assert P.parse_operacion("Arriendo de pieza en departamento compartido") == "pieza"


def test_temporada_se_detecta_antes_que_arriendo():
    assert P.parse_operacion("Arriendo por días, amoblado ejecutivo") == "temporada"
    assert P.parse_operacion("Arriendo de temporada en Vitacura") == "temporada"


# --- Falsos positivos que sacaron 51 departamentos reales de Vitacura -------
#
# Todos estos textos salieron de la primera corrida de verdad contra los
# portales: 328 avisos únicos, de los cuales 51 —el 16% del inventario— se
# descartaron con el motivo "es arriendo por temporada" sin serlo.
#
# El modo de fallar es el peor que tiene este radar: no revienta, no avisa, y
# deja un motivo que suena razonable. Nadie va a ir a revisar un descarte que
# dice algo creíble.

def test_comedor_de_diario_no_es_arriendo_por_dias():
    """El error que se llevó a los mejores departamentos del listado.

    En Chile el "comedor de diario" es el comedor chico de la cocina, y es una
    característica estándar de un departamento bueno. La regla original tenía
    `diario` como palabra suelta, así que descartaba justo los penthouses de
    Lo Gallo y Paul Claudel por describirse bien.
    """
    assert P.parse_operacion(
        "Living comedor con salida a gran terraza, cocina con comedor diario "
        "y gran logia. Departamento amplio de muy buena distribución") == "arriendo"
    assert P.parse_operacion(
        "Cocina independiente con comedor de diario. Loggia cerrada de gran "
        "tamaño") == "arriendo"


def test_el_pie_de_economicos_no_es_arriendo_por_dias():
    """`economicos.cl` es el clasificado de El Mercurio.

    Cada aviso suyo termina en "Diario: El Mercurio", así que la palabra
    suelta se llevaba la fuente entera — y es una de las que más entrega.
    """
    assert P.parse_operacion(
        "$ 1.350.000 Departamento en Arriendo en Vitacura 3 dormitorios "
        "Región: Metropolitana de Santiago Publicado el: 2026-08-16 00:18:00 "
        "Diario: El Mercurio") == "arriendo"


def test_un_arriendo_por_dias_de_verdad_si_se_detecta():
    """La corrección no puede dejar entrar lo que sí es por temporada."""
    for texto in ("Arriendo diario, departamento amoblado",
                  "Valor por día $85.000, mínimo 3 noches",
                  "Tarifa diaria desde $90.000",
                  "$120.000 diarios, aseo incluido",
                  "Arriendo por días, amoblado ejecutivo",
                  "Departamento vacacional, corta estadía"):
        assert P.parse_operacion(texto) == "temporada", texto


def test_un_bano_compartido_no_es_el_arriendo_de_una_pieza():
    """Otro caso real, con el mismo error de forma.

    "Dos dormitorios de servicio con baño compartido" describe un
    departamento grande, no una oferta de pieza. La palabra suelta descartaba
    departamentos de cuatro dormitorios en Vitacura.
    """
    assert P.parse_operacion(
        "- 2 Dormitorios de servicio con baño compartido. - Entrada de "
        "servicio independiente") == "arriendo"
    assert P.parse_operacion(
        "Segundo baño completo compartido con 2 habitaciones") == "arriendo"


def test_una_pieza_de_verdad_si_se_detecta():
    for texto in ("Arriendo de pieza en departamento compartido",
                  "Pieza en arriendo, Vitacura",
                  "Depto compartido, se busca compañera",
                  "Coliving en Nueva Costanera"):
        assert P.parse_operacion(texto) == "pieza", texto


def test_arriendo_gana_cuando_el_aviso_ofrece_las_dos():
    """Muchas fichas ofrecen venta y arriendo del mismo departamento."""
    assert P.parse_operacion("Venta y arriendo de departamento") == "arriendo"


def test_sin_senal_se_cree_a_la_fuente():
    """Un listado ya filtrado a arriendo no repite la palabra en cada tarjeta."""
    assert P.parse_operacion("Depto 3D 2B Vitacura 134 m²") == "arriendo"
    assert P.parse_operacion("Depto 3D 2B", kind_default="venta") == "venta"


# ---------------------------------------------------------------------------
# Tipo y comuna
# ---------------------------------------------------------------------------

def test_tipo():
    assert P.parse_tipo("Departamento en Vitacura") == "departamento"
    assert P.parse_tipo("Casa en condominio") == "casa"
    assert P.parse_tipo("Oficina 120 m²") == "oficina"


def test_bodega_como_accesorio_no_cambia_el_tipo():
    """'departamento con bodega' es un departamento."""
    assert P.parse_tipo("Departamento con bodega y estacionamiento") == "departamento"


def test_estacionamiento_solo_si_es_lo_que_se_arrienda():
    assert P.parse_tipo("Se arrienda estacionamiento") == "estacionamiento"


def test_comuna():
    assert P.parse_comuna("Depto en Vitacura, RM") == "Vitacura"
    assert P.parse_comuna("Las Condes, sector El Golf") == "Las Condes"


def test_comuna_mas_larga_gana():
    """'Isla de Maipo' contiene 'Maipo': la más específica identifica el lugar."""
    assert P.parse_comuna("Propiedad en Isla de Maipo") == "Isla de Maipo"


def test_barrio_insinua_la_comuna():
    """Los avisos de arriendo nombran el sector más seguido que la comuna."""
    assert P.comuna_por_barrio("Santa María de Manquehue, excelente ubicación") == "Vitacura"
    assert P.comuna_por_barrio("Sector Ñuñoa") == ""


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------

def test_fecha_chilena():
    assert P.parse_fecha("15-09-2026") == date(2026, 9, 15)
    assert P.parse_fecha("6 de agosto de 2026") == date(2026, 8, 6)
    assert P.parse_fecha("06 ago 2026") == date(2026, 8, 6)


def test_calle_que_parece_mes():
    """'MAYECURA 1116' no es el 7 de mayo del año 1116."""
    assert P.parse_fecha("MAYECURA 1116 DEPTO 201") is None


def test_disponibilidad():
    hoy = date(2026, 8, 16)
    assert P.parse_disponibilidad("Disponible de inmediato", ref=hoy) == hoy
    assert P.parse_disponibilidad(
        "Disponible a partir del 01-10-2026", ref=hoy) == date(2026, 10, 1)
    assert P.parse_disponibilidad("Departamento luminoso", ref=hoy) is None


def test_publicado_relativo():
    """Los portales lo muestran casi siempre en relativo."""
    hoy = date(2026, 8, 16)
    assert P.parse_publicado("Publicado hace 3 días", ref=hoy) == date(2026, 8, 13)
    assert P.parse_publicado("hace 2 meses", ref=hoy) == date(2026, 6, 17)
    assert P.parse_publicado("Publicado el 12-07-2026", ref=hoy) == date(2026, 7, 12)


def test_particular():
    assert P.es_particular("Publicado por el dueño, trato directo") is True
    assert P.es_particular("Corredora XYZ Propiedades") is False


# ---------------------------------------------------------------------------
# Códigos ISO de moneda — el formato de Yapo
#
# Salieron de mirar un aviso real: Yapo genera sus títulos desde su base de
# datos y escribe la moneda como código ISO, sin separadores de miles. Con los
# patrones que había, esa fuente entera entregaba avisos sin precio.
# ---------------------------------------------------------------------------

def test_clp_como_codigo_iso():
    """'Departamento en Luis Carrera 3 Dormitorios por CLP 1600000.00'."""
    m = P.parse_montos("Departamento en Luis Carrera 3 Dormitorios "
                       "por CLP 1600000.00")
    assert m["arriendo_clp"] == 1_600_000


def test_clf_es_la_uf():
    """CLF es el código ISO de la UF, y Yapo publica mucho arriendo así."""
    m = P.parse_montos("ARRIENDO AMPLIO DPTO VITACURA 3 Dormitorios "
                       "por CLF 46.00", valor_uf=40_000)
    assert m["arriendo_uf"] == 46
    assert m["arriendo_clp"] == 1_840_000


def test_clf_bajo_no_se_lee_como_pesos():
    """46 en CLF son 46 UF, no 46 pesos."""
    m = P.parse_montos("por CLF 33.00", valor_uf=40_000)
    assert m["arriendo_clp"] == 1_320_000
