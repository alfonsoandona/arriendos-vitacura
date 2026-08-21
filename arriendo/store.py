"""Estado persistente entre corridas.

Se guarda como JSON y no como SQLite a propósito: el estado vive versionado
en el repo, y un diff legible permite ver qué apareció en cada corrida sin
herramientas extra, desde el navegador del teléfono.

Este módulo hace tres cosas y las tres son específicas del arriendo:

  RECORDAR    qué ya se avisó, para no mandar el mismo departamento dos veces.
  CRUZAR      el mismo departamento publicado por varios portales, para que
              seis avisos sean un mensaje y no seis.
  DETECTAR    la baja de canon, que es LA señal del mercado de arriendo: un
              aviso que baja el precio lleva tiempo sin arrendarse.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, date
from pathlib import Path
from typing import Any

from .geo import haversine_km
from .models import Arriendo, clave_direccion, _normalize_key
from .tiempo import ahora_utc

INDICE = "vistos.json"
HALLAZGOS = "arriendos.json"

# Lo que el radar averigua con esfuerzo —visitando la ficha, geocodificando, o
# simplemente porque un portal lo publica y otro no— y que sin esto se tiraría
# a la basura al final de cada corrida.
#
# Es lo que hace que el radar mejore con el tiempo en vez de empezar de cero:
# si TocToc publicó la superficie y Yapo no, la próxima vez que llegue por
# Yapo ya se sabe cuánto mide.
_APRENDIDOS = ("direccion", "comuna", "lat", "lon", "m2_totales", "m2_utiles",
               "m2_terraza", "antiguedad_anos", "ano_construccion", "tipo",
               "piso", "dormitorios", "banos", "estacionamientos",
               "gastos_comunes_clp", "orientacion", "amoblado")


class Store:
    def __init__(self, directorio: str | Path):
        self.dir = Path(directorio)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ruta_indice = self.dir / INDICE
        self.ruta_hallazgos = self.dir / HALLAZGOS
        self.indice: dict[str, dict[str, Any]] = self._leer(self.ruta_indice, {})

    # -- io ---------------------------------------------------------------
    @staticmethod
    def _leer(ruta: Path, default: Any) -> Any:
        if not ruta.exists():
            return default
        try:
            with open(ruta, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # Un estado corrupto no debe voltear la corrida: se parte de cero
            # y a lo más se re-avisa algo ya visto.
            return default

    def _escribir(self, ruta: Path, data: Any) -> None:
        tmp = ruta.with_suffix(ruta.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True,
                      default=_serializar)
        tmp.replace(ruta)  # atómico: nunca deja un JSON a medio escribir

    # -- consultas --------------------------------------------------------
    def es_nuevo(self, l: Arriendo) -> bool:
        return l.fingerprint not in self.indice

    def ya_avisado(self, l: Arriendo) -> bool:
        return bool(self.indice.get(l.fingerprint, {}).get("avisado"))

    # Cuántos precios distintos recordar por departamento. Doce alcanza para
    # seis meses de corridas con cambios y no infla el JSON versionado: solo
    # se anota cuando el precio CAMBIA, no en cada corrida.
    MAX_HISTORIAL_PRECIO = 12

    def _anotar_precio(self, entrada: dict, l: Arriendo) -> list[dict]:
        """El historial de precios de este departamento, con el de hoy dentro.

        Solo se anota cuando el precio cambia. Guardar uno por corrida serían
        730 entradas al año por departamento para decir siempre lo mismo.

        Sirve para algo que la baja suelta no puede decir: la TENDENCIA. Un
        aviso que bajó una vez puede ser un ajuste; uno que bajó tres veces en
        dos meses es un propietario que no está logrando arrendar, y eso
        cambia por completo con qué número se llama a negociar.
        """
        historial = list(entrada.get("historial_precio") or [])
        if l.arriendo_clp is None:
            return historial
        if historial and historial[-1].get("clp") == l.arriendo_clp:
            return historial
        historial.append({"clp": l.arriendo_clp,
                          "cuando": date.today().isoformat()})
        return historial[-self.MAX_HISTORIAL_PRECIO:]

    def historial_precio(self, l: Arriendo) -> list[dict]:
        """Lo que se sabe de cómo se movió el precio de este departamento."""
        prev = (self.indice.get(l.fingerprint)
                or self._por_direccion(l)
                or self._por_url(l))
        return list((prev or {}).get("historial_precio") or [])

    def cambio_relevante(self, l: Arriendo, perfil: dict | None = None) -> str:
        """Detecta cambios que justifican volver a avisar algo ya visto.

        En arriendo la señal es la BAJA DE CANON, y no es un detalle: un aviso
        que baja el precio lleva semanas sin arrendarse, y eso significa dos
        cosas a la vez —que sigue disponible y que hay margen para negociar—.
        Es probablemente el mejor momento para llamar, y sin esto el radar se
        lo perdería entero por haberlo avisado una vez hace un mes.
        """
        prev = self.indice.get(l.fingerprint)
        if not prev:
            return ""

        cfg = ((perfil or {}).get("alertas") or {}).get("reavisar") or {}

        # La comparación es contra el precio con el que se AVISÓ, no contra el
        # de la corrida anterior. La diferencia importa: un canon que baja 2%
        # por corrida durante tres corridas cayó 6% en total y nunca habría
        # disparado el umbral del 4%, porque cada paso individual se queda
        # corto. Contra el precio avisado, la baja se acumula y se detecta.
        #
        # Es además el número correcto desde el lado del usuario: lo que le
        # interesa es cuánto bajó respecto de lo que él vio, no respecto de un
        # precio intermedio que nunca le llegó.
        antes = prev.get("precio_al_avisar") or prev.get("arriendo_clp")
        ahora = l.arriendo_clp
        umbral = float(cfg.get("baja_precio_pct", 4)) / 100
        if antes and ahora and ahora < antes * (1 - umbral):
            baja = round(100 * (antes - ahora) / antes)
            return (f"Bajó {baja}%: de ${antes:,.0f} a ${ahora:,.0f}"
                    .replace(",", "."))

        # Cruzar el umbral de "lleva mucho publicado" avisa UNA vez, no todos
        # los días: es una señal de negociación, no una alarma.
        dias_umbral = cfg.get("dias_publicado_aviso")
        dias = l.dias_publicado
        if dias_umbral and dias is not None and dias >= int(dias_umbral):
            if not prev.get("aviso_antiguedad_publicacion"):
                return f"Lleva {dias} días publicado — se negocia"

        return ""

    def _por_direccion(self, l: Arriendo) -> dict | None:
        """El registro anterior del mismo departamento, si no hay ambigüedad.

        Es la red que cruza portales cuando el fingerprint no alcanza. Pasa
        seguido: un portal publica "Alonso de Córdova 4200 depto 802" y otro
        solo "Alonso de Córdova 4200", así que uno tiene unidad en la llave y
        el otro no, y caen en fingerprints distintos.

        Si varios registros comparten la dirección sin unidad, es un edificio
        con varias unidades arrendándose a la vez: ahí heredar le pegaría a un
        departamento los datos de otro, así que no se hereda nada.
        """
        clave = clave_direccion(l.direccion, l.comuna)
        if not clave or not l.comuna:
            return None

        comuna = _normalize_key(l.comuna)
        candidatos = [
            e for e in self.indice.values()
            if e.get("clave_direccion") == clave
            and _normalize_key(e.get("comuna", "")) == comuna
        ]
        return candidatos[0] if len(candidatos) == 1 else None

    def _por_url(self, l: Arriendo) -> dict | None:
        """El registro anterior de esta misma página, si no hay ambigüedad.

        Última red. Vale solo cuando UN registro tiene esa URL: un listado
        paginado la comparte entre varias tarjetas, y ahí heredar sería
        pegarle a una los datos de otra.
        """
        if not l.url:
            return None
        candidatos = [e for e in self.indice.values() if e.get("url") == l.url]
        return candidatos[0] if len(candidatos) == 1 else None

    def completar(self, l: Arriendo) -> list[str]:
        """Le devuelve a un aviso lo que ya se sabía de él.

        Solo rellena campos vacíos, así que no puede pisar un dato fresco con
        uno viejo. El precio nunca se hereda —es justo lo que puede haber
        cambiado, y heredarlo escondería la baja de canon que interesa—.
        """
        prev = (self.indice.get(l.fingerprint)
                or self._por_direccion(l)
                or self._por_url(l))
        if not prev:
            return []

        recuperados = []
        for campo in _APRENDIDOS:
            if getattr(l, campo, None) not in (None, ""):
                continue
            valor = prev.get(campo)
            if valor in (None, ""):
                continue
            if campo == "direccion" and not clave_direccion(
                    str(valor), l.comuna or str(prev.get("comuna") or "")):
                # La memoria no puede deshacer una mejora del extractor. Cada
                # vez que el radar aprende a NO leer una dirección basura
                # —"Edificio de 18", "Antigüedad: 30", "Vitacura 3"— el aviso
                # vuelve a llegar con el campo vacío... y acá se le devolvía
                # la misma basura que se acababa de dejar de extraer, con la
                # firma de un dato aprendido. Lo que ya no se acepta al
                # leerlo tampoco se acepta al recordarlo.
                continue
            setattr(l, campo, valor)
            recuperados.append(campo)
        return recuperados

    # -- escritura --------------------------------------------------------
    def envio_pendiente(self, l: Arriendo) -> bool:
        """¿Quedó un aviso decidido pero no entregado? Eso SÍ se reintenta.

        Es distinto de "visto y nunca avisado": lo segundo es noticia vieja y
        no alerta salvo cambio; lo primero es una entrega que falló —Telegram
        caído, red— y perderla sería perder el departamento en silencio.
        """
        return bool(self.indice.get(l.fingerprint, {}).get("envio_pendiente"))

    def registrar(self, l: Arriendo, avisado: bool = False,
                  motivo: str = "", fallido: bool = False) -> None:
        fp = l.fingerprint
        prev = self.indice.get(fp, {})
        ahora = ahora_utc().isoformat(timespec="seconds")

        self.indice[fp] = {
            "url": l.url,
            "source": l.source,
            "titulo": l.title[:120],
            "direccion": l.direccion,
            "comuna": l.comuna,
            # Guardada aparte para poder cruzar por dirección sin recalcularla
            # sobre todo el índice en cada consulta.
            "clave_direccion": clave_direccion(l.direccion, l.comuna),
            "arriendo_clp": l.arriendo_clp,
            "score": l.score,
            "primera_vez": prev.get("primera_vez", ahora),
            "ultima_vez": ahora,
            "avisado": prev.get("avisado", False) or avisado,
            # La marca de reintento: la puso un envío fallido y la borra el
            # envío que sí llega.
            "envio_pendiente": (fallido or prev.get("envio_pendiente", False))
                               and not (avisado or prev.get("avisado", False)),
            "veces_visto": prev.get("veces_visto", 0) + 1,
            # Marca de una sola vez: el aviso de "lleva mucho publicado" no se
            # repite en cada corrida.
            "aviso_antiguedad_publicacion": (
                prev.get("aviso_antiguedad_publicacion", False)
                or "días publicado" in motivo),
            # El canon con el que se avisó, para poder medir la baja contra el
            # precio que el usuario ya vio y no contra el de ayer.
            "precio_al_avisar": (
                l.arriendo_clp if avisado else prev.get("precio_al_avisar")),
            "historial_precio": self._anotar_precio(prev, l),
            **{c: getattr(l, c) for c in _APRENDIDOS
               if getattr(l, c) not in (None, "")},
        }

    def guardar(self, hallazgos: list[Arriendo] | None = None) -> None:
        self._escribir(self.ruta_indice, self.indice)
        if hallazgos is not None:
            self._escribir(
                self.ruta_hallazgos,
                [a.to_dict() for a in sorted(hallazgos, key=_orden, reverse=True)],
            )

    # -- mantenimiento ----------------------------------------------------
    def purgar(self, dias: int = 120) -> int:
        """Olvida entradas no vistas en N días para que el índice no crezca sin fin.

        120 días y no 180 como en un radar de remates: un arriendo publicado
        se toma en semanas, así que un aviso que lleva cuatro meses sin
        aparecer ya se arrendó. Si reaparece, avisar de nuevo es correcto —es
        inventario que volvió al mercado—.
        """
        corte = ahora_utc().timestamp() - dias * 86400
        a_borrar = []
        for fp, e in self.indice.items():
            try:
                if datetime.fromisoformat(e["ultima_vez"]).timestamp() < corte:
                    a_borrar.append(fp)
            except (KeyError, ValueError):
                continue
        for fp in a_borrar:
            del self.indice[fp]
        return len(a_borrar)


def _orden(a: Arriendo) -> tuple:
    """(puntaje, confianza), igual que el tablero. Ver `scoring.orden`."""
    return (a.score, a.extras.get("confianza", 0))


def leer_tendencia(historial: list[dict]) -> str:
    """Qué dice el historial de precios, en una frase.

    Devuelve "" cuando no dice nada: con un solo precio no hay tendencia, y
    escribir "sin cambios" en cada ficha entrena a saltarse la línea.

    Es la diferencia entre saber que un aviso bajó y saber que lleva bajando.
    Lo segundo es lo que dice con qué número llamar.
    """
    puntos = [h for h in (historial or []) if h.get("clp")]
    if len(puntos) < 2:
        return ""

    primero, ultimo = puntos[0]["clp"], puntos[-1]["clp"]
    if primero == ultimo:
        return ""

    pct = round(100 * (ultimo - primero) / primero)
    bajas = sum(1 for a, b in zip(puntos, puntos[1:]) if b["clp"] < a["clp"])

    try:
        desde = date.fromisoformat(puntos[0]["cuando"])
        dias = (date.today() - desde).days
        lapso = f" en {dias} días" if dias else ""
    except (KeyError, ValueError):
        lapso = ""

    if ultimo < primero:
        cuantas = f"{bajas} bajas" if bajas > 1 else "1 baja"
        return f"{cuantas}{lapso}: {pct}% desde ${primero:,.0f}".replace(",", ".")
    return f"subió {pct}%{lapso}, desde ${primero:,.0f}".replace(",", ".")


def deduplicar(hallazgos: list[Arriendo]) -> list[Arriendo]:
    """Colapsa el mismo departamento publicado por varios portales.

    Es la operación que hace usable a este radar. El mismo departamento de
    Vitacura está en TocToc, en Yapo, en la página de su corredora y en el
    portal que le sindica el aviso: sin colapsarlo son cuatro mensajes de
    Telegram del mismo departamento y el radar se vuelve ruido en una semana.

    Entre las copias no se elige una: se FUSIONAN. Cada portal publica un
    subconjunto distinto de los datos —uno trae la superficie, otro el año,
    otro los gastos comunes— así que la copia fusionada sabe más que
    cualquiera de las originales.

    Entre las copias gana la que trae MÁS DATOS, no la de mejor puntaje: la
    deduplicación corre antes de evaluar —hay que evaluar una vez, no cuatro—
    así que en ese momento todas puntúan cero. El puntaje queda igual en el
    criterio de desempate, para que la función siga siendo correcta si alguien
    la llama después de evaluar.

    Las copias que no ganaron quedan anotadas en `extras["tambien_en"]` para
    poder abrirlas: a veces una trae mejores fotos o el teléfono directo.
    """
    por_fp: dict[str, list[Arriendo]] = {}
    for a in hallazgos:
        por_fp.setdefault(a.fingerprint, []).append(a)

    salida = [_colapsar(copias) for copias in por_fp.values()]
    return _colapsar_por_codigo(_colapsar_por_cercania(
        _colapsar_por_titulo(_colapsar_por_direccion(salida))))


def _colapsar_por_codigo(hallazgos: list[Arriendo]) -> list[Arriendo]:
    """Quinta pasada, y la más limpia: el código de la corredora.

    Una corredora publica su propiedad en su sitio, en TocToc, en Yapo y en
    tres metabuscadores, y en todos lleva el mismo "Cod. 108.143" que le
    puso su propio sistema. Ese número cruza portales mejor que cualquier
    dirección, porque la dirección la reescribe cada portal a su manera y el
    código viaja intacto dentro de la descripción.

    El caso que lo pidió (20-08, el usuario mandó las dos fichas): el mismo
    departamento como "Rotonda lo curro" en chilepropiedades y como
    "Vitacura 312 Metropolitana Juan XXIII 6859 301" en TocToc. Dos
    direcciones irreconciliables, geocodificadas a 249 metros una de otra —
    y el mismo Cod. 108.143 en las dos, con la misma descripción palabra por
    palabra.

    Para no fundir dos propiedades de corredoras distintas que casualmente
    comparten número, se exige además que COINCIDA la superficie o el precio
    (±1%, que es lo que se mueve un canon en UF por el valor del día) y que
    el programa no se contradiga.
    """
    from .parse import codigo_de_aviso

    por_codigo: dict[str, list[Arriendo]] = {}
    for a in hallazgos:
        cod = codigo_de_aviso(f"{a.title or ''} {a.raw_text or ''}")
        if cod:
            por_codigo.setdefault(cod, []).append(a)

    fundidos: set[int] = set()
    for grupo in por_codigo.values():
        if len(grupo) < 2:
            continue
        principal = grupo[0]
        for otra in grupo[1:]:
            if id(otra) in fundidos:
                continue
            if not _coincide_el_tamano(principal, otra):
                continue
            if _programa_se_contradice(principal, otra):
                continue
            ganador = max((principal, otra),
                          key=lambda x: (x.score, _riqueza(x)))
            perdedor = otra if ganador is principal else principal
            _fusionar(ganador, perdedor)
            otros = set(ganador.extras.get("tambien_en", []))
            otros |= set(perdedor.extras.get("tambien_en", []))
            otros.add(f"{perdedor.source}|{perdedor.url}")
            otros.discard(f"{ganador.source}|{ganador.url}")
            ganador.extras["tambien_en"] = sorted(otros)
            ganador.extras["fusion_por"] = "el mismo código de la corredora"
            fundidos.add(id(perdedor))
            principal = ganador

    return [a for a in hallazgos if id(a) not in fundidos]


def _coincide_el_tamano(a: Arriendo, b: Arriendo) -> bool:
    """¿Miden o cuestan lo mismo? Basta con una de las dos."""
    ma, mb = (a.m2_totales or a.m2_utiles), (b.m2_totales or b.m2_utiles)
    if ma and mb and abs(ma - mb) <= 1:
        return True
    pa, pb = a.arriendo_clp, b.arriendo_clp
    if pa and pb and abs(pa - pb) <= max(pa, pb) * 0.01:
        return True
    # Sin ninguno de los dos datos en ambos lados no hay con qué confirmar,
    # y el código solo no alcanza: se prefiere duplicar antes que fundir mal.
    return False


# Dos avisos a menos de esto son, como mucho, el mismo edificio. 120 metros
# es media cuadra larga: alcanza para dos geocodificaciones de la misma
# dirección escritas distinto, y no alcanza para saltar de una torre a otra.
_MISMO_EDIFICIO_KM = 0.12


def _colapsar_por_cercania(hallazgos: list[Arriendo]) -> list[Arriendo]:
    """Cuarta pasada: el mismo departamento con la dirección escrita distinto.

    El caso que lo pidió (20-08, el usuario mandó las dos fichas): el mismo
    departamento de UF 30 y 110 m² publicado por chilepropiedades como
    "Rotonda lo curro" y por toctoc como "Vitacura 312 Metropolitana Juan
    XXIII 6859 301". Dos direcciones que no se parecen en nada, dos
    fingerprints, dos fichas, dos filas en el tablero — y es UN
    departamento.

    Lo que sí coincide es lo que no se puede escribir de dos maneras: el
    canon EXACTO, la superficie y el punto en el mapa. La llave son los
    tres juntos, y los tres son necesarios:

    - el precio exacto solo, en un edificio nuevo, junta unidades distintas
      del mismo tipo;
    - la cercanía sola junta la torre A con la torre B;
    - los m² solos no dicen nada.

    Con los tres, la probabilidad de fundir dos departamentos distintos es
    la de que dos unidades del mismo edificio publiquen el mismo canon al
    peso y la misma superficie al metro. Y aun así se exige que el programa
    no se CONTRADIGA, que es la última red.
    """
    con_geo = [a for a in hallazgos if a.lat is not None and a.lon is not None]
    if len(con_geo) < 2:
        return hallazgos

    grupos: dict[tuple, list[Arriendo]] = {}
    for a in con_geo:
        precio = a.arriendo_clp or (a.arriendo_uf and round(a.arriendo_uf, 2))
        m2 = a.m2_totales or a.m2_utiles
        if not precio or not m2:
            continue
        grupos.setdefault((round(float(precio)), round(float(m2))),
                          []).append(a)

    fundidos: set[int] = set()
    for grupo in grupos.values():
        if len(grupo) < 2:
            continue
        principal = grupo[0]
        for otra in grupo[1:]:
            if id(otra) in fundidos or id(principal) in fundidos:
                continue
            if haversine_km(principal.lat, principal.lon,
                            otra.lat, otra.lon) > _MISMO_EDIFICIO_KM:
                continue
            if _programa_se_contradice(principal, otra):
                continue
            ganador = max((principal, otra),
                          key=lambda x: (x.score, _riqueza(x)))
            perdedor = otra if ganador is principal else principal
            _fusionar(ganador, perdedor)
            otros = set(ganador.extras.get("tambien_en", []))
            otros |= set(perdedor.extras.get("tambien_en", []))
            otros.add(f"{perdedor.source}|{perdedor.url}")
            otros.discard(f"{ganador.source}|{ganador.url}")
            ganador.extras["tambien_en"] = sorted(otros)
            ganador.extras["fusion_por"] = "mismo precio, m² y punto en el mapa"
            fundidos.add(id(perdedor))
            principal = ganador

    return [a for a in hallazgos if id(a) not in fundidos]


def _programa_se_contradice(a: Arriendo, b: Arriendo) -> bool:
    """¿Dicen cosas incompatibles sobre cuántas piezas tiene?

    Se tolera UN dormitorio de diferencia: la mitad de los portales cuenta
    la pieza de servicio y la otra mitad no, y esa discrepancia no es dos
    departamentos distintos — es la misma discusión de siempre.
    """
    for campo, tolerancia in (("dormitorios", 1), ("banos", 1)):
        x, y = getattr(a, campo), getattr(b, campo)
        if x is not None and y is not None and abs(x - y) > tolerancia:
            return True
    return False


def _colapsar_por_titulo(hallazgos: list[Arriendo]) -> list[Arriendo]:
    """Tercera pasada: el mismo aviso repetido entre PÁGINAS de un listado.

    El caso medido (auditoría del 19-08): trovit entrega "VITACURA, LO
    GALLO, MAR JONICO, PENTHOUSE" tres veces — una por página del listado,
    cada una con su URL ?page=N — y como no trae dirección, ni el
    fingerprint ni la pasada por dirección los juntan. Tres filas del mismo
    departamento en el tablero y el dashboard.

    La llave es el TEXTO COMPLETO normalizado (título + descripción), no el
    título: dos avisos distintos pueden compartir el título genérico
    ("Departamento en arriendo en Vitacura"), pero jamás comparten la
    descripción entera palabra por palabra — y las copias entre páginas la
    comparten exactamente, que es como se midió en las tres de trovit.
    Solo dentro de la misma fuente y solo entre avisos sin dirección: los
    cruces entre portales son trabajo de la pasada por dirección.
    """
    grupos: dict[tuple, list[Arriendo]] = {}
    sueltos: list[Arriendo] = []
    for a in hallazgos:
        texto = _normalize_key(f"{a.title or ''} {a.raw_text or ''}")
        if a.direccion or len(texto) < 80:
            sueltos.append(a)
            continue
        llave = (a.source,
                 hashlib.sha1(texto.encode()).hexdigest())
        grupos.setdefault(llave, []).append(a)
    return sueltos + [_colapsar(copias) for copias in grupos.values()]


def _colapsar(copias: list[Arriendo]) -> Arriendo:
    """Fusiona un grupo de copias en una sola, y anota de dónde salieron."""
    if len(copias) == 1:
        return copias[0]

    # Entre copias del mismo puntaje gana la que tiene LINK DIRECTO al aviso:
    # un mensaje cuyo link cae al listado completo del portal obliga a buscar
    # el departamento a mano entre miles.
    principal = max(copias, key=lambda a: (a.score,
                                           not a.extras.get("sin_link_directo"),
                                           _riqueza(a)))
    for otra in copias:
        if otra is not principal:
            _fusionar(principal, otra)

    otros = {f"{o.source}|{o.url}" for o in copias if o is not principal}
    otros |= set(principal.extras.get("tambien_en", []))
    for o in copias:
        otros |= set(o.extras.get("tambien_en", []))
    otros.discard(f"{principal.source}|{principal.url}")
    principal.extras["tambien_en"] = sorted(otros)
    return principal


def _corroboracion(a: Arriendo, clave: str) -> str:
    """Qué más tiene que coincidir, además de la calle, para fundir.

    Una calle CON altura identifica un edificio: "Alonso de Córdova 4200"
    es una dirección y basta. Una calle SIN altura identifica una calle
    entera, y en Vitacura una calle tiene decenas de edificios: la corrida
    del 20-08 fundió siete departamentos distintos de "Las Nieves" —de 3D
    y 248 m² a 5D y 380 m²— en un registro, y seis desaparecieron.

    Así que sin altura se exige una segunda señal: el canon exacto, o el
    programa y la superficie cuando el canon falta. Con altura no se exige
    nada, porque la dirección ya hizo el trabajo.

    Es deliberadamente conservador en la dirección segura: ante la duda se
    prefiere un mensaje de más a un departamento perdido en silencio.
    """
    if re.search(r"\b\d{3,}\b", clave):
        return ""
    if a.arriendo_clp:
        return f"clp:{int(a.arriendo_clp)}"
    if a.arriendo_uf:
        return f"uf:{a.arriendo_uf}"
    if a.dormitorios or a.m2_referencia:
        return f"prog:{a.dormitorios}/{a.m2_referencia}"
    return f"solo:{a.fingerprint}"


def _colapsar_por_direccion(hallazgos: list[Arriendo]) -> list[Arriendo]:
    """Segunda pasada: cruza las copias que el fingerprint no alcanzó a juntar.

    El fingerprint mete el número del departamento en la llave, y tiene que
    hacerlo: en un edificio de Vitacura se arriendan tres unidades distintas
    de la misma torre a la vez, y colapsarlas haría perder dos.

    Pero eso deja fuera el caso más común de todos: **un portal publica el
    número y el otro no**. TocToc dice "Alonso de Córdova 4200 depto 802" y
    Yapo dice "Alonso de Córdova 4200" — el mismo departamento, dos
    fingerprints, dos mensajes de Telegram.

    La regla es la misma que usa el estado para heredar datos: dentro de una
    dirección, un aviso SIN unidad se puede juntar con uno CON unidad solo si
    hay una sola unidad en juego. Con dos o más no se sabe a cuál pertenece, y
    ahí se prefiere duplicar antes que fusionar dos departamentos distintos:
    un mensaje de más se ignora, un departamento perdido no se recupera.
    """
    grupos: dict[tuple[str, str], list[Arriendo]] = {}
    sueltos: list[Arriendo] = []

    for a in hallazgos:
        clave = clave_direccion(a.direccion, a.comuna)
        if clave and a.comuna:
            grupos.setdefault(
                (clave, _normalize_key(a.comuna), _corroboracion(a, clave)),
                []).append(a)
        else:
            sueltos.append(a)

    salida = list(sueltos)
    for grupo in grupos.values():
        unidades = {str(a.extras.get("unidad", "")).upper()
                    for a in grupo if a.extras.get("unidad")}
        if len(unidades) <= 1:
            salida.append(_colapsar(grupo))
            continue

        # Varias unidades del mismo edificio: cada una es un departamento
        # distinto y no se pueden juntar.
        por_unidad: dict[str, list[Arriendo]] = {}
        for a in grupo:
            por_unidad.setdefault(
                str(a.extras.get("unidad", "")).upper(), []).append(a)

        # Los avisos sin unidad se intentan asignar por PRECIO antes de
        # rendirse. Misma dirección y mismo canon exacto es evidencia fuerte:
        # el portal que no publicó el número igual publicó lo que cuesta.
        for suelto in por_unidad.pop("", []):
            destino = _unidad_por_precio(suelto, por_unidad)
            if destino:
                por_unidad[destino].append(suelto)
            else:
                salida.append(suelto)

        salida.extend(_colapsar(c) for c in por_unidad.values())

    return salida


def _unidad_por_precio(suelto: Arriendo,
                       por_unidad: dict[str, list[Arriendo]]) -> str:
    """A qué unidad pertenece un aviso sin número, si el precio lo dice.

    Devuelve "" cuando no hay exactamente una coincidencia. Dos unidades de la
    misma torre al mismo precio existen —departamentos de planta idéntica— y
    ahí el precio no desambigua nada, así que se prefiere duplicar.
    """
    if suelto.arriendo_clp is None:
        return ""
    calzan = [u for u, avisos in por_unidad.items()
              if any(a.arriendo_clp == suelto.arriendo_clp for a in avisos)]
    return calzan[0] if len(calzan) == 1 else ""


# Los campos que se fusionan entre copias. El precio NO está: dos portales
# pueden publicar cánones distintos del mismo departamento (uno desactualizado,
# otro con los gastos comunes adentro) y elegir el menor inventaría una oferta
# que nadie hizo. Se conserva el de la copia principal y se dice de dónde salió.
_FUSIONABLES = _APRENDIDOS + ("orientacion", "ultimo_piso", "bodega",
                              "mascotas", "disponible_desde", "publicado_el",
                              "corredora", "garantia_meses")


def _riqueza(a: Arriendo) -> int:
    """Cuántos campos con dato trae. Desempata entre copias del mismo puntaje."""
    return sum(1 for c in _FUSIONABLES
               if getattr(a, c, None) not in (None, "", False))


def _fusionar(destino: Arriendo, origen: Arriendo) -> None:
    """Copia a `destino` lo que `origen` sabe y él no. Nunca pisa un dato."""
    for campo in _FUSIONABLES:
        if getattr(destino, campo, None) in (None, ""):
            valor = getattr(origen, campo, None)
            if valor not in (None, ""):
                setattr(destino, campo, valor)


def _serializar(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"No serializable: {type(o)}")
