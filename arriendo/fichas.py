"""Fichas en Markdown: el detalle completo de cada arriendo alertado.

Existen porque el mensaje de Telegram tiene ocho líneas y hay cosas que no
caben pero que sí hacen falta antes de ir a ver un departamento: de dónde
salió cada punto del puntaje, en qué otros portales está publicado, y sobre
todo **qué preguntar por teléfono**.

Viven en el repositorio y se leen desde el navegador del teléfono, así que no
hay nada que instalar ni ninguna cuenta que crear. El link del aviso apunta
acá.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from . import scoring as S
from .models import Arriendo
from .parse import strip_accents
from .scoring import RUBRO_COMPLETO, desglose, techo_alcanzable


def _pesos(valor: float | None) -> str:
    if valor is None:
        return "—"
    return "$" + f"{valor:,.0f}".replace(",", ".")


def _num(valor: float | None, sufijo: str = "") -> str:
    """A la chilena: punto para los miles, coma para el decimal."""
    if valor is None:
        return "—"
    if valor == int(valor):
        texto = f"{int(valor):,}".replace(",", ".")
    else:
        texto = f"{valor:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
        texto = texto.rstrip("0").rstrip(",")
    return f"{texto}{sufijo}"


def _decimal(valor: float, decimales: int = 1) -> str:
    """Un decimal a la chilena: 13,2 y no 13.2."""
    return f"{valor:.{decimales}f}".replace(".", ",")


def nombre_archivo(a: Arriendo) -> str:
    """Un nombre estable y legible para la ficha.

    Estable importa: la ficha se reescribe en cada corrida que vuelve a ver
    el departamento, y si el nombre cambiara quedarían fichas huérfanas y el
    link del mensaje de Telegram anterior apuntaría a un 404.
    """
    partes = [a.comuna or "sin-comuna", a.direccion or a.title or a.source]
    if (unidad := a.extras.get("unidad")):
        partes.append(f"depto-{unidad}")
    crudo = strip_accents(" ".join(partes)).lower()
    limpio = re.sub(r"[^a-z0-9]+", "-", crudo).strip("-")
    return f"{limpio[:90]}.md"


def url_ficha(a: Arriendo, repo: str = "", rama: str = "") -> str:
    """El link a la ficha en GitHub, para meterlo en el mensaje de Telegram.

    La rama se lee de GITHUB_REF_NAME —Actions la expone siempre— y NO se
    supone "main". La suposición vino del radar de remates, donde la rama
    default sí es main; acá la default es la rama de trabajo, así que cada
    link de ficha apuntaba a una rama inexistente y GitHub respondía 404.
    El usuario lo descubrió tocando el link en su teléfono, que es la peor
    forma de descubrirlo: el mensaje promete una ficha que no abre.
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
    rama = rama or os.environ.get("GITHUB_REF_NAME", "") or "main"
    if not repo:
        return ""
    return (f"https://github.com/{repo}/blob/{rama}/alertas/casos/"
            f"{nombre_archivo(a)}")


# ---------------------------------------------------------------------------
# Las preguntas
#
# Es la sección que más se usa y la que ningún portal da. Un arriendo se
# decide por teléfono en cinco minutos, y las preguntas que importan no son
# obvias: casi todas son cosas que no se pueden ver en las fotos y que, si se
# descubren en la visita, ya costaron una tarde.
#
# Las preguntas se generan según lo que FALTA en este aviso concreto, no como
# una lista fija: una checklist que siempre dice lo mismo se deja de leer.
# ---------------------------------------------------------------------------

