# Radar de Arriendos — Vitacura

Monitor de arriendos de departamento en Vitacura y en el anillo del Sport
Francés. Barre portales, corredoras y plataformas de arriendo; filtra contra un
perfil de búsqueda; y avisa por Telegram cuando aparece algo que calza, con el
link a la ficha completa.

**Qué busca:** departamentos de **más de 100 m² totales**, **3 dormitorios
mínimo**, hasta **$1.600.000** al mes, en **Vitacura (la comuna entera)** o a
**1,2 km o menos del Sport Francés**. Entre los que califican, prefiere los más
nuevos.

> **Este radar complementa a Portal Inmobiliario, no lo reemplaza.** Ya tienes
> alertas configuradas ahí, así que Portal Inmobiliario está deliberadamente
> apagado en [`fuentes.yml`](fuentes.yml) y lo que este radar cubre es todo lo
> demás: **41 portales** — TocToc, GoPlaceIt, Houm, Hey, Engel & Völkers,
> Chilepropiedades, Yapo, Economicos, Property Partners y las corredoras del
> sector oriente que publican solo en su propio sitio.

---

## Todo esto funciona sin computador

Corre entero en GitHub. No hay nada que instalar y nada que dejar prendido:
basta el navegador de un celular o la app de GitHub.

| Qué necesitas hacer | Cómo, desde el teléfono |
|---|---|
| **Recibir alertas** | Nada. Llegan solas por Telegram. Configuración: [`AVISOS.md`](AVISOS.md) |
| **Ver todo lo vigente** | [`alertas/`](alertas/) — tablero ordenado por puntaje |
| **Ver el detalle de uno** | El link del aviso, o `alertas/casos/` |
| **Cambiar la búsqueda** | Abres [`perfil.yml`](perfil.yml) → lápiz ✏️ → editas → *Commit changes* |
| **Agregar un portal** | Igual, pero en [`fuentes.yml`](fuentes.yml) |
| **Buscar ahora mismo** | Actions → *Radar de Arriendos* → *Run workflow* |
| **Ver qué fuentes sirven** | Actions → *Calibrar fuentes* → *Run workflow* |
| **Probar que Telegram anda** | Actions → *Probar aviso de Telegram* |
| **Ver qué falta** | [`PENDIENTES.md`](PENDIENTES.md) — campos por rellenar |

### ¿Cuánto cuesta?

**Cero.** El radar corre 2 veces al día y cada corrida toma unos 6 minutos:
alrededor de **360 minutos al mes**, dentro del plan gratuito de GitHub Actions
incluso con el repositorio privado (2.000 minutos al mes). En repositorio
público los minutos son ilimitados.

---

> **¿Qué falta para que quede al 100%?** Está todo en
> [`PENDIENTES.md`](PENDIENTES.md), en formato de campos por rellenar: el bot
> de Telegram, las 19 fuentes a las que les falta afinar la URL, y las
> decisiones que tomé yo y que quizás quieras cambiar.

---

## ⚠️ Lo primero que hay que hacer: calibrar

**Actions → Calibrar fuentes → Run workflow.**

Vale la pena entender por qué, porque es la única parte que no está terminada.

El entorno donde se escribió este código tiene bloqueado el acceso de red a
todos los portales objetivo (política de egreso: `403` en `CONNECT` para
toctoc.com, houm.com, yapo.cl y el resto). **Nunca se pudo abrir ninguno de
esos sitios.**

Las fuentes están en dos grupos, y `fuentes.yml` los distingue con
`url_confirmada`:

| | Cuántas | Qué se verificó | A dónde apuntan |
|---|---|---|---|
| ✔︎ **Confirmadas** | 20 | Se vio la URL exacta del listado filtrado | Al listado |
| ? **Por calibrar** | 19 | Solo que el dominio existe (DNS) | A la raíz del sitio |

Las de la segunda fila apuntan a la raíz a propósito: la raíz siempre carga y
deja el HTML guardado, que es con lo que después se escribe la ruta buena. Una
ruta inventada da 404, que en el reporte se ve igual que un sitio caído y no
deja nada con qué trabajar.

Y en los dos casos, que la página exista no garantiza **que el extractor la
entienda**. Son cosas distintas y esa segunda solo se comprueba con internet.

Escribir selectores CSS a ciegas habría sido adivinar. En vez de eso el
extractor usa tres pasadas que no dependen del diseño de cada sitio:

1. **JSON-LD** (`schema.org`). Cuando el sitio lo emite, los datos vienen
   estructurados y exactos.
2. **Estado embebido.** Los portales hechos en React o Nuxt dejan el listado
   completo en un `<script>` (`__NEXT_DATA__`). Es tan bueno como el JSON-LD y
   lo emiten muchos más sitios.
