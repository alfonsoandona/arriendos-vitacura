"""Los dos dashboards HTML: todo el mercado, y las últimas 24 horas.

Pedido del usuario (18-08): "un dashboard de todos los avisos con un
resumen, un mapa, etc; y otro con los últimos en las 24 horas; que se vayan
pisando; dos archivos en git". Viven en `docs/` —`index.html` y `hoy.html`—
y cada corrida los reescribe entero: no acumulan historia, la historia vive
en el estado y en `alertas/historial.md`.

Decisiones de diseño, para que el próximo que toque esto no las deshaga:

- **El mapa es cartografía de verdad, con red mínima.** Pedido del usuario
  (18-08, segunda ronda): "que el mapa sea interactivo, que si selecciono
  sepa qué inmueble es". Leaflet va VENDORED en `docs/lib/` (copiado desde
  `arriendo/recursos/leaflet/`, BSD-2) — cero CDN, así el único tráfico
  externo son los tiles de OpenStreetMap, que no hay forma de embeber. Si
  Leaflet no carga (archivo ausente, file://, red caída), el SVG geométrico
  de siempre queda como respaldo: la página nunca muestra un hoyo.

- **Los datos van UNA vez, como JSON embebido** (`<script type=
  "application/json">`): la tabla server-rendered sigue siendo la vista sin
  JavaScript, y el mismo JSON alimenta marcadores, filtros y orden. El
  `</` se escapa a `<\\/` para que un título malicioso no cierre el script.

- **El color es estado, no identidad**: azul = nuevo en las últimas 24 h,
  gris neutro = el stock que ya estaba. La identidad nunca depende del
  color — hay leyenda, y la tabla es la vista accesible de todo lo que el
  mapa y los gráficos dicen. Par azul/gris validado en claro y oscuro
  (separación CVD ≥ 15, contraste ≥ 3:1 sobre ambas superficies). Los
  marcadores del mapa se pintan por CLASE CSS, no por opción de Leaflet,
  para que hereden el tema claro/oscuro.

- **Filtros de a uno y compartidos**: el buscador y los chips filtran tabla
  Y mapa a la vez, porque son la misma pregunta ("¿cuáles me importan
  ahora?") sobre dos vistas. Un filtro por vista sería mentirle a una.

- **Una fila por departamento**, misma regla que el tablero: dos registros
  de la misma ficha son el mismo inmueble.
"""

from __future__ import annotations

import html as _html
import json
import math
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import scoring as S
from .models import Arriendo
from .fichas import (_bajo_precio, _corto, _maps, _pesos,
                     _sin_fichas_repetidas, nombre_archivo, url_ficha)
from .tiempo import ahora_utc

KM_POR_GRADO = 111.32
LADO = 720
MARGEN = 40

_RECURSOS = Path(__file__).parent / "recursos" / "leaflet"


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------

def _primera_vez(a: Arriendo) -> datetime | None:
    pv = a.extras.get("primera_vez")
    if not pv:
        return None
    try:
        return datetime.fromisoformat(str(pv)[:19])
    except ValueError:
        return None


def _es_de_24h(a: Arriendo, ahora: datetime) -> bool:
    """¿Entró al radar en las últimas 24 horas?"""
    if a.extras.get("nuevo_en_corrida"):
        return True
    pv = _primera_vez(a)
    return pv is not None and (ahora - pv) <= timedelta(hours=24)


def _bajo_en_24h(a: Arriendo, ahora: datetime) -> bool:
    if not _bajo_precio(a):
        return False
    ultimo = (a.extras.get("historial_precio") or [{}])[-1].get("cuando", "")
    try:
        return (ahora.date() - datetime.fromisoformat(str(ultimo)).date()).days <= 1
    except ValueError:
        return False


def _dias(a: Arriendo, ahora: datetime) -> int | None:
    pv = _primera_vez(a)
    if pv is not None:
        return max(0, (ahora - pv).days)
    return a.dias_publicado


def _e(t) -> str:
    return _html.escape(str(t if t is not None else ""), quote=True)


def _link_ficha(a: Arriendo) -> str:
    return url_ficha(a) or f"../alertas/casos/{nombre_archivo(a)}"


def _m2(a: Arriendo) -> tuple[float | None, str]:
    m2 = a.m2_totales or a.m2_utiles
    return m2, (f"{m2:.0f}" + ("" if a.m2_totales else " út.")) if m2 else "—"


def _datos_json(avisos: list[Arriendo], ahora: datetime) -> str:
    """El JSON que alimenta mapa, filtros y orden. Un registro por fila."""
    datos = []
    for a in avisos:
        m2, m2txt = _m2(a)
        maps = _maps(a) or (
            f"https://www.google.com/maps?q={a.lat},{a.lon}"
            if a.lat is not None else "")
        datos.append({
            "cod": a.codigo,
            "lat": a.lat, "lon": a.lon,
            "dir": _corto(a),
            "score": a.score,
            "precio": a.costo_mensual,
            "ptxt": _pesos(a.costo_mensual),
            "m2": m2, "m2txt": m2txt,
            "db": f"{a.dormitorios or '—'}/{a.banos or '—'}",
            "piso": a.piso,
            "anos": a.antiguedad_anos,
            "dias": _dias(a, ahora),
            "nuevo": _es_de_24h(a, ahora),
            "bajo": _bajo_precio(a),
            "gest": (a.extras.get("gestion") or {}).get("estado", ""),
            "ficha": _link_ficha(a),
            "aviso": a.url,
            "maps": maps,
            "fuente": a.source,
        })
    # `</` cerraría el <script> que envuelve esto; escapado deja de poder.
    return json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------------------
