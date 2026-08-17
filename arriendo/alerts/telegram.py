"""Alertas por Telegram.

El aviso está pensado para leerse en la pantalla de bloqueo, y esa restricción
es la que decide qué entra y qué no. Un mensaje de treinta líneas en un
teléfono no es un aviso: es un documento que hay que scrollear para saber si
vale la pena mirarlo, y eso lo convierte en algo que se ignora.

Acá queda solo lo que decide **si abrir o no**: qué es, dónde queda, cuánto
cuesta de verdad, qué puntúa y qué falta. Todo lo demás —el desglose del
puntaje, los otros portales donde está publicado, las preguntas que hay que
hacer antes de ir a verlo— vive en la ficha, y la última línea es el link.

Configuración, por variables de entorno (secrets del repositorio):

    TELEGRAM_TOKEN_ARRIENDOS     el que entrega @BotFather al crear el bot
    TELEGRAM_CHAT_ID_ARRIENDOS   a qué conversación mandar

Sin ninguna de las dos, el canal se apaga solo y lo dice en el log.

Por qué los nombres llevan `_ARRIENDOS` y no son los genéricos
------------------------------------------------------------

Este radar es hermano del **radar de remates** (repo `claude-code`), que usa
`TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` a secas. Son dos productos distintos que
avisan cosas distintas, y la decisión fue explícita: **otro bot, otro token.**

Los secrets de GitHub son por repositorio, así que en el caso normal no habría
choque. Los nombres distintos protegen los otros casos, que son los que
muerden: un secret a nivel de organización, un `.env` local compartido, o
copiar el workflow de un repo al otro. En cualquiera de esos, con el nombre
genérico los avisos de arriendo saldrían por el bot de remates sin que nada
avisara.

Y **no hay fallback al nombre genérico**, a propósito. Si alguien configura
`TELEGRAM_TOKEN` por costumbre, este radar no manda nada y lo dice en el log
nombrando la variable correcta. Ese es el fallo bueno: ruidoso y con el
arreglo escrito. El fallo malo sería mandar los arriendos por el bot
equivocado, que se ve igual que funcionar bien.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Any

import requests

from .. import scoring as S
from ..models import Arriendo

log = logging.getLogger(__name__)

API = "https://api.telegram.org"

# Los nombres de los secrets. Van con sufijo propio para no cruzarse con los
# del radar de remates: ver el docstring del módulo.
VAR_TOKEN = "TELEGRAM_TOKEN_ARRIENDOS"
VAR_CHAT_ID = "TELEGRAM_CHAT_ID_ARRIENDOS"

# El nombre genérico que usa el radar de remates. No se lee nunca; solo sirve
# para poder decirle a alguien que configuró el que no era.
VAR_GENERICA = "TELEGRAM_TOKEN"

# Telegram corta en 4096 caracteres y devuelve error si se pasa. Se recorta
# antes con margen: perder el final de una descripción es aceptable, perder el
# aviso entero no.
MAX_MENSAJE = 3800

# Minutos a pie por kilómetro.
MIN_POR_KM = 12


class Telegram:
    def __init__(self, token: str = "", chat_id: str = "", dry_run: bool = False,
                 caminable_km: float = 0.0, ancla: str = "",
                 tope_arriendo: float = 0.0, mediana_mercado: float = 0.0,
                 gc_tipico=None):
        self.token = token or os.environ.get(VAR_TOKEN, "")
        self.chat_id = chat_id or os.environ.get(VAR_CHAT_ID, "")
        self.dry_run = dry_run
        # Para poder decir "7 min caminando" en vez de "0,61 km", que es el
        # dato con el que se decide si vale la pena ir a verlo.
        self.caminable_km = caminable_km
        self.ancla = ancla
        # El tope del perfil, para poder marcar el aviso que se pasa. Sin él,
        # un arriendo de $1.690.000 se ve igual que uno de $1.450.000 y el
        # usuario descubre que se pasó del presupuesto recién al abrirlo.
        self.tope_arriendo = tope_arriendo
        # El canon mediano de lo que el radar ha visto en la zona, para poder
        # decir "12% bajo la mediana" en vez de solo el precio. Sale del
        # historial de búsquedas; en 0 la línea simplemente no se escribe.
        self.mediana_mercado = mediana_mercado
        # Estimador de gastos comunes: recibe los m² y devuelve un monto
        # típico de la zona, o None. Sale del historial de búsquedas; sin
        # datos suficientes queda en None y el mensaje dice lo de siempre.
        self.gc_tipico = gc_tipico
        self.s = requests.Session()

    @property
    def configurado(self) -> bool:
        return bool(self.token and self.chat_id)

    # ------------------------------------------------------------------
    def enviar(self, texto: str) -> bool:
        """Manda un mensaje. Devuelve si Telegram confirmó la entrega.

        Devolver el resultado y no None es el punto: acá se puede saber en el
        momento si el aviso llegó. Un radar que encuentra la propiedad y no
        consigue avisar se ve, desde afuera, exactamente igual que uno que no
        encontró nada.
        """
        if self.dry_run:
            print("\n" + "=" * 70)
            print("[DRY-RUN] Telegram:")
            print(texto)
            return True

        if not self.configurado:
            log.info("Telegram sin configurar (faltan %s o %s): no se manda "
                     "nada. El paso a paso está en AVISOS.md.",
                     VAR_TOKEN, VAR_CHAT_ID)
            if os.environ.get(VAR_GENERICA):
                # El error más probable, y el más difícil de ver: se copió la
                # configuración del radar de remates. Decirlo con nombre y
                # apellido ahorra la media hora de revisar todo lo demás.
                log.warning(
                    "Ojo: existe %s pero este radar NO la usa. Es un bot "
                    "distinto del radar de remates, a propósito. Crea un bot "
                    "nuevo con @BotFather y guárdalo como %s.",
                    VAR_GENERICA, VAR_TOKEN)
            return False

        try:
            r = self.s.post(
                f"{API}/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": texto[:MAX_MENSAJE],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
            if r.status_code == 200 and r.json().get("ok"):
                return True
            # El cuerpo de Telegram dice exactamente qué pasa —"chat not
            # found", "bot was blocked by the user"—, y esa frase es la
            # diferencia entre arreglarlo y adivinar.
            log.error("Telegram rechazó el mensaje (%s): %s",
                      r.status_code, r.text[:300])
        except requests.RequestException as e:
            log.error("Error de red hablando con Telegram: %s", e)
        return False

    # ------------------------------------------------------------------
    def alertar(self, a: Arriendo, motivo: str = "") -> bool:
        return self.enviar(_mensaje(a, motivo, self.caminable_km, self.ancla,
                                    self.tope_arriendo, self.mediana_mercado,
                                    self.gc_tipico))

    def resumen(self, stats: dict[str, Any], alertas: int,
                marca_dir: Any = None) -> None:
        """Avisa cuando el silencio dejaría de significar algo.

        No manda un mensaje por corrida. Un aviso que llega dos veces al día y
        nunca dice nada enseña a ignorarlo, y entonces el que importa también
        se ignora.

        Pero callar SIEMPRE tampoco sirve: desde el lado del usuario, cinco
        corridas sin novedad se ven idénticas a un radar caído. Así que hay
        exactamente dos motivos para hablar sin una propiedad que mostrar, y
        los dos son informativos: algo se rompió, o pasó una semana.
        """
        if alertas:
            return  # ya se avisó propiedad por propiedad

        problema = _que_se_rompio(stats)
        if problema:
            self.enviar(problema)
            _marcar_aviso(marca_dir)
            return

        if _toca_latido(marca_dir):
            self.enviar(_latido(stats))
            _marcar_aviso(marca_dir)


# ---------------------------------------------------------------------------
# El mensaje
# ---------------------------------------------------------------------------

def _escapar(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pesos(valor: float) -> str:
    """A la chilena: punto para los miles."""
    return "$" + f"{valor:,.0f}".replace(",", ".")


def _numero(valor: float, decimales: int = 0) -> str:
    crudo = f"{valor:,.{decimales}f}"
    return crudo.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _sin_comuna(direccion: str, comuna: str) -> str:
    """Saca la cola ", Vitacura, Región Metropolitana" del título.

    La comuna va en su propia línea, y repetida dentro del título se come el
    ancho de una pantalla de teléfono sin agregar nada.
    """
    texto = (direccion or "").strip()
    if not comuna:
        return texto
    recortado = re.split(rf",\s*{re.escape(comuna)}\b", texto, maxsplit=1,
                         flags=re.I)[0]
    return recortado.strip(" ,") or texto


# Dónde termina el nombre útil de un aviso y empieza el ruido: el precio, la
# moneda y la coletilla del portal.
#
# Hace falta cuando el aviso no publica dirección con altura. Yapo titula
# "Departamento en Luis Carrera 3 Dormitorios por CLP 1600000.00 Arriendo de
# Departamentos en Vitacura", y eso recortado a 90 caracteres es una línea
# ilegible en la pantalla de bloqueo — con el precio repetido, además, porque
# ya va en su propia línea.
_COLA_DEL_TITULO = re.compile(
    r"\s+(?:por\s+)?(?:CLP|CLF|UF|\$)\s*[\d.,]+.*$"
    r"|\s+Arriendo\s+de\s+(?:Departamentos?|Casas?)\b.*$"
    r"|\s*[-—|·]\s*(?:Arriendo|Venta)\b.*$",
    re.I)


def titulo_corto(a: Arriendo) -> str:
    """Cómo llamar a este departamento en una línea.

    Se prefiere la dirección al título del aviso: los títulos que escriben los
    portales son marketing —"Espectacular depto con vista panorámica"— y la
    dirección es lo que permite reconocerlo y compararlo.
    """
    if a.direccion:
        base = _sin_comuna(a.direccion, a.comuna)
        if (unidad := a.extras.get("unidad")):
            base = f"{base}, depto {unidad}"
        return base[:90]

    # Sin dirección hay que usar el título del aviso, pero limpio: el precio y
    # la coletilla del portal ocupan la mitad de la línea y no identifican
    # nada.
    titulo = _COLA_DEL_TITULO.sub("", (a.title or "").strip()).strip(" ,.-–—")
    return (titulo or a.title or "Aviso sin título")[:90]


def _ubicacion(a: Arriendo, caminable_km: float, ancla: str) -> str:
    """Dónde queda, dicho como se decide: en minutos caminando.

    "0,61 km del Sport Francés" obliga a hacer la cuenta. Acá ya está hecha.
    """
    partes = [_escapar(a.comuna)] if a.comuna else []

    if a.distancia_km is None:
        partes.append("sin ubicar en el mapa")
        return " · ".join(partes)

    km = f"{_numero(a.distancia_km, 1)} km"
    if caminable_km and a.distancia_km <= caminable_km:
        minutos = max(1, round(a.distancia_km * MIN_POR_KM))
        partes.append(f"{km} — {minutos} min caminando 🚶")
    elif ancla:
        partes.append(f"a {km} del {_escapar(ancla)}")
    else:
        partes.append(f"a {km} del ancla")
    return " · ".join(partes)


# Los campos con los que se decide, en el orden en que se echan de menos.
_DECISIVOS = (
    ("m2_totales", "superficie total"),
    ("antiguedad_anos", "antigüedad"),
    ("gastos_comunes_clp", "gastos comunes"),
    ("banos", "baños"),
)


def _falta(a: Arriendo) -> str:
    """Qué no se sabe todavía.

    Es la diferencia entre "este departamento no sirve" y "no sabemos si
    sirve", y sin decirlo el puntaje bajo se lee siempre como lo primero.
    """
    faltan = [nombre for campo, nombre in _DECISIVOS
              if getattr(a, campo, None) is None]
    return ", ".join(faltan[:3])


def _mensaje(a: Arriendo, motivo: str = "", caminable_km: float = 0.0,
             ancla: str = "", tope_arriendo: float = 0.0,
             mediana_mercado: float = 0.0, gc_tipico=None) -> str:
    """El aviso, escrito para decidir en tres segundos.

    El orden de las líneas es el diseño, y está pensado para cómo se lee de
    verdad un Telegram: la notificación muestra las dos o tres primeras líneas
    en la pantalla de bloqueo, y con eso ya se decide si vale la pena abrirlo.

    Antes el veredicto iba ABAJO, después de siete líneas de datos. O sea que
    lo único que resume todo lo demás quedaba justo fuera de lo que se alcanza
    a ver. Ahora el mensaje va de la conclusión al detalle:

        1. El veredicto     🔥 88 · Anda a verlo
        2. Qué es y dónde   Alonso de Córdova 4200 · Vitacura
        3. Cuánto           con el total y la comparación con el mercado
        4. Cómo es          superficie, programa, edad
        5. Cuándo           disponibilidad y cuánto lleva publicado
        6. Por qué este     lo mejor y lo que hay que preguntar
        7. Dónde verlo      el link, y los otros portales que lo publican

    Cada bloque se salta entero si no hay nada que decir: un mensaje con
    huecos rellenos de guiones enseña a no leerlo.
    """
    L: list[str] = []

    # ---- 1. el veredicto, arriba de todo -------------------------------
    #
    # La banda hace el trabajo que el número solo no puede: "88" no significa
    # nada sin otro con qué compararlo, "88 · Anda a verlo" sí. Y la confianza
    # va al lado porque un 88 sostenido por dos criterios y uno sostenido por
    # cinco piden cosas distintas — el primero, preguntar antes de ir.
    emoji, nombre = S.banda(a.score)
    confianza = a.extras.get("confianza")
    cabecera = f"{emoji} <b>{a.score}</b> · {nombre}"
    if isinstance(confianza, int) and confianza < 100:
        cabecera += f"  <i>({confianza}% de los datos)</i>"
    L.append(cabecera)

    # ---- 2. qué es y dónde ---------------------------------------------
    L.append(f"🏠 <b>{_escapar(titulo_corto(a))}</b>")
    L.append(f"📍 {_ubicacion(a, caminable_km, ancla)}")
    L.append("")

    # ---- 3. la plata ----------------------------------------------------
    #
    # Lleva el costo TOTAL al lado del canon: el aviso publica $1.490.000 y lo
    # que se paga son $1.670.000. Sin los dos números juntos, comparar dos
    # departamentos obliga a abrir los dos.
    if a.arriendo_clp:
        plata = f"💰 <b>{_pesos(a.arriendo_clp)}</b>"
        if a.gastos_comunes_clp:
            plata += (f" + GC {_pesos(a.gastos_comunes_clp)}"
                      f" = <b>{_pesos(a.costo_mensual)}</b>")
        else:
            # La estimación va marcada con ≈ y la palabra "típico": es un
            # dato del mercado, no de este departamento. Sin estimación
            # disponible se dice "no publicados" a secas, que es lo honesto.
            est = gc_tipico(a.m2_totales or a.m2_utiles) if gc_tipico else None
            if est:
                plata += (f"  <i>· GC no publicados, típico en la zona "
                          f"≈{_pesos(est)}</i>")
            else:
                plata += "  <i>· GC no publicados</i>"
        L.append(plata)

        # Contra el mercado, no solo contra el presupuesto. Es la línea que el
        # radar recién puede escribir desde que guarda historial: saber que
        # algo cuesta $1.490.000 no dice si es caro, saber que está 12% bajo
        # la mediana de lo que se publica en Vitacura sí.
        if (vs := _vs_mercado(a, mediana_mercado)):
            L.append(f"   {vs}")

        # "2 bajas en 62 días" dice algo que el precio de hoy no puede decir
        # solo: que el propietario no está logrando arrendar.
        if (tendencia := a.extras.get("tendencia_precio")):
            L.append(f"   📉 {_escapar(str(tendencia))}")

        # Un departamento que ya estuvo publicado y volvió no es una novedad:
        # es una oferta que no se arrendó.
        if (vuelta := _volvio(a)):
            L.append(f"   🔁 {_escapar(vuelta)}")

        # El que se pasa del tope entra a propósito —"cerca de" es parte del
        # pedido— pero tiene que decirlo, o se ve idéntico a uno que cabe.
        if tope_arriendo and a.arriendo_clp > tope_arriendo:
            sobre = a.arriendo_clp - tope_arriendo
            L.append(f"   ⚠️ {_pesos(sobre)} sobre tu tope "
                     f"({_pesos(tope_arriendo)}) — hay que negociar")
    else:
        # Sin esta rama el mensaje no trae línea de precio y se ve como un
        # olvido. Decirlo cambia qué hacer con el aviso: acá no hay nada que
        # comparar contra el presupuesto, hay que preguntar.
        L.append("💰 <b>Sin precio publicado</b> — hay que preguntar")

    # ---- 4. cómo es -----------------------------------------------------
    if (medidas := _medidas(a)):
        L.append(f"📐 {medidas}")
    if (detalles := _detalles(a)):
        L.append(f"🏗 {detalles}")

    # ---- 5. cuándo ------------------------------------------------------
    if (estado := _estado(a)):
        L.append(f"📅 {estado}")

    # ---- 6. por qué este ------------------------------------------------
    #
    # Dos líneas como máximo, y son las que convierten un puntaje en una
    # decisión. "88" dice cuánto; "lo mejor: nuevo y con margen de precio"
    # dice por qué, que es lo que se necesita para elegir entre dos.
    bueno, ojo = _lo_mejor_y_lo_peor(a)
    if bueno or ojo:
        L.append("")
    if bueno:
        L.append(f"✅ {_escapar(bueno)}")
    if ojo:
        L.append(f"⚠️ {_escapar(ojo)}")
    if (falta := _falta(a)):
        L.append(f"❓ Preguntar: {_escapar(falta)}")

    # Por qué se está reavisando algo ya avisado. Sin esto, el segundo mensaje
    # del mismo departamento se lee como un error del radar.
    if motivo:
        L.append(f"♻️ <b>{_escapar(motivo)}</b>")

    # ---- 7. dónde verlo -------------------------------------------------
    L.append("")
    L.append(f"🔗 <a href=\"{_escapar(a.url)}\">Ver en {_escapar(_portal(a))}</a>")

    # Los otros portales van como links de verdad, no como un conteo. "También
    # en 2 portal(es) más" obligaba a ir a buscarlos; además, que un
    # departamento esté en cuatro portales es señal de que lleva rato dando
    # vueltas, y eso se lee mejor con los nombres a la vista.
    if (otros := _otros_portales(a)):
        L.append(f"   <i>También en {otros}</i>")
    if (ficha := a.extras.get("ficha_url")):
        L.append(f"   <a href=\"{_escapar(str(ficha))}\">Ficha completa</a>")

    return "\n".join(L)


def _portal(a: Arriendo) -> str:
    """Cómo se llama el portal, para el texto del link.

    "Ver en TocToc" es una decisión distinta de "Ver en toctoc": el nombre
    dice qué esperar del aviso, y el id es de la configuración.
    """
    return str(a.extras.get("portal") or a.source or "el portal")


def _otros_portales(a: Arriendo) -> str:
    """Los otros portales que publican el mismo departamento, como links."""
    entradas = a.extras.get("tambien_en") or []
    partes = []
    for e in list(entradas)[:3]:
        fuente, _, url = str(e).partition("|")
        nombre = _escapar(fuente.replace("_", " ").title())
        partes.append(f"<a href=\"{_escapar(url)}\">{nombre}</a>" if url else nombre)
    if len(entradas) > 3:
        partes.append(f"y {len(entradas) - 3} más")
    return " · ".join(partes)


def _medidas(a: Arriendo) -> str:
    partes = []
    if a.m2_totales:
        partes.append(f"<b>{_numero(a.m2_totales)} m²</b>")
    elif a.m2_utiles:
        partes.append(f"<b>{_numero(a.m2_utiles)} m²</b> útiles")
    if a.dormitorios:
        partes.append(f"{a.dormitorios}D")
    if a.banos:
        partes.append(f"{a.banos}B")
    if a.estacionamientos:
        partes.append(f"{a.estacionamientos}E")
    if a.bodega:
        partes.append("bodega")
    return " · ".join(partes)


def _detalles(a: Arriendo) -> str:
    partes = []
    if a.antiguedad_anos is not None:
        partes.append(f"{a.antiguedad_anos:g} años")
    elif (techo := a.extras.get("antiguedad_techo")) is not None:
        partes.append(f"≤{techo:g} años")
    if a.piso is not None:
        # De dónde salió el piso cambia cuánto vale: el número del
        # departamento es una convención, no un dato publicado.
        aprox = " aprox." if a.extras.get("piso_origen") else ""
        partes.append(f"piso {a.piso}{aprox}")
    if a.orientacion:
        partes.append(_escapar(a.orientacion))
    if a.amoblado:
        partes.append(_escapar(a.amoblado))
    return " · ".join(partes)


def _estado(a: Arriendo) -> str:
    """Lo que cambia qué hacer HOY, no qué pensar del departamento."""
    partes = []
    if a.disponible_desde:
        if a.disponible_desde <= date.today():
            partes.append("disponible ya")
        else:
            partes.append(f"disponible {a.disponible_desde.strftime('%d-%m')}")
    dias = a.dias_publicado
    if dias is not None and dias >= 30:
        partes.append(f"{dias} días publicado — se negocia")
    if a.mascotas == "no acepta":
        partes.append("no acepta mascotas")
    return " · ".join(partes)


def _vs_mercado(a: Arriendo, mediana: float) -> str:
    """Cuánto se aparta del canon mediano de lo que el radar ha visto.

    Es la línea que convierte un precio en un juicio. $1.490.000 no dice si es
    caro; "12% bajo la mediana de Vitacura" sí, y es un número propio —sale de
    los avisos que este radar efectivamente vio— no de un índice publicado.

    Bajo el 4% de diferencia no se dice nada: "1% sobre la mediana" es ruido
    con aspecto de dato, y una línea que no aporta enseña a saltarse las que
    sí aportan.
    """
    if not mediana or not a.arriendo_clp:
        return ""
    pct = round(100 * (a.arriendo_clp - mediana) / mediana)
    if abs(pct) < 4:
        return "📊 en la mediana del mercado"
    if pct < 0:
        return f"📊 <b>{abs(pct)}% bajo</b> la mediana del mercado"
    return f"📊 {pct}% sobre la mediana del mercado"


# Cuántas razones mostrar de cada lado. Una sola se lee de un vistazo; tres
# ya es una lista y obliga a leer en vez de mirar.
_MAX_RAZONES = 2


def _lo_mejor_y_lo_peor(a: Arriendo) -> tuple[str, str]:
    """En qué criterios destaca y en cuáles flojea, por NOMBRE.

    El puntaje dice cuánto; esto dice en qué, que es lo que hace falta para
    elegir entre dos departamentos parecidos.

    Se nombran los criterios y no sus detalles, y ahí está la gracia: el
    detalle ya está en el mensaje. "Lo mejor: Vitacura · a 0,2 km · 8 años"
    repetía palabra por palabra las líneas 📍 y 🏗 que van justo arriba, y una
    línea que repite lo de arriba enseña a saltarse el bloque entero.

    Lo que falta en un mensaje de datos es la capa de juicio: cuál de los
    cinco criterios se está llevando el puntaje y cuál lo está frenando. Eso
    no se ve mirando los números, hay que compararlos contra sus pesos.

    El lado flojo importa más que el fuerte: es lo que uno descubriría después
    de manejar hasta allá.
    """
    rubros = [r for r in S.desglose(a) if r.medido and r.peso]
    if not rubros:
        return "", ""

    ordenados = sorted(rubros, key=lambda r: r.obtenido / r.peso, reverse=True)
    fuertes = [r.nombre.lower() for r in ordenados
               if r.obtenido / r.peso >= 0.85][:_MAX_RAZONES]
    flojos = [r.nombre.lower() for r in reversed(ordenados)
              if r.obtenido / r.peso <= 0.45][:_MAX_RAZONES]

    bueno = f"Fuerte en {' y '.join(fuertes)}" if fuertes else ""
    ojo = f"Flojo en {' y '.join(flojos)}" if flojos else ""
    return bueno, ojo


# ---------------------------------------------------------------------------
# El latido
#
# Vive acá y no en el orquestador porque es una decisión del CANAL: cuándo
# vale la pena interrumpir a alguien. El orquestador solo entrega los números.
# ---------------------------------------------------------------------------

DIAS_ENTRE_LATIDOS = 7
_MARCA = "ultimo_aviso.json"


def _ruta_marca(marca_dir: Any = None):
    from pathlib import Path

    from ..config import STATE_DIR

    return Path(marca_dir or STATE_DIR) / _MARCA


def _ultimo_aviso(marca_dir: Any = None) -> date | None:
    import json

    try:
        d = json.loads(_ruta_marca(marca_dir).read_text(encoding="utf-8"))
        return date.fromisoformat(d["cuando"])
    except Exception:                                            # noqa: BLE001
        # Sin marca legible se asume que toca: es preferible un mensaje de más
        # que dejar de dar señales de vida por un archivo corrupto.
        return None


def _marcar_aviso(marca_dir: Any = None) -> None:
    import json

    try:
        ruta = _ruta_marca(marca_dir)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps({"cuando": date.today().isoformat()}, ensure_ascii=False),
            encoding="utf-8")
    except OSError as e:
        log.warning("No se pudo anotar el último aviso: %s", e)


def _toca_latido(marca_dir: Any = None) -> bool:
    ultimo = _ultimo_aviso(marca_dir)
    if ultimo is None:
        return True
    return date.today() - ultimo >= timedelta(days=DIAS_ENTRE_LATIDOS)


def _que_se_rompio(stats: dict) -> str:
    """El texto del aviso si algo falló, o vacío si la corrida estuvo sana."""
    if stats.get("error"):
        return (f"⚠️ <b>El radar falló</b>\n\n{_escapar(str(stats['error'])[:400])}"
                "\n\nRevisa logs/ultima-corrida.md")

    # Ninguna fuente respondió. No es "hoy no hay arriendos": es un radar
    # ciego. Se mira aparte de las caídas porque esas se detectan comparando
    # con la corrida anterior, y la primera corrida no tiene con qué comparar
    # —que es justo cuando un apagón completo pasaría por "sin novedades"—.
    consultadas = stats.get("fuentes_consultadas", 0)
    if consultadas and not stats.get("fuentes_ok", 0):
        return (
            "⚠️ <b>El radar quedó ciego</b>\n\n"
            f"Ninguna de las {consultadas} fuentes entregó nada. "
            "Cuando fallan todas a la vez no es falta de inventario: es la "
            "corrida.\n\nRevisa logs/ultima-corrida.md"
        )

    # Cortar por tiempo no es un error, pero sí hay que decirlo: significa
    # que el radar miró una parte del mercado y no todo, y el usuario no
    # tiene cómo saberlo si no se lo dicen.
    pendientes = stats.get("corte_por_tiempo") or []
    if len(pendientes) >= 5:
        return (
            "⏱ <b>La corrida se cortó por tiempo</b>\n\n"
            f"Quedaron {len(pendientes)} fuentes sin revisar, así que esta "
            "vez el radar miró solo una parte del mercado.\n\n"
            "Se cortó a propósito, para alcanzar a avisar lo encontrado antes "
            "de que GitHub Actions matara el job.\n\n"
            "Revisa logs/ultima-corrida.md: si se repite, hay una fuente "
            "colgándose."
        )

    caidas = stats.get("fuentes_caidas") or []
    if caidas:
        lista = "\n".join(f"· {_escapar(str(c))}" for c in caidas[:8])
        return (
            "⚠️ <b>Fuentes que dejaron de entregar</b>\n\n"
            f"{lista}\n\n"
            "Venían trayendo avisos y hoy no trajeron ninguno. Puede ser que "
            "no haya inventario nuevo, o que el sitio haya cambiado."
        )
    return ""


def mensaje_bajas(bajas: list[dict]) -> str:
    """"Se arrendó": el cierre del ciclo de un aviso que se mandó.

    Solo para los que se AVISARON: de los demás nadie está esperando noticias.
    Y en un solo mensaje, no uno por departamento — la noticia de que algo ya
    no está disponible no amerita interrumpir tres veces.

    El dato que lo hace útil es "estuvo N días publicado": con unas cuantas de
    estas se aprende a qué velocidad se mueve el rango que se busca, que es lo
    que dice cuánto se puede esperar antes de decidir.
    """
    if not bajas:
        return ""
    L = ["📤 <b>Se fueron del mercado</b>", ""]
    for b in bajas[:6]:
        linea = f"· {_escapar(str(b.get('direccion') or '—')[:52])}"
        if b.get("clp"):
            linea += f" — {_pesos(b['clp'])}"
        if b.get("dias"):
            linea += f" · {b['dias']} días publicado"
        L.append(linea)
    if len(bajas) > 6:
        L.append(f"· y {len(bajas) - 6} más")
    L.append("")
    L.append("<i>Dejaron de aparecer en todos los portales: lo más probable "
             "es que se hayan arrendado.</i>")
    return "\n".join(L)


def mensaje_sobrantes(avisos: list) -> str:
    """El resumen de los que calificaron pero no cupieron en el tope.

    Sin esto, el tope de avisos por corrida era un recorte SILENCIOSO: ocho
    mensajes completos y los demás quedaban en el tablero sin que nada dijera
    que existían. Si el noveno era interesante, la única forma de enterarse
    era abrir el tablero por iniciativa propia.

    Una línea por departamento, no el mensaje completo: es un índice, y los
    que lo ameriten van a llegar con su aviso completo en la corrida
    siguiente — el resumen no los marca como avisados a propósito.
    """
    if not avisos:
        return ""
    L = [f"📋 <b>Además calificaron {len(avisos)} más</b> "
         "(llegan en detalle en las próximas corridas):", ""]
    for a in avisos[:10]:
        emoji, _ = S.banda(a.score)
        pr = _pesos(a.arriendo_clp) if a.arriendo_clp else "s/precio"
        L.append(f"{emoji} {a.score} · {pr} · "
                 f"{_escapar((a.direccion or a.title)[:44])}")
    if len(avisos) > 10:
        L.append(f"… y {len(avisos) - 10} más en el tablero")
    return "\n".join(L)


def _volvio(a: Any) -> str:
    """"Ya estuvo publicado", en una línea, si el historial lo vio antes.

    Devuelve "" cuando no aporta: sin fecha no se puede decir hace cuánto, y
    "ya estuvo" a secas no le sirve a nadie para decidir.
    """
    antes = a.extras.get("ya_estuvo")
    if not isinstance(antes, dict) or not antes.get("cuando"):
        return ""

    from datetime import date

    try:
        dias = (date.today() - date.fromisoformat(str(antes["cuando"]))).days
    except ValueError:
        return ""
    if dias < 30:
        # Menos de un mes no es "volvió": es el mismo aviso republicado, y
        # decirlo sería ruido en el mensaje.
        return ""

    texto = f"Ya estuvo publicado hace {dias // 30} mes(es)"
    viejo, nuevo = antes.get("clp"), a.arriendo_clp
    if viejo and nuevo and viejo != nuevo:
        pct = round(100 * (nuevo - viejo) / viejo)
        texto += f", a {_pesos(viejo)} ({pct:+d}%)"
    return texto


def _latido(stats: dict) -> str:
    """El "sigo acá" semanal, con los números que lo hacen creíble."""
    # El movimiento del mercado va en el latido porque es lo único que
    # distingue "el radar funciona y no hay nada" de "el radar funciona y hay
    # harto, pero nada te sirve". Sin esta línea, una semana con 14
    # departamentos nuevos que no calificaron se lee igual que una semana
    # muerta, y son dos situaciones que piden decisiones opuestas: la primera
    # dice que hay que revisar el presupuesto, la segunda que hay que esperar.
    movimiento = ""
    if stats.get("nuevos") or stats.get("se_fueron"):
        movimiento = (f"En el mercado: {stats.get('nuevos', 0)} nuevos, "
                      f"{stats.get('se_fueron', 0)} se dejaron de publicar\n")

    return (
        "🔎 <b>Sin novedades</b>\n\n"
        f"Avisos revisados: {stats.get('total', 0)}\n"
        f"Pasaron los filtros: {stats.get('candidatos', 0)}\n"
        f"Fuentes entregando: {stats.get('fuentes_ok', 0)}\n"
        f"{movimiento}\n"
        "Ningún departamento nuevo desde el último aviso. El radar corre dos "
        f"veces al día; este resumen sale cada {DIAS_ENTRE_LATIDOS} días si "
        "no hay nada que mostrar."
    )
