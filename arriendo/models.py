"""Modelo de datos normalizado.

Toda fuente —un portal grande, la página de una corredora, un aviso suelto—
termina convertida a un `Arriendo`. El resto del sistema solo conoce esta forma.

La parte más importante de este módulo no es el dataclass sino el
`fingerprint`, y conviene decir por qué: **el mismo departamento se publica en
seis portales a la vez**. Es la diferencia con un radar de remates, donde cada
edicto sale una o dos veces. Acá, sin una identidad estable que cruce portales,
un solo departamento de Vitacura manda seis mensajes de Telegram y el radar se
vuelve inutilizable en una semana.

La normalización de direcciones viene del radar de remates (repo `claude-code`),
donde se calibró contra duplicados reales entre agregadores. Se trajo entera
porque el problema es el mismo y ahí ya está resuelto.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any
from .tiempo import ahora_utc


@dataclass
class Arriendo:
    """Un arriendo publicado, normalizado."""

    # --- identidad ---
    source: str                      # id de la fuente, ej. "toctoc"
    url: str
    title: str = ""

    # --- ubicación ---
    direccion: str = ""
    comuna: str = ""
    lat: float | None = None
    lon: float | None = None

    # --- qué es ---
    tipo: str = ""                   # "departamento" | "casa" | ""
    # `operacion` separa el arriendo de la venta que se cuela en la grilla, y
    # del arriendo por día que no es lo que se busca.
    operacion: str = "arriendo"      # "arriendo" | "venta" | "temporada" | "pieza"

    # --- superficies ---
    # Se guardan las dos porque significan cosas distintas y el filtro duro es
    # sobre la TOTAL: útil es el interior, total incluye terrazas y balcones.
    m2_totales: float | None = None
    m2_utiles: float | None = None
    m2_terraza: float | None = None

    # --- programa ---
    dormitorios: int | None = None
    banos: int | None = None
    estacionamientos: int | None = None
    bodega: bool | None = None
    piso: int | None = None
    ultimo_piso: bool = False
    orientacion: str = ""

    # --- antigüedad ---
    # El dato canónico es la ANTIGÜEDAD EN AÑOS y no el año de construcción,
    # para que el perfil no se pudra solo con el paso del tiempo: un rango
    # escrito como "2016-2021" hay que reescribirlo cada enero, y uno escrito
    # como "5 a 10 años" no.
    #
    # El año se guarda igual porque es lo que publican los portales cuando
    # publican algo, y porque en la ficha se lee mejor "construido en 2018"
    # que "8 años". Uno se deriva del otro en `scoring._completar`.
    antiguedad_anos: int | None = None
    ano_construccion: int | None = None
    # "amoblado" | "sin amoblar" | "". El vacío es honesto: la mayoría de los
    # avisos no lo dice, y suponerlo cambia el precio esperado en 30%.
    amoblado: str = ""
    mascotas: str = ""               # "acepta" | "no acepta" | ""

    # --- economía ---
    # El canon mensual. En CLP porque así se publica el arriendo en Chile; la
    # UF existe pero es minoría, y cuando viene en UF se convierte y se deja
    # anotado de dónde salió.
    arriendo_clp: float | None = None
    arriendo_uf: float | None = None
    gastos_comunes_clp: float | None = None
    # Cuánto pide de garantía, en meses. Dato de decisión: dos meses de
    # garantía sobre 1,6 millones son 3,2 millones al firmar.
    garantia_meses: float | None = None

    # --- proceso ---
    disponible_desde: date | None = None
    publicado_el: date | None = None
    corredora: str = ""

    # --- trazabilidad ---
    raw_text: str = ""               # texto crudo del que se extrajo todo
    scraped_at: datetime = field(default_factory=ahora_utc)
    extras: dict[str, Any] = field(default_factory=dict)

    # --- resultado del scoring (lo llena scoring.py) ---
    score: int = 0
    razones: list[str] = field(default_factory=list)
    distancia_km: float | None = None
    # Descarte duro. `descartado` es el veredicto y `motivo_descarte` la
    # explicación en una línea; `clase_descarte` es la misma cosa en una
    # palabra, para poder contar por qué se cae el inventario sin leer prosa.
    descartado: bool = False
    motivo_descarte: str = ""
    clase_descarte: str = ""

    # ------------------------------------------------------------------
    @property
    def costo_mensual(self) -> float | None:
        """Lo que de verdad se paga al mes: canon más gastos comunes.

        Es el número con el que se decide y casi ningún portal lo muestra. Un
        departamento de $1.500.000 con $380.000 de gastos comunes cuesta más
        que uno de $1.750.000 con $120.000, y mirando solo el canon el orden
        sale al revés.

        Devuelve None si no hay canon. Si hay canon y no hay gastos comunes
        devuelve el canon solo —no se inventa un promedio— y quien lo use
        tiene que mirar `gastos_comunes_clp` para saber si el número está
        completo.
        """
        if self.arriendo_clp is None:
            return None
        return self.arriendo_clp + (self.gastos_comunes_clp or 0)

    @property
    def gastos_comunes_pct(self) -> float | None:
        """Gastos comunes como % del canon. None si falta cualquiera de los dos."""
        if not self.arriendo_clp or self.gastos_comunes_clp is None:
            return None
        return round(100 * self.gastos_comunes_clp / self.arriendo_clp, 1)

    @property
    def m2_referencia(self) -> float | None:
        """La superficie con la que se filtra, y de dónde salió.

        El filtro duro es sobre m² TOTALES. Cuando el aviso solo publica la
        útil hay que decidir, y la geometría decide sola: la total nunca es
        menor que la útil, así que una útil de 118 m² garantiza una total de
        al menos 118. Ahí se puede usar la útil y el filtro sigue siendo
        correcto.

        Al revés no funciona —una útil de 92 m² no dice nada sobre la total,
        que puede ser 105 con terrazas— y por eso `scoring` trata ese caso
        como dato ausente en vez de como incumplimiento.
        """
        if self.m2_totales is not None:
            return self.m2_totales
        return self.m2_utiles

    @property
    def dias_publicado(self) -> int | None:
        """Cuántos días lleva publicado. Es la palanca de negociación.

        Acepta la fecha como texto además de como `date`, y no es cosmético:
        al guardarse en JSON la fecha se vuelve un string, así que cualquier
        `Arriendo` reconstruido desde el estado —o desde el historial— llegaba
        acá con un `str` y reventaba con un TypeError. Y reventaba en medio de
        `evaluar`, o sea que se llevaba la corrida entera por un dato que
        además es opcional.
        """
        fecha = self.publicado_el
        if not fecha:
            return None
        if isinstance(fecha, str):
            try:
                fecha = date.fromisoformat(fecha[:10])
            except ValueError:
                return None
        if isinstance(fecha, datetime):
            fecha = fecha.date()
        return (ahora_utc().date() - fecha).days

    # Alfabeto sin caracteres ambiguos (sin 0/O, 1/I/L): el código se dicta
    # por teléfono y se escribe a mano.
    _ALFABETO_CODIGO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

    @property
    def codigo(self) -> str:
        """Código corto y estable del departamento, p. ej. "#K9D2M".

        Existe porque el mismo departamento aparece en varios portales y en
        varias corridas, y hacía falta una forma de NOMBRARLO: "me gustó el
        K9D2M" en vez de "el tercero de ayer, el de la Leticia no sé cuánto".
        Deriva del fingerprint, así que las copias que la deduplicación logra
        juntar comparten código — y si dos mensajes distintos muestran el
        mismo código, son el mismo departamento aunque el radar no haya
        podido fusionarlos a tiempo.
        """
        n = int(self.fingerprint[:12], 16)
        letras = []
        for _ in range(5):
            letras.append(self._ALFABETO_CODIGO[n % 31])
            n //= 31
        return "".join(letras)

    @property
    def fingerprint(self) -> str:
        """Identidad estable para deduplicar entre portales y entre corridas.

        Se prefiere la dirección normalizada por sobre la URL, y acá eso es lo
        único que funciona: el mismo departamento está publicado por la
        corredora, por dos portales que le sindican el aviso y por el dueño,
        con cuatro URLs y cuatro títulos distintos.

        Cuando el aviso trae el número del departamento, ese número ENTRA en
        la llave. Es la diferencia con un remate: en un edificio de Vitacura
        se arriendan tres departamentos distintos de la misma torre a la vez,
        y colapsarlos en uno haría perder dos.

        El precio NO entra. Un portal publica $1.550.000 y otro "desde
        1.55 millones" por el mismo departamento, y una baja de canon tiene
        que reconocerse como el mismo aviso más barato —que es la señal que
        interesa— y no como uno nuevo.
        """
        base = clave_direccion(self.direccion, self.comuna)
        # Solo una dirección CON ALTURA puede ser la identidad. "Las Nieves"
        # sin número no es un edificio: es una calle con decenas, y usarla de
        # identidad fundió siete departamentos distintos —de 3D/248 m² a
        # 5D/380 m²— en un registro el 20-08. Sin altura se cae a la URL, que
        # identifica la PUBLICACIÓN; juntar publicaciones de la misma calle
        # que además comparten el canon exacto es trabajo de `deduplicar`,
        # que sí puede exigir esa corroboración. El precio sigue fuera de la
        # identidad, así que una baja de canon se reconoce igual.
        if base and self.comuna and re.search(r"(?<!\d)\d{3,}", base):
            unidad = _normalize_key(self.extras.get("unidad", "") or "")
            key = f"{base}|{_normalize_key(self.comuna)}"
            if unidad:
                key += f"|{unidad}"
            return hashlib.sha1(key.encode()).hexdigest()[:16]

        # Sin dirección utilizable se cae a la URL, pero la URL sola no basta:
        # un listado paginado la comparte entre varias tarjetas. El texto de
        # la tarjeta es lo que las distingue.
        detalle = _normalize_key(self.title or self.raw_text)[:120]
        return hashlib.sha1(f"{self.url}|{detalle}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scraped_at"] = self.scraped_at.isoformat()
        for campo in ("disponible_desde", "publicado_el"):
            valor = getattr(self, campo)
            d[campo] = valor.isoformat() if valor else None
        d["fingerprint"] = self.fingerprint
        d["costo_mensual"] = self.costo_mensual
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Arriendo":
        d = dict(d)
        for calculado in ("fingerprint", "costo_mensual", "gastos_comunes_pct",
                          "m2_referencia", "dias_publicado"):
            d.pop(calculado, None)
        if isinstance(d.get("scraped_at"), str):
            d["scraped_at"] = datetime.fromisoformat(d["scraped_at"])
        for campo in ("disponible_desde", "publicado_el"):
            if isinstance(d.get(campo), str):
                d[campo] = date.fromisoformat(d[campo])
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Normalización de direcciones
#
# Todo lo que sigue viene del radar de remates, calibrado contra duplicados
# reales. Cada limpieza tiene un caso que la justifica y por eso se conservan
# los ejemplos: sin ellos, la próxima persona que lea esto va a "simplificar"
# alguna y a reintroducir un duplicado que ya se había arreglado.
# ---------------------------------------------------------------------------

_ACCENTS = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def _normalize_key(s: str) -> str:
    """Normaliza texto para comparación: sin tildes, sin puntuación, compacto.

    'Av. Lo Beltrán #2.500, Depto 41-B' -> 'av lo beltran 2500 depto 41 b'
    """
    s = (s or "").translate(_ACCENTS).lower()
    # El separador de miles se elimina ANTES que la puntuación: si no,
    # '#2.340' quedaría como '2 340' y no calzaría con '2340' de otra fuente.
    s = re.sub(r"(?<=\d)[.,](?=\d)", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# "Nº", "N°", "No.", "#": marcas del número de la calle. Cada portal usa la
# suya para la misma dirección, así que sobran en una llave de identidad.
# El "º" hay que nombrarlo: Python lo considera \w y sobrevive a la limpieza
# de puntuación de _normalize_key.
_MARCA_NUMERO = re.compile(r"\b(?:n[º°o]?|nro|num(?:ero)?)\s*(?=\d)", re.I)

# Cada portal abrevia distinto la misma avenida. Se llevan todas a una forma.
_TIPOS_VIA = {
    "avenida": "av", "avda": "av", "avd": "av", "av": "av",
    "pasaje": "psje", "psje": "psje", "pje": "psje",
    "callejon": "callejon", "calle": "calle", "camino": "camino",
}


def _sin_via_inicial(clave: str) -> str:
    """Saca el tipo de vía del principio: no identifica nada.

    Un portal escribe "Calle Alonso de Córdova 4200" y otro "Alonso de
    Córdova 4200". Canonizar la palabra no alcanza —"calle x 4200" sigue
    siendo distinto de "x 4200"— y el mismo departamento alerta dos veces.

    El riesgo asumido: dos calles del mismo nombre y número que solo se
    distingan por el tipo de vía quedarían con la misma llave. Con la comuna
    también en la llave, eso no existe en la zona del perfil.
    """
    partes = clave.split()
    return " ".join(partes[1:]) if partes and partes[0] in _TIPOS_VIA else clave


# La cola administrativa que algunos portales pegan y otros no: "…4200,
# Vitacura, Región Metropolitana" frente a "…Nº 4200". No aporta identidad
# —la comuna ya va aparte en el fingerprint— y sin sacarla la misma
# propiedad no calza entre fuentes.
_COLA_ADMINISTRATIVA = re.compile(r"\b(?:comuna|region|provincia|ciudad)\b.*$")


def clave_direccion(direccion: str, comuna: str = "") -> str:
    """Llave de comparación de direcciones entre portales distintos.

    Cuatro limpiezas, todas con un duplicado real detrás:

        'Alonso de Córdova Nº 4200'          marca del número
        'Alonso De Cordova Vitacura 4200'    comuna intercalada
        'Calle Alonso de Cordova 4200'       tipo de vía al principio
        'Alonso de Córdova 4200, Vitacura,   cola administrativa
         Región Metropolitana'

    Las cuatro son el mismo edificio y sin limpiar son cuatro llaves distintas.
    """
    clave = _normalize_key(direccion)
    if not clave:
        return ""

    clave = _MARCA_NUMERO.sub("", clave)
    sin_cola = _COLA_ADMINISTRATIVA.sub("", clave).strip()
    # Salvo que la dirección SEA la cola: "comuna de Vitacura" a secas es lo
    # único que trae algún aviso, y dejarla vacía la volvería inidentificable.
    if sin_cola:
        clave = sin_cola
    clave = _sin_via_inicial(clave)

    # Una "dirección" que es SOLO el nombre de la comuna no identifica nada, y
    # usarla de llave es catastrófico: en la corrida real del 17-08, 37 avisos
    # DISTINTOS de GoPlaceIt y Top Propiedades cuya dirección quedó como
    # "Vitacura" a secas se fusionaron en un solo registro — 37 departamentos
    # que no tienen nada que ver, colapsados en uno, con un solo mensaje y
    # 36 perdidos. Peor que no deduplicar, por mucho.
    if _normalize_key(comuna) and clave.strip() == _normalize_key(comuna).strip():
        return ""

    c = _normalize_key(comuna)
    if c:
        # La comuna al FINAL es igual de prescindible que la región: sobra en
        # "Alonso de Córdova 4200 Vitacura" y falta en "Alonso de Córdova Nº
        # 4200", que es el mismo edificio. Se pide que quede el número de la
        # calle: sin él lo que sobrevive no es una dirección.
        sin_final = re.sub(rf"\s+\b{re.escape(c)}\b$", "", clave)
        if sin_final != clave and re.search(r"\d", sin_final):
            clave = sin_final

        sin_comuna = re.sub(rf"\b{re.escape(c)}\b\s+(?=\d)", "", clave)
        # Solo si queda un nombre de calle de verdad. En 'Av. Vitacura 5480'
        # la comuna ES la calle, y sacarla dejaría '5480', que calzaría con
        # cualquier otra dirección del mismo número.
        nombre = [p for p in sin_comuna.split()
                  if p not in _TIPOS_VIA and not p.isdigit()]
        if nombre:
            clave = sin_comuna

    clave = re.sub(r"\s+", " ", clave).strip()

    # Última defensa, y la puso una corrida real: el 20-08 la llave
    # "vitacura metropolitana" —lo único que toctoc pone en el campo
    # dirección de la mitad de sus avisos— fundió CINCUENTA Y TRES
    # departamentos distintos en un solo registro. Igual "bedrooms 2"
    # (engelvoelkers publica sus specs en inglés) y "vitacura 2".
    #
    # El guardia de "la dirección ES la comuna" de más arriba no las
    # atrapaba porque traen una palabra más. La regla correcta no es
    # comparar con la comuna: es exigir que quede ALGO que pueda ser el
    # nombre de una calle — y ni la región, ni el país, ni una
    # característica del departamento lo son.
    # Se saca también el nombre de la comuna: "Vitacura, Metropolitana" no
    # deja nada, pero "Av. Vitacura 5480" sí es una calle de verdad. Lo que
    # las separa es la ALTURA: las numeraciones chilenas de estas comunas
    # tienen tres dígitos o más, así que un "Vitacura 2" suelto no es una
    # dirección y un "Vitacura 5480" sí.
    # La comuna del aviso, y TODAS las comunas conocidas. Que se ignore solo
    # la propia deja un agujero por el que ya se coló la peor fusión del
    # radar: cuando el aviso llega sin comuna —y llegan muchos— "Vitacura,
    # Región Metropolitana" conservaba la palabra "Vitacura" como si fuera un
    # nombre de calle, y esa llave junta todo lo que no tiene dirección. El
    # 17-08 fundió 37 departamentos distintos en un registro; el 20-08, 53.
    #
    # Una comuna es una comuna la traiga o no el aviso. Lo que salva a
    # "Av. Vitacura 5480" es la ALTURA, que se comprueba más abajo.
    from .parse import COMUNAS_CONOCIDAS
    c = _normalize_key(comuna)
    ignorables = (_NO_SON_CALLE | _CONECTORES | set(c.split())
                  | {p for x in COMUNAS_CONOCIDAS
                     for p in _normalize_key(x).split()})
    palabras = [p for p in clave.split()
                if p not in ignorables and not p.isdigit()]
    if not palabras and not re.search(r"(?<!\d)\d{3,}", clave):
        return ""

    # Y una palabra que jamás es calle descalifica la llave entera, tenga la
    # altura que tenga: "Edificio de 18" reduce a "edificio de 18" y sobrevive
    # a la regla de arriba porque "edificio" es una palabra como cualquier
    # otra. El guardia vive ACÁ y no solo en el extractor porque el extractor
    # no es el único que escribe direcciones: la memoria del store le devuelve
    # a cada aviso lo que ya sabía de él, así que sin esto cada mejora del
    # extractor se deshacía sola en la corrida siguiente — el aviso llegaba
    # limpio y la memoria le devolvía la misma basura, con la firma de un dato
    # aprendido.
    if any(p in NUNCA_EN_UNA_CALLE for p in clave.split()):
        return ""

    # Un número de uno o dos dígitos como ÚNICA cifra no es una altura: las
    # numeraciones de estas comunas no bajan de 100. "Dropdown Productos 1"
    # (un menú de la página) y "PRINCIPAL EN SUITE CON 2" (un pedazo de la
    # descripción) llegaron así al tablero. Una dirección SIN cifras sigue
    # valiendo —"Candelaria Goyenechea, Lo Castillo" ubica en el mapa aunque
    # no identifique el edificio—; lo que no vale es una cifra que finge ser
    # altura y no puede serlo.
    if re.search(r"\d", clave) and not re.search(r"(?<!\d)\d{3,}", clave):
        return ""
    return clave


# El encabezado administrativo con el que algunos portales arman su campo
# dirección: la comuna, su ID interno y la región, ANTES de la calle. toctoc
# publicaba así cuatro direcciones del 21-08 —"Vitacura 312 Metropolitana
# Juan XXIII 6859 301"— y el 312 (que es el ID de la comuna, no una altura)
# se llevaba a "Vitacura" como nombre de calle. La dirección real, Juan
# XXIII 6859, quedaba de relleno y el aviso sin edificio identificable.
#
# Lo que delata al prefijo es la secuencia entera: comuna conocida, un
# número, y una región. Ninguna calle chilena tiene una región en medio.
_PREFIJO_ADMINISTRATIVO = re.compile(
    r"^\s*[\w\s]{3,24}?\s+\d{1,5}\s+(?:regi[oó]n\s+(?:de\s+)?)?"
    r"(?:metropolitana|de\s+santiago)\b[\s,.\-]*", re.I)


def sin_encabezado_administrativo(direccion: str) -> str:
    """La dirección sin el "<comuna> <id> <región>" que algunos le anteponen."""
    d = (direccion or "").strip()
    m = _PREFIJO_ADMINISTRATIVO.match(d)
    if not m:
        return d
    # Solo si lo que abre es de verdad una comuna: "Avenida Vitacura 312,
    # Metropolitana" es una dirección completa y correcta, y el prefijo se le
    # parece. La diferencia es que ahí la comuna NO abre la frase.
    from .parse import COMUNAS_CONOCIDAS, norm
    cabeza = norm(d[:m.end()]).split()
    conocidas = {norm(c) for c in COMUNAS_CONOCIDAS}
    for largo in (3, 2, 1):
        if " ".join(cabeza[:largo]) in conocidas:
            return d[m.end():].strip(" ,.-")
    return d


def limpiar_direccion(direccion: str, comuna: str = "") -> str:
    """La dirección lista para mostrar, o "" si no identifica ningún lugar.

    Es el único lugar donde se decide si una dirección sirve, y vive acá —y
    no en el extractor— porque el extractor no es el único que las escribe:
    la memoria del store le devuelve a cada aviso lo que ya sabía de él, así
    que una limpieza que vive en un solo lado se deshace desde el otro. La
    corrida del 21-08 19:13 lo mostró entero: el extractor ya rechazaba
    "Vitacura 312 Metropolitana Avda. Presidente Kennedy", el aviso llegaba
    limpio, y la memoria se lo devolvía igual que siempre.

    Si no sirve para identificar, tampoco se muestra ni se manda a Google
    Maps: una dirección inventada en el mapa es peor que un aviso sin pin.
    """
    d = sin_encabezado_administrativo(direccion)
    if not d or not clave_direccion(d, comuna):
        return ""
    # Y la COLA administrativa se recorta también de lo que se muestra, no
    # solo de la llave: houm publica "Aníbal Pinto, Region Metropolitana" y
    # mitula "…, Vitacura, Provincia de Santiago, Región Metropolitana de
    # Santiago, 7630574, CHL". El link a Google Maps agrega la comuna y el
    # país por su cuenta, así que la cola solo duplicaba —"…Vitacura,
    # Vitacura, Chile"— y alargaba la fila de la tabla. Se corta en la coma
    # ANTERIOR a la palabra administrativa: la cola siempre llega como
    # elemento propio de la lista, nunca en medio del nombre de la calle.
    # Dos tijeras, gana la que corte antes: una palabra administrativa corta
    # DESDE ella ("…, Region Metropolitana" se va entera), y la primera
    # comuna conocida corta DESPUÉS de ella — lo que sigue a la comuna solo
    # puede ser más administración ("…, Vitacura, Chile, Metropolitana de
    # Santiago", "…, Vitacura, Santiago, 7630571, CHL").
    from .parse import COMUNAS_CONOCIDAS
    conocidas = {_normalize_key(c) for c in COMUNAS_CONOCIDAS}
    partes = [p.strip() for p in d.split(",")]
    corte = len(partes)
    for i, parte in enumerate(partes[1:], start=1):
        clave = _normalize_key(parte)
        if _COLA_ADMINISTRATIVA.match(clave):
            corte = i
            break
        if clave in conocidas:
            corte = i + 1
            break
    recortada = ", ".join(partes[:corte])
    # Solo si lo que queda sigue identificando; si el recorte se lo lleva
    # todo (la dirección ERA la cola), se prefiere la original completa.
    return recortada if recortada and clave_direccion(recortada, comuna) else d


# Palabras que jamás son, por sí solas, el nombre de una calle: la cola
# administrativa (región, provincia, país) y las specs que algunos portales
# meten en el campo dirección, en castellano y en inglés.
_CONECTORES = frozenset("de del la las el los y en".split())

# Palabras que NINGUNA calle chilena tiene en su nombre. Salieron una por
# una de auditar las 163 direcciones que el radar tenía guardadas el 21-08:
# "Edificio de 18", "GAS CON HORNO DE 4", "POCOS DEPARTAMENTOS SOLO 4",
# "Consta de 5", "DUPLEX CON VISTAS DESPEJADAS 120", "Antigüedad: 30",
# "Útiles. Dormitorios: 3", "Cava. 2", "Quinchos 2", "Propiedad Comercial de
# 2", "ID 44348", "Meson... Mapa FOIX REALTY 100". Todas tienen la misma
# forma: una frase del aviso con un número al final que pasa por altura, y
# palabras con mayúscula que pasan por nombre de calle.
#
# Una sola de estas palabras descalifica la dirección entera, porque no hay
# tal cosa como una calle "Edificio" ni una calle "Dormitorios". La lista es
# deliberadamente conservadora: quedan FUERA las que sí aparecen en
# nomenclatura real —parque (Camino El Parque), costanera, vista en
# singular, plaza, jardín— aunque también aparezcan en avisos malos. Perder
# una dirección buena es peor que dejar pasar una mala: la buena identifica
# el edificio y es la llave con la que se fusionan las publicaciones.
NUNCA_EN_UNA_CALLE = frozenset("""
    edificio edificios departamento departamentos depto deptos duplex dúplex
    penthouse loft consta cuenta dispone incluye gas horno cocina living
    comedor terraza terrazas logia quincho quinchos cava bodega bodegas
    conserjeria conserjería estacionamiento estacionamientos ascensor
    ascensores piscina gimnasio sauna
    dormitorio dormitorios pieza piezas habitacion habitación habitaciones
    bano baño banos baños bedrooms bathrooms
    antiguedad antigüedad superficie terreno util útil utiles útiles
    metros mts m2 uf clp arriendo arriendos venta ventas precio canon
    id cod codigo código rol
    pocos poco solo sólo únicamente partir pasos apenas quedan
    amoblado amoblada remodelado remodelada impecable espectacular hermoso
    hermosa luminoso luminosa exclusivo exclusiva moderno moderna amplio
    amplia acogedor acogedora
    mapa realty propiedad propiedades inmobiliaria corredora broker
    comercial vistas despejado despejada despejados despejadas
""".split())

_NO_SON_CALLE = frozenset("""
metropolitana region regiones rm provincia santiago chile chl cl comuna
bedrooms bathrooms rooms beds baths dormitorios dormitorio banos bano
habitaciones habitacion piezas pieza m2 mts mt sqm parking parks
estacionamientos estacionamiento bodega bodegas piso pisos depto
departamento apartamento casa uf clp precio
""".split())