3. **Heurística sobre tarjetas.** Bloques con un enlace más señales de arriendo
   (un monto en pesos, m², dormitorios) a los que se les aplica el parser de
   texto libre.

Esto funciona sin conocer cada sitio, pero produce ruido. La calibración dice
qué fuente entrega de verdad y cuál hay que arreglar.

### Cómo leer el resultado

El reporte queda en el resumen del run (se lee desde el teléfono) y el HTML
crudo queda como artifact:

```
| Fuente            | URL | Estado             | Avisos | En zona | Pasan filtros |
|-------------------|-----|--------------------|--------|---------|---------------|
| TocToc            | ✔︎  | ✅ entrega         |   47   |   31    |       4       |
| Houm              | ✔︎  | ⚠️ cero resultados |    0   |    0    |       0       |
| Property Partners | ?   | ⚠️ cero resultados |    0   |    0    |       0       |
```

La columna **URL** es la que hace útil al reporte: las dos últimas filas dicen
lo mismo y significan cosas distintas. Houm está confirmada, así que su cero
es el extractor; Property Partners está por calibrar, así que su cero es
casi seguro la ruta.

| Resultado | Qué significa | Qué hacer |
|---|---|---|
| ✅ **entrega** | Esa fuente ya funciona | Nada |
| ⚠️ **cero** con `✔︎` | La URL es buena pero no se reconocieron los avisos | Baja el artifact y abre el HTML. Si trae los avisos, agrégale `selector_card`. Si viene vacío, ponle `motor: navegador` |
| ⚠️ **cero** con `?` | Casi seguro que la ruta está mala | Filtra a mano en el sitio y copia la URL |
| ❌ **sin respuesta** | 404, bloqueo anti-bot o robots.txt | Si es 403, prueba `motor: navegador`. Si es DNS, el dominio cambió |

### Paginación

Por omisión cada fuente lee solo la primera página del listado, que son unos
20 avisos. Los portales grandes tienen más, así que llevan `paginacion` en
`fuentes.yml`:

```yaml
paginacion: {paginas: 3, parametro: page}     # ...?page=2
```

Se corta sola cuando una página no trae nada, y conserva los filtros que ya
trae la URL. El tope de fichas de detalle (`detalle.max`) es un presupuesto de
la **fuente completa**, no de cada página: si fuera por página, tres páginas
con `max: 12` serían 39 cargas de navegador y una sola fuente se comería el
presupuesto de 30 minutos del job.

### El presupuesto de tiempo

El job de Actions corta a los 30 minutos. Con 39 fuentes la corrida normal
toma unos 12, pero si varias se cuelgan hasta su timeout el peor caso llega a
54 — y que lo corte Actions es el peor final posible: mata el proceso, así que
no se manda ninguna alerta, no se guarda el estado y no se escribe la
bitácora. Se pierde también todo lo que las fuentes sanas ya habían entregado.

Por eso el radar se corta solo a los **18 minutos** (`--tope-minutos`): avisa
lo que alcanzó a encontrar, guarda el estado, y deja anotado en la bitácora
qué fuentes quedaron sin mirar.

### La UF del día

No es un detalle. **Yapo publica buena parte de su inventario de Vitacura en
UF** (`CLF 46.00`), así que para esa fuente el precio en pesos no lo publica
nadie: lo calcula este radar. Y de ese cálculo depende el veredicto:

```
UF 39 × $40.800 (constante)  = $1.591.200   → entra
UF 39 × $41.500 (UF real)    = $1.618.500   → se pasa del tope
```

Se resuelve con una cascada: la variable `VALOR_UF` si está, si no la API
pública del día, si no la caché de la última corrida —un valor de hace dos
días es mucho mejor que uno escrito hace meses— y solo al final la constante.
La bitácora dice cuál se usó.

### El arreglo que más rinde, y se hace desde el teléfono

Abre el portal en el navegador del celular, filtra a mano por **Vitacura +
arriendo + departamento + 3 dormitorios**, copia la URL que te queda en la
barra, y pégala en `fuentes.yml` reemplazando la que está.

Menos páginas que barrer, muchísimo menos ruido, y ninguna línea de código.

---

## Cómo decide

### Lo que descarta

Son los cuatro requisitos que se pidieron como obligatorios. Todo lo demás
puntúa pero no descarta.

| Requisito | Regla |
|---|---|
| **Zona** | Vitacura (comuna entera) **o** ≤ 1,2 km del Sport Francés |
| **Superficie** | **Más de** 100 m² totales |
| **Dormitorios** | 3 o más |
| **Precio** | Hasta $1.600.000, con 12% de holgura negociable |
| **Qué es** | Departamento en arriendo — no venta, no temporada, no pieza |

