# Pendientes

**Estado al 21-08-2026, corrida de las 18:35.** El radar corre solo 3 veces
al día, avisa por Telegram y publica el dashboard. Nada de esta lista lo
detiene: todo lo que está acá lo mejora.

Se trabaja **conversando en el chat**: tú contestas, yo edito y pusheo.

---

## 📊 Dónde está parado hoy

| | |
|---|---|
| Corrida | **268s** · 31/31 fuentes · sin errores (venía de 498s) |
| Inventario | 1.497 avisos crudos → 521 únicos → **56 candidatos** |
| Fichas | 45 leídas → 4 ganaron el año, 7 se descartaron al verlas |
| Filtro de antigüedad | **funcionando**: descarta al que publica año y resulta viejo |

**Cobertura de datos en los candidatos** — la lista de tareas está acá:

| Dato | Hoy | Ayer |
|---|---|---|
| dirección | **85%** | 62% |
| en el mapa | **80%** | 41% |
| precio | **62%** | 41% |
| año de construcción | **14%** ⚠️ | 12% |

Lo que movió esos números hoy: la ficha técnica de goplaceit (vive ARRIBA
del título y el ancla la botaba entera), "Mts" leído como m² (el widget de
servicios cercanos ponía "1008 m² totales" a un depto de 240), y 17
direcciones que no eran direcciones.

---

## 🔴 MI LISTA — en orden de impacto

### 1. El año de construcción: 14% ⚠️ *el criterio SÍ O SÍ, y el dato más escaso*

Sin el año no se puede ni aceptar ni descartar, que es la peor posición
posible. Tres frentes abiertos, en orden de rendimiento:

- **La libreta de edificios ya está andando** (`arriendo/edificios.py`): lo
  que un aviso enseña sobre una dirección le sirve a todos los avisos de esa
  dirección, hoy y siempre. Hoy conoce 6 edificios y todavía no rescata a
  nadie —a ninguno de los que le falta el año le coincide la dirección con
  uno que lo tenga— pero el valor es acumulativo por diseño: cada corrida la
  deja más gorda. **A medir en una semana.**
- **Auditar dónde publica el año cada portal**, como se hizo con goplaceit.
  toctoc lo trae en el 10% y mitula en el 16%; los demás en cero. Ese cero
  casi nunca es del portal: es del extractor.
- **El presupuesto de fichas subió a 100** (venía de 45) porque ahora rinde.
  Falta medir cuánto devuelve.

### 2. El precio: 62%, y el hueco está localizado

| Fuente | precio | qué le pasa |
|---|---|---|
| nuroa | 17% | su URL de arriendos entrega 22 ventas de cada 25 — es del portal |
| trovit | 21% | apagada hoy |
| goplaceit | 50% | sube con cada ficha que se alcanza a leer (era 20%) |
| doomos | 54% | **sin auditar** |
| fuenzalida | 89% | **sin auditar** |

Auditar doomos y fuenzalida contra su HTML real es lo próximo. Ese trabajo
encontró bugs reales **todas las veces** (mitula, nuroa, yapo, goplaceit).

### 3. El paso de tests se toma 6 minutos de cada corrida

Seis minutos por corrida son dieciocho al día de alertas que llegan más
tarde de lo necesario, y la suite completa corre en 6 segundos acá. Ya quedó
cronometrado por tramos: la próxima corrida dice en el log cuál de los tres
se los lleva.

### 4. Portales que responden pero entregan cero

`busconido` · `assetplan` · `remax` · `enlaceinmobiliario` (403) ·
`economicos` (403 intermitente — hoy entregó 0, en la corrida anterior 52)

Tengo el HTML real de varios guardado en la rama `diagnostico-datos`, así que
se calibran sin volver a visitarlos.

### 5. Limpieza menor

- Direcciones con HTML crudo adentro (`"Eventos</p><p><strong>Superficie…"`).
- toctoc antepone la dirección de la corredora a la del departamento
  (`"Vitacura 312 Metropolitana Juan XXIII 6859 301"` — la buena es Juan
  XXIII 6859).
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
- **17 direcciones que no eran direcciones**, publicadas en el tablero y
  mandadas a Google Maps ("Edificio de 18", "Antigüedad: 30", "ID 44348").
- Un candidato que era la **calculadora de crédito hipotecario** de
  chilepropiedades.

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