def _preguntas(a: Arriendo) -> list[str]:
    preguntas: list[str] = []

    if a.gastos_comunes_clp is None:
        preguntas.append(
            "**¿Cuánto son los gastos comunes?** Es la pregunta más rentable "
            "de la lista: en departamentos de más de 100 m² en Vitacura van "
            "entre $150.000 y $400.000 al mes, y eso mueve el costo real "
            "más que cualquier negociación del canon.")
    elif (pct := a.gastos_comunes_pct) and pct > 20:
        preguntas.append(
            f"**Los gastos comunes son altos** ({_decimal(pct)}% del canon). "
            "Preguntar qué incluyen y si hay algún gasto extraordinario "
            "vigente — una reparación de fachada se reparte entre todos "
            "los departamentos y puede durar años.")

    if a.antiguedad_anos is None and "antiguedad_techo" not in a.extras:
        preguntas.append(
            "**¿De qué año es el edificio?** Define casi un cuarto del "
            "puntaje de este radar y el aviso no lo publica.")

    if a.m2_totales is None:
        preguntas.append(
            "**¿Cuántos m² totales y cuántos útiles?** El aviso no aclara "
            "cuál de las dos publicó, y el filtro de este radar es sobre la "
            "total.")

    if a.estacionamientos is None:
        preguntas.append(
            "**¿Incluye estacionamiento y bodega, o se pagan aparte?** En "
            "arriendo se cobran por separado con frecuencia.")

    if a.garantia_meses is None:
        preguntas.append(
            "**¿Cuántos meses de garantía?** Con dos meses sobre este canon "
            "son "
            + (_pesos(a.arriendo_clp * 2) if a.arriendo_clp else "varios millones")
            + " al firmar, además del primer mes y la comisión.")

    if not a.extras.get("particular"):
        preguntas.append(
            "**¿Cuánto es la comisión de corretaje?** Lo habitual es medio "
            "mes más IVA, a cargo del arrendatario.")

    if a.mascotas == "":
        preguntas.append("**¿El reglamento del edificio acepta mascotas?**")

    dias = a.dias_publicado
    if dias is not None and dias >= 45:
        preguntas.append(
            f"**Lleva {dias} días publicado.** Vale la pena preguntar "
            "directamente si hay flexibilidad en el canon: a esa altura el "
            "propietario ya perdió más en meses vacíos que lo que cede "
            "bajando el precio.")

    preguntas.append(
        "**¿Está disponible para visitar esta semana?** En Vitacura, un "
        "departamento de más de 100 m² con 3 dormitorios dentro de tu "
        "presupuesto se "
        "toma en días.")

    return preguntas


# ---------------------------------------------------------------------------
# La ficha
# ---------------------------------------------------------------------------

def escribir_ficha(a: Arriendo, directorio: Path, perfil: dict | None = None,
                   motivo: str = "") -> Path:
    """Escribe (o reescribe) la ficha de un arriendo. Devuelve la ruta."""
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre_archivo(a)
    ruta.write_text(_ficha(a, perfil or {}, motivo), encoding="utf-8")
    return ruta