Hay dos decisiones dentro de esto que conviene conocer.

**Un dato ausente nunca descarta.** Si el aviso no publica los metros, el
departamento entra igual, no suma los puntos de ese rubro, y la alerta dice qué
falta. Descartar por dato faltante es el error más caro que puede cometer este
radar porque *no se ve*: la propiedad no aparece en ninguna parte y nadie la va
a extrañar. Un dato **presente** que no cumple sí descarta.

**La superficie filtra de forma asimétrica, y es geometría.** La superficie
total nunca es menor que la útil. Entonces una útil de 118 m² *confirma* que
cumple, pero una útil de 92 m² no puede *rechazar*: ese mismo departamento con
15 m² de terraza tiene 107 m² totales y sí cumple. Cuando solo viene la útil y
es baja, el aviso entra como "dato faltante" y se revisa a mano.

### Lo que puntúa

100 puntos repartidos así:

| Rubro | Peso | Por qué |
|---|---|---|
| **Ubicación** | 26 | Vitacura vale más que la distancia — ver abajo |
| **Antigüedad** | 24 | "Ideal más nuevo" es el criterio de orden que se pidió |
| **Precio** | 20 | Contra el presupuesto, y descontando gastos comunes altos |
| **Superficie** | 16 | Más grande es mejor, con rendimientos decrecientes sobre 170 m² |
| **Programa** | 14 | Dormitorios y baños: cómo están repartidos esos metros |

Más las **preferencias**, que no deciden si un departamento califica sino cuál
preferir entre los que ya calificaron: piso alto (pero no el último),
orientación nororiente, estacionamientos, holgura de m² por dormitorio, y
palabras del aviso.

Y son **asimétricas**: suman hasta 6 puntos y restan hasta 12. La razón está
medida: un departamento de 8 años y 134 m² a $1.500.000 que publicaba piso,
orientación y estacionamientos le ganaba a uno de 2 años y 150 m² a
$1.250.000 que no publicaba nada de eso. El segundo es mejor por donde se lo
mire; su aviso simplemente decía menos.

Un aviso que no dice en qué piso está no es un primer piso: es un aviso que no
lo dice. Así que una **virtud** conocida es un desempate y pesa poco; un
**defecto** conocido —primer piso, sin estacionamiento, 20 m² por
dormitorio— es información de decisión y pesa fuerte.

### Tres decisiones de diseño que conviene conocer

**1. Vitacura entra entera, sin mirar distancia.** El ancla está a menos de 400
metros del límite con Las Condes, así que dentro de un radio Las Condes gana
por distancia casi siempre. Con un multiplicador por comuna, un departamento de
Vitacura en la periferia de la comuna va por delante de uno de Las Condes
pegado al club. Sin eso, "prioriza Vitacura comuna entera" quedaría escrito en
el perfil y desmentido por el tablero.

**2. El puntaje se mide sobre lo que se pudo medir.** La antigüedad pesa 24
puntos y los portales de arriendo la publican poco. Cobrarla como 0 dejaría a
un departamento perfecto en 55 puntos, debajo de uno peor que sí publicó el
año, y el puntaje perdería su única función: ordenar. Por eso la alerta dice
"medido sobre 76 de 100" cuando corresponde, y la ficha dice hasta cuánto
podría llegar si se consiguieran los datos que faltan.

**3. El número que importa es el costo mensual, no el canon.** En departamentos
de más de 100 m² en Vitacura los gastos comunes van entre $150.000 y $400.000
al mes. Un departamento de $1.500.000 con $380.000 de gastos comunes cuesta más
que uno de $1.700.000 con $120.000, y mirando solo el canon el orden sale al
revés. Casi ningún portal muestra la suma; la alerta la muestra siempre.

---

## Deduplicación entre portales

Es la pieza que hace usable este radar, y la razón por la que un scraper
ingenuo no sirve para arriendos.

**El mismo departamento está publicado en cuatro portales a la vez**: en
TocToc, en Yapo, en la página de su corredora y en el portal que le sindica el
aviso. Con cuatro URLs, cuatro títulos y cuatro formas de escribir la misma
dirección:

```
Alonso de Córdova 4200
Alonso de Córdova Nº 4200
Calle Alonso de Cordova 4200
Alonso De Cordova Vitacura 4200
```

Las cuatro se normalizan a la misma llave. Sin eso, un solo departamento manda
cuatro mensajes de Telegram y el radar se vuelve ruido en una semana.

Y las copias no se descartan: **se fusionan**. Cada portal publica un
subconjunto distinto de los datos —uno trae los gastos comunes, otro el año de
construcción, otro la superficie total— así que el aviso resultante está más
completo que ninguno de los originales. Los otros enlaces quedan en la ficha,
porque a veces una copia trae mejores fotos o el teléfono directo.

