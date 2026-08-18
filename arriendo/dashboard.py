"""Los dos dashboards HTML: todo el mercado, y las últimas 24 horas.

Pedido del usuario (18-08): "un dashboard de todos los avisos con un
resumen, un mapa, etc; y otro con los últimos en las 24 horas; que se vayan
pisando; dos archivos en git". Viven en `docs/` —`index.html` y `hoy.html`—
y cada corrida los reescribe entero: no acumulan historia, la historia vive
en el estado y en `alertas/historial.md`.

Decisiones de diseño, para que el próximo que toque esto no las deshaga:

- **Autocontenidos.** Cero CDN, cero tiles de mapa, cero librerías: el
  archivo abre desde el repo, sin conexión y sin que nadie sepa qué se está
  mirando. Los gráficos son SVG calculado acá en Python; el único JavaScript
  es ordenar y filtrar la tabla.

- **El mapa es geometría, no cartografía** (heredado del radar de remates):
  el ancla al centro, los radios del perfil, y cada departamento donde sus
  coordenadas lo pongan. Un fondo de calles obligaría a pedir tiles. Cada
  punto abre Google Maps al tocarlo, que es donde uno quiere terminar.

- **El color es estado, no identidad**: azul = nuevo en las últimas 24 h,
  gris neutro = el stock que ya estaba. El gris es deliberadamente neutro
  (recesivo); la identidad nunca depende del color — hay leyenda, y la
  tabla es la vista accesible de todo lo que el mapa y los gráficos dicen.
  Par azul/gris validado en claro y oscuro (separación CVD ≥ 15, contraste
  ≥ 3:1 sobre ambas superficies).

- **Una fila por departamento**, misma regla que el tablero: dos registros
  de la misma ficha son el mismo inmueble.
"""

from __future__ import annotations

import html as _html
import math
import re
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


# ---------------------------------------------------------------------------
# Piezas SVG
# ---------------------------------------------------------------------------

def _proyectar(lat: float, lon: float, ancla: tuple[float, float],
               km_por_lado: float) -> tuple[float, float]:
    lat0, lon0 = ancla
    dx = (lon - lon0) * KM_POR_GRADO * math.cos(math.radians(lat0))
    dy = (lat - lat0) * KM_POR_GRADO
    escala = (LADO - 2 * MARGEN) / km_por_lado
    return LADO / 2 + dx * escala, LADO / 2 - dy * escala


def _mapa_svg(avisos: list[Arriendo], perfil: dict, ahora: datetime) -> str:
    """El mapa del barrio. '' si no hay nada que dibujar."""
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
    fuera = len(con_geo) - len(en_mapa)

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
    pie = (f"{len(en_mapa)} en el mapa"
           + (f" · {fuera} con coordenadas fuera de este encuadre" if fuera else "")
           + f" · {len(avisos) - len(con_geo)} aún sin ubicar")
    leyenda = ('<span class="lg"><i class="dot nuevo"></i> nuevo (24 h)</span>'
               '<span class="lg"><i class="dot"></i> ya estaba</span>'
               '<span class="lg">⊕ ' + _e(nombre_ancla) + "</span>")
    return (f'<section class="card"><h2>📍 El barrio</h2>'
            f'<div class="leyenda">{leyenda}</div>'
            f'<div class="mapa">{"".join(P)}</div>'
            f'<p class="pie">{pie}. Tocar un punto abre Google Maps. '
            f"Las direcciones se ubican de a 25 por corrida; el mapa se "
            f"llena solo.</p></section>")


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
# Tabla
# ---------------------------------------------------------------------------

