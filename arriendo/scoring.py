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
PESO_PREFERENCIAS = 14


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


# Debajo de esto, normalizar sería una opinión sacada de dos datos. Ubicación
# sola (26) no alcanza; ubicación más precio (46) ya empieza a decir algo.
_MINIMO_PARA_NORMALIZAR = 40


def _normalizar(bruto: int, medibles: int) -> int:
    """El puntaje sobre lo que se pudo medir, no sobre lo que se soñó medir.

    La antigüedad pesa 24 puntos y los portales de arriendo la publican poco:
    cobrarla como 0 hace que un departamento perfecto de Vitacura puntúe 55 y
    quede debajo de uno peor que sí publicó el año.

    Con pocos criterios medidos NO se normaliza: un 100% sacado de un solo
    dato es peor que un número bajo y honesto.
    """
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

    # 1. La comuna núcleo entra entera, sin mirar distancia.
    if comuna and comuna in nucleo:
        return True, "", distancia

    # 2. Fuera del núcleo, manda el anillo.
    anillo = float((perfil.get("radio_km") or {}).get("anillo") or 0)
    if distancia is not None and anillo:
        if distancia <= anillo:
            return True, "", distancia
        return (False,
                f"a {distancia:.1f} km del ancla, fuera del anillo de {anillo:g} km",
                distancia)

    # 3. Sin coordenadas, la comuna es la red de seguridad.
    if comuna and comuna in vecinas:
        # Una vecina sin coordenadas es "quizás": Las Condes tiene 99 km² y
        # solo un borde cae dentro del anillo. Entra —descartar por dato
        # faltante es el error caro— y el scoring le cobra la incertidumbre.
        return True, "", None

    if comuna:
        conocidas = set(nucleo) | set(vecinas)
        if comuna not in conocidas:
            return False, f"comuna fuera de la zona: {l.comuna}", None

    # Sin comuna y sin coordenadas no se puede ubicar. Entra igual: la fuente
    # ya venía filtrada por comuna en su URL, y descartar acá botaría todo lo
    # que publique la dirección solo en la ficha de detalle.
    return True, "", None


def _distancia_al_ancla(l: Arriendo, perfil: dict) -> float | None:
    ancla = perfil.get("ancla") or {}
    if l.lat is None or l.lon is None or ancla.get("lat") is None:
        return None
    return round(haversine_km(l.lat, l.lon, ancla["lat"], ancla["lon"]), 2)


def _descarta_por_requisitos(l: Arriendo, perfil: dict) -> tuple[str, str]:
    """El primer requisito obligatorio que un dato CONOCIDO incumple.

    Devuelve (motivo, clase). Vacío si pasa todo, que incluye el caso de que
    no se sepa nada: los datos ausentes no descartan.
    """
    req = _requisitos(perfil)

    # --- operación: lo que no es un arriendo no se evalúa ---
    excluir = perfil.get("excluir") or {}
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

    if antig <= ideal:
        factor = 1.0
    elif antig <= bueno:
        factor = 1.0 - 0.35 * (antig - ideal) / max(bueno - ideal, 1)
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

    # Bajo el tope: puntaje completo al 80% del presupuesto o menos, y cae
    # suave hasta el tope. No premia lo muy barato sin límite —un arriendo de
    # $600.000 para 100 m² en Vitacura es un error de dato o un problema— pero
    # sí premia el margen.
    if monto <= tope * 0.80:
        factor = 1.0
    elif monto <= tope:
        factor = 1.0 - 0.25 * (monto - tope * 0.80) / (tope * 0.20)
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
            puntos -= 2
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
            puntos -= 2
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

    return max(-PESO_PREFERENCIAS, min(PESO_PREFERENCIAS, puntos)), razones


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

    ev.score = max(0, min(100, _normalizar(bruto, ev.medibles) + ev.preferencias))
    ev.razones = [f"{r.nombre}: {r.detalle}" for r in ev.rubros if r.medido]
    ev.razones += razones_pref

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
    return max(0, min(100, logrado + por_medir + l.extras.get("preferencias", 0)))


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
