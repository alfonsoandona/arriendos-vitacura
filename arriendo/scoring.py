"""Filtros duros y puntaje de un `Arriendo` contra el perfil.

El módulo hace dos cosas que conviene no confundir, y están separadas a
propósito porque el pedido las separa:

  DESCARTAR   Los requisitos que se dijeron como obligatorios: departamento,
              más de 100 m² totales, tres piezas, y cerca de 1,6 millones.
              Más la zona: Vitacura o el anillo de 1,2 km.

  PUNTUAR     Todo lo demás. "Ideal más nuevo, entonces vamos poniendo
              puntaje" es literalmente el criterio de orden, y por eso la
              antigüedad se lleva el rubro más pesado después de la ubicación.

Tres reglas de diseño, en orden de importancia:

1. **Un dato ausente nunca descarta.** Descartar por dato faltante es el error
   más caro que puede cometer este radar porque no se ve: la propiedad no
   aparece en ninguna parte y nadie va a extrañarla. Un dato PRESENTE que no
   cumple sí descarta.

2. **El puntaje se mide sobre lo que se pudo medir.** Cobrar como 0 un dato
   que ningún portal publica hace que todo puntúe bajo y que el puntaje deje
   de servir para ordenar, que es para lo único que existe.

3. **Vitacura primero.** No es un empate que la distancia resuelve: es una
   instrucción, y está implementada como multiplicador para que se cumpla
   aunque la geometría diga otra cosa.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import parse as P
from .geo import haversine_km
from .models import Arriendo


# ---------------------------------------------------------------------------
# Reparto de los 100 puntos
#
# Ubicación y antigüedad se llevan la mitad entre las dos, y eso es el pedido
# traducido a números: "prioriza Vitacura comuna entera" e "ideal más nuevo".
# ---------------------------------------------------------------------------
PESO_UBICACION = 26
PESO_ANTIGUEDAD = 24
PESO_PRECIO = 20
PESO_SUPERFICIE = 16
PESO_PROGRAMA = 14

# El rubro completo: los cinco criterios que deciden si una propiedad
# califica. Las preferencias van aparte y no entran en la normalización.
RUBRO_COMPLETO = (PESO_UBICACION + PESO_ANTIGUEDAD + PESO_PRECIO
                  + PESO_SUPERFICIE + PESO_PROGRAMA)

# Piso, orientación, estacionamientos, holgura, keywords. No dicen si una
# propiedad califica: dicen cuál preferir entre las que ya calificaron. Van
# fuera del porcentaje porque metiéndolas adentro dos propiedades distintas
# saturaban las dos en 100 y el desempate desaparecía.
# El tope de las preferencias es ASIMÉTRICO, y es la corrección más
# importante que tiene este módulo.
#
# El problema medido: un departamento de 8 años, 134 m² a $1.500.000 que
# publicaba piso 12, orientación nororiente, dos estacionamientos y bodega le
# ganaba a uno de 2 años, 150 m² a $1.250.000 que no publicaba nada de eso.
# El segundo es mejor por donde se lo mire; simplemente su aviso decía menos.
#
# Es el mismo error que la normalización del rubro existe para evitar —cobrar
# lo que no se pudo medir— entrando por la puerta de al lado. Un aviso que no
# dice en qué piso está no es un primer piso: es un aviso que no lo dice.
#
# La asimetría es la respuesta: un DEFECTO conocido (primer piso, sin
# estacionamiento, 20 m² por dormitorio) es información de decisión y pesa
# fuerte; una VIRTUD conocida es un desempate y pesa poco. Así, no publicar
# los extras cuesta poco, y publicar un defecto cuesta lo que corresponde.
PESO_PREFERENCIAS = 6        # lo más que puede SUMAR
PENALIZACION_MAXIMA = 12     # lo más que puede RESTAR

# ---------------------------------------------------------------------------
# Qué significa el número
#
# Un puntaje de 0 a 100 solo sirve si el mismo número quiere decir lo mismo
# siempre. La primera versión no cumplía eso, y se vio apenas hubo datos
# reales: de 91 candidatos, CINCUENTA quedaron entre 60 y 69. El puntaje no
# ordenaba nada justo donde había que decidir.
#
# La causa no era la fórmula sino qué se estaba metiendo dentro de ella. El
# número mezclaba dos preguntas distintas:
#
#     ¿QUÉ TAN BUENO ES?     lo que se quiere saber.
#     ¿CUÁNTO SABEMOS DE ÉL? lo que estaba escondido adentro.
#
# En la corrida real, NINGÚN aviso de los 91 pudo medir los cinco criterios.
# 22 midieron uno solo. Un 65 sacado de un dato se veía idéntico a un 65
# sacado de cuatro, y el tablero ordenaba mal por eso: un departamento
# mediocre bien documentado quedaba debajo de uno apenas conocido.
#
# La solución es separarlas y publicar las dos.
# ---------------------------------------------------------------------------

# El puntaje de un aviso del que no se sabe nada.
#
# No es 0. Un 0 dice "es malo", y de un aviso sin datos no se sabe eso: se
# sabe que no se sabe. 50 es la forma numérica de "ni idea", y es el ancla
# hacia la que se encoge un puntaje sacado de poca evidencia.
PUNTAJE_NEUTRO = 50

# Las bandas. Existen para que el número signifique algo sin tener que
# comparar contra otro: "83" no dice nada solo, "83, muy bueno" sí.
#
# Los cortes no son redondos por gusto. 85 es el piso de "anda a verlo hoy" y
# está puesto donde, con los datos reales, quedan los que cumplen todo y
# además son nuevos o baratos. 55 es el piso de "sirve" y coincide con el
# techo de un aviso sin precio: por definición no puede subir de ahí.
BANDAS = (
    (85, "🔥", "Anda a verlo"),
    (70, "⭐", "Muy bueno"),
    (55, "👍", "Sirve"),
    (40, "🤔", "Dudoso"),
    (0,  "▫️", "Al fondo"),
)

# Lo más que puede puntuar un aviso que no publica el arriendo.
#
# El precio no es un criterio más: es el requisito con el que empieza el
# pedido. Un aviso sin precio no se puede verificar contra el presupuesto, así
# que puede ser bueno pero nunca "muy bueno" — 69 es el techo de la banda
# "Sirve" y es exactamente lo que se quiere decir de él.
#
# Es un techo y no un descarte: un 5D de 226 m² en Vitacura sin precio
# publicado puede ser justo el que se busca. Lo que no puede es pasar por
# delante de uno verificado.
#
# Y se aplica COMPRIMIENDO el puntaje a esa escala, no recortándolo: ver
# `evaluar`. Recortar dejaba a los 39 avisos sin precio empatados en el mismo
# número, o sea media tabla sin orden.
TOPE_SIN_PRECIO = 69


def banda(score: int) -> tuple[str, str]:
    """(emoji, nombre) de la banda de un puntaje."""
    for piso, emoji, nombre in BANDAS:
        if score >= piso:
            return emoji, nombre
    return BANDAS[-1][1], BANDAS[-1][2]


def orden(l: Arriendo) -> tuple:
    """Con qué se ordena el tablero y la cola de avisos.

    Es (puntaje, confianza), y el segundo término es la parte nueva.

    Hace falta porque el puntaje solo no alcanza para ordenar: por diseño no
    castiga al aviso escueto —ver la regla nº2 arriba— así que dos avisos con
    el mismo puntaje pueden estar sostenidos por cinco criterios o por uno. Y
    cuando hay que elegir cuál de los dos mirar primero, el que sabemos que es
    bueno le gana al que parece bueno.

    Va acá y no adentro del puntaje a propósito. Meterlo adentro sería
    exactamente lo que la regla nº2 prohíbe: bajarle el puntaje a un
    departamento porque su portal escribe poco. El aviso escueto de un
    particular en Yapo suele ser el mejor negocio justamente porque nadie lo
    maquilló; lo que corresponde es mirarlo después, no descontarle puntos.
    """
    return (l.score, l.extras.get("confianza", 0))


def _intentar_encoger_no_va(*_a, **_k):  # pragma: no cover - marcador histórico
    """Acá vivió un encogimiento del puntaje hacia 50 según la confianza.

    Se probó y se sacó: la idea era que un puntaje sostenido por poca
    evidencia no afirmara tanto, pero en la práctica le bajaba el puntaje a
    todo aviso que publicara poco, que es literalmente lo que la regla nº2 de
    este módulo existe para no hacer. Dos tests lo atraparon en el acto.

    La misma información se publica ahora como un segundo número —la
    confianza— y se usa para ORDENAR, no para puntuar. Queda anotado para que
    a nadie se le ocurra de nuevo.
    """
    raise NotImplementedError


@dataclass
class Rubro:
    """Una línea del puntaje: cuánto valía, cuánto sacó, y si se pudo medir.

    Existe para poder contestar "¿qué le falta para 100?" con la aritmética a
    la vista en vez de con una impresión. Un 40/100 puede ser un departamento
    malo o uno del que no se sabe casi nada, y desde el número solo esas dos
    cosas se ven idénticas.
    """
    nombre: str
    peso: int
    obtenido: int
    medido: bool
    detalle: str
    # Qué dato concreto habría que conseguir para poder medirlo. Vacío cuando
    # ya se midió: ahí no falta nada, falta que la propiedad sea otra.
    falta: str = ""


@dataclass
class Evaluacion:
    score: int = 0
    razones: list[str] = field(default_factory=list)
    rubros: list[Rubro] = field(default_factory=list)
    descartado: bool = False
    motivo_descarte: str = ""
    clase_descarte: str = ""
    # Cuántos puntos del rubro se pudieron evaluar de verdad.
    medibles: int = 0
    preferencias: int = 0
    # Qué parte del rubro se pudo medir, de 0 a 100. Va publicada junto al
    # puntaje: ver el bloque "Qué significa el número".
    confianza: int = 0
    # El aviso no publica el arriendo. No descarta, pero topea el puntaje:
    # ver el comentario en `evaluar`.
    sin_precio: bool = False


# Debajo de esto, normalizar sería una opinión sacada de dos datos. Ubicación
# sola (26) no alcanza; ubicación más precio (46) ya empieza a decir algo.
_MINIMO_PARA_NORMALIZAR = 40


def _normalizar(bruto: int, medibles: int) -> int:
    """El puntaje sobre lo que se pudo medir, no sobre lo que se soñó medir.

    La antigüedad pesa 24 puntos y los portales de arriendo la publican poco:
    cobrarla como 0 hace que un departamento perfecto de Vitacura puntúe 55 y
    quede debajo de uno peor que sí publicó el año.

    Con muy pocos criterios medidos NO se normaliza, y ese umbral se queda: un
    100% sacado de un solo dato es una afirmación que ese dato no sostiene.
    Debajo del mínimo el puntaje se deja crudo —sale bajo, y es honesto que
    salga bajo— y la confianza publicada dice por qué.
    """
    if medibles <= 0:
        return 0
    if medibles < _MINIMO_PARA_NORMALIZAR or medibles >= RUBRO_COMPLETO:
        return max(0, min(100, bruto))
    return max(0, min(100, round(100 * bruto / medibles)))


# ---------------------------------------------------------------------------
# Filtros duros
# ---------------------------------------------------------------------------

def _requisitos(perfil: dict) -> dict:
    return perfil.get("requisitos") or {}


def _tolerancia(perfil: dict, campo: str) -> float:
    return float((_requisitos(perfil).get("tolerancias") or {}).get(campo, 0))


def tope_arriendo(perfil: dict) -> tuple[float | None, float | None]:
    """(tope pedido, tope con holgura). El segundo es el que descarta.

    El "cerca de" del pedido —"cerca de 1.6 millones máximo"— vive acá: entre
    el tope y el tope con holgura el departamento entra pero pierde puntos.
    Un arriendo de $1.650.000 que cumple todo lo demás se negocia; uno de
    $2.200.000 no, porque no hay negociación que lo baje 600 mil.
    """
    rango = _requisitos(perfil).get("arriendo_clp") or {}
    tope = rango.get("max")
    if tope is None:
        return None, None
    holgura = float(rango.get("holgura_pct", 0)) / 100
    return float(tope), float(tope) * (1 + holgura)


def monto_comparable(l: Arriendo, perfil: dict) -> float | None:
    """Qué monto se compara contra el tope: el canon o el costo total.

    Lo decide `requisitos.comparar` en el perfil. Por omisión es el canon,
    porque es lo que dice el pedido y es el único número que todos los avisos
    publican, pero el costo total está a un cambio de línea de distancia.
    """
    if _requisitos(perfil).get("comparar") == "total":
        return l.costo_mensual
    return l.arriendo_clp


def evaluar_zona(l: Arriendo, perfil: dict) -> tuple[bool, str, float | None]:
    """¿Entra en la zona? Devuelve (entra, motivo si no, distancia_km).

    La regla es la del perfil y es una disyunción, no una intersección:

        Vitacura (la comuna entera)  O  a 1,2 km o menos del Sport Francés

    El orden de las comprobaciones importa. La comuna se mira PRIMERO y sin
    tocar la distancia, porque Vitacura entra completa: un departamento en el
    extremo norte de la comuna está a 3 km del club y tiene que entrar igual.
    Mirar la distancia primero lo descartaría antes de llegar a la regla que
    lo salva.
    """
    nucleo = [P.norm(c) for c in (perfil.get("comunas") or {}).get("nucleo") or []]
    vecinas = [P.norm(c) for c in (perfil.get("comunas") or {}).get("vecinas") or []]
    comuna = P.norm(l.comuna)

    distancia = _distancia_al_ancla(l, perfil)

    radios = perfil.get("radio_km") or {}
    anillo = float(radios.get("anillo") or 0)
    sin_comuna = float(radios.get("sin_comuna") or 0)

    # Una comuna DEDUCIDA del barrio no es lo mismo que una leída del aviso.
    # "Alonso de Córdova" insinúa Vitacura, pero es una insinuación: si las
    # coordenadas ponen la propiedad a 100 km, mandan las coordenadas.
    #
    # Sin esta distinción, un aviso de Viña del Mar en una calle homónima
    # entraba como Vitacura con coordenadas y todo, que es peor que un
    # descarte: entra al tablero con datos que se contradicen entre sí.
    deducida = bool(l.extras.get("comuna_origen"))

    def _demasiado_lejos() -> bool:
        return (distancia is not None and sin_comuna and distancia > sin_comuna)

    if deducida and _demasiado_lejos():
        return (False,
                f"a {distancia:.0f} km del ancla: la comuna se dedujo del "
                f"barrio y las coordenadas la desmienten", distancia)

    # 1. La comuna núcleo entra entera, sin mirar distancia.
    if comuna and comuna in nucleo:
        return True, "", distancia

    # 2. Una comuna conocida que no es del núcleo ni vecina está fuera y punto.
    if comuna and comuna not in vecinas:
        return False, f"comuna fuera de la zona: {l.comuna}", distancia

    # 3. Comuna VECINA: acá sí manda el anillo, y descarta.
    #
    # Es el único caso donde la distancia puede botar algo, y tiene que serlo:
    # el anillo existe para dejar entrar el borde de Las Condes que queda
    # pegado al club, no para filtrar Vitacura.
    if comuna and comuna in vecinas:
        if distancia is None:
            # Una vecina sin coordenadas es un "quizás": Las Condes tiene
            # 99 km² y solo un borde cae dentro del anillo. Entra —descartar
            # por dato faltante es el error caro— y el scoring le cobra la
            # incertidumbre con el multiplicador de comuna.
            return True, "", None
        if distancia <= anillo:
            return True, "", distancia
        return (False,
                f"{l.comuna} a {distancia:.1f} km, fuera del anillo de "
                f"{anillo:g} km", distancia)

    # 4. Comuna DESCONOCIDA. Acá el anillo no se puede aplicar.
    #
    # Aplicarlo era un bug real y del tipo peor: silencioso. Un departamento
    # de Vitacura cuya comuna no se alcanzó a leer —pasa cuando el aviso la
    # nombra solo en la ficha de detalle— quedaba descartado por estar a 2,9
    # km del club, cuando Vitacura entera es zona válida y se extiende mucho
    # más allá del anillo.
    #
    # Con la comuna en blanco no se sabe si el anillo aplica, así que no se
    # usa. Lo que sí se puede descartar es lo que está tan lejos que no cabe
    # en ninguna de las comunas del perfil: para eso está `sin_comuna`, un
    # radio generoso que cubre Vitacura completa y nada más.
    if _demasiado_lejos():
        return (False,
                f"a {distancia:.1f} km del ancla, demasiado lejos para estar "
                f"en la zona (y el aviso no dice la comuna)", distancia)

    # Sin comuna y sin coordenadas no se puede ubicar. Entra igual: la fuente
    # ya venía filtrada por comuna en su URL, y descartar acá botaría todo lo
    # que publique la dirección solo en la ficha de detalle.
    return True, "", distancia


def _distancia_al_ancla(l: Arriendo, perfil: dict) -> float | None:
    ancla = perfil.get("ancla") or {}
    if l.lat is None or l.lon is None or ancla.get("lat") is None:
        return None
    return round(haversine_km(l.lat, l.lon, ancla["lat"], ancla["lon"]), 2)


def _dominio_excluido(url: str, dominios: list[str]) -> str:
    """El dominio excluido al que apunta esta URL, o "" si no hay ninguno.

    Compara por sufijo de host, no por "está contenido en la URL": un aviso
    de `trovit.cl/redirect?to=portalinmobiliario.com/...` lleva el dominio
    dentro del query y no es un aviso de Portal Inmobiliario, es un enlace de
    Trovit que todavía no se ha seguido. Descartarlo por eso perdería el
    aviso sin motivo.

    El sufijo se compara con un punto delante para que `inmobiliario.com` no
    calce con `portalinmobiliario.com`, que es otro sitio.
    """
    from urllib.parse import urlparse

    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return ""
    for d in dominios:
        d = str(d).lower().strip().lstrip(".")
        if host == d or host.endswith("." + d):
            return d
    return ""


def _descarta_por_requisitos(l: Arriendo, perfil: dict) -> tuple[str, str]:
    """El primer requisito obligatorio que un dato CONOCIDO incumple.

    Devuelve (motivo, clase). Vacío si pasa todo, que incluye el caso de que
    no se sepa nada: los datos ausentes no descartan.
    """
    req = _requisitos(perfil)

    excluir = perfil.get("excluir") or {}

    # --- portales excluidos ---
    #
    # Va primero de todo: si el aviso viene de un portal que no queremos, da
    # lo mismo cuánto mida ni cuánto cueste. Es el filtro que permite usar
    # metabuscadores sin traicionar la premisa del proyecto.
    if (dominio := _dominio_excluido(l.url, excluir.get("dominios") or [])):
        return f"viene de {dominio}, que ya tienes cubierto aparte", "portal"

    # --- operación: lo que no es un arriendo no se evalúa ---
    if l.operacion == "venta" and excluir.get("ventas", True):
        return "es una publicación de venta, no de arriendo", "operacion"
    if l.operacion == "temporada" and excluir.get("temporada", True):
        return "es arriendo por temporada / por días", "operacion"
    if l.operacion == "pieza" and excluir.get("piezas", True):
        return "es el arriendo de una pieza, no del departamento", "operacion"

    # --- tipo ---
    tipos = [P.norm(t) for t in (req.get("tipo") or [])]
    if l.tipo and tipos and P.norm(l.tipo) not in tipos:
        return f"es {l.tipo}, no {' ni '.join(tipos)}", "tipo"

    # --- superficie ---
    #
    # El único filtro asimétrico del módulo, y la asimetría es geometría, no
    # una concesión: la superficie TOTAL nunca es menor que la ÚTIL.
    #
    #   Con la total conocida       decide, en los dos sentidos.
    #   Solo con la útil            puede CONFIRMAR pero nunca RECHAZAR. Una
    #                               útil de 118 m² garantiza una total de al
    #                               menos 118 y el filtro puede usarla. Una
    #                               útil de 92 m² no dice nada: ese mismo
    #                               departamento con 15 m² de terraza tiene
    #                               107 totales y sí cumple.
    #
    # Tratar la útil como si fuera la total en los dos sentidos descartaría en
    # silencio departamentos que cumplen, y ese es el error que no se ve.
    minimo = (req.get("m2_totales") or {}).get("min")
    if minimo is not None and l.m2_totales is not None:
        estricto = bool((req.get("m2_totales") or {}).get("estricto"))
        piso = float(minimo) - _tolerancia(perfil, "m2_totales")
        # `estricto` implementa el "más de 100", no el "100 o más": un
        # departamento de exactamente 100,0 m² no cumple "más de 100".
        fuera = l.m2_totales < piso or (estricto and l.m2_totales == piso)
        if fuera:
            comparador = "más de" if estricto else "al menos"
            return (f"{l.m2_totales:g} m² totales, no llega a {comparador} "
                    f"{piso:g} m²", "superficie")

    # --- dormitorios ---
    minimo = (req.get("dormitorios") or {}).get("min")
    if minimo is not None and l.dormitorios is not None:
        piso = float(minimo) - _tolerancia(perfil, "dormitorios")
        if l.dormitorios < piso:
            return (f"{l.dormitorios} dormitorios, bajo el mínimo de {minimo:g}",
                    "dormitorios")

    # --- precio ---
    _, techo = tope_arriendo(perfil)
    monto = monto_comparable(l, perfil)
    if techo is not None and monto is not None and monto > techo:
        que = ("costo total" if req.get("comparar") == "total" else "arriendo")
        return (f"{que} de ${monto:,.0f} sobre el máximo negociable de "
                f"${techo:,.0f}".replace(",", "."), "precio")

    return "", ""


# ---------------------------------------------------------------------------
# Los cinco rubros
# ---------------------------------------------------------------------------

def _rubro_ubicacion(l: Arriendo, perfil: dict, distancia: float | None) -> Rubro:
    """Dónde queda, con Vitacura pesando más que la distancia.

    El multiplicador por comuna es el que hace cumplir "prioriza Vitacura
    comuna entera". Sin él, el tablero mostraría lo contrario de lo pedido: el
    ancla está a menos de 400 m del límite con Las Condes, así que dentro del
    anillo Las Condes gana por distancia casi siempre.
    """
    prioridades = perfil.get("prioridad_comuna") or {}
    peso_comuna = 0.0
    for nombre, valor in prioridades.items():
        if P.norm(nombre) == P.norm(l.comuna):
            peso_comuna = float(valor)
            break

    if not l.comuna and distancia is None:
        return Rubro("Ubicación", PESO_UBICACION, 0, False,
                     "sin comuna ni coordenadas",
                     falta="la dirección o la comuna")

    # La parte que aporta la distancia: completa dentro de la zona caminable,
    # decreciendo hasta el borde del anillo.
    radios = perfil.get("radio_km") or {}
    preferente = float(radios.get("preferente") or 0)
    anillo = float(radios.get("anillo") or 0)

    if distancia is None:
        # Sin coordenadas se puntúa solo por comuna, y se dice. Una comuna
        # núcleo sin ubicar vale bastante —Vitacura entera es la zona— pero no
        # el máximo, porque dentro de Vitacura la distancia sigue ordenando.
        obtenido = round(PESO_UBICACION * peso_comuna * 0.75)
        detalle = f"{l.comuna or 'comuna desconocida'}, sin ubicar en el mapa"
        return Rubro("Ubicación", PESO_UBICACION, obtenido, bool(l.comuna),
                     detalle, falta="" if l.comuna else "la dirección")

    if preferente and distancia <= preferente:
        factor = 1.0
        como = f"a {distancia:g} km — zona caminable"
    elif anillo and distancia <= anillo:
        # Cae linealmente entre el borde de lo caminable y el del anillo.
        tramo = max(anillo - preferente, 0.01)
        factor = 1.0 - 0.35 * (distancia - preferente) / tramo
        como = f"a {distancia:g} km del Sport Francés"
    else:
        # Más lejos que el anillo y aun así presente: es Vitacura, que entra
        # entera. Se puntúa por la comuna y la distancia solo modula un poco.
        factor = max(0.45, 1.0 - 0.12 * (distancia - anillo))
        como = f"a {distancia:g} km — Vitacura, fuera del anillo"

    obtenido = round(PESO_UBICACION * peso_comuna * factor)
    detalle = f"{l.comuna or 'sin comuna'} · {como}"
    return Rubro("Ubicación", PESO_UBICACION, obtenido, True, detalle)


def _rubro_antiguedad(l: Arriendo, perfil: dict) -> Rubro:
    """"Ideal más nuevo". El rubro que ordena el tablero.

    La curva es por tramos y no lineal porque así se comporta el producto: un
    edificio de menos de 10 años trae termopanel, calefacción central y
    estacionamientos amplios; entre 32 y 45 años la diferencia ya casi no se
    nota.
    """
    cfg = perfil.get("antiguedad") or {}
    ideal = float(cfg.get("ideal_max", 10))
    bueno = float(cfg.get("bueno_max", 20))
    aceptable = float(cfg.get("aceptable_max", 35))

    antig = l.antiguedad_anos
    aproximado = False
    if antig is None:
        # El techo declarado ("a estrenar", "edificio nuevo") es un dato de
        # verdad y en arriendo aparece más seguido que el año exacto. Se usa,
        # y se dice que es un techo: no afirma la edad, la acota.
        antig = l.extras.get("antiguedad_techo")
        aproximado = antig is not None

    if antig is None:
        return Rubro("Antigüedad", PESO_ANTIGUEDAD, 0, False,
                     "el aviso no publica el año", falta="el año de construcción")

    # La curva baja DESDE EL AÑO CERO, sin meseta arriba.
    #
    # Antes el tramo ideal era plano: cualquier edificio de 10 años o menos
    # sacaba el mismo puntaje, así que uno de 2 años y uno de 10 empataban en
    # el rubro que existe para separarlos. "Ideal más nuevo, entonces vamos
    # poniendo puntaje" pide justamente lo contrario, y la meseta lo anulaba
    # en el tramo donde está la mayoría del inventario nuevo de Vitacura.
    #
    # Lo mismo pasaba abajo con el precio, y entre las dos mesetas el puntaje
    # dejaba de ordenar: dos departamentos bien distintos quedaban a 3 puntos
    # uno del otro y una preferencia de +6 daba vuelta la comparación.
    #
    # Ahora la pendiente del tramo ideal es suave —de 1,00 a 0,85 en diez
    # años— para que separar sin exagerar: dos años de diferencia valen algo,
    # pero no tanto como veinte.
    if antig <= ideal:
        factor = 1.0 - 0.15 * antig / max(ideal, 1)
    elif antig <= bueno:
        factor = 0.85 - 0.20 * (antig - ideal) / max(bueno - ideal, 1)
    elif antig <= aceptable:
        factor = 0.65 - 0.40 * (antig - bueno) / max(aceptable - bueno, 1)
    else:
        # Sobre el tramo aceptable puntúa el mínimo pero NO descarta: un
        # edificio de los ochenta de 140 m² bien mantenido en Vitacura sigue
        # siendo un arriendo razonable, y botarlo por el año sería decidir por
        # el usuario.
        factor = max(0.10, 0.25 - 0.01 * (antig - aceptable))

    obtenido = round(PESO_ANTIGUEDAD * factor)
    if aproximado:
        detalle = f"a lo más {antig:g} años (declarado, no publicado)"
    else:
        ano = l.ano_construccion
        detalle = f"{antig:g} años" + (f" (construido en {ano})" if ano else "")
    return Rubro("Antigüedad", PESO_ANTIGUEDAD, obtenido, True, detalle)


def _rubro_precio(l: Arriendo, perfil: dict) -> Rubro:
    """Cuánto cuesta contra el presupuesto, y cuánto de eso son gastos comunes.

    Dos cosas puntúan acá y la segunda es la que ningún portal muestra: un
    departamento de $1.500.000 con $380.000 de gastos comunes cuesta más que
    uno de $1.700.000 con $120.000, y mirando solo el canon el orden sale al
    revés.
    """
    tope, techo = tope_arriendo(perfil)
    monto = monto_comparable(l, perfil)

    if monto is None or tope is None:
        return Rubro("Precio", PESO_PRECIO, 0, False,
                     "el aviso no publica el valor", falta="el arriendo mensual")

    # Bajo el tope, el puntaje baja de forma continua desde el 55% del
    # presupuesto: ahí está el piso donde un arriendo más barato ya no es
    # mejor negocio sino un dato sospechoso —$900.000 por 130 m² en Vitacura
    # es un error de parseo o un problema del departamento—. De ahí al tope
    # cae parejo, sin meseta.
    #
    # La meseta anterior llegaba hasta el 80% del tope, o sea que con el
    # presupuesto en $1.700.000 todo lo que costara menos de $1.360.000
    # puntuaba idéntico. Un arriendo de $1.250.000 y uno de $1.350.000 no son
    # lo mismo, y este es el rubro donde esa diferencia tiene que aparecer.
    PISO_SOSPECHOSO = 0.55
    if monto <= tope * PISO_SOSPECHOSO:
        factor = 1.0
    elif monto <= tope:
        tramo = (monto - tope * PISO_SOSPECHOSO) / (tope * (1 - PISO_SOSPECHOSO))
        factor = 1.0 - 0.25 * tramo
    else:
        # Entre el tope y el techo negociable: entra, pero se le cobra. Es el
        # "cerca de 1.6 millones" del pedido convertido en puntos.
        sobre = (monto - tope) / max(techo - tope, 1)
        factor = 0.55 - 0.45 * sobre

    obtenido = round(PESO_PRECIO * max(factor, 0.0))

    partes = [f"${monto:,.0f}".replace(",", ".")]
    if l.gastos_comunes_clp is not None:
        pct = l.gastos_comunes_pct
        partes.append(f"GC ${l.gastos_comunes_clp:,.0f}".replace(",", "."))
        # Los gastos comunes ajustan el puntaje dentro del mismo rubro.
        cfg = (perfil.get("preferencias") or {}).get("gastos_comunes_pct") or {}
        bueno = float(cfg.get("bueno_max", 12))
        tolerable = float(cfg.get("tolerable_max", 20))
        if pct is not None:
            if pct > tolerable:
                obtenido = round(obtenido * 0.80)
                partes.append(f"{pct:g}% del canon — altos")
            elif pct <= bueno:
                partes.append(f"{pct:g}% del canon — bajos")
            else:
                partes.append(f"{pct:g}% del canon")
    else:
        partes.append("GC no publicados")

    if monto > tope:
        partes.append(f"sobre el tope de ${tope:,.0f}".replace(",", "."))

    return Rubro("Precio", PESO_PRECIO, obtenido, True, " · ".join(partes))


def _rubro_superficie(l: Arriendo, perfil: dict) -> Rubro:
    """Más grande es mejor, con rendimientos decrecientes."""
    m2 = l.m2_referencia
    if m2 is None:
        return Rubro("Superficie", PESO_SUPERFICIE, 0, False,
                     "el aviso no publica los metros", falta="los m² totales")

    cfg = (perfil.get("preferencias") or {}).get("m2_totales") or {}
    minimo = float((_requisitos(perfil).get("m2_totales") or {}).get("min") or 100)
    comodo = float(cfg.get("comodo", 130))
    tope_util = float(cfg.get("tope_util", 170))

    if m2 >= tope_util:
        factor = 1.0
    elif m2 >= comodo:
        factor = 0.85 + 0.15 * (m2 - comodo) / max(tope_util - comodo, 1)
    elif m2 >= minimo:
        factor = 0.55 + 0.30 * (m2 - minimo) / max(comodo - minimo, 1)
    else:
        # Bajo el mínimo pero dentro de la tolerancia: entró por poco.
        factor = 0.30

    cual = "totales" if l.m2_totales is not None else "útiles (total no publicada)"
    detalle = f"{m2:g} m² {cual}"
    if l.m2_terraza:
        detalle += f", terraza {l.m2_terraza:g} m²"
    return Rubro("Superficie", PESO_SUPERFICIE, round(PESO_SUPERFICIE * factor),
                 True, detalle)


def _rubro_programa(l: Arriendo, perfil: dict) -> Rubro:
    """Dormitorios y baños: cómo están repartidos esos metros."""
    if l.dormitorios is None and l.banos is None:
        return Rubro("Programa", PESO_PROGRAMA, 0, False,
                     "el aviso no publica dormitorios ni baños",
                     falta="dormitorios y baños")

    req_dorm = float((_requisitos(perfil).get("dormitorios") or {}).get("min") or 3)
    prefs = perfil.get("preferencias") or {}
    banos_deseables = float((prefs.get("banos") or {}).get("minimo_deseable", 3))

    puntos = 0.0
    partes = []

    # Dormitorios: 60% del rubro. Cumplir el mínimo ya vale la mayor parte;
    # uno de más suma, pero no indefinidamente.
    if l.dormitorios is not None:
        extra = min(max(l.dormitorios - req_dorm, 0), 2) / 2
        puntos += PESO_PROGRAMA * 0.60 * (0.75 + 0.25 * extra)
        partes.append(f"{l.dormitorios}D")

    # Baños: 40%.
    if l.banos is not None:
        factor = min(l.banos / banos_deseables, 1.0)
        puntos += PESO_PROGRAMA * 0.40 * factor
        partes.append(f"{l.banos}B")
    if l.extras.get("pieza_servicio"):
        partes.append("+ servicio")

    return Rubro("Programa", PESO_PROGRAMA, round(puntos), True,
                 " · ".join(partes))


# ---------------------------------------------------------------------------
# Preferencias — los desempates
# ---------------------------------------------------------------------------

def evaluar_preferencias(l: Arriendo, perfil: dict) -> tuple[int, list[str]]:
    """Puntos de desempate entre propiedades que ya calificaron.

    Van fuera de la normalización a propósito: si entraran en el porcentaje,
    dos departamentos distintos saturarían los dos en 100 y el desempate
    —que es justo para lo que existen— desaparecería.
    """
    prefs = perfil.get("preferencias") or {}
    puntos = 0
    razones: list[str] = []

    # -- piso --
    cfg = prefs.get("piso") or {}
    if l.piso is not None:
        minimo = cfg.get("minimo_deseable")
        if minimo and l.piso >= minimo:
            puntos += 2
            razones.append(f"piso {l.piso}")
        if cfg.get("penalizar_ultimo") and l.ultimo_piso:
            puntos -= 2
            razones.append("último piso / penthouse")
        elif l.piso <= 1:
            # Un primer piso en Vitacura es un defecto de verdad —seguridad,
            # ruido, vista— y no un matiz. Pesa más que cualquier virtud.
            puntos -= 3
            razones.append("primer piso")

    # -- orientación --
    preferidas = [P.norm(o) for o in (prefs.get("orientacion") or {}).get("preferida") or []]
    if l.orientacion and P.norm(l.orientacion) in preferidas:
        puntos += 2
        razones.append(f"orientación {l.orientacion}")

    # -- holgura: m² por dormitorio --
    cfg = prefs.get("holgura_m2_por_dormitorio") or {}
    minimo = cfg.get("minimo_deseable")
    m2 = l.m2_referencia
    if minimo and m2 and l.dormitorios:
        holgura = m2 / l.dormitorios
        if holgura >= minimo:
            puntos += 2
            razones.append(f"{holgura:.0f} m² por dormitorio")
        else:
            # Distingue un 130 m² de 3 dormitorios amplios de un 130 m²
            # picado en 5 piezas chicas. Los dos pasan el filtro duro.
            puntos -= 1
            razones.append(f"solo {holgura:.0f} m² por dormitorio")

    # -- estacionamientos --
    cfg = prefs.get("estacionamientos") or {}
    minimo = cfg.get("minimo_deseable")
    if l.estacionamientos is not None:
        if minimo and l.estacionamientos >= minimo:
            puntos += 2
            razones.append(f"{l.estacionamientos} estacionamientos")
        elif l.estacionamientos == 0:
            # Un departamento de 134 m² y tres dormitorios en Vitacura sin
            # estacionamiento obliga a arrendar uno aparte: son $80.000 al mes
            # que no salen en el aviso.
            puntos -= 3
            razones.append("sin estacionamiento")

    if l.bodega:
        puntos += 1
        razones.append("con bodega")

    # -- amoblado --
    preferido = P.norm(prefs.get("amoblado_preferido") or "")
    if preferido and l.amoblado:
        if P.norm(l.amoblado) == preferido:
            puntos += 2
            razones.append(l.amoblado)
        else:
            puntos -= 2
            razones.append(f"{l.amoblado} (se prefiere {preferido})")

    # -- palabras --
    texto = P.norm(f"{l.title} {l.raw_text}")
    bonus = [k for k in (prefs.get("keywords_bonus") or []) if P.norm(k) in texto]
    if bonus:
        puntos += min(len(bonus), 3)
        razones.append(", ".join(bonus[:3]))
    alertas = [k for k in (prefs.get("keywords_alerta") or []) if P.norm(k) in texto]
    if alertas:
        puntos -= min(len(alertas), 2)
        razones.append(f"ojo: {', '.join(alertas[:2])}")

    # -- publicado hace rato: se negocia --
    dias = l.dias_publicado
    if dias is not None and dias >= 45:
        puntos += 2
        razones.append(f"publicado hace {dias} días — se negocia")

    # -- lo publica el dueño: sin comisión --
    if l.extras.get("particular"):
        puntos += 1
        razones.append("trato directo, sin comisión")

    return max(-PENALIZACION_MAXIMA, min(PESO_PREFERENCIAS, puntos)), razones


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def _completar(l: Arriendo) -> None:
    """Rellena lo que se puede deducir del texto que ya se tiene.

    Solo campos vacíos, y cada deducción deja constancia de que lo es. Un dato
    deducido presentado como publicado es peor que uno ausente: cuando falta,
    la alerta lo dice y alguien mira; cuando está y es falso, nadie lo revisa.
    """
    texto = f"{l.title} {l.raw_text} {l.direccion}"

    if l.antiguedad_anos is None and l.ano_construccion is not None:
        l.antiguedad_anos = P.hoy().year - l.ano_construccion
    if l.antiguedad_anos is None and "antiguedad_techo" not in l.extras:
        if (techo := P.techo_antiguedad(texto)) is not None:
            l.extras["antiguedad_techo"] = techo

    if l.piso is None:
        if (piso := P.piso_desde_numero(texto)) is not None:
            l.piso = piso
            l.extras["piso_origen"] = "deducido del número del departamento"

    if not l.comuna:
        if (comuna := P.comuna_por_barrio(texto)):
            l.comuna = comuna
            l.extras["comuna_origen"] = "deducida del barrio"


def evaluar(l: Arriendo, perfil: dict) -> Arriendo:
    """Evalúa un arriendo: lo descarta o lo puntúa. Devuelve el mismo objeto."""
    _completar(l)

    ev = Evaluacion()

    entra, motivo, distancia = evaluar_zona(l, perfil)
    l.distancia_km = distancia
    if not entra:
        return _aplicar(l, ev, descarte=(motivo, "zona"))

    incumple, clase = _descarta_por_requisitos(l, perfil)
    if incumple:
        return _aplicar(l, ev, descarte=(incumple, clase))

    ev.rubros = [
        _rubro_ubicacion(l, perfil, distancia),
        _rubro_antiguedad(l, perfil),
        _rubro_precio(l, perfil),
        _rubro_superficie(l, perfil),
        _rubro_programa(l, perfil),
    ]

    bruto = sum(r.obtenido for r in ev.rubros)
    ev.medibles = sum(r.peso for r in ev.rubros if r.medido)
    ev.preferencias, razones_pref = evaluar_preferencias(l, perfil)

    # La confianza: qué parte del rubro se pudo medir de verdad. Es la segunda
    # mitad de la respuesta y va publicada junto al puntaje, no escondida
    # adentro de él. Un 83 con 80% de confianza y un 83 con 20% son dos cosas
    # muy distintas y hay que poder distinguirlas de un vistazo.
    ev.confianza = round(100 * ev.medibles / RUBRO_COMPLETO)

    # El puntaje: el rubro sobre lo medible, más las preferencias.
    #
    # Ya no se escala por 0,94 como antes. Ese factor existía para dejarle
    # espacio arriba a las preferencias positivas, pero le robaba seis puntos
    # a todo el mundo y hacía que un departamento perfecto nunca pudiera
    # llegar a 100. Con las preferencias topeadas en +6 y el clamp final, el
    # espacio ya está garantizado sin deformar la escala: ahora un 100 es
    # alcanzable y quiere decir "cumple todo lo medible y además tiene extras".
    ev.score = max(0, min(100, _normalizar(bruto, ev.medibles) + ev.preferencias))
    ev.razones = [f"{r.nombre}: {r.detalle}" for r in ev.rubros if r.medido]
    ev.razones += razones_pref

    # El precio no es un criterio más: es EL requisito.
    #
    # Normalizar sobre lo medible es lo correcto para todo lo demás —un aviso
    # que no publica el año no debería hundirse por eso— pero con el precio
    # produce lo contrario de lo que se pidió. En la primera corrida real, 39
    # de los 68 candidatos no publicaban precio y se quedaron con los seis
    # primeros lugares del tablero: departamentos de 226 y 325 m² puntuando 90
    # sin que nadie supiera si costaban $1,4 millones o $4,5.
    #
    # Y esos habrían sido los primeros seis mensajes de Telegram, empujando
    # fuera del tope de la corrida a los que sí cumplían el presupuesto
    # verificado. El pedido empieza con "no más de 1,6 millones"; un aviso sin
    # precio no se puede verificar contra eso.
    #
    # Se resuelve con un techo, no con un descarte. Un 5D de 226 m² en
    # Vitacura sin precio publicado sigue valiendo la pena mirarlo —puede ser
    # justo el que se busca— así que se queda en el tablero y sigue pudiendo
    # avisar. Lo que no puede es pasar por delante de uno verificado.
    if not next((r.medido for r in ev.rubros if r.nombre == "Precio"), True):
        ev.sin_precio = True
        # Se COMPRIME hacia el techo, no se recorta contra él.
        #
        # La primera versión hacía `min(score, TOPE)`, y eso produjo el peor
        # síntoma que tuvo este puntaje: los 39 avisos sin precio quedaron
        # todos clavados en el mismo número. La mitad del tablero empatada,
        # sin forma de saber cuál mirar primero — que es exactamente lo que un
        # puntaje existe para resolver.
        #
        # Multiplicar en vez de cortar respeta el techo igual y conserva el
        # orden entre ellos: un 5D de 226 m² recién construido sigue quedando
        # por encima de un 3D viejo, los dos bajo el techo.
        ev.score = round(ev.score * TOPE_SIN_PRECIO / 100)
        ev.razones.append(
            "sin precio publicado: no se puede verificar contra el "
            "presupuesto, así que no compite con los que sí lo publican")

    return _aplicar(l, ev)


def _aplicar(l: Arriendo, ev: Evaluacion,
             descarte: tuple[str, str] | None = None) -> Arriendo:
    if descarte:
        ev.descartado, (ev.motivo_descarte, ev.clase_descarte) = True, descarte
        ev.score = 0
    l.score = ev.score
    l.razones = ev.razones
    l.descartado = ev.descartado
    l.motivo_descarte = ev.motivo_descarte
    l.clase_descarte = ev.clase_descarte
    l.extras["medibles"] = ev.medibles
    l.extras["rubros"] = [
        {"nombre": r.nombre, "peso": r.peso, "obtenido": r.obtenido,
         "medido": r.medido, "detalle": r.detalle, "falta": r.falta}
        for r in ev.rubros
    ]
    l.extras["preferencias"] = ev.preferencias
    l.extras["confianza"] = ev.confianza
    # Lo lee la alerta, para decirlo en vez de mostrar un mensaje sin precio y
    # que parezca que se olvidó.
    if ev.sin_precio:
        l.extras["sin_precio"] = True
    return l


def desglose(l: Arriendo) -> list[Rubro]:
    """Los rubros del último `evaluar`, para mostrarlos en la ficha."""
    return [Rubro(**r) for r in l.extras.get("rubros", [])]


def techo_alcanzable(l: Arriendo) -> int:
    """Hasta cuánto podría llegar si se consiguieran los datos que faltan.

    Es la cifra que separa "este departamento no sirve" de "no sabemos si
    sirve", y sin ella un puntaje bajo se lee siempre como lo primero.
    """
    rubros = desglose(l)
    if not rubros:
        return l.score
    logrado = sum(r.obtenido for r in rubros)
    por_medir = sum(r.peso for r in rubros if not r.medido)

    # El techo se calcula con la MISMA fórmula que el puntaje real pero
    # suponiendo dos cosas: que los datos que faltan aparecen, y que cuando
    # aparecen son buenos. Por eso los rubros sin medir suman su peso completo
    # y la confianza pasa a 100 — sin lo segundo, el techo saldría encogido
    # hacia el neutro y podría quedar por DEBAJO del puntaje que ya tiene, que
    # es justo lo que esta función existe para no hacer.
    return max(0, min(100, _normalizar(logrado + por_medir, RUBRO_COMPLETO)
                      + l.extras.get("preferencias", 0)))


def debe_alertar(l: Arriendo, perfil: dict) -> bool:
    """¿Este hallazgo merece interrumpir a alguien?"""
    if l.descartado:
        return False
    cfg = perfil.get("alertas") or {}
    if l.score >= int(cfg.get("score_minimo", 35)):
        return True
    # Red de seguridad: cumple los filtros duros pero le faltan datos para
    # puntuar. El mercado de arriendo se mueve en días, así que perder uno por
    # esperar un dato es peor que revisarlo a mano.
    if cfg.get("alertar_incompletos", True):
        medibles = l.extras.get("medibles", 0)
        return medibles < RUBRO_COMPLETO and techo_alcanzable(l) >= int(
            cfg.get("score_minimo", 35))
    return False
