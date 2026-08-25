# Pendientes

**Estado al 25-08-2026, tras la revisión de entrega final.** El radar corre
solo 3 veces al día (09:00, 13:00 y 19:00 de Chile), avisa por Telegram y
publica el dashboard. Diez corridas seguidas sin un solo error.

Se trabaja **conversando en el chat**: tú contestas, yo edito y pusheo.

---

## 📊 Dónde está parado hoy

| | |
|---|---|
| Corridas | 10 seguidas sin errores (21-08 a 24-08) · 5-7 min cada una |
| Inventario | ~1.300 avisos crudos → ~460 únicos → **~40 candidatos** por corrida |
| Fuentes | 28 activas de 45 registradas; 24 entregan avisos |
| Tests | **701**, sin red, corren en 7 segundos |

**Cobertura de datos en los candidatos:**

| Dato | Ahora | Al partir la auditoría (21-08 AM) |
|---|---|---|
| precio | **87%** | 41% |
| dirección | 60% | 62% |
| en el mapa | 58% | 41% |
| año de construcción | **6%** ⚠️ | 12% |

La dirección se ve igual y el año se ve peor, y las dos cosas son el mismo
fenómeno: las direcciones que no ubicaban nada se BORRAN en vez de mostrarse
("Vitacura", "Edificio de 18", "Antigüedad: 30"), y el inventario rota — los
candidatos de hoy son avisos nuevos que llegan sin año, no los mismos de la
semana pasada. El año es acumulativo por diseño (ver el punto 1).

---

## 🔴 MI LISTA — lo único abierto

### 1. El año de construcción ⚠️ *el criterio SÍ O SÍ, y el dato más escaso*

**Ya no es un problema del lector** — se auditó ficha por ficha contra el
HTML real de las cinco fuentes más grandes: toctoc y mitula lo publican
rotulado y se lee bien; engelvoelkers lo escribía en prosa y se arregló;
chilepropiedades y houm simplemente **no lo publican**. El hueco es de los
portales.

Lo que lo va a cerrar es **la libreta de edificios** (`state/edificios.json`):
lo que un aviso enseña sobre una dirección le sirve a todos los avisos de esa
dirección, para siempre. Ya conoce 10 edificios (9 con año útil; 1 anulado
porque dos portales dijeron años distintos y ante la duda se calla). Todavía
no rescata a nadie — el valor es acumulativo. **Medir el 28-08**: cuántos
edificios conoce y cuántos avisos ganaron el año por ella.

### 2. Fuentes intermitentes — vigilar, no arreglar

mitula (60 ó 0), doomos (31 ó 0) y economicos (51 ó 0) alternan sin patrón:
anti-bot intermitente, no muerte. Quedaron marcadas `entrega_variable` para
que sus ceros no disparen la alarma de "dejó de entregar". Si alguna se queda
en cero UNA SEMANA seguida, eso ya es otra cosa y hay que mirarla.

---

## ✅ Cerrado en la revisión final (25-08)

- **busconido CONFIRMADA**: su ruta candidata entregó 23 avisos en diez
  corridas seguidas. Era el portal 100% de arriendo que faltaba.
- **assetplan apagada**: el experimento de la espera extra entregó 1 aviso en
  diez corridas, a 14-22 s de Chromium cada una, y ni ese fue candidato.
- **La cola administrativa ya no se muestra**: "Aníbal Pinto, Region
  Metropolitana" → "Aníbal Pinto". La primera comuna conocida cierra la
  dirección.
- **README al día**: 45 fuentes registradas / 28 activas (decía 41), 701
  tests (decía 433), 3 corridas al día (decía 2).
- **Una llave YAML duplicada** en busconido que la hacía reportarse como no
  confirmada; los tres YAML quedaron verificados contra duplicados.
- **Direcciones con HTML crudo**: ya no queda ninguna en el estado.
- **Verificación de entrega**: suite completa 3 veces (701/701 las tres),
  pyflakes limpio, dashboard reconstruido 3 veces desde el estado real (byte
  a byte idéntico) y probado en Chromium — tabla, buscador, chips, mapa y el
  popup del pin con sus cuatro links, cero errores de JavaScript.

## ✅ Cerrado el 21-08 (resumen)

toctoc recuperada (timeout tolerante) · la ficha técnica que vivía ARRIBA del
título (precio+año de goplaceit) · "Mts" ya no es m² · el canon de doomos
(55%→93%) · "150 m 2" con espacio (yapo) · 17 direcciones que no eran
direcciones · la limpieza unificada en `limpiar_direccion` (extractor y
memoria) · `es_nuevo` con la URL de red (47 falsos "nuevos" → 3) · "Vitacura,
Metropolitana" fuera de las llaves · el paso de tests de 5 min 53 s → 15 s ·
comunavitacura, zentagroup, trovit y nestoria apagadas con motivo medido ·
libreta de edificios en producción.

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