---

## Cuándo vuelve a avisar de algo ya avisado

Un departamento se avisa **una sola vez**… salvo que cambie algo que importa:

- **El canon baja 4% o más.** Es la mejor señal del mercado de arriendo: un
  aviso que baja de precio lleva semanas sin arrendarse, así que sigue
  disponible *y* hay margen para negociar. Perdérselo sería perder el mejor
  momento para llamar.
- **Cruza los 45 días publicado.** Se avisa una sola vez, no todos los días.

---

## Uso local

No hace falta para operar el radar, pero sirve para probar cosas.

```bash
pip install -r requirements-dev.txt

# Corrida completa sin mandar nada: imprime los avisos que mandaría
python -m arriendo run --dry-run --verbose

# Probar el filtrado con HTML local, sin tocar la red.
# Guarda la página del portal desde el navegador y córrela por acá.
python -m arriendo demo pagina-guardada.html --url https://toctoc.com/

# Limitar a una sola fuente
python -m arriendo run --fuente toctoc --dry-run

# Ver qué fuentes entregan
python -m arriendo calibrar --reporte calibracion.md

# Verificar las coordenadas del ancla contra el mapa
python -m arriendo geocode

# Mandar un mensaje de prueba por Telegram
python -m arriendo probar-aviso
```

---

## Cómo está armado

```
perfil.yml          Qué se busca. El único archivo que hay que editar.
fuentes.yml         De dónde. Un bloque por portal.

arriendo/
  models.py         El Arriendo normalizado y la llave de deduplicación.
  parse.py          Texto libre en español chileno -> datos.
  scoring.py        Filtros duros y puntaje.
  geo.py            Distancia al ancla y geocodificación.
  store.py          Memoria entre corridas y fusión entre portales.
  fichas.py         Las fichas en Markdown y el tablero.
  bitacora.py       Qué pasó en cada corrida.
  uf.py             El valor de la UF, con respaldo en cascada.
  cli.py            Orquestación y línea de comandos.
  sources/
    base.py         HTTP educado: reintentos, rate limit, robots.txt.
    generic.py      Las tres pasadas de extracción.
    navegador.py    Chromium, para los portales que arman la página con JS.
    registry.py     Carga de fuentes.yml, paginación y barrido.
  alerts/
    telegram.py     El mensaje de ocho líneas.

alertas/            Tablero y fichas. Se lee desde el teléfono.
state/              Qué se vio y qué se avisó. Versionado.
logs/               Bitácora de cada corrida. Versionada.
tests/              340 tests.
```

### Sobre los tests

340 tests, todos sin red — y sin red de verdad: `tests/conftest.py` corta el
socket, así que un test que intente salir a internet falla en el acto. No es
paranoia: un bug de argparse hacía que `arriendo --fuentes f.yml run` ignorara
el archivo y cargara el catálogo real, y el síntoma fue un test de validación
de YAML que salió a consultar los diecisiete portales y tardó 84 segundos. Los fixtures de `tests/fixtures/` son HTML escrito
como lo escriben los portales chilenos, con el ruido real incluido: el menú de
navegación, el panel de filtros facetados, el botón de "publica tu propiedad"
metido dentro de cada tarjeta, y avisos de venta y de temporada mezclados en la
misma grilla.

Los tests del parser de montos son los que más rinden, porque ahí está el
problema que separa un aviso de arriendo de uno de venta: **en un aviso de
arriendo hay varios montos en pesos a la vez** —canon, gastos comunes,
garantía, comisión— y quedarse con el primero convierte $220.000 de gastos
comunes en el arriendo del departamento.

```bash
python -m pytest tests/ -q
```

---

## Estado actual

| Pieza | Estado |
|---|---|
| Parser de avisos chilenos (montos, superficies, programa) | ✅ 74 tests |
| Filtros duros y puntaje | ✅ 61 tests |
| Deduplicación y fusión entre portales | ✅ 31 tests |
| Extracción (JSON-LD, SPA, tarjetas) | ✅ 42 tests |
| Alertas por Telegram y fichas | ✅ 53 tests |
| Configuración, fuentes, paginación y CLI | ✅ 45 tests |
| Corrida completa de punta a punta | ✅ 13 tests |
| Automatización (GitHub Actions, 2x al día) | ✅ Configurada |
| URLs de 20 portales | ✅ Confirmadas una por una |
| URLs de otros 19 portales | ⚠️ Solo dominio verificado — apuntan a la raíz |
| **Que el extractor entienda cada portal** | ⚠️ **Correr `Calibrar fuentes`** |
| **Telegram** | ⚠️ **Falta crear el bot — ver `PENDIENTES.md`** |

El último punto es el que falta, y está explicado arriba.