# El mapa: Leaflet arriba, SVG geométrico como respaldo
# ---------------------------------------------------------------------------

def _proyectar(lat: float, lon: float, ancla: tuple[float, float],
               km_por_lado: float) -> tuple[float, float]:
    lat0, lon0 = ancla
    dx = (lon - lon0) * KM_POR_GRADO * math.cos(math.radians(lat0))
    dy = (lat - lat0) * KM_POR_GRADO
    escala = (LADO - 2 * MARGEN) / km_por_lado
    return LADO / 2 + dx * escala, LADO / 2 - dy * escala


def _mapa_svg(avisos: list[Arriendo], perfil: dict, ahora: datetime) -> str:
    """El respaldo geométrico: ancla, radios y puntos. '' si no hay nada."""
    ancla_cfg = perfil.get("ancla") or {}
    if ancla_cfg.get("lat") is None:
        return ""
    ancla = (float(ancla_cfg["lat"]), float(ancla_cfg["lon"]))
    nombre_ancla = ancla_cfg.get("nombre") or "el ancla"

    con_geo = [a for a in avisos if a.lat is not None and a.lon is not None]
    if not con_geo:
        return ""

    radios = perfil.get("radio_km") or {}
    r_pref = float(radios.get("preferente") or 0)
    r_anillo = float(radios.get("anillo") or 0)

    # El lado del mapa: que quepan los puntos del barrio, no el que quedó a
    # 30 km — ese comprimiría a los demás en una moneda. Tope 8 km de lado.
    dists = sorted(
        math.hypot((a.lon - ancla[1]) * KM_POR_GRADO
                   * math.cos(math.radians(ancla[0])),
                   (a.lat - ancla[0]) * KM_POR_GRADO) for a in con_geo)
    km_por_lado = min(8.0, max(3.0, 2.2 * (dists[int(len(dists) * 0.9)]
                                           if dists else 1.5)))
    escala = (LADO - 2 * MARGEN) / km_por_lado

    en_mapa = [a for a in con_geo
               if abs((a.lon - ancla[1]) * KM_POR_GRADO
                      * math.cos(math.radians(ancla[0]))) < km_por_lado / 2
               and abs((a.lat - ancla[0]) * KM_POR_GRADO) < km_por_lado / 2]

    P: list[str] = []
    P.append(f'<svg viewBox="0 0 {LADO} {LADO}" role="img" '
             f'aria-label="Mapa de candidatos alrededor de {_e(nombre_ancla)}">')
    # Los radios del perfil, del más ancho al más angosto.
    for r, texto in ((r_anillo, f"{r_anillo:g} km"),
                     (r_pref, f"{r_pref:g} km")):
        if r > 0:
            P.append(f'<circle cx="{LADO/2}" cy="{LADO/2}" r="{r*escala:.1f}" '
                     'class="anillo"/>')
            P.append(f'<text x="{LADO/2:.0f}" '
                     f'y="{LADO/2 - r*escala - 6:.1f}" class="anillo-txt" '
                     f'text-anchor="middle">{texto}</text>')
    # El ancla.
    P.append(f'<g class="ancla"><circle cx="{LADO/2}" cy="{LADO/2}" r="7"/>'
             f'<text x="{LADO/2}" y="{LADO/2 + 24}" text-anchor="middle" '
             f'class="ancla-txt">{_e(nombre_ancla)}</text></g>')

    for a in sorted(en_mapa, key=lambda x: _es_de_24h(x, ahora)):
        x, y = _proyectar(a.lat, a.lon, ancla, km_por_lado)
        clase = "punto nuevo" if _es_de_24h(a, ahora) else "punto"
        titulo = _corto(a)
        canon = _pesos(a.arriendo_clp) if a.arriendo_clp else "sin precio"
        destino = _maps(a) or a.url
        P.append(
            f'<a href="{_e(destino)}" target="_blank" rel="noopener">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" class="{clase}">'
            f'<title>{_e(titulo)} · {_e(canon)} · ⭐{a.score} · #{a.codigo}'
            f'</title></circle></a>')

    P.append("</svg>")
    return "".join(P)


def _mapa_card(avisos: list[Arriendo], perfil: dict, ahora: datetime) -> str:
    """La sección del mapa: Leaflet si hay ancla, SVG o texto de respaldo."""
    ancla_cfg = perfil.get("ancla") or {}
    con_geo = [a for a in avisos if a.lat is not None and a.lon is not None]
    llenandose = ('<p class="vacio"><b>El mapa se está llenando.</b><br>'
                  "Las direcciones se ubican de a 25 por corrida (cortesía "
                  "con OpenStreetMap); en un par de corridas los puntos "
                  "aparecen solos acá.</p>")
    if ancla_cfg.get("lat") is None:
        if not con_geo:
            return (f'<section class="card"><h2>📍 El barrio</h2>'
                    f"{llenandose}</section>")
        return ""

    radios = perfil.get("radio_km") or {}
    svg = _mapa_svg(avisos, perfil, ahora)
    respaldo = f'<div class="mapa">{svg}</div>' if svg else llenandose
    sin_geo = len(avisos) - len(con_geo)
    pie = (f"{len(con_geo)} en el mapa · {sin_geo} aún sin ubicar. "
           "Tocar un punto muestra QUÉ departamento es, con sus links. "
           "En el teléfono, el mapa se mueve con dos dedos.")
    leyenda = ('<span class="lg"><i class="dot nuevo"></i> nuevo (24 h)</span>'
               '<span class="lg"><i class="dot"></i> ya estaba</span>'
               '<span class="lg">⊕ ' + _e(ancla_cfg.get("nombre") or "el ancla")
               + "</span>")
    return (
        '<section class="card" id="card-mapa"><h2>📍 El barrio</h2>'
        f'<div class="leyenda">{leyenda}</div>'
        f'<div id="mapa" hidden data-lat="{float(ancla_cfg["lat"])}" '
        f'data-lon="{float(ancla_cfg["lon"])}" '
        f'data-nombre="{_e(ancla_cfg.get("nombre") or "el ancla")}" '
        f'data-rpref="{float(radios.get("preferente") or 0):g}" '
        f'data-ranillo="{float(radios.get("anillo") or 0):g}"></div>'
        f'<div id="mapa-svg">{respaldo}</div>'
        f'<p class="pie">{pie}</p></section>')