def _ficha(a: Arriendo, perfil: dict, motivo: str = "") -> str:
    L: list[str] = []

    titulo = a.direccion or a.title or "Arriendo sin dirección"
    L.append(f"# {titulo}")
    L.append("")
    if motivo:
        L.append(f"> ♻️ **{motivo}**")
        L.append("")

    # -- el resumen de una línea, que es lo que se lee primero --
    L.append(f"**{a.score}/100** · {a.comuna or 'comuna desconocida'}"
             + (f" · a {_decimal(a.distancia_km, 2)} km del Sport Francés"
                if a.distancia_km is not None else " · sin ubicar"))
    L.append("")

    # -- el dinero --
    L.append("## Cuánto cuesta")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Arriendo | {_pesos(a.arriendo_clp)} |")
    L.append(f"| Gastos comunes | {_pesos(a.gastos_comunes_clp)}"
             + (f" ({_decimal(a.gastos_comunes_pct)}% del canon)"
                if a.gastos_comunes_pct is not None else "") + " |")
    L.append(f"| **Costo mensual** | **{_pesos(a.costo_mensual)}**"
             + ("" if a.gastos_comunes_clp is not None
                else " ⚠️ sin gastos comunes") + " |")
    if a.garantia_meses:
        garantia = a.arriendo_clp * a.garantia_meses if a.arriendo_clp else None
        L.append(f"| Garantía | {_num(a.garantia_meses)} mes(es)"
                 + (f" = {_pesos(garantia)}" if garantia else "") + " |")
    if a.arriendo_uf:
        L.append(f"| Publicado en UF | UF {_num(a.arriendo_uf)} |")
    if a.arriendo_clp and (m2 := a.m2_referencia):
        L.append(f"| Por m² | {_pesos(a.arriendo_clp / m2)} / m² |")
    L.append("")

    if (historial := a.extras.get("historial_precio")):
        L.append("")
        L.append("### Cómo se movió el precio")
        L.append("")
        L.append("| Cuándo | Arriendo |")
        L.append("|---|---|")
        anterior = None
        for punto in historial:
            delta = ""
            if anterior and punto.get("clp"):
                pct = round(100 * (punto["clp"] - anterior) / anterior)
                if pct:
                    delta = f" ({pct:+d}%)"
            L.append(f"| {punto.get('cuando', '—')} | "
                     f"{_pesos(punto.get('clp'))}{delta} |")
            anterior = punto.get("clp") or anterior
        L.append("")
        if (tendencia := a.extras.get("tendencia_precio")):
            L.append(f"**{tendencia}.** Un aviso que lleva bajando es un "
                     "propietario que no está logrando arrendar, y eso cambia "
                     "con qué número conviene llamar.")
            L.append("")

    # -- la propiedad --
    L.append("## Qué es")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Tipo | {a.tipo or '—'} |")
    L.append(f"| Superficie total | {_num(a.m2_totales, ' m²')} |")
    L.append(f"| Superficie útil | {_num(a.m2_utiles, ' m²')} |")
    if a.m2_terraza:
        L.append(f"| Terraza | {_num(a.m2_terraza, ' m²')} |")
    L.append(f"| Dormitorios | {a.dormitorios or '—'}"
             + (" + pieza de servicio" if a.extras.get("pieza_servicio") else "")
             + " |")
    L.append(f"| Baños | {a.banos or '—'} |")
    L.append(f"| Estacionamientos | {a.estacionamientos if a.estacionamientos is not None else '—'} |")
    L.append(f"| Bodega | {_si_no(a.bodega)} |")
    if a.antiguedad_anos is not None:
        L.append(f"| Antigüedad | {a.antiguedad_anos} años"
                 + (f" (construido en {a.ano_construccion})"
                    if a.ano_construccion else "") + " |")
    elif (techo := a.extras.get("antiguedad_techo")) is not None:
        L.append(f"| Antigüedad | a lo más {techo} años "
                 f"(declarado en el aviso, no publicado como dato) |")
    else:
        L.append("| Antigüedad | — |")
    if a.piso is not None:
        origen = (f" _{a.extras['piso_origen']}_"
                  if a.extras.get("piso_origen") else "")
        L.append(f"| Piso | {a.piso}{origen} |")
    if a.orientacion:
        L.append(f"| Orientación | {a.orientacion} |")
    if a.amoblado:
        L.append(f"| Amoblado | {a.amoblado} |")
    if a.mascotas:
        L.append(f"| Mascotas | {a.mascotas} |")
    if a.disponible_desde:
        cuando = ("ya" if a.disponible_desde <= date.today()
                  else a.disponible_desde.strftime("%d-%m-%Y"))
        L.append(f"| Disponible | {cuando} |")
    if (dias := a.dias_publicado) is not None:
        L.append(f"| Publicado | hace {dias} días |")
    L.append("")

    # -- el puntaje, abierto --
    L.append("## De dónde sale el puntaje")
    L.append("")
    rubros = desglose(a)
    if rubros:
        L.append("| Rubro | Puntos | Qué se midió |")
        L.append("|---|---|---|")
        for r in rubros:
            puntos = f"{r.obtenido}/{r.peso}" if r.medido else f"— /{r.peso}"
            detalle = r.detalle + (f" · _falta {r.falta}_"
                                   if not r.medido and r.falta else "")
            L.append(f"| {r.nombre} | {puntos} | {detalle} |")

        prefs = a.extras.get("preferencias", 0)
        if prefs:
            signo = "+" if prefs > 0 else ""
            L.append(f"| _Preferencias_ | {signo}{prefs} | "
                     f"desempate entre las que ya calificaron |")
        L.append("")

        medibles = a.extras.get("medibles", 0)
        if medibles < RUBRO_COMPLETO:
            techo = techo_alcanzable(a)
            L.append(f"> El puntaje se midió sobre **{medibles} de "
                     f"{RUBRO_COMPLETO}** puntos posibles: hay rubros que "
                     f"este aviso no publica. Con esos datos podría llegar a "
                     f"**{techo}/100**.")
            L.append(">")
            L.append("> Por eso un puntaje bajo acá no significa "
                     "necesariamente un mal departamento: puede ser uno del "
                     "que se sabe poco.")
            L.append("")

    if a.razones:
        L.append("<details><summary>Detalle criterio por criterio</summary>")
        L.append("")
        for r in a.razones:
            L.append(f"- {r}")
        L.append("")
        L.append("</details>")
        L.append("")

    # -- las preguntas --
    L.append("## Qué preguntar antes de ir")
    L.append("")
    for p in _preguntas(a):
        L.append(f"- [ ] {p}")
    L.append("")

    # -- los enlaces --
    L.append("## Dónde está publicado")
    L.append("")
    L.append(f"- [{a.extras.get('portal') or a.source}]({a.url})")
    for otro in a.extras.get("tambien_en", []):
        fuente, _, url = str(otro).partition("|")
        L.append(f"- [{fuente}]({url})")
    L.append("")
    if len(a.extras.get("tambien_en", [])) >= 2:
        L.append("> Publicado en varios portales a la vez. Suele significar "
                 "que lleva tiempo en el mercado, y a veces que hay más de "
                 "una corredora compitiendo por arrendarlo — que es una "
                 "buena posición para negociar.")
        L.append("")

    # -- la trazabilidad --
    L.append("---")
    L.append("")
    L.append("<details><summary>Datos crudos del aviso</summary>")
    L.append("")
    L.append("```")
    L.append((a.raw_text or "(sin texto)")[:1500])
    L.append("```")
    L.append("")
    L.append(f"Leído de `{a.source}` vía `{a.extras.get('via', '?')}` el "
             f"{a.scraped_at:%d-%m-%Y %H:%M} UTC.")
    L.append("")
    L.append("</details>")
    L.append("")

    return "\n".join(L)


