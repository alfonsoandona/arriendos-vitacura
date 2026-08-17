"""Extracción de datos desde texto libre en español chileno.

Los avisos de arriendo no tienen formato: son la descripción que escribió la
corredora, más una grilla de atributos que cada portal rotula distinto. Todo lo
que sigue son heurísticas sobre texto crudo, y cada una devuelve `None` antes
que adivinar.

Hay una diferencia grande con un parser de avisos de VENTA, y ordena casi todo
este módulo: en un aviso de arriendo hay **varios montos en pesos a la vez** y
significan cosas distintas —canon, gastos comunes, garantía, comisión— mientras
que en una venta hay un precio y ya. Quedarse con el primero convierte
$220.000 de gastos comunes en el arriendo del departamento. Por eso los montos
no se leen de a uno: se leen todos, se clasifican por lo que los rotula, y
recién ahí se decide cuál es cuál.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from datetime import datetime as _dt
from .tiempo import ahora_utc


def hoy() -> date:
    return ahora_utc().date()


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "")
        if unicodedata.category(c) != "Mn"
    )


def norm(s: str) -> str:
    """Minúsculas, sin tildes, espacios colapsados. Para matching de keywords."""
    return re.sub(r"\s+", " ", strip_accents(s or "").lower()).strip()


# ---------------------------------------------------------------------------
# Números en formato chileno
# ---------------------------------------------------------------------------

def parse_numero(raw: str) -> float | None:
    """Convierte un número escrito a la chilena: punto=miles, coma=decimal.

    '1.550.000' -> 1550000.0
    '1.550,25'  -> 1550.25
    '1,6'       -> 1.6
    '1550000'   -> 1550000.0
    """
    if not raw:
        return None
    s = raw.strip().replace(" ", "")
    if not re.fullmatch(r"[\d.,]+", s):
        return None

    # Convención inglesa: comas cada tres dígitos, DOS veces o más. Los
    # portales que sindican avisos desde plataformas gringas publican
    # "$1,550,000", y con la regla chilena eso no es ningún número.
    #
    # Se exigen dos grupos, no uno, porque "1,550" es genuinamente ambiguo: en
    # Chile es 1,55 y en inglés 1550. Con dos comas ya no hay ambigüedad.
    if re.fullmatch(r"\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?", s):
        return float(s.replace(",", ""))

    if "," in s:
        entero, _, dec = s.rpartition(",")
        entero = entero.replace(".", "")
        if not entero:
            entero = "0"
        if not (entero.isdigit() and dec.isdigit()):
            return None
        try:
            return float(f"{entero}.{dec}")
        except ValueError:
            return None

    if "." in s:
        partes = s.split(".")
        # Si los grupos posteriores son todos de 3 dígitos, son separadores de
        # miles ('1.550.000'). Si no, es un decimal ('1.5').
        if all(len(p) == 3 for p in partes[1:]) and partes[0].isdigit():
            return float(s.replace(".", ""))
        if len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
            return float(s)
        return None

    return float(s) if s.isdigit() else None


# ---------------------------------------------------------------------------
# Montos
#
# El corazón del módulo. Ver el docstring de arriba para el porqué.
# ---------------------------------------------------------------------------

# Un número debe empezar y terminar en dígito: así el punto que cierra la
# frase ("...$1.550.000.") no queda dentro del grupo capturado.
_NUM = r"(\d[\d.,]*\d|\d)"

# Valor de la UF, para convertir los avisos que publican el canon en UF. Es un
# valor por omisión y se puede pisar: el CLI lo lee de la variable de entorno
# VALOR_UF, y varios portales publican el del día en su encabezado.
#
# No se consulta a una API a propósito: una dependencia de red más es una
# forma más de que la corrida falle entera, y para decidir si un arriendo
# está cerca de 1,6 millones un error de 1% en la UF no cambia nada.
VALOR_UF_DEFECTO = 40_800.0


def _monto(match: re.Match, grupo: int = 1) -> float | None:
    return parse_numero(match.group(grupo))


# Las tres formas de escribir un monto en pesos en un aviso chileno.
_MONTOS_CLP = [
    # "$1.550.000", "$ 1.550.000.-"
    re.compile(rf"\$\s*{_NUM}"),
    # "1,6 millones", "1.5 millón"
    re.compile(rf"{_NUM}\s*millon(?:es)?\b", re.I),
    # "1.550.000 pesos"
    re.compile(rf"{_NUM}\s*pesos\b", re.I),
    # El código ISO de la moneda, que es como publican los portales que
    # generan sus títulos desde una base de datos. Yapo escribe
    # "Departamento en Luis Carrera 3 Dormitorios por CLP 1600000.00" y sin
    # esta forma esa fuente entera entrega avisos sin precio: el número no
    # lleva separadores de miles, así que ningún otro patrón lo reconoce.
    re.compile(rf"\bCLP\s*\$?\s*{_NUM}", re.I),
    # Un número grande y pelado, sin símbolo. Va último y es el más riesgoso:
    # se exige forma de monto chileno CON separadores de miles ("1.550.000")
    # para no leer el número de la calle ni un código de aviso.
    re.compile(r"(?<![\d.,$])(\d{1,3}(?:\.\d{3}){1,2})(?![\d.,])"),
]

_MONTOS_UF = [
    re.compile(rf"\bu\.?\s?f\.?\s*\$?\s*{_NUM}", re.I),
    # CLF es el código ISO de la UF, y Yapo lo usa mucho: buena parte de sus
    # arriendos de Vitacura se publican como "CLF 46.00". Sin esto quedaban
    # sin precio, que en este radar significa no poder aplicar el filtro de
    # presupuesto — el criterio central del pedido.
    re.compile(rf"\bCLF\s*{_NUM}", re.I),
    re.compile(rf"{_NUM}\s*u\.?\s?f\.?(?![a-z])", re.I),
]


# Lo que rotula cada monto. El orden importa: se prueba de la etiqueta más
# específica a la más genérica, porque "gastos comunes" también contiene
# "comunes" y "valor arriendo" también contiene "arriendo".
_ETIQUETAS = [
    ("gastos_comunes", re.compile(
        r"gastos?\s*com(?:un(?:es)?)?\.?|g\.?\s*c\.?(?![a-z])|"
        r"gasto\s*com[uú]n|contribuci[oó]n(?:es)?\s*mensual", re.I)),
    ("garantia", re.compile(r"garant[ií]a|dep[oó]sito", re.I)),
    ("comision", re.compile(r"comisi[oó]n|corretaje|honorarios?", re.I)),
    ("contribuciones", re.compile(r"contribuciones", re.I)),
    ("venta", re.compile(r"\bventa\b|\bvenden?\b|precio\s*de\s*venta", re.I)),
    ("arriendo", re.compile(
        r"arriendo|arrienda|canon|renta\s*mensual|valor\s*mensual|"
        r"precio\s*mensual|\bmensual(?:idad)?\b|\bal\s*mes\b|/\s*mes\b|"
        # "Precio convertido: $1.817.308" es como goplaceit muestra el canon
        # en pesos cuando el aviso está publicado en UF.
        r"\bmes\b|precio\s*convertido", re.I)),
]

# Cuánto texto mirar a cada lado del monto para encontrar su etiqueta.
#
# Antes es más ancho que después porque en castellano la etiqueta va casi
# siempre delante ("Gastos comunes: $220.000"). El "después" existe para la
# forma invertida, que también se usa ("$220.000 de gastos comunes").
_VENTANA_ANTES = 42
_VENTANA_DESPUES = 26


# Dónde termina de verdad una frase.
#
# No sirve cortar en cualquier punto seguido de espacio: en castellano las
# abreviaturas llevan punto y son justamente las que rodean a los montos
# —"aprox.", "G.C.", "mts."—. Con el corte ingenuo, "Gastos comunes aprox.
# $220.000" perdía su etiqueta y el gasto común quedaba sin clasificar.
#
# Un punto solo cierra la frase cuando lo que sigue empieza en mayúscula. El
# resto de los separadores (punto y coma, barra vertical, viñeta, salto de
# línea) sí cortan siempre: ninguno aparece dentro de una abreviatura.
# El "+" y el "más" cortan igual que un punto y coma, y son el caso que más
# aparece: en un aviso chileno el canon y los gastos comunes se escriben
# sumados —"$1.450.000 + G.C. $180.000"— y ese "+ G.C." rotula al monto que
# viene DESPUÉS, no al de antes. Sin el corte, el canon quedaba clasificado
# como gasto común y el aviso se quedaba sin arriendo.
_FIN_DE_FRASE = re.compile(
    r"[.;]\s+(?=[A-ZÁÉÍÓÚÑ¡¿])|[;|\n·•]|\s[-–]\s|\+|\s+m[aá]s\s+")

# Una palabra con mayúscula seguida de dos puntos es el rótulo de lo que
# VIENE, nunca de lo que quedó atrás. Sin este corte, en "promedio del sector
# $2.400.000 Precio convertido: $1.817.308" el rótulo del segundo monto caía
# en la ventana DESPUÉS del primero, y el promedio del sector quedaba
# rotulado como canon. La forma invertida legítima —"$220.000 de gastos
# comunes"— viene en minúscula y sin dos puntos, y no se toca.
_ROTULO_DE_LO_QUE_VIENE = re.compile(
    r"\s(?=[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*(?:\s+[\wáéíóúñ.]+){0,2}\s*:)")


def _etiqueta_de(texto: str, inicio: int, fin: int,
                 desde: int = 0, hasta: int | None = None) -> str:
    """Cómo está rotulado el monto que va de `inicio` a `fin`.

    `desde` y `hasta` acotan la búsqueda al hueco entre este monto y sus
    vecinos, y no son un detalle: sin ellos la ventana hacia adelante se come
    la etiqueta del monto SIGUIENTE. Medido con un aviso real —"Valor
    $1.450.000 + G.C. $180.000"— el canon quedaba rotulado como gasto común
    porque el "+ G.C." del monto de al lado caía dentro de su ventana.

    Devuelve "" cuando nada lo rotula, que es un resultado legítimo y
    frecuente: la mitad de los avisos escriben el canon a secas en el título.
    """
    hasta = len(texto) if hasta is None else hasta

    antes = texto[max(desde, inicio - _VENTANA_ANTES):inicio]
    despues = texto[fin:min(hasta, fin + _VENTANA_DESPUES)]

    antes = _FIN_DE_FRASE.split(antes)[-1]
    despues = _FIN_DE_FRASE.split(despues)[0]
    despues = _ROTULO_DE_LO_QUE_VIENE.split(despues)[0]

    for nombre, patron in _ETIQUETAS:
        if patron.search(antes):
            return nombre
    for nombre, patron in _ETIQUETAS:
        if patron.search(despues):
            return nombre
    return ""


# Bandas de plausibilidad. Un monto fuera de su banda no es ese concepto, sea
# cual sea la etiqueta que lo acompañe: los portales rotulan mal seguido.
#
# Son anchas a propósito. No están para filtrar por presupuesto —de eso se
# encarga el perfil, que es donde el usuario puede cambiarlo— sino para
# distinguir un canon de un precio de venta y de un número de teléfono.
BANDA_ARRIENDO = (250_000, 20_000_000)
BANDA_GASTOS_COMUNES = (15_000, 1_500_000)

# De acá para arriba, en pesos, es un precio de VENTA y no un arriendo.
# Nadie arrienda un departamento en cincuenta millones al mes.
PISO_PRECIO_VENTA = 50_000_000


# El código de la publicación, escrito con puntos de miles como si fuera un
# monto. "Cod. 109.892" y "{[COD-110.607-VD]}" son avisos REALES de la corrida
# del 17-08: el número cae justo en la banda de los gastos comunes y, como no
# tenía otro rótulo cerca, la regla 3 lo anotaba como GC — $109.892 de gastos
# comunes que no existen, sumados al costo mensual con que se comparan los
# departamentos. Se borra ANTES de ubicar montos, reemplazando por espacios
# para que las posiciones del resto del texto no se muevan.
_CODIGO_DE_AVISO = re.compile(
    r"\bc[oó]d(?:igo)?\b\.?\s*[:#°-]?\s*(?:fuenzalida\s+)?"
    r"(?:[A-Z]{1,5}\*?\s?)?[\d.]+(?:-\w+)?", re.I)

# El código SIN la palabra "cod": la corredora lo escribe con su sigla pegada
# al número — "AT397.842*", "*MPB*411.605", "RS* 412.741" son avisos REALES
# de Fuenzalida. El número cae justo en la banda de un arriendo y el canon
# verdadero ($36 millones, comercial) queda fuera de banda, así que una
# propiedad industrial de Rancagua entraba al tablero "a $397.842" — bajo el
# tope y compitiendo con los departamentos de verdad.
#
# SIN re.IGNORECASE a propósito: en minúscula, "por 850.000" calzaría con la
# forma sigla+espacio y borraría un precio real. La sigla en mayúsculas y el
# espacio solo después del asterisco acotan el patrón a lo que es un código.
_CODIGO_CON_SIGLA = re.compile(
    r"(?<![\w.,])(?:(?!UF|CLP|CLF)[A-ZÁÉÍÓÚÑ]{2,5}\*?"
    r"|[A-ZÁÉÍÓÚÑ]{2,5}\*\s)\d{3}\.\d{3}(?![\d.])")


def _montos_etiquetados(texto: str) -> list[tuple[float, str, int]]:
    """Todos los montos en pesos del texto, con su etiqueta y su posición.

    Va en dos fases —primero se ubican todos los montos, después se rotula
    cada uno— y ese orden es el que permite acotar la ventana de cada monto al
    hueco que lo separa de sus vecinos. Rotulando sobre la marcha no se puede:
    todavía no se sabe dónde empieza el siguiente.

    Se deduplica por posición porque los patrones se pisan: "$1.550.000" lo
    encuentra el patrón del signo peso y también el del número pelado.
    """
    t = _CODIGO_DE_AVISO.sub(lambda m: " " * len(m.group(0)), texto or "")
    t = _CODIGO_CON_SIGLA.sub(lambda m: " " * len(m.group(0)), t)

    # Fase 1: ubicar. Se guarda el tramo COMPLETO del match (con el "$" o el
    # "millones"), que es el que hay que saltarse al mirar los alrededores.
    crudos: dict[int, tuple[float, int, int]] = {}
    for i, patron in enumerate(_MONTOS_CLP):
        for m in patron.finditer(t):
            valor = _monto(m)
            if valor is None:
                continue
            # "1,6 millones" y "1.5 millón" vienen en millones.
            if i == 1:
                if not 0.2 <= valor <= 100:
                    continue
                valor *= 1_000_000
            inicio = m.start(1)
            if inicio in crudos:
                continue
            crudos[inicio] = (valor, m.start(), m.end())

    orden = sorted(crudos)

    # Fase 2: rotular, cada uno acotado por sus vecinos.
    salida: list[tuple[float, str, int]] = []
    for j, inicio in enumerate(orden):
        valor, span_ini, span_fin = crudos[inicio]
        desde = crudos[orden[j - 1]][2] if j else 0
        hasta = crudos[orden[j + 1]][1] if j + 1 < len(orden) else None
        etiqueta = _etiqueta_de(t, span_ini, span_fin, desde, hasta)

        # Fase 3: heredar la etiqueta del monto de al lado cuando los dos son
        # el mismo concepto escrito como par o como rango.
        #
        # El caso real que lo motivó, de un penthouse de Lo Curro:
        #
        #     "*Gastos comunes: $400.000 en verano y $490.000 en invierno"
        #
        # El primero queda rotulado; el segundo no tiene ninguna etiqueta
        # cerca, así que caía a "sin rotular" y de ahí a canon por ser el mayor
        # de los sueltos. Resultado: un departamento de 227 m² y 3 dormitorios
        # en Vitacura publicado a $490.000, primero en el tablero. No existe
        # ese arriendo; era el gasto común de invierno.
        #
        # La condición es estricta a propósito: el hueco entre los dos montos
        # tiene que ser corto, no puede tener un fin de frase —eso incluye el
        # "+" y el "más", que es como se escribe "canon MÁS gastos comunes" y
        # justamente NO es una continuación— y tiene que traer una conjunción
        # o un guión de rango. Con eso, "$1.450.000 + G.C. $180.000" sigue
        # rotulando cada monto por su cuenta.
        if not etiqueta and j and salida[-1][1]:
            hueco = t[crudos[orden[j - 1]][2]:span_ini]
            if _es_continuacion(hueco):
                etiqueta = salida[-1][1]

        salida.append((valor, etiqueta, inicio))
    return salida


# Un hueco de hasta 40 caracteres: "en verano y", "(sube a", "o", "hasta", y
# el guión pegado de un rango escrito a la chilena: "$250.000-$300.000". Más
# largo que eso ya es otra frase aunque tenga una "y" adentro.
#
# El guión va SIN espacios alrededor a propósito. Con espacios —"$1.500.000 -
# GC $200.000"— es un separador de items y no un rango, y `_FIN_DE_FRASE` ya
# lo trata como fin de frase; heredar ahí sería exactamente el error que este
# módulo existe para no cometer.
_MAX_HUECO_CONTINUACION = 40
_CONECTOR = re.compile(r"\s(?:y|o|u|a|hasta)\s|/|\ba\s+\$", re.I)
# El rango escrito pegado: "$250.000-$300.000". El hueco entre los dos montos
# es el guión solo, sin nada más.
_RANGO_PEGADO = re.compile(r"^\s*[-–—]\s*$")


def _es_continuacion(hueco: str) -> bool:
    """¿Los dos montos que rodean este hueco son el mismo concepto?"""
    if len(hueco) > _MAX_HUECO_CONTINUACION:
        return False
    if _RANGO_PEGADO.match(hueco):
        # Se chequea antes que el fin de frase porque `_FIN_DE_FRASE` trata
        # " - " como separador de items, y con razón: "$1.500.000 - GC
        # $200.000" son dos conceptos. Pero entre dos montos y sin nada más en
        # medio, un guión es un rango. Los dos casos existen en los avisos
        # reales y se distinguen por lo que hay alrededor, no por el guión.
        return True
    if _FIN_DE_FRASE.search(hueco):
        return False
    return bool(_CONECTOR.search(hueco))


# Los gastos comunes publicados en UF: "Gastos comunes: UF 15,15" es una
# ficha REAL de iCasas. La banda en UF es angosta porque un GC de tres
# cifras en UF sería un canon.
_GC_EN_UF = re.compile(
    r"gastos?\s*com(?:un(?:es)?)?\.?\s*:?\s*(?:de\s+)?"
    r"u\.?\s?f\.?\s*\$?\s*([\d.,]+)", re.I)
_BANDA_GC_UF = (0.3, 40)


def _gc_en_uf(texto: str, valor_uf: float | None) -> float | None:
    m = _GC_EN_UF.search(texto or "")
    if not m:
        return None
    v = parse_numero(m.group(1))
    if v and _en(v, _BANDA_GC_UF):
        return round(v * (valor_uf or VALOR_UF_DEFECTO))
    return None


def montos_rotulados(texto: str, valor_uf: float | None = None) -> dict:
    """Solo los montos que están ROTULADOS, sin ninguna de las heurísticas.

    Es el modo para leer una FICHA entera: ahí los números sueltos son los
    promedios del sector, el valor UF del día y los avisos del widget de
    similares, y las reglas 2 y 3 de `parse_montos` —pensadas para el texto
    corto de una tarjeta— los tomarían por el canon o los gastos comunes.
    En una página completa, lo que no tiene rótulo no es de esta propiedad.
    """
    out: dict = {}
    for valor, etiqueta, _ in _montos_etiquetados(texto or ""):
        if etiqueta == "arriendo" and _en(valor, BANDA_ARRIENDO):
            out.setdefault("arriendo_clp", valor)
        elif etiqueta == "gastos_comunes" and _en(valor, BANDA_GASTOS_COMUNES):
            out.setdefault("gastos_comunes_clp", valor)
    if "gastos_comunes_clp" not in out:
        if (gc := _gc_en_uf(texto or "", valor_uf)) is not None:
            out["gastos_comunes_clp"] = gc
    return out


def parse_montos(texto: str, valor_uf: float | None = None) -> dict:
    """Separa canon, gastos comunes y garantía de un aviso de arriendo.

    Devuelve un dict con las llaves que encontró; las que no, no aparecen.

    La regla de decisión, en orden:

      1. Lo que está ROTULADO manda, siempre que caiga en su banda de
         plausibilidad. "Gastos comunes: $220.000" es gastos comunes y no hay
         nada que discutir.
      2. Entre los montos SIN rotular, el canon es el MAYOR que caiga en la
         banda de arriendo. Es la regla que acierta en los avisos que escriben
         los dos números sueltos —"$1.550.000 / $220.000"—, porque los gastos
         comunes de un departamento son siempre una fracción del canon.
      3. Si ya hay un canon rotulado, un monto sin rotular que sea bastante
         menor se toma como gastos comunes solo si no hay otro candidato.
         Acá se prefiere no anotar nada: un gasto común inventado se suma al
         costo mensual y ensucia la comparación entre departamentos.
    """
    t = texto or ""
    out: dict = {}

    montos = _montos_etiquetados(t)

    rotulados: dict[str, float] = {}
    sin_rotular: list[float] = []
    for valor, etiqueta, _ in montos:
        if etiqueta == "arriendo" and _en(valor, BANDA_ARRIENDO):
            rotulados.setdefault("arriendo", valor)
        elif etiqueta == "gastos_comunes" and _en(valor, BANDA_GASTOS_COMUNES):
            rotulados.setdefault("gastos_comunes", valor)
        elif etiqueta in ("venta", "") and valor >= PISO_PRECIO_VENTA:
            rotulados.setdefault("venta", valor)
        elif etiqueta in ("", "arriendo"):
            # Sin rótulo utilizable. Entra al montón todo lo que pueda ser un
            # canon O un gasto común, no solo lo que quepa en la banda del
            # canon: los gastos comunes están por debajo de ese piso y
            # exigirles la banda del arriendo los tiraba en silencio.
            #
            # Un "arriendo" fuera de banda también cae acá: la etiqueta decía
            # arriendo pero el monto era de venta, así que estaba describiendo
            # la página y no el número.
            if _en(valor, BANDA_ARRIENDO) or _en(valor, BANDA_GASTOS_COMUNES):
                sin_rotular.append(valor)

    if "arriendo" in rotulados:
        out["arriendo_clp"] = rotulados["arriendo"]
    else:
        # Regla 2: entre los sin rotular, el canon es el mayor que llegue a la
        # banda de un arriendo. Un monto que solo alcanza la banda de gastos
        # comunes NO puede ser el canon: dejarlo pasar convertiría un aviso
        # que solo publica "G.C. $190.000" en un arriendo de $190.000, que
        # además pasaría el filtro de presupuesto y alertaría.
        candidatos = [v for v in sin_rotular if _en(v, BANDA_ARRIENDO)]
        if candidatos:
            out["arriendo_clp"] = max(candidatos)

    if "gastos_comunes" in rotulados:
        out["gastos_comunes_clp"] = rotulados["gastos_comunes"]
    elif "arriendo_clp" in out:
        # Regla 3. Un monto sin rotular claramente menor que el canon y
        # dentro de la banda de gastos comunes. Se exige que sea el ÚNICO
        # candidato: con dos, cualquiera de los dos podría ser la garantía o
        # la comisión, y un gasto común inventado ensucia el costo mensual,
        # que es el número con el que se comparan los departamentos.
        canon = out["arriendo_clp"]
        candidatos = [v for v in sin_rotular
                      if v != canon and v < canon * 0.6
                      and _en(v, BANDA_GASTOS_COMUNES)]
        if len(candidatos) == 1:
            out["gastos_comunes_clp"] = candidatos[0]

    # El GC en UF entra recién acá: si hubiera uno en pesos, ese manda.
    if "gastos_comunes_clp" not in out:
        if (gc := _gc_en_uf(t, valor_uf)) is not None:
            out["gastos_comunes_clp"] = gc

    if (venta := rotulados.get("venta")):
        out["precio_venta_clp"] = venta

    # El canon en UF. Va después del de pesos porque cuando vienen los dos, el
    # de pesos es el que el portal calculó y el que el arrendatario paga.
    if "arriendo_clp" not in out:
        uf = parse_arriendo_uf(t)
        if uf is not None:
            out["arriendo_uf"] = uf
            out["arriendo_clp"] = round(uf * (valor_uf or VALOR_UF_DEFECTO))

    if (garantia := parse_garantia(t)) is not None:
        out["garantia_meses"] = garantia

    return out


def _en(valor: float, banda: tuple[float, float]) -> bool:
    return banda[0] <= valor <= banda[1]


# El canon en UF: "UF 42 mensuales", "42 UF". La banda es estrecha porque un
# arriendo en UF de tres cifras ya es un precio de venta mal leído.
BANDA_ARRIENDO_UF = (5, 500)


def parse_arriendo_uf(texto: str) -> float | None:
    """Canon mensual expresado en UF. None si el aviso no lo publica así."""
    for patron in _MONTOS_UF:
        for m in patron.finditer(texto or ""):
            v = _monto(m)
            if v is not None and _en(v, BANDA_ARRIENDO_UF):
                # El valor de la UF del día, que varios portales muestran en
                # el encabezado, cae justo en la banda de un precio de venta y
                # no en esta. Aun así se descarta explícitamente lo que venga
                # rotulado como valor del día.
                antes = norm(texto[max(0, m.start() - 30):m.start()])
                if "valor uf" in antes or "uf hoy" in antes:
                    continue
                return v
    return None


# "Garantía: 1 mes", "2 meses de garantía", "garantía equivalente a un mes".
_GARANTIA = [
    re.compile(r"garant[ií]a[^.]{0,30}?(\d{1,2})\s*mes", re.I),
    re.compile(r"(\d{1,2})\s*mes(?:es)?\s*(?:de\s*)?garant[ií]a", re.I),
]
_GARANTIA_EN_LETRAS = re.compile(
    r"garant[ií]a[^.]{0,30}?\b(un|dos|tres)\s*mes", re.I)
_UNO_A_TRES = {"un": 1, "dos": 2, "tres": 3}


def parse_garantia(texto: str) -> float | None:
    """Meses de garantía. None si el aviso no lo dice."""
    for patron in _GARANTIA:
        m = patron.search(texto or "")
        if m and 0 < int(m.group(1)) <= 12:
            return float(m.group(1))
    m = _GARANTIA_EN_LETRAS.search(strip_accents(texto or ""))
    if m:
        return float(_UNO_A_TRES[m.group(1).lower()])
    return None


# ---------------------------------------------------------------------------
# Superficies
# ---------------------------------------------------------------------------

_M2 = re.compile(
    rf"{_NUM}\s*(?:m2|m²|mt2|mts2|mts\.?|metros?\s*cuadrados?)\b",
    re.I,
)

# La misma medida escrita al revés, como rótulo de columna: "mt2 118".
_M2_INVERTIDO = re.compile(rf"(?:m2|m²|mt2|mts2)\s*{_NUM}", re.I)

_QUAL_UTIL = ("util", "utiles", "interior", "habitable", "construid")
_QUAL_TOTAL = ("total", "totales")
_QUAL_TERRAZA = ("terraza", "terrazas", "balcon", "balcones", "logia")
_QUAL_TERRENO = ("terreno", "sitio", "predio", "lote", "jardin")

# Cada medida vive en su propia cláusula: "superficie útil 118 m², total
# 134 m2". Separar por estos delimitadores evita que el calificador de una
# cláusula contamine a la vecina.
#
# La coma y el punto NO cortan cuando van entre dígitos, porque ahí no separan
# nada: son el decimal y el separador de miles del formato chileno. Cortando
# ahí, "134,5 m²" se leería como 5 m².
_DELIMITADORES = re.compile(r"(?<!\d)[,.](?!\d)|[;\n|/]|\s-\s")

_QUALS = (("m2_terreno", _QUAL_TERRENO), ("m2_terraza", _QUAL_TERRAZA),
          ("m2_totales", _QUAL_TOTAL), ("m2_utiles", _QUAL_UTIL))

# Fuera de esta banda no es la superficie de un departamento. El techo es
# generoso para no perder un piso completo mal rotulado; el piso descarta la
# superficie de la bodega y del estacionamiento.
BANDA_M2 = (15, 1200)


def _calificador(texto: str) -> str:
    """Qué clase de superficie nombra este trozo, si nombra alguna."""
    n = norm(texto)
    for campo, palabras in _QUALS:
        if any(re.search(rf"\b{q}", n) for q in palabras):
            return campo
    return ""


def _calificador_pegado(cola: str) -> str:
    """El calificador solo si viene inmediatamente después del número.

    "134 m² totales" lo tiene pegado y es suyo. "118 m² más terraza de" lo
    tiene detrás de una preposición, y ahí ya está hablando del siguiente.
    """
    primera = norm(cola).split()[:1]
    return _calificador(primera[0]) if primera else ""


def _sin_calificador_inicial(antes: str) -> str:
    """Descarta el calificador que abre el trozo: era del número anterior."""
    palabras = norm(antes).split()
    if palabras and _calificador(palabras[0]):
        palabras = palabras[1:]
    return " ".join(palabras)


def parse_superficies(texto: str) -> dict[str, float]:
    """Extrae m² clasificados en útil / total / terraza / terreno.

    El calificador se busca dentro de la misma cláusula que el número, no en
    una ventana de caracteres: en estos avisos las medidas van enumeradas y
    las ventanas se pisan entre sí.

    Cuando una cláusula trae DOS medidas hay que decidir a cuál le toca cada
    calificador, y el orden solo no alcanza:

        "118 m² útiles más 16 m² de terraza"        útil es del 1°
        "Departamento de 134 m² en terreno de 200"  terreno es del 2°

    Lo que las separa es la adyacencia: un calificador pegado al número es de
    ese número; uno que llega después de un "en", un "de" o un "más" anuncia
    el que viene.

    Al final se completa lo que la geometría permite deducir. Es específico de
    arriendos y vale la pena: el filtro duro del perfil es sobre m² TOTALES y
    los portales publican la útil mucho más seguido, así que sin esta pasada
    el criterio central del pedido se queda sin dato en la mitad de los avisos.
    """
    out: dict[str, float] = {}
    sin_calificar: list[float] = []

    for clausula in _DELIMITADORES.split(texto or ""):
        medidas = list(_M2.finditer(clausula))
        for i, m in enumerate(medidas):
            v = parse_numero(m.group(1))
            if v is None or not _en(v, BANDA_M2):
                continue

            if len(medidas) == 1:
                # Una sola medida: el calificador puede ir a cualquier lado
                # ("134 m² totales", "superficie total 134 m²") y no hay con
                # qué confundirlo. La cláusula entera es la lectura correcta.
                cual = _calificador(clausula)
            else:
                es_ultima = i + 1 >= len(medidas)
                hasta = len(clausula) if es_ultima else medidas[i + 1].start()
                antes = clausula[medidas[i - 1].end():m.start()] if i else clausula[:m.start()]
                if i:
                    antes = _sin_calificador_inicial(antes)
                cola = clausula[m.end():hasta]

                cual = _calificador_pegado(cola)
                if not cual and es_ultima:
                    # La ÚLTIMA medida de la cláusula puede quedarse con un
                    # calificador que llega detrás de una preposición, porque
                    # no queda ningún número después que pueda reclamarlo.
                    #
                    # "118 m² útiles más 16 m² de terraza": ese "de terraza"
                    # es del 16 y de nadie más. Sin esta regla la terraza
                    # quedaba sin clasificar, y con ella se deduce la
                    # superficie TOTAL —118 + 16— que es justo el dato sobre
                    # el que filtra el perfil.
                    #
                    # Para las medidas del medio la regla sigue siendo la
                    # estricta: ahí un calificador tras preposición anuncia al
                    # que viene ("99 m² en terreno de 180 m²").
                    cual = _calificador(cola)
                cual = cual or _calificador(antes)

            if cual:
                out.setdefault(cual, v)
            else:
                sin_calificar.append(v)

    if not out and not sin_calificar:
        # Última pasada, con la unidad ADELANTE: las grillas de atributos
        # rotulan la columna y ponen el número después ("Superficie mt2 118").
        # Va al final y solo si no se encontró nada, porque leída al revés
        # esta forma es ambigua.
        for m in _M2_INVERTIDO.finditer(texto or ""):
            v = parse_numero(m.group(1))
            if v is not None and _en(v, BANDA_M2):
                sin_calificar.append(v)

    _completar_superficies(out, sin_calificar)
    return out


def _completar_superficies(out: dict[str, float], sin_calificar: list[float]) -> None:
    """Deduce lo que falta, solo cuando la deducción es segura.

    Tres reglas, y ninguna inventa un número que no esté en el texto:

    1. Útil + terraza = total. Es aritmética, no estimación, y es la forma en
       que los portales chilenos definen la superficie total.
    2. Con dos medidas sin calificar, la mayor es la total y la menor la útil.
       Los avisos las escriben en ese orden y nunca al revés.
    3. Con una sola medida sin calificar se anota como ÚTIL y no como total.
       Es la lectura conservadora: si resulta ser la total, el filtro la trata
       igual (`m2_referencia`); si se anotara como total y en realidad era la
       útil, un departamento de 118 útiles + 20 de terraza entraría al
       tablero declarando 118 totales, que es un dato falso.
    """
    if len(sin_calificar) >= 2 and "m2_totales" not in out and "m2_utiles" not in out:
        out["m2_totales"] = max(sin_calificar)
        out["m2_utiles"] = min(sin_calificar)
    elif sin_calificar and "m2_utiles" not in out and "m2_totales" not in out:
        out["m2_utiles"] = max(sin_calificar)

    if "m2_totales" not in out and "m2_utiles" in out and "m2_terraza" in out:
        total = out["m2_utiles"] + out["m2_terraza"]
        if _en(total, BANDA_M2):
            out["m2_totales"] = round(total, 2)

    # Una total menor que la útil es un error de lectura de alguno de los dos.
    # Se descarta la total, que es la que se deduce; la útil casi siempre
    # viene rotulada.
    if (out.get("m2_totales") is not None and out.get("m2_utiles") is not None
            and out["m2_totales"] < out["m2_utiles"]):
        del out["m2_totales"]


# ---------------------------------------------------------------------------
# Antigüedad
# ---------------------------------------------------------------------------

# El año acepta el punto de miles porque los portales lo escriben así:
# "Año de construcción: 1.978" es una ficha REAL de iCasas — y justo la clase
# de dato que decide el requisito duro de <30 años.
_ANO_CONSTRUCCION = re.compile(
    r"(?:ano|año)\s*(?:de\s*)?(?:construccion|construcción|edificacion|edificación)"
    r"\s*[:\-]?\s*(\d\.?\d{3})",
    re.I,
)
_CONSTRUIDO_EN = re.compile(
    r"(?:construid[oa]|edificad[oa]|entregad[oa]|recepcionad[oa])"
    r"\s*(?:en|el|el\s*ano|el\s*año)?\s*(\d\.?\d{3})",
    re.I,
)
_ANTIGUEDAD = re.compile(
    r"(?:antiguedad|antigüedad)\s*[:\-]?\s*(?:de\s*)?(\d{1,3})\s*(?:anos|años)?",
    re.I,
)
_ANTIGUEDAD_INV = re.compile(r"(\d{1,3})\s*(?:anos|años)\s*de\s*antig", re.I)

# "Edificio nuevo", "a estrenar", "recién entregado". No es un año, pero es
# información de verdad sobre la antigüedad y en arriendo aparece mucho más
# seguido que la fecha exacta.
#
# Se traduce a un TECHO de antigüedad, no a un valor: "a estrenar" garantiza
# que tiene 2 años o menos, no que tenga exactamente 0.
_A_ESTRENAR = re.compile(
    r"\ba\s*estrenar\b|\bpor\s*estrenar\b|\bnuevo\s*a\s*estrenar\b"
    r"|reci[eé]n\s*(?:entregad|construid|terminad)", re.I)
_EDIFICIO_NUEVO = re.compile(
    r"\bedificio\s+nuevo\b|\bproyecto\s+nuevo\b|\bprimer\s+arriendo\b", re.I)

TECHO_A_ESTRENAR = 2
TECHO_EDIFICIO_NUEVO = 5


def parse_antiguedad(texto: str, ref: date | None = None) -> tuple[int | None, int | None]:
    """Devuelve (ano_construccion, antiguedad_anos).

    Cualquiera de los dos puede venir en el texto; se deriva el otro.
    """
    ref = ref or hoy()
    t = texto or ""

    ano = None
    for pat in (_ANO_CONSTRUCCION, _CONSTRUIDO_EN):
        m = pat.search(t)
        if m:
            candidato = int(m.group(1).replace(".", ""))
            if 1900 <= candidato <= ref.year + 2:
                ano = candidato
                break

    antig = None
    for pat in (_ANTIGUEDAD, _ANTIGUEDAD_INV):
        m = pat.search(t)
        if m:
            candidato = int(m.group(1))
            if 0 <= candidato <= 150:
                antig = candidato
                break

    if ano is not None and antig is None:
        antig = ref.year - ano
    elif antig is not None and ano is None:
        ano = ref.year - antig

    return ano, antig


def techo_antiguedad(texto: str) -> int | None:
    """El máximo de años que puede tener, según lo que dice el aviso.

    Es un TECHO y se reporta como tal. Usarlo como si fuera la antigüedad
    metería un dato falso donde no hay ninguno, y un dato falso es peor que
    uno ausente: cuando falta, el radar avisa igual y alguien mira; cuando
    está y es mentira, nadie lo revisa.
    """
    t = texto or ""
    if _A_ESTRENAR.search(t):
        return TECHO_A_ESTRENAR
    if _EDIFICIO_NUEVO.search(t):
        return TECHO_EDIFICIO_NUEVO
    return None


# ---------------------------------------------------------------------------
# Programa: dormitorios, baños, estacionamientos, bodega
# ---------------------------------------------------------------------------

# "pieza" está a propósito: el pedido dice "3 piezas mínimo" y en un aviso
# chileno pieza y dormitorio son la misma cosa.
_DORM = re.compile(
    r"(\d{1,2})\s*(?:dormitorios?|dorm\b|piezas?|habitaciones?|hab\b)", re.I)
_BANO = re.compile(r"(\d{1,2})\s*(?:banos?|baños?|bao?s\b)", re.I)
# Formato compacto de las grillas de portal: "3D/2B", "3D 2B", "3D2B".
_COMPACTO = re.compile(r"\b(\d{1,2})\s*d\s*[/\-y ]?\s*(\d{1,2})\s*b\b", re.I)

# Dormitorios escritos con letras, que es como los escribe una descripción
# ("tres dormitorios amplios") frente a la grilla ("3D").
_NUMEROS_EN_LETRAS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
}
_DORM_EN_LETRAS = re.compile(
    rf"\b({'|'.join(_NUMEROS_EN_LETRAS)})\s+(?:dormitorios?|piezas?|habitaciones?)\b",
    re.I)
_BANO_EN_LETRAS = re.compile(
    rf"\b({'|'.join(_NUMEROS_EN_LETRAS)})\s+ba[nñ]os?\b", re.I)

# La pieza de servicio se cuenta aparte y hay que decidir qué hacer con ella.
#
# Decisión: NO suma al total de dormitorios. El pedido es "3 piezas mínimo"
# y quien lo escribió está pensando en dormitorios de la familia, no en la
# pieza de servicio de 6 m² detrás de la cocina. Contarla haría entrar
# departamentos de 2 dormitorios + servicio como si fueran de 3.
#
# Sí se guarda como dato aparte, porque suma metros y suma comodidad.
_PIEZA_SERVICIO = re.compile(
    r"(?:pieza|dormitorio|cuarto|habitaci[oó]n)\s+(?:y\s+ba[nñ]o\s+)?de\s+servicio"
    r"|servicio\s+completo|dependencias?\s+de\s+servicio", re.I)

_ESTACIONAMIENTO = re.compile(
    r"(\d{1,2})\s*(?:estacionamientos?|estac\.?\b|parkings?)", re.I)
_ESTACIONAMIENTO_INV = re.compile(
    r"estacionamientos?\s*:?\s*(\d{1,2})\b", re.I)
_ESTACIONAMIENTO_EN_LETRAS = re.compile(
    rf"\b({'|'.join(_NUMEROS_EN_LETRAS)})\s+estacionamientos?\b", re.I)

_BODEGA_SI = re.compile(r"\b(?:con\s+)?bodegas?\b", re.I)
_BODEGA_NO = re.compile(r"\bsin\s+bodega\b", re.I)


def _en_letras(patron: re.Pattern, texto: str) -> int | None:
    m = patron.search(strip_accents(texto or ""))
    return _NUMEROS_EN_LETRAS.get(m.group(1).lower()) if m else None


def parse_programa(texto: str) -> dict:
    """Dormitorios, baños, estacionamientos y bodega.

    Devuelve solo lo que encontró. Un dict y no una tupla porque son cuatro
    datos independientes y las tuplas de cuatro se leen mal en el punto de uso.
    """
    t = texto or ""
    out: dict = {}

    m = _COMPACTO.search(t)
    if m:
        dorm, bano = int(m.group(1)), int(m.group(2))
        if 0 < dorm <= 20:
            out["dormitorios"] = dorm
        if 0 < bano <= 20:
            out["banos"] = bano

    if "dormitorios" not in out:
        md = _DORM.search(t)
        if md and 0 < int(md.group(1)) <= 20:
            out["dormitorios"] = int(md.group(1))
        elif (v := _en_letras(_DORM_EN_LETRAS, t)):
            out["dormitorios"] = v

    if "banos" not in out:
        mb = _BANO.search(t)
        if mb and 0 < int(mb.group(1)) <= 20:
            out["banos"] = int(mb.group(1))
        elif (v := _en_letras(_BANO_EN_LETRAS, t)):
            out["banos"] = v

    for patron in (_ESTACIONAMIENTO, _ESTACIONAMIENTO_INV):
        me = patron.search(t)
        if me and 0 < int(me.group(1)) <= 10:
            out["estacionamientos"] = int(me.group(1))
            break
    else:
        if (v := _en_letras(_ESTACIONAMIENTO_EN_LETRAS, t)):
            out["estacionamientos"] = v
        elif re.search(r"\bestacionamiento\b(?!s)", t, re.I):
            # Singular sin número: es uno. Aparece así en la mitad de los
            # avisos ("con estacionamiento y bodega").
            out["estacionamientos"] = 1

    if _BODEGA_NO.search(t):
        out["bodega"] = False
    elif _BODEGA_SI.search(t):
        out["bodega"] = True

    if _PIEZA_SERVICIO.search(t):
        out["pieza_servicio"] = True

    return out


# ---------------------------------------------------------------------------
# Piso y orientación
# ---------------------------------------------------------------------------

_PISO = [
    re.compile(r"\bpiso\s*(?:n[°º]?\s*)?(\d{1,2})\b", re.I),      # "piso 12"
    re.compile(r"\b(\d{1,2})\s*[°ºa]?\s*piso\b", re.I),           # "12° piso"
]

_ORDINALES = {
    "primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2,
    "tercer": 3, "tercero": 3, "tercera": 3, "cuarto": 4, "cuarta": 4,
    "quinto": 5, "quinta": 5, "sexto": 6, "sexta": 6, "septimo": 7,
    "septima": 7, "octavo": 8, "octava": 8, "noveno": 9, "novena": 9,
    "decimo": 10, "decima": 10, "undecimo": 11, "duodecimo": 12,
    "decimoprimero": 11, "decimosegundo": 12, "decimotercero": 13,
    "decimocuarto": 14, "decimoquinto": 15, "decimosexto": 16,
    "decimoseptimo": 17, "decimoctavo": 18, "decimonoveno": 19,
    "vigesimo": 20,
}

_PISO_EN_LETRAS = re.compile(
    rf"\b(?:(decimo|vigesimo)\s+)?({'|'.join(_ORDINALES)})\s+piso\b", re.I)

_ULTIMO_PISO = re.compile(
    r"\b(penthouse|pent house|ultimo piso|último piso|piso superior)\b", re.I
)


def _piso_en_letras(texto: str) -> int | None:
    m = _PISO_EN_LETRAS.search(strip_accents(texto or "").lower())
    if not m:
        return None
    decena, unidad = m.group(1), m.group(2)
    valor = _ORDINALES.get(unidad)
    if valor is None:
        return None
    # "décimo quinto" = 15; "vigésimo primero" = 21. Solo suma cuando la
    # segunda palabra es una unidad: "décimo décimo" no existe.
    if decena and valor <= 9:
        valor += _ORDINALES["decimo"] if decena.startswith("decim") else 20
    return valor


def parse_piso(texto: str) -> int | None:
    """Piso del departamento. None si no se informa."""
    if (v := _piso_en_letras(texto)) and 0 < v <= 60:
        return v

    for pat in _PISO:
        m = pat.search(texto or "")
        if m:
            v = int(m.group(1))
            # Sobre 60 pisos no hay edificios residenciales en Santiago: es
            # el número de departamento leído como piso.
            if 0 < v <= 60:
                return v
    return None


def es_ultimo_piso(texto: str) -> bool:
    return bool(_ULTIMO_PISO.search(texto or ""))


# El número del departamento. Se guarda como parte de la IDENTIDAD, que es
# para lo que más sirve acá: en un mismo edificio se arriendan varias
# unidades a la vez, y sin el número colapsarían en un solo aviso.
_NUMERO_DEPTO = re.compile(
    r"\b(?:departamento|depto\.?|dpto\.?|dep\.?|of\.?)\s*(?:n[°º]\s*)?"
    r"(\d{2,4}[a-z]?)\b", re.I)


def parse_unidad(texto: str) -> str:
    """El número del departamento dentro del edificio, si el aviso lo nombra."""
    m = _NUMERO_DEPTO.search(texto or "")
    return m.group(1).upper() if m else ""


def piso_desde_numero(texto: str) -> int | None:
    """El piso que insinúa el número del departamento.

    En Chile la convención es número = piso + unidad: el 202 es el segundo
    departamento del piso 2, el 1502 el segundo del piso 15. No es una regla
    —hay edificios que numeran corrido— así que esto INFIERE, y quien lo use
    tiene que decir de dónde salió.

    Solo 3 y 4 dígitos. Un "depto 12" tanto puede ser el 12 del piso 1 como
    la unidad 12 de un edificio numerado corrido.
    """
    unidad = parse_unidad(texto)
    solo_digitos = re.sub(r"[^\d]", "", unidad)
    if len(solo_digitos) < 3:
        return None
    piso = int(solo_digitos[:-2])
    return piso if 0 < piso <= 60 else None


# El orden importa: "nororiente" debe probarse antes que "norte" y "oriente",
# si no una de las dos parciales se lo lleva primero.
_ORIENTACIONES = [
    ("nororiente", ("nororiente", "nor oriente", "norte oriente", "nor-oriente")),
    ("norponiente", ("norponiente", "nor poniente", "norte poniente")),
    ("suroriente", ("suroriente", "sur oriente")),
    ("surponiente", ("surponiente", "sur poniente")),
    ("oriente", ("oriente",)),
    ("poniente", ("poniente",)),
    ("norte", ("norte",)),
    ("sur", ("sur",)),
]


def parse_orientacion(texto: str) -> str:
    """Orientación del departamento, normalizada. '' si no se informa."""
    t = norm(texto)
    for canonica, variantes in _ORIENTACIONES:
        if any(re.search(rf"\b{v}\b", t) for v in variantes):
            return canonica
    return ""


# ---------------------------------------------------------------------------
# Amoblado y mascotas
# ---------------------------------------------------------------------------

# El orden importa dos veces: "sin amoblar" contiene "amoblar", y
# "semi amoblado" no es ninguna de las dos cosas.
_SIN_AMOBLAR = re.compile(r"\bsin\s+amobla(?:r|do)\b|\bno\s+amoblado\b", re.I)
_SEMI_AMOBLADO = re.compile(r"\bsemi[\s-]?amoblado\b", re.I)
_AMOBLADO = re.compile(r"\bamoblad[oa]\b|\bamueblad[oa]\b|\bcon\s+muebles\b", re.I)


def parse_amoblado(texto: str) -> str:
    """"amoblado" | "semi amoblado" | "sin amoblar" | "".

    Importa más de lo que parece: en Vitacura un mismo departamento amoblado
    se arrienda entre 25% y 40% más caro, así que comparar un amoblado contra
    uno pelado por el canon es comparar dos productos distintos.
    """
    t = texto or ""
    if _SIN_AMOBLAR.search(t):
        return "sin amoblar"
    if _SEMI_AMOBLADO.search(t):
        return "semi amoblado"
    if _AMOBLADO.search(t):
        return "amoblado"
    return ""


_NO_MASCOTAS = re.compile(
    r"\bno\s+(?:se\s+)?(?:aceptan?|admiten?|permiten?)\s+mascotas?\b"
    r"|\bsin\s+mascotas?\b|\bmascotas?\s*:?\s*no\b", re.I)
_SI_MASCOTAS = re.compile(
    r"\b(?:se\s+)?(?:aceptan?|admiten?|permiten?)\s+mascotas?\b"
    r"|\bpet\s*friendly\b|\bmascotas?\s*:?\s*s[ií]\b", re.I)


def parse_mascotas(texto: str) -> str:
    """"acepta" | "no acepta" | "". El "no" se busca primero: contiene al "sí"."""
    t = texto or ""
    if _NO_MASCOTAS.search(t):
        return "no acepta"
    if _SI_MASCOTAS.search(t):
        return "acepta"
    return ""


# ---------------------------------------------------------------------------
# Operación: arriendo, venta, temporada o pieza
#
# Es el filtro más rentable de todo el módulo. Los portales mezclan las cuatro
# cosas en la misma grilla, y sin separarlas el radar alerta departamentos en
# venta de 12.000 UF porque "no supo" que no eran arriendos.
# ---------------------------------------------------------------------------

_ES_VENTA = re.compile(
    r"\b(?:en\s+)?venta\b|\bse\s+vende\b|\bvendo\b|\bprecio\s+de\s+venta\b"
    r"|\bpropiedad\s+en\s+venta\b", re.I)
_ES_ARRIENDO = re.compile(
    r"\barriendo\b|\bse\s+arrienda\b|\barriendan?\b|\ben\s+arriendo\b"
    r"|\barrendar\b|\bcanon\b", re.I)
# Arriendo por temporada.
#
# OJO con las palabras sueltas. La primera versión de esta regla incluía
# `\bdiario\b`, y contra 328 avisos reales de Vitacura acertó CERO veces: las
# 57 apariciones de "diario" eran una de estas dos cosas.
#
#   "cocina con comedor de diario"    → el comedor chico de la cocina. En
#                                       Chile es una característica estándar de
#                                       un departamento bueno, así que la regla
#                                       descartaba justo los mejores: los
#                                       penthouses de Lo Gallo y Paul Claudel.
#   "Diario: El Mercurio"             → el pie de cada aviso de economicos.cl,
#                                       que es el clasificado de El Mercurio.
#                                       Se llevaba la fuente entera.
#
# Y el modo de fallar era el peor de todos: el aviso se descartaba en silencio,
# con un motivo que sonaba razonable —"es arriendo por temporada"— así que
# nadie iba a ir a revisarlo.
#
# La regla ahora exige que "diario" venga pegado a algo que hable de plata o de
# arriendo. Un aviso por días lo dice de esa forma; una cocina, no.
_ES_TEMPORADA = re.compile(
    r"\btemporada\b|\bpor\s+d[ií]as?\b|\bpor\s+semanas?\b"
    r"|\barriendos?\s+diari[oa]s?\b"
    r"|\b(?:valor|precio|tarifa|canon|arriendo)\s+(?:por\s+)?d[ií]a(?:ri[oa])?s?\b"
    r"|\$\s*[\d.,]+\s*(?:diarios?|por\s+d[ií]a)\b"
    r"|\bairbnb\b|\bamoblado\s+ejecutivo\b|\bcorta\s+estad[ií]a\b"
    r"|\bvacacional\b|\bpor\s+noche\b", re.I)

# Arriendo de una pieza, no del departamento.
#
# Mismo error y misma corrección que arriba: `\bcompartid[oa]\b` a secas se
# comió departamentos de cuatro dormitorios en Vitacura porque el aviso decía
# "dos dormitorios de servicio con baño compartido". Un baño compartido entre
# dos piezas de servicio es una descripción normal de un departamento grande,
# no una oferta de pieza. Ahora lo compartido tiene que ser la vivienda.
_ES_PIEZA = re.compile(
    r"\barriendo\s+(?:de\s+)?(?:pieza|habitaci[oó]n)\b|\bpieza\s+en\s+arriendo\b"
    r"|\b(?:depto|dpto|departamento|casa|piso|vivienda)\s+compartid[oa]\b"
    r"|\bcompartir\s+(?:depto|dpto|departamento|casa|piso)\b"
    r"|\bcowork(?:ing)?\b|\bcoliving\b", re.I)


def parse_operacion(texto: str, kind_default: str = "arriendo") -> str:
    """Qué operación es este aviso.

    El orden es deliberado y cada paso tiene un caso detrás:

    1. **Pieza** primero. "Arriendo de pieza" contiene "arriendo", así que
       mirar arriendo antes dejaría entrar todas las piezas compartidas.
    2. **Temporada** después, por lo mismo: "arriendo por días" es arriendo
       para la gramática y no lo es para el pedido.
    3. Recién ahí, arriendo contra venta. Si el aviso dice las dos cosas
       —muchas fichas ofrecen "venta y arriendo" del mismo departamento— gana
       arriendo: es lo que se está buscando y el aviso sí ofrece eso.
    4. Sin ninguna señal, se cree lo que diga la fuente. Un listado ya
       filtrado a arriendo publica avisos que no repiten la palabra en cada
       tarjeta, y descartarlos por callar sería descartar la fuente entera.
    """
    t = texto or ""
    if _ES_PIEZA.search(t):
        return "pieza"
    if _ES_TEMPORADA.search(t):
        return "temporada"
    if _ES_ARRIENDO.search(t):
        return "arriendo"
    if _ES_VENTA.search(t):
        return "venta"
    return kind_default


# ---------------------------------------------------------------------------
# Tipo de propiedad
# ---------------------------------------------------------------------------

_TIPO_DEPTO = ("departamento", "depto", "dpto", "apartamento", "penthouse")
_TIPO_CASA = ("casa", "townhouse", "town house", "pareada", "chalet")

# Inmuebles que no son vivienda. Entran al listado porque los portales los
# publican en la misma grilla y salen de acá porque ninguna estrategia del
# perfil los contempla.
_TIPO_NO_VIVIENDA = (
    ("oficina", r"oficinas?"),
    ("local comercial", r"locales?\s+comerciales?|local\s+comercial"),
    ("estacionamiento", r"estacionamientos?"),
    ("bodega", r"bodegas?"),
    ("terreno", r"parcelas?|predios?|loteos?|lotes?|sitios?\s+eriazos?"),
)

# La misma palabra, cuando cuelga de un "con", un "y" o un "más", describe un
# extra del departamento y no lo que se arrienda: "departamento con bodega".
_ACCESORIO = re.compile(
    r"\b(?:con|y|e|mas|incluye[n]?|ademas\s+de|junto\s+a|\+)\s+$")

# Señales de que el aviso describe una VIVIENDA, diga o no la palabra.
#
# Es el candado que le falta a la guarda de accesorios. El clasificado
# chileno es telegráfico: "1.500.000 Agustín del Castillo. 4 dormitorios, 3
# baños, servicio, estacionamiento, bodega" — nunca dice "departamento", y la
# coma no es un conector, así que la guarda de "con/y/más" no aplicaba y el
# aviso quedaba clasificado como ESTACIONAMIENTO y descartado.
#
# Medido en la corrida real: siete departamentos de Vitacura de economicos.cl
# —Kennedy/Tabancura, Bicentenario, Lo Castillo, 240 m² piso alto— botados
# como "es estacionamiento, no departamento". El mismo modo de fallar del
# resto de esta auditoría: en silencio y con un motivo que suena razonable.
#
# La regla es de sentido común y por eso es robusta: un aviso que declara
# dormitorios está arrendando algo donde se duerme. Un estacionamiento, una
# bodega o una oficina no tienen dormitorios.
_ES_VIVIENDA = re.compile(
    r"\d+\s*dormitorios?\b|\bdormitorio\s+en\s+suite\b"
    r"|\b\d+\s*d\s*/?\s*\d+\s*b\b|\bwalk[- ]?in\s+closet\b",
    re.I,
)


def parse_tipo(texto: str) -> str:
    t = norm(texto)
    # Se busca departamento primero: "casa" aparece con frecuencia dentro de
    # frases como "casa club" o "casa matriz" en fichas de departamentos.
    if any(re.search(rf"\b{re.escape(k)}", t) for k in _TIPO_DEPTO):
        return "departamento"
    if any(re.search(rf"\b{re.escape(k)}", t) for k in _TIPO_CASA):
        return "casa"

    # Con dormitorios declarados es una vivienda: no se sabe si departamento o
    # casa, pero SÍ se sabe que no es un estacionamiento ni una oficina. Se
    # devuelve "" —tipo desconocido— y el filtro de tipo no descarta por dato
    # ausente, que es exactamente el comportamiento correcto acá.
    if _ES_VIVIENDA.search(texto or ""):
        return ""

    for canonico, patron in _TIPO_NO_VIVIENDA:
        for m in re.finditer(rf"\b(?:{patron})\b", t):
            if not _ACCESORIO.search(t[max(0, m.start() - 24):m.start()]):
                return canonico
    return ""


# ---------------------------------------------------------------------------
# Comuna
# ---------------------------------------------------------------------------

# Las 52 comunas de la Región Metropolitana, completas.
#
# Faltar comunas no es neutro: cuando la comuna verdadera no está en la lista,
# el detector sigue buscando y se queda con la primera que SÍ esté, aunque
# aparezca dentro del nombre de una calle. Una propiedad en Buin se guardaba
# como Maipú porque su dirección es "MAIPU 748".
#
# El orden importa para el desempate por largo, no por posición: ver
# `parse_comuna`.
COMUNAS_RM = [
    # Las del perfil y su entorno inmediato.
    "Vitacura", "Las Condes", "Lo Barnechea", "Providencia", "Ñuñoa",
    "La Reina", "Peñalolén", "Macul", "Santiago",
    # Resto de la provincia de Santiago.
    "Cerrillos", "Cerro Navia", "Conchalí", "El Bosque", "Estación Central",
    "Huechuraba", "Independencia", "La Cisterna", "La Florida", "La Granja",
    "La Pintana", "Lo Espejo", "Lo Prado", "Maipú", "Pedro Aguirre Cerda",
    "Pudahuel", "Quilicura", "Quinta Normal", "Recoleta", "Renca",
    "San Joaquín", "San Miguel", "San Ramón",
    # Provincias de Cordillera, Chacabuco, Maipo, Melipilla y Talagante.
    "Puente Alto", "Pirque", "San José de Maipo",
    "Colina", "Lampa", "Tiltil",
    "San Bernardo", "Buin", "Calera de Tango", "Paine",
    "Melipilla", "Alhué", "Curacaví", "María Pinto", "San Pedro",
    "Talagante", "El Monte", "Isla de Maipo", "Padre Hurtado", "Peñaflor",
]

# Fuera de la Región Metropolitana. No están para buscarlas —el perfil no sale
# de Vitacura— sino para poder DESCARTARLAS: varios portales son nacionales, y
# una propiedad cuya comuna no se reconoce queda con el campo vacío, que el
# radar trata como "quizás sirve" y deja pasar hasta la alerta.
COMUNAS_FUERA_RM = [
    "Viña del Mar", "Valparaíso", "Quilpué", "Villa Alemana", "Concón",
    "Quillota", "San Antonio", "Puchuncaví", "Los Andes", "San Felipe",
    "Rancagua", "Machalí", "San Fernando", "Rengo", "Santa Cruz",
    "Curicó", "Talca", "Linares", "Constitución",
    "Chillán", "Concepción", "Talcahuano", "San Pedro de la Paz", "Coronel",
    "Los Ángeles", "Temuco", "Padre Las Casas", "Villarrica", "Pucón",
    "Valdivia", "Osorno", "Puerto Montt", "Puerto Varas", "Castro",
    "La Serena", "Coquimbo", "Ovalle", "Illapel",
    "Copiapó", "Vallenar", "Antofagasta", "Calama", "Iquique", "Alto Hospicio",
    "Arica", "Punta Arenas", "Coyhaique",
]

COMUNAS_CONOCIDAS = COMUNAS_RM + COMUNAS_FUERA_RM


# "Santiago" dentro de estas frases NO es la comuna Santiago: es la región, la
# provincia o el nombre de la ciudad completa. Se borran del texto ANTES de
# buscar comunas.
#
# El error que esto corrige se midió en la primera corrida real y es de los
# caros: economicos.cl remata cada aviso con "Región: Metropolitana de
# Santiago", así que un departamento de Vitacura cuya tarjeta no repetía la
# comuna quedaba con comuna "Santiago" — y Santiago es una comuna CONOCIDA que
# no es vecina, así que el filtro de zona lo descartaba con toda seguridad y
# en silencio. Trece departamentos de Vitacura reales —Lo Castillo, Juan
# XXIII, Bicentenario, Agustín del Castillo— botados por el pie de página.
#
# El descarte silencioso con motivo razonable es el peor modo de fallar que
# tiene este radar: nadie va a ir a revisar "comuna fuera de la zona".
_NO_ES_LA_COMUNA_SANTIAGO = re.compile(
    r"regi[oó]n\s*:?\s*metropolitana(?:\s+de\s+santiago)?"
    r"|metropolitana\s+de\s+santiago"
    r"|provincia\s+de\s+santiago"
    r"|santiago\s+de\s+chile"
    r"|gran\s+santiago",
    re.I,
)


def parse_comuna(texto: str, candidatas: list[str] | None = None) -> str:
    """Detecta la comuna. Devuelve el nombre canónico, con tildes.

    Entre varias que calcen gana la más LARGA, no la primera de la lista:
    "Isla de Maipo" contiene "Maipo", y preferir la más larga elige la más
    específica, que es la que de verdad identifica el lugar.

    "Santiago" solo cuenta cuando aparece FUERA de las frases de región y
    provincia: ver _NO_ES_LA_COMUNA_SANTIAGO.
    """
    t = norm(_NO_ES_LA_COMUNA_SANTIAGO.sub(" ", texto or ""))
    mejor = ""
    for c in (candidatas or COMUNAS_RM):
        if re.search(rf"\b{re.escape(norm(c))}\b", t) and len(c) > len(mejor):
            mejor = c
    return mejor


# Los barrios de Vitacura. Sirven cuando el aviso nombra el sector y no la
# comuna, que en los portales de arriendo pasa seguido: "Santa María de
# Manquehue", "Jardín del Este", "Lo Curro" identifican Vitacura tan bien
# como la palabra Vitacura.
#
# Se usan solo si no se detectó comuna: son señal, no evidencia. "Nueva
# Costanera" es una calle de Vitacura, pero también hay avisos que la nombran
# como referencia estando en otra comuna.
BARRIOS_VITACURA = [
    "santa maria de manquehue", "jardin del este", "lo curro",
    "nueva costanera", "alonso de cordova", "el golf de manquehue",
    "vitacura oriente", "santa blanca", "escrivá de balaguer",
    "escriva de balaguer", "padre hurtado norte",
]


def comuna_por_barrio(texto: str) -> str:
    """La comuna que insinúa el nombre del barrio. "" si ninguno calza."""
    t = norm(texto)
    if any(b in t for b in (norm(x) for x in BARRIOS_VITACURA)):
        return "Vitacura"
    return ""


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_MESES_ABREV = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}

_FECHA_TEXTO = re.compile(
    r"(\d{1,2})\s*(?:de\s*)?([a-zá-ú]{3,})\.?\s*(?:de[l]?\s*)?(\d{4})", re.I
)
_FECHA_NUM = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")


def _mes(nombre: str) -> int | None:
    """El número del mes, o None si esa palabra no es un mes.

    Las abreviaturas son abreviaturas. Aceptar cualquier palabra que EMPIECE
    con esas tres letras leería la calle "MAYECURA" como "may".
    """
    n = norm(nombre).rstrip(".")
    if n in _MESES:
        return _MESES[n]
    if len(n) <= 4:
        return _MESES_ABREV.get(n) or _MESES_ABREV.get(n[:3])
    return None


def _fecha_creible(y: int, mo: int, d: int) -> date | None:
    """La fecha, si puede ser la de un aviso. None si es un número disfrazado."""
    if not (2000 <= y <= hoy().year + 5):
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_fecha(texto: str) -> date | None:
    """Primera fecha válida. Asume día-mes-año (convención chilena)."""
    t = texto or ""

    m = _FECHA_TEXTO.search(t)
    if m:
        mes = _mes(m.group(2))
        if mes and (f := _fecha_creible(int(m.group(3)), mes, int(m.group(1)))):
            return f

    m = _FECHA_NUM.search(t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        return _fecha_creible(y, mo, d)

    return None


_DISPONIBLE_YA = re.compile(
    r"disponible\s*(?:desde\s*)?(?:ya|ahora|inmediat|de\s*inmediato)"
    r"|entrega\s+inmediata|disponibilidad\s+inmediata", re.I)
_DISPONIBLE_DESDE = re.compile(r"disponible\s*(?:a\s*partir\s*del?|desde)\s*:?\s*", re.I)


def parse_disponibilidad(texto: str, ref: date | None = None) -> date | None:
    """Desde cuándo se puede entrar. None si el aviso no lo dice.

    "Disponible ya" devuelve la fecha de referencia (hoy), que es un dato de
    verdad y no un relleno: separa lo que se puede tomar ahora de lo que se
    entrega en tres meses, y esa diferencia decide si vale la pena ir a verlo.
    """
    t = texto or ""
    if _DISPONIBLE_YA.search(t):
        return ref or hoy()
    m = _DISPONIBLE_DESDE.search(t)
    if m:
        return parse_fecha(t[m.end():m.end() + 60])
    return None


# "Publicado hace 3 días", "hace 2 meses", "publicado el 12-07-2026".
_HACE = re.compile(r"hace\s+(\d{1,3})\s*(d[ií]as?|semanas?|meses?|horas?)", re.I)
_PUBLICADO_EL = re.compile(r"publicad[oa]\s*(?:el|:)?\s*", re.I)

_A_DIAS = {"hora": 0, "horas": 0, "dia": 1, "dias": 1,
           "semana": 7, "semanas": 7, "mes": 30, "meses": 30}


def parse_publicado(texto: str, ref: date | None = None) -> date | None:
    """Cuándo se publicó el aviso. Es la palanca de negociación.

    Un aviso que lleva dos meses publicado no se arrendó al precio de lista, y
    eso es exactamente lo que hace que valga la pena llamar. Los portales lo
    muestran casi siempre en relativo ("hace 2 meses"), así que se convierte a
    fecha para poder compararlo entre corridas.
    """
    from datetime import timedelta

    t = texto or ""
    ref = ref or hoy()

    m = _HACE.search(t)
    if m:
        cantidad = int(m.group(1))
        unidad = norm(m.group(2)).rstrip(".")
        dias = _A_DIAS.get(unidad)
        if dias is not None and cantidad <= 400:
            return ref - timedelta(days=cantidad * dias)

    m = _PUBLICADO_EL.search(t)
    if m:
        return parse_fecha(t[m.end():m.end() + 40])
    return None


# ---------------------------------------------------------------------------
# Corredora
# ---------------------------------------------------------------------------

_PARTICULAR = re.compile(
    r"\bpublicado\s+por\s+(?:el\s+)?(?:due[nñ]o|propietario)\b"
    r"|\btrato\s+directo\b|\bsin\s+comisi[oó]n\b|\bparticular\b", re.I)


def es_particular(texto: str) -> bool:
    """¿Lo publica el dueño? Sin comisión de corretaje son medio mes menos."""
    return bool(_PARTICULAR.search(texto or ""))
