# Pendientes

**Estado al 21-08-2026, corrida de las 19:30.** El radar corre solo 3 veces
al día, avisa por Telegram y publica el dashboard. Nada de esta lista lo
detiene: todo lo que está acá lo mejora.

Se trabaja **conversando en el chat**: tú contestas, yo edito y pusheo.

---

## 📊 Dónde está parado hoy

| | |
|---|---|
| Corrida | **207s** · 29/29 fuentes · sin errores (venía de 498s) |
| Inventario | 1.301 avisos crudos → 459 únicos → **35 candidatos** |
| Fichas | 37 leídas → 3 ganaron el año, 2 descartados por viejos |
| Alertas | 1 esta corrida · **3 avisos "aparecieron"** (venían 47 de ruido) |

**Cobertura de datos en los candidatos:**

| Dato | Ahora | En la mañana |
|---|---|---|
| precio | **91%** | 41% |
| dirección | 65% | 62% |
| en el mapa | 62% | 41% |
| año de construcción | **17%** ⚠️ | 12% |

La dirección y el mapa se ven casi iguales que en la mañana, y eso esconde
dos movimientos en direcciones opuestas: se ganaron direcciones nuevas y se
BORRARON las que no ubicaban nada ("Vitacura", "Edificio de 18", "Antigüedad:
30"). Un aviso que antes tenía un pin sobre el centro de la comuna hoy no
tiene pin — y está mejor así: un punto que no sabemos es peor que ninguno.

Lo que movió los números: la ficha técnica de goplaceit (vive ARRIBA del
título y el ancla la botaba entera), "Mts" leído como m² (el widget de
servicios ponía "1008 m² totales" a un depto de 240), el canon de doomos
—que se pinta arriba de su tarjeta— y 17 direcciones que no eran
direcciones.

---

## 🔴 MI LISTA — en orden de impacto

### 1. El año de construcción: 17% ⚠️ *el criterio SÍ O SÍ, y el dato más escaso*

**Ya no es un problema del lector.** Audité doce fichas reales de las cinco
fuentes más grandes buscando dónde publica cada una el año:

| Fuente | Fichas con año | Estado |
|---|---|---|
| toctoc | 2 de 3, rotulado | se lee bien |
| mitula | 1 de 3, rotulado | se lee bien |
| engelvoelkers | 1 de 3, en prosa | **arreglado hoy** |
| chilepropiedades | 0 de 3 | no lo publica |
| houm | 0 de 3 | no lo publica |

El hueco es de los portales, no del extractor. Quedan tres frentes:

- **La libreta de edificios** (`arriendo/edificios.py`): lo que un aviso
  enseña sobre una dirección le sirve a todos los avisos de esa dirección,
  hoy y siempre. Ya conoce 7 edificios y ya hizo su primer trabajo real: dos
  portales dijeron años distintos del mismo edificio y **anuló la entrada en
  vez de elegir**. Todavía no rescata a nadie —a ninguno de los que le falta
  el año le coincide la dirección con uno que lo tenga— y el valor es
  acumulativo por diseño. Ya va en 8 edificios. **A medir en una semana.**
- **Más fichas.** El presupuesto subió a 100 (la última corrida usó 65) y
  ahora rinde: 6 avisos ganaron el año y 24 se descartaron al verlos.
- **Las fuentes que sí lo publican y todavía no se auditan:** doomos,
  fuenzalida, icasas, accesoinmobiliario.

### 2. El precio: 91%, y lo que queda es del portal

| Fuente | precio | qué le pasa |
|---|---|---|
| nuroa | 17% | su URL de arriendos entrega 22 ventas de cada 25 — es del portal |
| goplaceit | 50% | sube con cada ficha que se alcanza a leer (era 20%) |
| doomos | **93%** | era 55%: su canon se pinta arriba de la tarjeta |
| fuenzalida | 91% | |
| yapo · icasas · economicos · remax | 100% | |

Auditados contra su HTML real. Lo que queda es nuroa, y ahí el problema es
que su listado de arriendos trae sobre todo ventas: no hay precio de
arriendo que leer.

### 3. ~~El paso de tests se toma 6 minutos de cada corrida~~ ✅

Cronometrado y arreglado hoy: **5 min 53 s → 15 s**, medido en el runner.
353 segundos de suite con 6 de CPU: la red está cortada en los tests, cada
intento de bajar una ficha fallaba, y el cliente esperaba 2, 4 y 8 segundos
antes de reintentar algo condenado. De punta a punta, la corrida completa
—instalar, verificar, barrer, publicar el dashboard— pasó de ~15 minutos a
~7.

### 4. Portales que responden pero entregan cero

`busconido` · `assetplan` · `remax` · `enlaceinmobiliario` (403) ·
`economicos` (403 intermitente — hoy entregó 0, en la corrida anterior 52)

Tengo el HTML real de varios guardado en la rama `diagnostico-datos`, así que
se calibran sin volver a visitarlos.

### 5. Limpieza menor

- Direcciones con HTML crudo adentro (`"Eventos</p><p><strong>Superficie…"`).
- houm publica la calle sin altura y con la comuna en "Region
  Metropolitana": sirve para el mapa, no para identificar el edificio.

---

## ✅ Cerrado hoy (21-08)

- **toctoc** volvió a 770 avisos (venía de 318 por un timeout que se llevaba
  la página entera).
- **goplaceit**: 281s → 116s y precio 20% → 50%. El scroll que se probó no
  servía; lo que servía era leer la cabecera de su ficha.
- **yapo**: sus 30 avisos llegaban sin m² porque escribe "150 m 2", con
  espacio. Ahora 18 de 30 lo traen.
- **comunavitacura** apagada: su HTML es el directorio municipal (bomberos,
  colegios, peluquerías), no un portal de arriendos.
- **zentagroup** apagada: no es una corredora, es una consultora de IA.
- **trovit y nestoria** apagadas: 52 avisos y ninguno con link propio.
- **doomos**: 55% → 93% con precio. Su canon se pinta ARRIBA del bloque que
  la detección de tarjetas reconoce, igual que la ficha de goplaceit.
- **17 direcciones que no eran direcciones**, publicadas en el tablero y
  mandadas a Google Maps ("Edificio de 18", "Antigüedad: 30", "ID 44348").
- **La limpieza de direcciones vivía en un solo lado**: el extractor las
  rechazaba y la memoria del store se las devolvía intactas al aviso. Ahora
  hay una sola función y la usan los dos.
- **47 avisos "aparecieron" y ninguno era nuevo**: la huella se calcula con
  la dirección, así que limpiarlas re-bautizaba a los avisos. Con el año
  arreglado eso habría sido una andanada de alertas repetidas al teléfono.
  `es_nuevo` ahora usa la URL como red.
- Un candidato que era la **calculadora de crédito hipotecario** de
  chilepropiedades.
- **"Vitacura, Metropolitana" como dirección**: el guardia solo ignoraba la
  comuna QUE EL AVISO TRAÍA, y cuando llega sin comuna esa palabra sobrevivía
  como nombre de calle. Es la llave que fundió 37 departamentos el 17-08.
- **El paso de tests del workflow**: 5 min 53 s de cada corrida, esperando
  reintentos imposibles.

---

## 🙋 TU LISTA

### Paso 1 · Estrenar la gestión (2 min)

Escríbeme una frase con cualquier aviso que mires:

```
"descarta el #FX6GA, ya se arrendó"
"llamé por el #BB6M4, visita el jueves"
"el #VQ3SD en realidad son 95 m²"
```

Un `descartado` no vuelve a sonar nunca; los contactados salen marcados
📞📅 en tabla y mapa; y lo que corrijas **pisa** al aviso y lo re-puntúa.

### Paso 2 · URLs de corredoras (2 min c/u)

Entras → filtras **arriendo + departamento + Vitacura** → me pegas la URL.
Como hiciste con nuroa: eso solo ya subió sus m² de 0% a 100%.

```
century21.cl           ______________________________________
enlaceinmobiliario.cl  ______________________________________
arriendos.cl           ______________________________________
clasificados.cl        ______________________________________
rentas.cl              ______________________________________
busconido.cl           ______________________________________
```

**Y si ves cualquier portal que "llegue sin info", mándamelo con el link.**
Los cuatro arreglos más grandes del radar salieron de un link tuyo.

### Paso 3 · Tres respuestas de perfil (1 min)

```
Mascotas:           tengo / no aplica
Amoblado:           sin amoblar / amoblado / da lo mismo
Estacionamientos:   mínimo ____ , ideal ____
```

---

## 📁 Dónde queda el rastro

| Archivo | Qué guarda |
|---|---|
| `logs/corridas/AAAA-MM-DD-HHMM.log` | el log **completo** de cada corrida, para siempre |
| `logs/historial.jsonl` | una línea por corrida desde el día uno |
| `alertas/casos/` | la ficha de cada aviso, aunque el aviso ya no exista |
| `state/arriendos.json` | cada aviso con su **texto crudo** |
| `state/edificios.json` | la libreta: qué año se construyó cada edificio conocido |
| rama `diagnostico-datos` | el HTML real de cada portal, para depurar sin visitarlos |