def _histograma_svg(avisos: list[Arriendo], perfil: dict) -> str:
    """Dónde están los precios, con el tope del perfil marcado."""
    precios = sorted(a.arriendo_clp for a in avisos if a.arriendo_clp)
    if len(precios) < 4:
        return ""
    tope, _holgura = S.tope_arriendo(perfil)
    paso = 200_000
    lo = int(precios[0] // paso * paso)
    hi = int(precios[-1] // paso * paso + paso)
    bins: dict[int, int] = {}
    for p in precios:
        b = int(p // paso * paso)
        bins[b] = bins.get(b, 0) + 1
    maximo = max(bins.values())
    mediana = precios[len(precios) // 2]

    W, H, MX, MY = 720, 220, 46, 30
    n_bins = (hi - lo) // paso
    ancho = (W - 2 * MX) / max(1, n_bins)

    P = [f'<svg viewBox="0 0 {W} {H}" role="img" '
         'aria-label="Distribución de cánones">']
    for i in range(n_bins):
        b = lo + i * paso
        c = bins.get(b, 0)
        if not c:
            continue
        alto = (H - 2 * MY) * c / maximo
        x = MX + i * ancho
        P.append(f'<rect x="{x + 1:.1f}" y="{H - MY - alto:.1f}" '
                 f'width="{ancho - 2:.1f}" height="{alto:.1f}" rx="4" '
                 f'class="barra"><title>{_pesos(b)}–{_pesos(b + paso)}: '
                 f'{c} aviso{"s" if c != 1 else ""}</title></rect>')
    # La base, el tope del perfil y la mediana.
    P.append(f'<line x1="{MX}" y1="{H - MY}" x2="{W - MX}" y2="{H - MY}" '
             'class="eje"/>')
    for valor, clase, texto in ((tope, "tope", f"tope {_pesos(tope)}"),
                                (mediana, "mediana",
                                 f"mediana {_pesos(mediana)}")):
        if valor and lo <= valor <= hi:
            x = MX + (valor - lo) / (hi - lo) * (W - 2 * MX)
            P.append(f'<line x1="{x:.1f}" y1="{MY - 10}" x2="{x:.1f}" '
                     f'y2="{H - MY}" class="{clase}"/>')
            anchor = "end" if valor > (lo + hi) / 2 else "start"
            P.append(f'<text x="{x + (-6 if anchor == "end" else 6):.1f}" '
                     f'y="{MY - 14}" class="marca-txt {clase}-txt" '
                     f'text-anchor="{anchor}">{texto}</text>')
    P.append(f'<text x="{MX}" y="{H - 8}" class="eje-txt">{_pesos(lo)}</text>')
    P.append(f'<text x="{W - MX}" y="{H - 8}" class="eje-txt" '
             f'text-anchor="end">{_pesos(hi)}</text>')
    P.append("</svg>")
    return (f'<section class="card"><h2>💰 Dónde están los precios</h2>'
            f'<div class="histo">{"".join(P)}</div>'
            f'<p class="pie">{len(precios)} candidatos con canon publicado. '
            f'Sobre el tope entran igual hasta la holgura del perfil, '
            f'con el puntaje penalizado.</p></section>')


# ---------------------------------------------------------------------------
# Filtros y tabla
# ---------------------------------------------------------------------------

def _filtros(total: int) -> str:
    """La barra pegajosa que filtra tabla Y mapa a la vez."""
    chips = "".join(
        f'<button type="button" data-modo="{m}"'
        f'{" class=activo" if m == "todos" else ""}>{t}</button>'
        for m, t in (("todos", "Todos"), ("nuevos", "🆕 Nuevos"),
                     ("bajaron", "📉 Bajaron"), ("mapa", "📍 En el mapa"),
                     ("gestion", "📞 Con gestión")))
    return (
        '<section class="filtros" id="filtros">'
        '<input id="filtro" type="search" '
        'placeholder="Buscar calle, código, portal…" '
        'aria-label="Buscar en la tabla y el mapa">'
        f'<div class="chips" role="group" aria-label="Filtrar">{chips}</div>'
        '<select id="orden" aria-label="Ordenar">'
        '<option value="score">⭐ mejor puntaje</option>'
        '<option value="precio">$ más barato</option>'
        '<option value="m2">m² más grande</option>'
        '<option value="dias">más recién publicado</option>'
        '<option value="anos">menos años</option></select>'
        f'<span id="cuenta" class="cuenta">{total} avisos</span>'
        "</section>")


def _fila_tabla(a: Arriendo, ahora: datetime) -> str:
    dias = _dias(a, ahora)
    gest = (a.extras.get("gestion") or {}).get("estado", "")
    marca = {"visita": "📅", "contactado": "📞", "visto": "👁"}.get(gest, "")
    chips = ("<span class=chip>🆕</span>" if _es_de_24h(a, ahora) else "") + \
        (f"<span class=chip>{marca}</span>" if marca else "") + \
        ("<span class=chip>📉</span>" if _bajo_precio(a) else "")
    links = [f'<a href="{_e(_link_ficha(a))}">ficha</a>',
             f'<a href="{_e(a.url)}" target="_blank" rel="noopener">aviso</a>']
    if a.lat is not None:
        links.append(f'<a href="#card-mapa" data-mapa="{_e(a.codigo)}">mapa</a>')
    if (maps := _maps(a)):
        links.append(f'<a href="{_e(maps)}" target="_blank" '
                     f'rel="noopener">📍</a>')
    m2, m2txt = _m2(a)

    def td(texto, rotulo="", v=None) -> str:
        # Un dato ausente no aporta en la tarjeta del teléfono: la clase
        # `nada` lo esconde ahí (en el escritorio la columna se queda, que
        # la grilla alineada es lo que hace comparable a la tabla).
        clase = ' class="nada"' if str(texto) in ("—", "—/—") else ""
        datal = f' data-l="{rotulo}"' if rotulo else ""
        datav = f' data-v="{v}"' if v is not None else ""
        return f"<td{clase}{datal}{datav}>{texto}</td>"

    canon = _pesos(a.costo_mensual) + (
        "" if a.gastos_comunes_clp is not None or not a.arriendo_clp else " *")
    return (
        f'<tr id="r-{_e(a.codigo)}" data-cod="{_e(a.codigo)}">'
        f'<td data-l="⭐" data-v="{a.score}"><b>{a.score}</b></td>'
        f"<td><code>#{a.codigo}</code></td>"
        f'<td class="dir">{_e(_corto(a))} {chips}<br>'
        f'<span class="links">{" · ".join(links)}</span></td>'
        + td(canon, "$/mes", a.costo_mensual or 0)
        + td(_pesos(a.gastos_comunes_clp), "GC", a.gastos_comunes_clp or 0)
        + td(m2txt, "m²", m2 or 0)
        + td(f'{a.dormitorios or "—"}/{a.banos or "—"}', "D/B")
        + td(a.piso if a.piso is not None else "—", "piso")
        + td(a.antiguedad_anos if a.antiguedad_anos is not None else "—",
             "años")
        + td(dias if dias is not None else "—", "días",
             dias if dias is not None else -1)
        + "</tr>")


def _tabla(avisos: list[Arriendo], ahora: datetime) -> str:
    filas = "\n".join(_fila_tabla(a, ahora) for a in avisos)
    cab = "".join(
        f'<th data-col="{i}" data-num="{n}">{t}</th>' for i, (t, n) in
        enumerate((("⭐", 1), ("Código", 0), ("Dirección", 0),
                   ("$/mes", 1), ("GC", 1), ("m²", 1), ("D/B", 0),
                   ("Piso", 0), ("Años", 0), ("Días", 1))))
    return (
        '<section class="card"><h2>📋 Todos, para comparar</h2>'
        f'<div class="scroll"><table id="tabla"><thead><tr>{cab}</tr></thead>'
        f"<tbody>{filas}</tbody></table></div>"
        '<p class="pie">`*` = costo sin gastos comunes (el aviso no los '
        "publica). Tocar una columna la ordena; tocar un código lo copia "
        "(para anotarlo en gestion.yml).</p></section>")


# ---------------------------------------------------------------------------
# La página
# ---------------------------------------------------------------------------

_CSS = """
:root { color-scheme: light dark;
  --sup:#fcfcfb; --sup2:#f3f2ef; --tinta:#0b0b0b; --tinta2:#52514e;
  --borde:#e3e2de; --azul:#2a78d6; --gris:#8b8a85; --rojo:#c62f2f;
  --verde:#1d7a33; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --sup:#1a1a19; --sup2:#242423; --tinta:#f4f4f2; --tinta2:#c3c2b7;
  --borde:#3a3937; --azul:#3987e5; --gris:#8b8a85; --rojo:#e66767;
  --verde:#4fae63; } }
:root[data-theme="dark"] {
  --sup:#1a1a19; --sup2:#242423; --tinta:#f4f4f2; --tinta2:#c3c2b7;
  --borde:#3a3937; --azul:#3987e5; --gris:#8b8a85; --rojo:#e66767;
  --verde:#4fae63; }
* { box-sizing:border-box; }
body { margin:0; background:var(--sup); color:var(--tinta);
  font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width:860px; margin:0 auto; padding:12px 14px 48px; }
header.top { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center;
  padding:14px 2px 4px; }
header.top h1 { font-size:1.25rem; margin:0; }
nav a { text-decoration:none; color:var(--azul); font-weight:600;
  padding:4px 10px; border-radius:999px; }
nav a.activo { background:var(--azul); color:#fff; }
#tema { margin-left:auto; font:inherit; font-size:1.05rem; line-height:1;
  background:var(--sup2); border:1px solid var(--borde); border-radius:999px;
  padding:6px 10px; cursor:pointer; color:var(--tinta); }
.sello { color:var(--tinta2); font-size:.85rem; width:100%; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
  gap:10px; margin:10px 0; }
.kpi { background:var(--sup2); border:1px solid var(--borde);
  border-radius:12px; padding:10px 12px; }
.kpi b { display:block; font-size:1.5rem; line-height:1.1; }
.kpi span { color:var(--tinta2); font-size:.8rem; }
.kpi[data-modo] { cursor:pointer; }
.kpi[data-modo]:hover, .kpi[data-modo]:focus-visible {
  border-color:var(--azul); outline:none; }
.card { background:var(--sup2); border:1px solid var(--borde);
  border-radius:14px; padding:14px 14px 10px; margin:14px 0; }
.card h2 { font-size:1.02rem; margin:0 0 8px; }
.pie { color:var(--tinta2); font-size:.8rem; margin:8px 0 2px; }
.leyenda { display:flex; gap:14px; font-size:.82rem; color:var(--tinta2);
  margin-bottom:6px; flex-wrap:wrap; }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%;
  background:var(--gris); margin-right:4px; }
.dot.nuevo { background:var(--azul); }

/* --- la barra de filtros, pegada arriba --- */
.filtros { position:sticky; top:0; z-index:1100; background:var(--sup);
  display:flex; flex-wrap:wrap; gap:8px; align-items:center;
  padding:10px 0 8px; border-bottom:1px solid var(--borde); }
.filtros input { flex:1 1 190px; padding:8px 12px; font:inherit;
  border:1px solid var(--borde); border-radius:10px; background:var(--sup2);
  color:var(--tinta); min-width:0; }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chips button { font:inherit; font-size:.82rem; padding:5px 11px;
  border-radius:999px; border:1px solid var(--borde); background:var(--sup2);
  color:var(--tinta); cursor:pointer; white-space:nowrap; }
.chips button.activo { background:var(--azul); border-color:var(--azul);
  color:#fff; }
#orden { font:inherit; font-size:.82rem; padding:5px 8px; border-radius:10px;
  border:1px solid var(--borde); background:var(--sup2); color:var(--tinta); }
.cuenta { color:var(--tinta2); font-size:.82rem; margin-left:auto;
  white-space:nowrap; }

/* --- mapa --- */
#mapa { height:440px; border-radius:10px; z-index:0; }
@media (max-width:680px) { #mapa { height:340px; } }
.leaflet-container { background:var(--sup2); font:inherit; }
:root[data-theme="dark"] .leaflet-tile-pane { filter:invert(1)
  hue-rotate(180deg) brightness(.9) contrast(.9) saturate(.6); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .leaflet-tile-pane { filter:invert(1)
    hue-rotate(180deg) brightness(.9) contrast(.9) saturate(.6); } }
.pt { fill:var(--gris); fill-opacity:.88; stroke:var(--sup);
  stroke-width:2; cursor:pointer; }
.pt-nuevo { fill:var(--azul); }
.pt-ancla { fill:var(--tinta); stroke:var(--sup); stroke-width:2; }
.lbl-ancla { background:transparent; border:none; box-shadow:none;
  color:var(--tinta); font-weight:600; font-size:.8rem; }
.lbl-ancla::before { display:none; }
.anillo-mapa { stroke:var(--gris); stroke-width:1.5;
  stroke-dasharray:6 6; fill:none; }
.anillo-mapa.pref { stroke:var(--azul); }
.leaflet-popup-content-wrapper, .leaflet-tooltip { background:var(--sup);
  color:var(--tinta); border:1px solid var(--borde); }
.leaflet-popup-tip { background:var(--sup); }
.leaflet-tooltip { font-size:.8rem; }
.leaflet-bar a { background:var(--sup); color:var(--tinta);
  border-bottom-color:var(--borde); }
.leaflet-container .leaflet-control-attribution { background:var(--sup);
  color:var(--tinta2); }
.pop { font-size:.85rem; line-height:1.55; }
.pop-dir { font-weight:600; font-size:.92rem; }
.pop-datos { color:var(--tinta2); }
.pop-links a { color:var(--azul); }

/* --- SVG de respaldo e histograma --- */
.mapa svg, .histo svg { width:100%; height:auto; display:block; }
.anillo { fill:none; stroke:var(--borde); stroke-width:2;
  stroke-dasharray:6 6; }
.anillo-txt, .eje-txt, .marca-txt, .ancla-txt { fill:var(--tinta2);
  font-size:13px; }
.ancla circle { fill:var(--tinta); }
.punto { fill:var(--gris); fill-opacity:.85; stroke:var(--sup);
  stroke-width:2; }
.punto.nuevo { fill:var(--azul); }
.punto:hover { fill-opacity:1; }
.barra { fill:var(--azul); }
.eje { stroke:var(--borde); stroke-width:2; }
.tope { stroke:var(--rojo); stroke-width:2; }
.mediana { stroke:var(--tinta2); stroke-width:2; stroke-dasharray:4 4; }
.tope-txt { fill:var(--rojo); }

/* --- tabla --- */
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.86rem;
  min-width:640px; }
th { text-align:left; cursor:pointer; position:sticky; top:0;
  background:var(--sup2); border-bottom:2px solid var(--borde);
  padding:6px 8px; white-space:nowrap; user-select:none; }
td { border-bottom:1px solid var(--borde); padding:6px 8px;
  vertical-align:top; }
td.dir { min-width:200px; }
#tabla tr[hidden] { display:none !important; }
tr.destello { animation:destello 1.8s ease-out; }
@keyframes destello { 0%,55% { background:rgba(42,120,214,.22); }
  100% { background:transparent; } }
.links { font-size:.78rem; } .links a { color:var(--azul); }
.chip { font-size:.8rem; margin-left:2px; }
code { background:var(--sup); padding:1px 5px; border-radius:6px;
  font-size:.8rem; cursor:copy; }
code.copiado { outline:1px solid var(--verde); }
.vacio { text-align:center; padding:40px 10px; color:var(--tinta2); }
.vacio b { font-size:1.1rem; color:var(--tinta); }
footer { color:var(--tinta2); font-size:.8rem; margin-top:18px; }
footer a { color:var(--azul); }

/* --- en pantalla angosta, la tabla se vuelve tarjetas --- */
@media (max-width:680px) {
  table { min-width:0; }
  #tabla thead { display:none; }
  #tabla tbody { display:block; }
  #tabla tbody tr { display:flex; flex-wrap:wrap; gap:2px 14px;
    border:1px solid var(--borde); border-radius:12px; margin:10px 0;
    padding:10px 12px; background:var(--sup); }
  #tabla td { display:flex; gap:5px; border:none; padding:0;
    align-items:baseline; font-size:.84rem; }
  #tabla td::before { content:attr(data-l); color:var(--tinta2);
    font-size:.72rem; }
  #tabla td.dir { order:-1; width:100%; display:block; font-size:.95rem;
    min-width:0; }
  #tabla td.dir::before { content:none; }
  #tabla td.nada { display:none; }
  .chips { flex-wrap:nowrap; overflow-x:auto; max-width:100%;
    -webkit-overflow-scrolling:touch; scrollbar-width:none; }
  .chips::-webkit-scrollbar { display:none; }
}
"""

_JS = """
'use strict';
var MAPA = null, CAPA = null, MARCAS = null;
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const DATOS = (() => {
  try { return JSON.parse($('#datos').textContent); } catch (e) { return []; }
})();
const XCOD = {};
DATOS.forEach(d => { XCOD[d.cod] = d; });
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---- tema claro/oscuro, con memoria ---- */
const bTema = $('#tema');
if (bTema) bTema.onclick = () => {
  const raiz = document.documentElement;
  const oscuro = raiz.dataset.theme
    ? raiz.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  raiz.dataset.theme = oscuro ? 'light' : 'dark';
  try { localStorage.setItem('tema', raiz.dataset.theme); } catch (e) {}
};

/* ---- filtros: tabla y mapa a la vez ---- */
const estado = { q: '', modo: 'todos' };
const PRUEBA = {
  todos: d => true,
  nuevos: d => !!(d && d.nuevo),
  bajaron: d => !!(d && d.bajo),
  gestion: d => !!(d && d.gest),
  mapa: d => !!(d && d.lat != null),
};
function pasa(fila) {
  const d = XCOD[fila.dataset.cod];
  if (!(PRUEBA[estado.modo] || PRUEBA.todos)(d)) return false;
  return !estado.q || fila.textContent.toLowerCase().includes(estado.q);
}
function aplicar() {
  const filas = $$('#tabla tbody tr');
  let vis = 0;
  filas.forEach(f => { const ok = pasa(f); f.hidden = !ok; if (ok) vis++; });
  const n = $('#cuenta');
  if (n) n.textContent = vis === filas.length
    ? filas.length + ' avisos' : vis + ' de ' + filas.length;
  $$('.chips button').forEach(b =>
    b.classList.toggle('activo', b.dataset.modo === estado.modo));
  if (MARCAS) for (const cod in MARCAS) {
    const fila = document.getElementById('r-' + cod);
    if (!fila || !fila.hidden) MARCAS[cod].addTo(CAPA);
    else CAPA.removeLayer(MARCAS[cod]);
  }
}
const busca = $('#filtro');
if (busca) busca.oninput = () => {
  estado.q = busca.value.trim().toLowerCase(); aplicar();
};
$$('.chips button').forEach(b => b.onclick = () => {
  estado.modo = b.dataset.modo; aplicar();
});
$$('.kpi[data-modo]').forEach(k => {
  const activar = () => { estado.modo = k.dataset.modo; aplicar(); };
  k.onclick = activar;
  k.onkeydown = ev => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activar(); }
  };
});

/* ---- orden: por columna (escritorio) o por selector (tarjetas) ---- */
$$('#tabla th').forEach(th => th.onclick = () => {
  const t = th.closest('table'), tb = t.tBodies[0];
  const i = +th.dataset.col, num = th.dataset.num === '1';
  const asc = th.dataset.asc !== '1';
  t.querySelectorAll('th').forEach(o => delete o.dataset.asc);
  th.dataset.asc = asc ? '1' : '0';
  Array.from(tb.rows).sort((a, b) => {
    const ca = a.cells[i], cb = b.cells[i];
    const va = num ? +(ca.dataset.v ?? ca.textContent) : ca.textContent.trim();
    const vb = num ? +(cb.dataset.v ?? cb.textContent) : cb.textContent.trim();
    return (va > vb ? 1 : va < vb ? -1 : 0) * (asc ? 1 : -1);
  }).forEach(r => tb.appendChild(r));
});
const CLAVES = {
  score: [d => d ? d.score : -1, -1],
  precio: [d => d && d.precio != null ? d.precio : Infinity, 1],
  m2: [d => d && d.m2 != null ? d.m2 : -1, -1],
  dias: [d => d && d.dias != null ? d.dias : Infinity, 1],
  anos: [d => d && d.anos != null ? d.anos : Infinity, 1],
};
const orden = $('#orden');
if (orden) orden.onchange = () => {
  const par = CLAVES[orden.value] || CLAVES.score;
  const tb = $('#tabla tbody');
  if (!tb) return;
  Array.from(tb.rows).sort((a, b) =>
    (par[0](XCOD[a.dataset.cod]) - par[0](XCOD[b.dataset.cod])) * par[1]
  ).forEach(r => tb.appendChild(r));
};

/* ---- el mapa de verdad (si Leaflet cargó) ---- */
(function () {
  const cont = document.getElementById('mapa');
  if (!cont || typeof L === 'undefined') return;
  const svg = document.getElementById('mapa-svg');
  if (svg) svg.hidden = true;
  cont.hidden = false;

  const lat0 = +cont.dataset.lat, lon0 = +cont.dataset.lon;
  // En el teléfono, arrastrar con un dedo secuestraría el scroll de la
  // página: ahí el mapa se mueve con dos dedos (pellizco) y el pie lo dice.
  MAPA = L.map(cont, { scrollWheelZoom: false, dragging: !L.Browser.mobile });
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">' +
      'OpenStreetMap</a>',
  }).addTo(MAPA);

  const rPref = +cont.dataset.rpref || 0, rAnillo = +cont.dataset.ranillo || 0;
  if (rAnillo) L.circle([lat0, lon0], { radius: rAnillo * 1000, fill: false,
    className: 'anillo-mapa' }).addTo(MAPA);
  if (rPref) L.circle([lat0, lon0], { radius: rPref * 1000, fill: false,
    className: 'anillo-mapa pref' }).addTo(MAPA);
  L.circleMarker([lat0, lon0], { radius: 7, className: 'pt-ancla' })
    .addTo(MAPA)
    .bindTooltip(cont.dataset.nombre, { permanent: true, direction: 'bottom',
      className: 'lbl-ancla', offset: [0, 8] });

  function ficha(d) {
    const chips = (d.nuevo ? ' 🆕' : '') + (d.bajo ? ' 📉' : '')
      + (d.gest ? ' · ' + esc(d.gest) : '');
    const datos = [d.precio != null ? d.ptxt + '/mes' : 'sin precio',
      d.m2txt !== '—' ? d.m2txt + ' m²' : '',
      d.db !== '—/—' ? 'D/B ' + esc(d.db) : '',
      d.piso != null ? 'piso ' + d.piso : '',
      d.anos != null ? d.anos + ' años' : '',
      '⭐ ' + d.score].filter(Boolean).join(' · ');
    const links = ['<a href="' + esc(d.ficha) + '">ficha</a>',
      '<a href="' + esc(d.aviso) + '" target="_blank" rel="noopener">aviso</a>',
      d.maps ? '<a href="' + esc(d.maps)
        + '" target="_blank" rel="noopener">Google Maps</a>' : '',
      '<a href="#r-' + esc(d.cod) + '" data-fila="' + esc(d.cod)
        + '">ver en la tabla</a>'].filter(Boolean).join(' · ');
    return '<div class="pop"><code>#' + esc(d.cod) + '</code>' + chips
      + '<div class="pop-dir">' + esc(d.dir) + '</div>'
      + '<div class="pop-datos">' + datos + '</div>'
      + '<div class="pop-links">' + links + '</div></div>';
  }

  CAPA = L.layerGroup().addTo(MAPA);
  MARCAS = {};
  const vistos = {}, puntos = [];
  DATOS.filter(d => d.lat != null && d.lon != null).forEach(d => {
    const k = d.lat.toFixed(5) + ',' + d.lon.toFixed(5);
    const n = vistos[k] = (vistos[k] || 0) + 1;
    let lat = d.lat, lon = d.lon;
    if (n > 1) {   // dos geocodificados al mismo punto: se separan tantito
      lat += 1.4e-4 * Math.sin(n * 2.4);
      lon += 1.4e-4 * Math.cos(n * 2.4);
    }
    const m = L.circleMarker([lat, lon], { radius: 9,
      className: 'pt' + (d.nuevo ? ' pt-nuevo' : '') });
    m.bindTooltip(esc(d.dir) + ' · ' + esc(d.ptxt));
    m.bindPopup(ficha(d), { maxWidth: 300 });
    m.addTo(CAPA);
    MARCAS[d.cod] = m;
    puntos.push([lat, lon]);
  });

  // El encuadre inicial: el barrio, no el punto que quedó a 30 km.
  const cerca = puntos.filter(p => MAPA.distance(p, [lat0, lon0]) < 4000);
  const zona = L.latLngBounds(cerca.length ? cerca : [[lat0, lon0]]);
  zona.extend([lat0, lon0]);
  if (rPref) zona.extend(L.latLng(lat0, lon0).toBounds(rPref * 2200));
  MAPA.fitBounds(zona.pad(0.08), { maxZoom: 15 });
})();

/* ---- links cruzados: mapa → fila, fila → mapa ---- */
document.addEventListener('click', ev => {
  const aFila = ev.target.closest('[data-fila]');
  if (aFila) {
    ev.preventDefault();
    const fila = document.getElementById('r-' + aFila.dataset.fila);
    if (!fila) return;
    if (fila.hidden) {
      estado.q = ''; estado.modo = 'todos';
      if (busca) busca.value = '';
      aplicar();
    }
    if (MAPA) MAPA.closePopup();
    fila.scrollIntoView({ behavior: 'smooth', block: 'center' });
    fila.classList.remove('destello');
    void fila.offsetWidth;
    fila.classList.add('destello');
    return;
  }
  const alMapa = ev.target.closest('[data-mapa]');
  if (alMapa && MARCAS && MARCAS[alMapa.dataset.mapa]) {
    ev.preventDefault();
    const m = MARCAS[alMapa.dataset.mapa];
    document.getElementById('card-mapa')
      .scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (!MAPA.hasLayer(CAPA) || !CAPA.hasLayer(m)) m.addTo(CAPA);
    MAPA.setView(m.getLatLng(), Math.max(MAPA.getZoom(), 16));
    m.openPopup();
    return;
  }
  // Tocar un código lo copia, listo para pegarlo en gestion.yml.
  const cod = ev.target.closest('td code, .pop code');
  if (cod && navigator.clipboard) {
    navigator.clipboard.writeText(cod.textContent.replace('#', ''))
      .then(() => {
        cod.classList.add('copiado');
        setTimeout(() => cod.classList.remove('copiado'), 900);
      }).catch(() => {});
  }
});

aplicar();
"""


def _kpis(vivos, nuevos, bajaron, con_geo, mediana) -> str:
    piezas = [
        (str(len(vivos)), "candidatos vivos", "todos"),
        (str(len(nuevos)), "nuevos en 24 h", "nuevos"),
        (str(len(bajaron)), "bajando de precio", "bajaron"),
        (_pesos(mediana) if mediana else "—", "canon mediano", ""),
        (str(len(con_geo)), "ubicados en el mapa", "mapa"),
    ]
    piezas_html = []
    for v, t, modo in piezas:
        extra = (f' data-modo="{modo}" role="button" tabindex="0" '
                 f'title="Filtrar: {_e(t)}"') if modo else ""
        piezas_html.append(
            f"<div class=kpi{extra}><b>{_e(v)}</b><span>{_e(t)}</span></div>")
    return '<div class="kpis">' + "".join(piezas_html) + "</div>"


def _pagina(titulo: str, activo: str, cuerpo: str, ahora: datetime,
            datos_json: str = "[]") -> str:
    sello = ahora.strftime("%d-%m-%Y %H:%M") + " UTC"
    nav = (f'<nav><a href="index.html"'
           f'{" class=activo" if activo == "todos" else ""}>Todos</a>'
           f'<a href="hoy.html"'
           f'{" class=activo" if activo == "hoy" else ""}>Últimas 24 h</a>'
           "</nav>")
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{_e(titulo)}</title>
<script>try{{const t=localStorage.getItem('tema');
if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<link rel="stylesheet" href="lib/leaflet.css">
<style>{_CSS}</style></head>
<body><main>
<header class="top"><h1>📡 {_e(titulo)}</h1>{nav}
<button id="tema" type="button" title="Cambiar tema"
 aria-label="Cambiar entre tema claro y oscuro">🌗</button>
<span class="sello">Actualizado: {sello} · se pisa en cada corrida</span>
</header>
{cuerpo}
<footer>El detalle de cada uno vive en su ficha. ¿Viste o llamaste por
alguno? Anótalo en <a
href="https://github.com/alfonsoandona/arriendos-vitacura/edit/claude/telegram-vitacura-rentals-alert-48wnuy/gestion.yml">gestion.yml</a>
con su código — un <code>descartado</code> no vuelve a sonar.</footer>
</main>
<script type="application/json" id="datos">{datos_json}</script>
<script src="lib/leaflet.js"></script>
<script>{_JS}</script></body></html>"""


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def _copiar_leaflet(directorio: Path) -> None:
    """Leaflet vendored a docs/lib/, junto a su licencia (BSD-2)."""
    destino = directorio / "lib"
    destino.mkdir(parents=True, exist_ok=True)
    for nombre in ("leaflet.js", "leaflet.css", "LICENSE"):
        origen = _RECURSOS / nombre
        if origen.exists():
            shutil.copyfile(origen, destino / nombre)


def escribir_dashboards(hallazgos: list[Arriendo], directorio: Path,
                        perfil: dict | None = None) -> tuple[Path, Path]:
    """Escribe docs/index.html y docs/hoy.html. Se pisan en cada corrida."""
    perfil = perfil or {}
    directorio.mkdir(parents=True, exist_ok=True)
    _copiar_leaflet(directorio)
    ahora = ahora_utc()

    vivos = _sin_fichas_repetidas(
        sorted([a for a in hallazgos if not a.descartado],
               key=S.orden, reverse=True))
    nuevos = [a for a in vivos if _es_de_24h(a, ahora)]
    bajaron = [a for a in vivos if _bajo_precio(a)]
    con_geo = [a for a in vivos if a.lat is not None]
    precios = sorted(a.arriendo_clp for a in vivos if a.arriendo_clp)
    mediana = precios[len(precios) // 2] if precios else None

    cuerpo_todos = (
        _kpis(vivos, nuevos, bajaron, con_geo, mediana)
        + _filtros(len(vivos))
        + _mapa_card(vivos, perfil, ahora)
        + _histograma_svg(vivos, perfil)
        + _tabla(vivos, ahora))
    indice = directorio / "index.html"
    indice.write_text(
        _pagina("Radar de Arriendos · Vitacura", "todos", cuerpo_todos, ahora,
                _datos_json(vivos, ahora)),
        encoding="utf-8")

    de_hoy = _sin_fichas_repetidas(
        nuevos + [a for a in bajaron
                  if _bajo_en_24h(a, ahora) and a not in nuevos])
    if de_hoy:
        cuerpo_hoy = (
            _kpis(de_hoy, nuevos, [a for a in de_hoy if _bajo_precio(a)],
                  [a for a in de_hoy if a.lat is not None], mediana)
            + _filtros(len(de_hoy))
            + _mapa_card(de_hoy, perfil, ahora)
            + _tabla(de_hoy, ahora))
        datos_hoy = _datos_json(de_hoy, ahora)
    else:
        cuerpo_hoy = ('<section class="card"><p class="vacio">'
                      "<b>Sin novedades en las últimas 24 horas.</b><br>"
                      "Ni avisos nuevos ni bajas de precio. El mercado de "
                      "arriendo se mueve en días: mañana puede haber."
                      "</p></section>")
        datos_hoy = "[]"
    hoy = directorio / "hoy.html"
    hoy.write_text(
        _pagina("Últimas 24 horas · Vitacura", "hoy", cuerpo_hoy, ahora,
                datos_hoy),
        encoding="utf-8")
    return indice, hoy