def _fila_tabla(a: Arriendo, ahora: datetime) -> str:
    dias = _dias(a, ahora)
    gest = (a.extras.get("gestion") or {}).get("estado", "")
    marca = {"visita": "📅", "contactado": "📞", "visto": "👁"}.get(gest, "")
    chips = ("<span class=chip>🆕</span>" if _es_de_24h(a, ahora) else "") + \
        (f"<span class=chip>{marca}</span>" if marca else "") + \
        ("<span class=chip>📉</span>" if _bajo_precio(a) else "")
    links = [f'<a href="{_e(_link_ficha(a))}">ficha</a>',
             f'<a href="{_e(a.url)}" target="_blank" rel="noopener">aviso</a>']
    if (maps := _maps(a)):
        links.append(f'<a href="{_e(maps)}" target="_blank" '
                     f'rel="noopener">📍</a>')
    m2 = a.m2_totales or a.m2_utiles
    m2txt = (f"{m2:.0f}" + ("" if a.m2_totales else " út.")) if m2 else "—"
    return (
        f"<tr>"
        f'<td data-v="{a.score}"><b>{a.score}</b></td>'
        f"<td><code>#{a.codigo}</code></td>"
        f'<td class="dir">{_e(_corto(a))} {chips}<br>'
        f'<span class="links">{" · ".join(links)}</span></td>'
        f'<td data-v="{a.costo_mensual or 0}">{_pesos(a.costo_mensual)}'
        f'{"" if a.gastos_comunes_clp is not None or not a.arriendo_clp else " *"}</td>'
        f'<td data-v="{a.gastos_comunes_clp or 0}">{_pesos(a.gastos_comunes_clp)}</td>'
        f'<td data-v="{m2 or 0}">{m2txt}</td>'
        f"<td>{a.dormitorios or '—'}/{a.banos or '—'}</td>"
        f"<td>{a.piso if a.piso is not None else '—'}</td>"
        f"<td>{a.antiguedad_anos if a.antiguedad_anos is not None else '—'}</td>"
        f'<td data-v="{dias if dias is not None else -1}">'
        f"{dias if dias is not None else '—'}</td>"
        f"</tr>")


def _tabla(avisos: list[Arriendo], ahora: datetime) -> str:
    filas = "\n".join(_fila_tabla(a, ahora) for a in avisos)
    cab = "".join(
        f'<th data-col="{i}" data-num="{n}">{t}</th>' for i, (t, n) in
        enumerate((("⭐", 1), ("Código", 0), ("Dirección", 0),
                   ("$/mes", 1), ("GC", 1), ("m²", 1), ("D/B", 0),
                   ("Piso", 0), ("Años", 0), ("Días", 1))))
    return (
        '<section class="card"><h2>📋 Todos, para comparar</h2>'
        '<input id="filtro" type="search" '
        'placeholder="Filtrar por calle, código, portal…" '
        'aria-label="Filtrar la tabla">'
        f'<div class="scroll"><table id="tabla"><thead><tr>{cab}</tr></thead>'
        f"<tbody>{filas}</tbody></table></div>"
        '<p class="pie">`*` = costo sin gastos comunes (el aviso no los '
        "publica). Tocar una columna la ordena.</p></section>")


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
main { max-width:820px; margin:0 auto; padding:12px 14px 48px; }
header.top { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:baseline;
  padding:14px 2px 4px; }
header.top h1 { font-size:1.25rem; margin:0; }
nav a { text-decoration:none; color:var(--azul); font-weight:600;
  padding:4px 10px; border-radius:999px; }
nav a.activo { background:var(--azul); color:#fff; }
.sello { color:var(--tinta2); font-size:.85rem; width:100%; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
  gap:10px; margin:10px 0; }
.kpi { background:var(--sup2); border:1px solid var(--borde);
  border-radius:12px; padding:10px 12px; }
.kpi b { display:block; font-size:1.5rem; line-height:1.1; }
.kpi span { color:var(--tinta2); font-size:.8rem; }
.card { background:var(--sup2); border:1px solid var(--borde);
  border-radius:14px; padding:14px 14px 10px; margin:14px 0; }
.card h2 { font-size:1.02rem; margin:0 0 8px; }
.pie { color:var(--tinta2); font-size:.8rem; margin:8px 0 2px; }
.leyenda { display:flex; gap:14px; font-size:.82rem; color:var(--tinta2);
  margin-bottom:6px; }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%;
  background:var(--gris); margin-right:4px; }
.dot.nuevo { background:var(--azul); }
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
#filtro { width:100%; padding:8px 12px; margin-bottom:8px; font:inherit;
  border:1px solid var(--borde); border-radius:10px; background:var(--sup);
  color:var(--tinta); }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.86rem;
  min-width:640px; }
th { text-align:left; cursor:pointer; position:sticky; top:0;
  background:var(--sup2); border-bottom:2px solid var(--borde);
  padding:6px 8px; white-space:nowrap; user-select:none; }
td { border-bottom:1px solid var(--borde); padding:6px 8px;
  vertical-align:top; }
td.dir { min-width:200px; }
.links { font-size:.78rem; } .links a { color:var(--azul); }
.chip { font-size:.8rem; margin-left:2px; }
code { background:var(--sup); padding:1px 5px; border-radius:6px;
  font-size:.8rem; }
