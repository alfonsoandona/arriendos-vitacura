# Pendientes

**Estado al 21-08-2026, corrida de las 12:33.** El radar corre solo 3 veces
al día, avisa por Telegram y publica el dashboard. Nada de esta lista lo
detiene: todo lo que está acá lo mejora.

Se trabaja **conversando en el chat**: tú contestas, yo edito y pusheo.

---

## 📊 Dónde está parado hoy

| | |
|---|---|
| Corrida | 182s · 32/33 fuentes · sin errores |
| Inventario | 918 avisos crudos → 433 únicos → **49 candidatos** |
| Unificación | grupo mayor: 4 publicaciones (venía de 53) |
| Filtro de antigüedad | **funcionando**: 30 avisos con año conocido, **24 descartados por viejos** |

**Cobertura de datos en los 49 candidatos** — la lista de tareas está acá:

| Dato | Cobertura |
|---|---|
| m² totales | 60% |
| dirección | 62% |
| precio | **41%** ⚠️ |
| gastos comunes | 41% |
| en el mapa | 41% |
| piso | 33% |
| año de construcción | **12%** ⚠️ |

---

## 🔴 MI LISTA — en orden de impacto

### 1. TocToc se cae por timeout y se lleva media cosecha ⚠️ *lo más urgente*

**Medido hoy:** toctoc entregó **318 avisos cuando entrega ~650**. Dos de sus
tres búsquedas murieron con `Timeout 30000ms exceeded`. Es la fuente número
uno del radar —un tercio de todo el inventario— así que cuando se cae, la
corrida entera baja de 1.400 avisos crudos a 918.

**Qué haría:** subir el tope de navegación para las fuentes lentas y
reintentar la página caída en vez de darla por perdida. Es un cambio chico
con el mayor retorno de toda la lista.

### 2. El precio: solo 41% de los candidatos lo trae

Sin canon no se puede aplicar tu filtro de presupuesto — el criterio central
del pedido. Es el mismo tipo de agujero que ya cerré en el año, y esas
auditorías (mitula, nuroa, yapo) encontraron bugs reales **todas las veces**.

**Qué haría:** auditar portal por portal contra el HTML guardado, como con
nuroa: ver dónde publica cada uno el precio y por qué no se está leyendo.

### 3. Diecinueve candidatos sin link a su propia ficha

De 48 candidatos, **19 no tienen link directo al aviso**. Eso los deja fuera
del lector de fichas, que es de donde salen el año, los gastos comunes y el
piso. Es la causa de fondo detrás de los dos puntos anteriores: si no hay
ficha que abrir, no hay datos que ganar. Nuroa pasó de 0 a 25 links con este
mismo trabajo.

### 4. Dos portales que responden 403

- **economicos** (El Mercurio): 403 recurrente, con GET y con navegador.
  Entregaba 20 avisos.
- **enlaceinmobiliario**: 403.

### 5. Siete fuentes que cargan y extraen cero

`busconido` · `comunavitacura` · `assetplan` · `remax` · `century21` ·
`zentagroup` · `arriendos_cl`

Tengo el HTML real de todas guardado en la rama `diagnostico-datos`, así que
se calibran sin volver a visitarlas.

### 6. La libreta de edificios

Que un edificio enseñe su año **una vez** y sirva para todos los avisos
futuros de esa dirección. Es tu idea del rol/SII por un camino gratis. Con
el año ya funcionando, esto lo multiplica: los avisos se repiten mucho por
edificio.

### 7. Limpieza menor

Direcciones con HTML crudo adentro (`"Eventos</p><p><strong>Superficie…"`).

---

## 🙋 TU LISTA

### Decisión 1 · El filtro de antigüedad *(la que más cambia el producto)*

Hoy el filtro de "menos de 30 años" **solo descarta a quien publica el año**
y resulta viejo — 24 descartados así, funciona bien. Pero al 88% que **no
publica el año** no lo toca: solo le baja el puntaje.

- **(a)** Dejarlo así — mejor ver un edificio viejo que perderse uno bueno
- **(b)** Duro: sin año publicado, se descarta
- **(c)** Intermedio: sin año no suena por Telegram, pero sí sale en el dashboard

### Decisión 2 · El tope de 8 alertas por corrida

Si califican más de 8, los que no caben **no vuelven a sonar** (solo salen en
el mensaje "👉 Ver la lista completa").

- **(a)** Dejarlo así · **(b)** que el que no cupo reintente mañana ·
  **(c)** subir el tope a ____

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
zentagroup.com         ______________________________________
enlaceinmobiliario.cl  ______________________________________
arriendos.cl           ______________________________________
clasificados.cl        ______________________________________
rentas.cl              ______________________________________
busconido.cl           ______________________________________
```

**Y si ves cualquier portal que "llegue sin info", mándamelo con el link.**
Los tres arreglos más grandes del radar salieron de un link tuyo.

### Paso 3 · Cuatro respuestas de perfil (1 min)

```
Mascotas:           tengo / no aplica
Amoblado:           sin amoblar / amoblado / da lo mismo
Estacionamientos:   mínimo ____ , ideal ____
Pieza de servicio:  ¿cuenta como dormitorio? sí / no      (hoy: NO)
```

---

## 📁 Dónde queda el rastro

| Archivo | Qué guarda |
|---|---|
| `logs/corridas/AAAA-MM-DD-HHMM.log` | el log **completo** de cada corrida, para siempre |
| `logs/historial.jsonl` | una línea por corrida desde el día uno |
| `alertas/casos/` | la ficha de cada aviso, aunque el aviso ya no exista |
| `state/arriendos.json` | cada aviso con su **texto crudo** |
| rama `diagnostico-datos` | el HTML real de cada portal, para depurar sin visitarlos |
