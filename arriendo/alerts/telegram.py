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

from ..models import Arriendo
from ..scoring import RUBRO_COMPLETO

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
                 tope_arriendo: float = 0.0):
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
                                    self.tope_arriendo))

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


def _veredicto(a: Arriendo) -> str:
    """El puntaje, con sobre cuánto se midió.

    "⭐ 62/100" a secas se lee más seguro de lo que es: el puntaje es bajo
    cuando faltan datos, no solo cuando el departamento es malo, y esas dos
    cosas piden acciones distintas.
    """
    sobre = ""
    medibles = a.extras.get("medibles")
    if isinstance(medibles, int) and medibles < RUBRO_COMPLETO:
        sobre = f" · medido sobre {medibles} de {RUBRO_COMPLETO}"
    return f"⭐ {a.score}/100{sobre}"


def _mensaje(a: Arriendo, motivo: str = "", caminable_km: float = 0.0,
             ancla: str = "", tope_arriendo: float = 0.0) -> str:
    """El aviso, pensado para leerse en la pantalla de bloqueo."""
    lineas = [f"🏠 <b>{_escapar(titulo_corto(a))}</b>"]
    lineas.append(f"📍 {_ubicacion(a, caminable_km, ancla)}")

    # --- la línea del dinero ---
    #
    # Va primero de las de datos porque es la que decide, y lleva el costo
    # TOTAL al lado del canon: el aviso publica $1.500.000 y lo que se paga
    # son $1.720.000. Sin los dos números juntos, comparar dos departamentos
    # obliga a abrir los dos.
    if a.arriendo_clp:
        plata = _pesos(a.arriendo_clp)
        if a.gastos_comunes_clp:
            total = _pesos(a.costo_mensual)
            plata += f" + GC {_pesos(a.gastos_comunes_clp)} = <b>{total}</b>"
        else:
            plata += " · GC no publicados"
        lineas.append(f"💰 {plata}")

        # El aviso que se pasa del tope entra a propósito —el pedido dice
        # "cerca de 1,6 millones" y $1.690.000 se negocia— pero tiene que
        # decirlo. Sin esta línea se ve idéntico a uno que sí cabe en el
        # presupuesto, y el usuario se entera recién al abrirlo.
        if tope_arriendo and a.arriendo_clp > tope_arriendo:
            sobre = a.arriendo_clp - tope_arriendo
            lineas.append(
                f"⚠️ {_pesos(sobre)} sobre tu tope de {_pesos(tope_arriendo)}"
                " — hay que negociar")

    medidas = []
    if a.m2_totales:
        medidas.append(f"{_numero(a.m2_totales)} m² tot")
    elif a.m2_utiles:
        medidas.append(f"{_numero(a.m2_utiles)} m² útiles")
    if a.dormitorios:
        medidas.append(f"{a.dormitorios}D")
    if a.banos:
        medidas.append(f"{a.banos}B")
    if a.estacionamientos:
        medidas.append(f"{a.estacionamientos}E")
    if a.bodega:
        medidas.append("bodega")
    if medidas:
        lineas.append("📐 " + " · ".join(medidas))

    detalles = []
    if a.antiguedad_anos is not None:
        detalles.append(f"{a.antiguedad_anos} años")
    elif (techo := a.extras.get("antiguedad_techo")) is not None:
        detalles.append(f"≤{techo} años")
    if a.piso is not None:
        # De dónde salió el piso cambia cuánto vale: el número del
        # departamento es una convención, no un dato publicado.
        aprox = " aprox." if a.extras.get("piso_origen") else ""
        detalles.append(f"piso {a.piso}{aprox}")
    if a.orientacion:
        detalles.append(_escapar(a.orientacion))
    if a.amoblado:
        detalles.append(_escapar(a.amoblado))
    if detalles:
        lineas.append("🏗 " + " · ".join(detalles))

    # Disponibilidad y antigüedad de la publicación: las dos cambian qué
    # hacer hoy, no solo qué pensar del departamento.
    estado = []
    if a.disponible_desde:
        if a.disponible_desde <= date.today():
            estado.append("disponible ya")
        else:
            estado.append(f"disponible {a.disponible_desde.strftime('%d-%m')}")
    dias = a.dias_publicado
    if dias is not None and dias >= 30:
        estado.append(f"publicado hace {dias} días — se negocia")
    if a.mascotas == "no acepta":
        estado.append("no acepta mascotas")
    if estado:
        lineas.append("📅 " + " · ".join(estado))

    if (falta := _falta(a)):
        lineas.append(f"❓ Falta: {falta}")

    lineas.append(_veredicto(a))

    if motivo:
        lineas.append(f"♻️ {_escapar(motivo)}")

    # Cuántos portales lo publican. Es una señal por sí sola: un departamento
    # en cuatro portales lleva rato dando vueltas.
    if (otros := a.extras.get("tambien_en")):
        lineas.append(f"🔁 También en {len(otros)} portal(es) más")

    lineas.append("")
    # El nombre del portal va en el link. Importa para decidir si abrirlo: un
    # aviso de Houm trae plano y disponibilidad real, y uno de Yapo puede ser
    # de hace tres meses.
    portal = _escapar(str(a.extras.get("portal") or a.source))
    if (ficha := a.extras.get("ficha_url")):
        lineas.append(f'📄 <a href="{_escapar(str(ficha))}">Ficha completa</a>')
        lineas.append(f'🔗 <a href="{_escapar(a.url)}">Ver en {portal}</a>')
    else:
        # Sin ficha va el aviso original: un mensaje sin ningún link no se
        # puede seguir.
        lineas.append(f'🔗 <a href="{_escapar(a.url)}">Ver en {portal}</a>')

    return "\n".join(lineas)


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


def _latido(stats: dict) -> str:
    """El "sigo acá" semanal, con los números que lo hacen creíble."""
    return (
        "🔎 <b>Sin novedades</b>\n\n"
        f"Avisos revisados: {stats.get('total', 0)}\n"
        f"Pasaron los filtros: {stats.get('candidatos', 0)}\n"
        f"Fuentes entregando: {stats.get('fuentes_ok', 0)}\n\n"
        "Ningún departamento nuevo desde el último aviso. El radar corre dos "
        f"veces al día; este resumen sale cada {DIAS_ENTRE_LATIDOS} días si "
        "no hay nada que mostrar."
    )