.vacio { text-align:center; padding:40px 10px; color:var(--tinta2); }
.vacio b { font-size:1.1rem; color:var(--tinta); }
footer { color:var(--tinta2); font-size:.8rem; margin-top:18px; }
footer a { color:var(--azul); }
"""

_JS = """
document.querySelectorAll('#tabla th').forEach(th => th.onclick = () => {
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
const f = document.getElementById('filtro');
if (f) f.oninput = () => {
  const q = f.value.toLowerCase();
  document.querySelectorAll('#tabla tbody tr').forEach(r =>
    r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none');
};
"""


def _kpis(vivos, nuevos, bajaron, con_geo, mediana) -> str:
    piezas = [
        (str(len(vivos)), "candidatos vivos"),
        (str(len(nuevos)), "nuevos en 24 h"),
        (str(len(bajaron)), "bajando de precio"),
        (_pesos(mediana) if mediana else "—", "canon mediano"),
        (str(len(con_geo)), "ubicados en el mapa"),
    ]
    return ('<div class="kpis">'
            + "".join(f"<div class=kpi><b>{_e(v)}</b><span>{_e(t)}</span></div>"
                      for v, t in piezas)
            + "</div>")


def _pagina(titulo: str, activo: str, cuerpo: str, ahora: datetime) -> str:
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
<title>{_e(titulo)}</title><style>{_CSS}</style></head>
<body><main>
<header class="top"><h1>📡 {_e(titulo)}</h1>{nav}
<span class="sello">Actualizado: {sello} · se pisa en cada corrida</span>
</header>
{cuerpo}
<footer>El detalle de cada uno vive en su ficha. ¿Viste o llamaste por
alguno? Anótalo en <a
href="https://github.com/alfonsoandona/arriendos-vitacura/edit/claude/telegram-vitacura-rentals-alert-48wnuy/gestion.yml">gestion.yml</a>
con su código — un <code>descartado</code> no vuelve a sonar.</footer>
</main><script>{_JS}</script></body></html>"""


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def escribir_dashboards(hallazgos: list[Arriendo], directorio: Path,
                        perfil: dict | None = None) -> tuple[Path, Path]:
    """Escribe docs/index.html y docs/hoy.html. Se pisan en cada corrida."""
    perfil = perfil or {}
    directorio.mkdir(parents=True, exist_ok=True)
    ahora = ahora_utc()

    vivos = _sin_fichas_repetidas(
        sorted([a for a in hallazgos if not a.descartado],
               key=S.orden, reverse=True))
    nuevos = [a for a in vivos if _es_de_24h(a, ahora)]
    bajaron = [a for a in vivos if _bajo_precio(a)]
    con_geo = [a for a in vivos if a.lat is not None]
    precios = sorted(a.arriendo_clp for a in vivos if a.arriendo_clp)
    mediana = precios[len(precios) // 2] if precios else None

    mapa = _mapa_svg(vivos, perfil, ahora)
    if not mapa:
        mapa = ('<section class="card"><h2>📍 El barrio</h2>'
                '<p class="vacio"><b>El mapa se está llenando.</b><br>'
                "Las direcciones se ubican de a 25 por corrida (cortesía "
                "con OpenStreetMap); en un par de corridas los puntos "
                "aparecen solos acá.</p></section>")

    cuerpo_todos = (
        _kpis(vivos, nuevos, bajaron, con_geo, mediana)
        + mapa
        + _histograma_svg(vivos, perfil)
        + _tabla(vivos, ahora))
    indice = directorio / "index.html"
    indice.write_text(
        _pagina("Radar de Arriendos · Vitacura", "todos", cuerpo_todos, ahora),
        encoding="utf-8")

    de_hoy = _sin_fichas_repetidas(
        nuevos + [a for a in bajaron
                  if _bajo_en_24h(a, ahora) and a not in nuevos])
    if de_hoy:
        cuerpo_hoy = (
            _kpis(de_hoy, nuevos, [a for a in de_hoy if _bajo_precio(a)],
                  [a for a in de_hoy if a.lat is not None], mediana)
            + (_mapa_svg(de_hoy, perfil, ahora) or "")
            + _tabla(de_hoy, ahora))
    else:
        cuerpo_hoy = ('<section class="card"><p class="vacio">'
                      "<b>Sin novedades en las últimas 24 horas.</b><br>"
                      "Ni avisos nuevos ni bajas de precio. El mercado de "
                      "arriendo se mueve en días: mañana puede haber."
                      "</p></section>")
    hoy = directorio / "hoy.html"
    hoy.write_text(
        _pagina("Últimas 24 horas · Vitacura", "hoy", cuerpo_hoy, ahora),
        encoding="utf-8")
    return indice, hoy
