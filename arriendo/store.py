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

import json
from datetime import datetime, date
from pathlib import Path
from typing import Any

from .models import Arriendo, clave_direccion, _normalize_key

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

        antes = prev.get("arriendo_clp")
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
            if getattr(l, campo, None) in (None, "") and prev.get(campo) not in (None, ""):
                setattr(l, campo, prev[campo])
                recuperados.append(campo)
        return recuperados

    # -- escritura --------------------------------------------------------
    def registrar(self, l: Arriendo, avisado: bool = False,
                  motivo: str = "") -> None:
        fp = l.fingerprint
        prev = self.indice.get(fp, {})
        ahora = datetime.utcnow().isoformat(timespec="seconds")

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
            **{c: getattr(l, c) for c in _APRENDIDOS
               if getattr(l, c) not in (None, "")},
        }

    def guardar(self, hallazgos: list[Arriendo] | None = None) -> None:
        self._escribir(self.ruta_indice, self.indice)
        if hallazgos is not None:
            self._escribir(
                self.ruta_hallazgos,
                [a.to_dict() for a in sorted(hallazgos, key=lambda x: -x.score)],
            )

    # -- mantenimiento ----------------------------------------------------
    def purgar(self, dias: int = 120) -> int:
        """Olvida entradas no vistas en N días para que el índice no crezca sin fin.

        120 días y no 180 como en un radar de remates: un arriendo publicado
        se toma en semanas, así que un aviso que lleva cuatro meses sin
        aparecer ya se arrendó. Si reaparece, avisar de nuevo es correcto —es
        inventario que volvió al mercado—.
        """
        corte = datetime.utcnow().timestamp() - dias * 86400
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

    Se conserva como principal la de mayor puntaje, y las otras quedan
    anotadas en `extras["tambien_en"]` para poder abrirlas: a veces una trae
    mejores fotos o el teléfono directo.
    """
    por_fp: dict[str, list[Arriendo]] = {}
    for a in hallazgos:
        por_fp.setdefault(a.fingerprint, []).append(a)

    salida: list[Arriendo] = []
    for copias in por_fp.values():
        if len(copias) == 1:
            salida.append(copias[0])
            continue

        principal = max(copias, key=lambda a: (a.score, _riqueza(a)))
        for otra in copias:
            if otra is principal:
                continue
            _fusionar(principal, otra)

        principal.extras["tambien_en"] = sorted(
            {f"{o.source}|{o.url}" for o in copias if o is not principal})
        salida.append(principal)

    return salida


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