def _si_no(v: bool | None) -> str:
    return "—" if v is None else ("sí" if v else "no")


# ---------------------------------------------------------------------------
# El tablero
# ---------------------------------------------------------------------------

def escribir_tablero(hallazgos: list[Arriendo], directorio: Path,
                     perfil: dict | None = None) -> Path:
    """La tabla de todo lo vigente, ordenada por puntaje.

    Es la vista que responde "¿qué hay hoy?" sin abrir Telegram, y la que
    permite comparar: el costo mensual real de todos, uno debajo del otro.
    """
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / "README.md"

    vivos = sorted([a for a in hallazgos if not a.descartado],
                   key=S.orden, reverse=True)
    descartados = [a for a in hallazgos if a.descartado]

    L = ["# Tablero de arriendos", ""]
    L.append(f"Actualizado: {date.today().strftime('%d-%m-%Y')} · "
             f"{len(vivos)} candidatos · {len(descartados)} descartados")
    L.append("")

    if not vivos:
        L.append("_Sin candidatos en la última corrida._")
        L.append("")
    else:
        L.append("| # | Dirección | Comuna | Costo mensual | m² | D/B | Años | Puntaje |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, a in enumerate(vivos, 1):
            ficha = f"[{_corto(a)}](casos/{nombre_archivo(a)})"
            costo = _pesos(a.costo_mensual)
            if a.gastos_comunes_clp is None and a.arriendo_clp:
                costo += " *"
            m2 = _m2_tabla(a)
            db = f"{a.dormitorios or '—'}/{a.banos or '—'}"
            anos = (str(a.antiguedad_anos) if a.antiguedad_anos is not None
                    else "—")
            L.append(f"| {i} | {ficha} | {a.comuna or '—'} | {costo} | {m2} "
                     f"| {db} | {anos} | {a.score} |")
        L.append("")
        L.append("`*` = costo sin gastos comunes, porque el aviso no los publica.")
        L.append("`út.` = superficie útil; el aviso no publicó la total.")
        L.append("")

    if descartados:
        L.append("<details><summary>Descartados y por qué "
                 f"({len(descartados)})</summary>")
        L.append("")
        L.append("| Aviso | Motivo |")
        L.append("|---|---|")
        for a in sorted(descartados, key=lambda x: x.clase_descarte):
            L.append(f"| [{_corto(a)}]({a.url}) | {a.motivo_descarte} |")
        L.append("")
        L.append("</details>")
        L.append("")

    ruta.write_text("\n".join(L), encoding="utf-8")
    return ruta


def _corto(a: Arriendo) -> str:
    """El nombre del aviso para la tabla, sin la comuna repetida.

    La comuna tiene su propia columna. Repetida dentro de la dirección se come
    el ancho de la pantalla de un teléfono sin agregar nada.
    """
    texto = (a.direccion or a.title or a.url).strip()
    if a.comuna:
        sin_comuna = re.split(rf",\s*{re.escape(a.comuna)}\b", texto,
                              maxsplit=1, flags=re.I)[0].strip(" ,")
        texto = sin_comuna or texto
    # El pipe rompe la tabla de Markdown.
    return texto.replace("|", "/")[:60]


def _m2_tabla(a: Arriendo) -> str:
    """Los metros, diciendo cuáles son.

    El filtro del perfil es sobre la superficie TOTAL, así que una fila que
    muestra 118 sin aclarar que son útiles se lee como si ya hubiera pasado
    el filtro, cuando en realidad está pendiente de confirmar.
    """
    if a.m2_totales is not None:
        return _num(a.m2_totales)
    if a.m2_utiles is not None:
        return f"{_num(a.m2_utiles)} út."
    return "—"
