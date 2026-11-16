#!/usr/bin/env python3
"""Bot visual para Frogs Kitchen (ADB + OpenCV)."""
import os
import random
import subprocess
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

_print = print


def print(*args, **kw):    # noqa: A001 — antepone la hora a cada línea del log
    _print(time.strftime("%H:%M:%S"), *args, **kw)


# ============================================================
# CONFIGURACIÓN (lo único que normalmente tocarás)
# ============================================================
DIR_PLANTILLAS = "."          # carpeta donde están los .png
DEBUG = False                 # True = imprime la similitud de cada plantilla por fotograma
TOQUE_SUELO = (327, 1118)     # coordenada neutra para cerrar menús (respaldo si no hay X)
# Puntos neutros (suelo vacío) para cerrar los menús sin X. Se toca el más lejano al último
# botón pulsado, para no acertar nunca en el propio menú. Añade/quita los que quieras.
SUELOS_SEGUROS = [(327, 1118), (780, 700), (200, 600), (850, 1500), (250, 1500)]
MAX_COMPRAS_RAFAGA = 10
MAX_FALLOS_CAPTURA = 60       # capturas fallidas seguidas antes de parar (cada una espera ~10 s e
                              # intenta reconectar ADB: 60 ≈ 10 min de margen para que vuelva el móvil)
AUTO_SIGUIENTE_UBICACION = True   # False = el bot ignora el botón de próxima ubicación

# --- Velocidad (rangos en segundos; se elige un valor al azar dentro del rango) ---
CAPTURA_RAW = True            # True = screencap sin PNG (más rápido); si falla, vuelve a PNG solo
CAPTURA_MODO = "adb"          # "adb" = screencap (~0.5 s/captura) | "v4l2" = stream de scrcpy (~0.03 s)
V4L2_DISPOSITIVO = "/dev/video2"   # el mismo de scrcpy --v4l2-sink (solo si CAPTURA_MODO = "v4l2")
LATENCIA_VIDEO = 0.25         # s tras un toque en los que se descartan fotogramas (aún no lo reflejan)
RESOLUCION = (1080, 2316)     # ancho, alto nativos; si el stream llega distinto se reescala a esto
SCROLL_ACTIVO = False         # desplaza la pantalla arriba/abajo cada cierto tiempo
SCROLL_CADA = (100, 140)      # s entre desplazamientos (al azar en el rango), haga lo que haga el bot
SCROLL_REPETICIONES = 4       # deslizamientos seguidos en la misma dirección (el nivel es largo)
SCROLL_X = 540                # x del deslizamiento (centro)
SCROLL_Y = (1900, 400)        # y inicial y final de un deslizamiento "hacia abajo" (dedo sube): 1500 px
SCROLL_DURACION = 350         # ms por deslizamiento (más lento = sin inercia, no toca nada)
ESCALA_BUSQUEDA = 0.5        # el matching se hace a esta escala (0.5 ≈ 5x más rápido; 1.0 = sin reducir)
PAUSA_INACTIVO = (0.05, 0.1)   # pausa entre ciclos cuando NO se ha hecho nada (antes 1.5-3.0)
ESPERA_MENU = 1.0             # máximo que espera a que aparezca el botón verde tras abrir un menú
ESPERA_MENU_FLECHA = 3.0      # lo mismo, pero para las flechas roja y amarilla (su menú tarda más)
ESPERA_BOTON = 0.3            # s que espera tras VER el botón verde por 1ª vez, a que deje de moverse
PAUSA_COMPRA = (0.01, 0.05)    # entre compras de la ráfaga (antes 0.4-0.7)
ESPERA_CIERRE = 0.1           # tras cerrar un menú (antes 0.8)
DURACION_TOQUE = (2, 20)     # ms que dura cada toque (antes 80-150)
TOQUE_ASINCRONO = True        # True = no espera a que acabe el toque y captura ya (solapa ambos)
REPETIR_TIP = 1.0             # s sin volver a tocar una propina en el mismo sitio
MAX_TOQUES_DIRECTOS = 8       # máximo de propinas que se tocan en un mismo fotograma
# Anti-atasco: una propina real desaparece al tocarla. Si "la misma" sigue ahí tras tantos
# toques seguidos, es un falso positivo (decoración, anuncio...) y se ignora un tiempo
MAX_TOQUES_MISMA_PROPINA = 5
BLOQUEO_PROPINA = 120         # s que se ignora un punto atascado
GAP_PROPINA = 5.0             # s máx. entre toques para considerarlos "seguidos"

# --- Propinas (monedas): se detectan por color y tamaño porque la moneda GIRA ---
ZONA_PROPINA = (0, 300, 1080, 1900)   # x0, y0, x1, y1 donde se buscan (sin barras de arriba/abajo)
ALTO_MONEDA = (47, 52)                # px; al girar el alto NO cambia (~50), el ancho sí
ANCHO_MONEDA = (10, 56)               # px
RELLENO_MONEDA = 0.75                 # fracción mínima del rectángulo ocupada por color dorado

# --- Salida de menús cuando no hay botón de cerrar (X) ---
# "suelo" = toca TOQUE_SUELO | "atras" = tecla Atrás de Android | "ninguno" = no toca nada
SALIDA_FALLBACK = "suelo"

# --- Diagnóstico: guarda la captura justo cuando algo falla (carpeta "fallos/") ---
GUARDAR_FALLOS = True         # guarda el fotograma al salir de un menú sin haber comprado nada
MAX_FALLOS_GUARDADOS = 20     # tope de archivos para no llenar el disco


@dataclass(frozen=True)
class Objetivo:
    archivo: str
    umbral: float = 0.85
    offset_y: int = 0         # desplazamiento vertical del clic (px, hacia abajo)
    tipo: str = "otro"        # categoría: decide la prioridad (ver PRIORIDAD_TIPO)
    botones: tuple = ()       # botones verdes que le corresponden al abrir su menú (vacío = todos)
    espera_menu: float = None # s máx. esperando ese botón (None = ESPERA_MENU)
    prioridad: int = 0        # desempate dentro del mismo tipo: menor = se atiende antes
    salida: str = "X"         # cómo se cierra su menú: "X" (botón cerrar.png) | "suelo" (sin X)


# Prioridad por tipo: número menor = se atiende antes. Para cambiar el orden, cambia aquí
# los números; para un tipo nuevo, añade una entrada y ponle tipo="..." a sus Objetivo.
PRIORIDAD_TIPO = {
    "propina": 0,
    "caja": 1,
    "alerta": 2,
}

# Botones verdes de compra, según el menú que abre cada activador:
BOTON_ESTACION = Objetivo("boton_verde.png", 0.82)     # menú de estación (flechas roja y amarilla)
BOTON_MEJORAS = Objetivo("boton_verde_2.png", 0.82)    # menú de mejoras (flecha verde con "!")

_ACTIVADORES = [
    Objetivo("(monedas por color)", tipo="propina"),   # sin PNG: ver buscar_monedas()
    Objetivo("caja_new.png", 0.85, tipo="caja"),
    Objetivo("caja_rana.png", 0.85, tipo="caja"),
    Objetivo("caja_diamante.png", 0.85, tipo="caja"),
    Objetivo("flecha_roja.png", 0.83, offset_y=35, tipo="alerta", prioridad=0,
             botones=(BOTON_ESTACION,), espera_menu=ESPERA_MENU_FLECHA, salida="suelo"),
    Objetivo("flecha_amarilla.png", 0.85, offset_y=35, tipo="alerta", prioridad=1,
             botones=(BOTON_ESTACION,), espera_menu=ESPERA_MENU_FLECHA, salida="suelo"),
    Objetivo("flecha_verde.png", 0.92, tipo="alerta", prioridad=2,   # estricto: exige el "!"
             botones=(BOTON_MEJORAS,)),
]
# Orden de atención: primero por prioridad de su tipo y, dentro del tipo, por su campo
# `prioridad` (la roja antes que la amarilla). Si empatan, manda el orden de la lista.
ACTIVADORES = sorted(_ACTIVADORES, key=lambda o: (PRIORIDAD_TIPO.get(o.tipo, 99), o.prioridad))
# Tipos que no abren ningún menú y pueden salir varios a la vez: se tocan TODOS los que
# haya en el fotograma y se sigue (sin ráfaga de compra ni salir_de_menus).
TIPOS_DIRECTOS = {"propina"}
# Todos los botones verdes: respaldo para activadores sin botón asignado (las cajas) y diagnóstico
BOTONES_COMPRA = [BOTON_ESTACION, BOTON_MEJORAS]
CERRAR_MENU = Objetivo("cerrar.png", 0.82)
CERRAR_IDLE = Objetivo("cerrar.png", 0.85)
# Popups que bloquean el juego: se tocan una sola vez, sin ráfaga de compra ni salir_de_menus
ACCIONES_UNICAS = [
    Objetivo("boton_siguiente.png", 0.85),   # "¡A la próxima ubicación!" (texto en dos líneas)
    Objetivo("proxima_ubicacion.png", 0.85), # variante con el texto en una línea
    Objetivo("preparado.png", 0.85),         # botón "¡Preparado!" (recórtalo de una captura nativa)
]


# ============================================================
# DISPOSITIVO (ADB)
# ============================================================
def capturar_png():
    r = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def capturar_raw():
    """screencap sin comprimir: cabecera (w, h, formato...) + píxeles RGBA.
    Devuelve None si el formato no es el esperado (p. ej. HDR)."""
    r = subprocess.run(["adb", "exec-out", "screencap"], capture_output=True)
    d = r.stdout
    if r.returncode != 0 or len(d) < 16:
        return None
    w = int.from_bytes(d[0:4], "little")
    h = int.from_bytes(d[4:8], "little")
    fmt = int.from_bytes(d[8:12], "little")
    n = w * h * 4
    if fmt != 1 or len(d) - n not in (12, 16):   # 1 = RGBA_8888
        return None
    px = np.frombuffer(d, np.uint8, count=n, offset=len(d) - n).reshape(h, w, 4)
    return cv2.cvtColor(px, cv2.COLOR_RGBA2BGR)


class LectorVideo:
    """Lee en segundo plano el stream de v4l2 (scrcpy) y se queda con el último fotograma.
    obtener() solo devuelve fotogramas NUEVOS (posteriores al que entregó la vez anterior)
    y posteriores a `despues_de`, para no decidir sobre una imagen que aún no refleja el toque."""

    def __init__(self, cap):
        self.cap = cap
        self._cerrojo = threading.Lock()
        self._ultimo = None
        self._instante = 0.0
        self._entregado = 0.0
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        while True:
            ok, frame = self.cap.read()
            if ok:
                with self._cerrojo:
                    self._ultimo, self._instante = frame, time.time()
            else:
                time.sleep(0.01)

    def obtener(self, despues_de=0.0, timeout=2.0):
        limite = time.time() + timeout
        while time.time() < limite:
            with self._cerrojo:
                if self._ultimo is not None and self._instante > max(despues_de, self._entregado):
                    self._entregado = self._instante
                    return self._ultimo
            time.sleep(0.003)
        return None


_lector = None
_ultimo_toque = 0.0       # instante del último comando enviado al móvil


def _capturar_v4l2():
    global _lector
    if _lector is None:
        cap = cv2.VideoCapture(V4L2_DISPOSITIVO, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise SystemExit(f"No se puede abrir {V4L2_DISPOSITIVO}. ¿Está scrcpy en marcha con "
                             f"--v4l2-sink={V4L2_DISPOSITIVO}? (o pon CAPTURA_MODO = \"adb\")")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        _lector = LectorVideo(cap)
    frame = _lector.obtener(despues_de=_ultimo_toque + LATENCIA_VIDEO)
    if frame is not None and (frame.shape[1], frame.shape[0]) != RESOLUCION:
        frame = cv2.resize(frame, RESOLUCION, interpolation=cv2.INTER_AREA)
    return frame


def capturar():
    global CAPTURA_RAW
    if CAPTURA_MODO == "v4l2":
        return _capturar_v4l2()
    if CAPTURA_RAW:
        img = capturar_raw()
        if img is not None:
            return img
        print("Captura raw no válida en este móvil, vuelvo a PNG.")
        CAPTURA_RAW = False
    return capturar_png()


def _adb(args):
    """Lanza un comando adb; en modo asíncrono no espera a que termine."""
    global _ultimo_toque
    _ultimo_toque = time.time()
    if TOQUE_ASINCRONO:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(args)


def _swipe(x, y, dispersion=8):
    x = int(x + random.randint(-dispersion, dispersion))
    y = int(y + random.randint(-dispersion, dispersion))
    duracion = random.randint(*DURACION_TOQUE)
    return f"input swipe {x} {y} {x} {y} {duracion}"


_ultimo_clic = [None]     # último punto tocado (el botón verde, al salir de un menú)


def tocar(x, y):
    _ultimo_clic[0] = (x, y)
    _adb(["adb", "shell", _swipe(x, y)])


def tocar_varios(puntos):
    """Todos los toques en UN solo comando adb (un único arranque de shell).
    Dispersión pequeña (4 px): la moneda de canto mide solo ~10 px de ancho."""
    _adb(["adb", "shell", " ; ".join(_swipe(x, y, 4) for x, y in puntos)])


# ============================================================
# VISIÓN
# ============================================================
_cache = {}


def plantilla(archivo):
    """Carga cada PNG una sola vez."""
    if archivo not in _cache:
        ruta = os.path.join(DIR_PLANTILLAS, archivo)
        img = cv2.imread(ruta, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"Falta la plantilla: {ruta}")
        _cache[archivo] = img
    return _cache[archivo]


def _reducir(img):
    if ESCALA_BUSQUEDA == 1.0:
        return img
    return cv2.resize(img, None, fx=ESCALA_BUSQUEDA, fy=ESCALA_BUSQUEDA,
                      interpolation=cv2.INTER_AREA)


_cache_red = {}
_pantalla_red = [None, None]      # [fotograma original, su versión reducida]


def plantilla_reducida(archivo):
    if archivo not in _cache_red:
        _cache_red[archivo] = _reducir(plantilla(archivo))
    return _cache_red[archivo]


def pantalla_reducida(pantalla):
    """Reduce el fotograma una sola vez aunque se busquen 10 plantillas en él."""
    if _pantalla_red[0] is not pantalla:
        _pantalla_red[:] = [pantalla, _reducir(pantalla)]
    return _pantalla_red[1]


def buscar(pantalla, obj):
    """Devuelve (similitud, x, y) de la mejor coincidencia, sin tocar nada.
    x, y siempre en píxeles nativos."""
    tpl = plantilla(obj.archivo)
    res = cv2.matchTemplate(pantalla_reducida(pantalla), plantilla_reducida(obj.archivo),
                            cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    h, w = tpl.shape[:2]
    return (score, int(loc[0] / ESCALA_BUSQUEDA) + w // 2,
            int(loc[1] / ESCALA_BUSQUEDA) + h // 2 + obj.offset_y)


def buscar_todos(pantalla, obj):
    """Todas las coincidencias de obj en el fotograma (no solo la mejor).
    Devuelve [(x, y)] de los centros, de más a menos parecida, sin solapes."""
    tpl = plantilla(obj.archivo)
    res = cv2.matchTemplate(pantalla_reducida(pantalla), plantilla_reducida(obj.archivo),
                            cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where((res >= obj.umbral) & (res <= 1.01))   # descarta inf/NaN de zonas lisas
    h, w = tpl.shape[:2]
    hr, wr = max(1, int(h * ESCALA_BUSQUEDA)), max(1, int(w * ESCALA_BUSQUEDA))
    elegidos = []
    for i in np.argsort(-res[ys, xs]):
        x, y = int(xs[i]), int(ys[i])
        if all(abs(x - ex) >= wr or abs(y - ey) >= hr for ex, ey in elegidos):
            elegidos.append((x, y))
            if len(elegidos) >= MAX_TOQUES_DIRECTOS:
                break
    return [(int(x / ESCALA_BUSQUEDA) + w // 2, int(y / ESCALA_BUSQUEDA) + h // 2 + obj.offset_y)
            for x, y in elegidos]


def buscar_monedas(pantalla, obj=None):
    """Monedas/propinas por color y tamaño, no por plantilla: al girar cambia su ancho
    y su dibujo (cara, canto, reverso), pero el alto (~50 px) y el dorado se mantienen.
    Devuelve [(x, y)] de los centros."""
    x0, y0, x1, y1 = ZONA_PROPINA
    hsv = cv2.cvtColor(pantalla[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    mascara = cv2.inRange(hsv, (8, 120, 170), (32, 255, 255))   # dorado/naranja saturado
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mascara)
    puntos = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        relleno = area / (w * h)
        es_moneda = (ALTO_MONEDA[0] <= h <= ALTO_MONEDA[1]
                     and ANCHO_MONEDA[0] <= w <= ANCHO_MONEDA[1]
                     and relleno >= RELLENO_MONEDA)
        if DEBUG and 38 <= h <= 70 and 6 <= w <= 70:
            print(f"   blob dorado ({x0 + x + w // 2},{y0 + y + h // 2}) {w}x{h} "
                  f"relleno={relleno:.2f} -> {'MONEDA' if es_moneda else 'descartado'}")
        if es_moneda:
            puntos.append((x0 + x + w // 2, y0 + y + h // 2))
    return puntos[:MAX_TOQUES_DIRECTOS]


# Cómo se buscan los tipos directos: por defecto con plantillas (buscar_todos); los que
# aparecen aquí usan su propio detector, con la misma firma (pantalla, obj) -> [(x, y)]
DETECTORES = {"propina": buscar_monedas}

_recientes = []   # [(x, y, instante)] de los últimos toques directos
_historial_propina = []   # [x, y, toques seguidos, último toque, bloqueado hasta]
_atascadas = []           # puntos recién bloqueados (ciclo() guarda una captura de ellos)


def nuevos_puntos(puntos):
    """Quita los puntos tocados hace menos de REPETIR_TIP s (la misma propina
    aún en pantalla mientras se recoge)."""
    ahora = time.time()
    _recientes[:] = [r for r in _recientes if ahora - r[2] < REPETIR_TIP]
    libres = [(x, y) for x, y in puntos
              if all(abs(x - rx) > 40 or abs(y - ry) > 40 for rx, ry, _ in _recientes)]
    _recientes.extend((x, y, ahora) for x, y in libres)
    # Anti-atasco: cuenta toques seguidos en el mismo sitio y bloquea los que no desaparecen
    _historial_propina[:] = [e for e in _historial_propina if ahora - e[3] < 600 or ahora < e[4]]
    utiles = []
    for x, y in libres:
        e = next((e for e in _historial_propina
                  if abs(e[0] - x) <= 40 and abs(e[1] - y) <= 40), None)
        if e is None:
            e = [x, y, 0, ahora, 0.0]       # x, y, toques seguidos, último toque, bloqueado hasta
            _historial_propina.append(e)
        if ahora < e[4]:
            continue
        e[2] = e[2] + 1 if ahora - e[3] < GAP_PROPINA else 1
        e[3] = ahora
        if e[2] > MAX_TOQUES_MISMA_PROPINA:
            e[4], e[2] = ahora + BLOQUEO_PROPINA, 0
            _atascadas.append((x, y))
            continue
        utiles.append((x, y))
    return utiles


def tocar_si(pantalla, obj):
    """Busca obj en el fotograma dado y lo toca si supera el umbral."""
    score, x, y = buscar(pantalla, obj)
    if DEBUG:
        print(f"{obj.archivo:22s} {score:.2f}")
    if score >= obj.umbral:
        tocar(x, y)
        print(f"-> CLIC en {obj.archivo} ({x},{y}) sim={score:.2f}")
        return True
    return False


def tocar_mejor(pantalla, objetivos):
    """Prueba varias plantillas del mismo elemento y toca la de mayor similitud
    entre las que superan su umbral (para elementos con varias variantes)."""
    mejor = None
    for obj in objetivos:
        score, x, y = buscar(pantalla, obj)
        if DEBUG:
            print(f"{obj.archivo:22s} {score:.2f}")
        if score >= obj.umbral and (mejor is None or score > mejor[0]):
            mejor = (score, x, y, obj)
    if mejor is None:
        return False
    score, x, y, obj = mejor
    tocar(x, y)
    print(f"-> CLIC en {obj.archivo} ({x},{y}) sim={score:.2f}")
    return True


# ============================================================
# ACCIONES
# ============================================================
_fallos_guardados = 0


def guardar_fallo(pantalla, motivo, marcar=None):
    """Guarda el fotograma en fallos/ (con un círculo rojo en `marcar`, si se da) e
    imprime la similitud de los botones de compra, para ver qué pasó."""
    global _fallos_guardados
    if not GUARDAR_FALLOS or pantalla is None or _fallos_guardados >= MAX_FALLOS_GUARDADOS:
        return
    img = pantalla.copy()
    if marcar is not None:
        cv2.circle(img, marcar, 30, (0, 0, 255), 5)
    os.makedirs("fallos", exist_ok=True)
    ruta = f"fallos/{motivo}_{int(time.time())}.png"
    cv2.imwrite(ruta, img)
    _fallos_guardados += 1
    sims = ", ".join(f"{o.archivo}={buscar(pantalla, o)[0]:.2f}" for o in BOTONES_COMPRA)
    print(f"   [guardado {ruta}] similitud botones: {sims}")


def comprar_en_rafaga(activador=None):
    """Compra mientras haya botón verde. Usa solo los botones del activador que abrió
    el menú (si los tiene) y espera su espera_menu. Mientras no haya comprado nada, sigue
    capturando para dar tiempo a que el menú termine de abrirse (cada captura ya hace
    de espera). Al VER el botón por primera vez espera ESPERA_BOTON s a que deje de
    moverse y vuelve a capturar antes de tocarlo."""
    botones = activador.botones if activador and activador.botones else BOTONES_COMPRA
    espera = activador.espera_menu if activador and activador.espera_menu is not None \
        else ESPERA_MENU
    limite = time.time() + espera
    compras = 0
    estabilizado = ESPERA_BOTON <= 0
    pantalla = None
    while compras < MAX_COMPRAS_RAFAGA:
        pantalla = capturar()
        if pantalla is not None and not estabilizado and compras == 0:
            if any(buscar(pantalla, b)[0] >= b.umbral for b in botones):
                time.sleep(ESPERA_BOTON)      # 1er avistamiento: que acabe la animación
                estabilizado = True
                continue                      # y se vuelve a capturar con el botón quieto
        if pantalla is not None and tocar_mejor(pantalla, botones):
            compras += 1
            time.sleep(random.uniform(*PAUSA_COMPRA))
        elif compras == 0 and time.time() < limite:
            continue                      # el menú aún se está abriendo
        else:
            if compras == 0:              # se rinde sin haber comprado nada
                guardar_fallo(pantalla, "sin_boton_verde")
            break
    return pantalla      # último fotograma: el menú sigue abierto en él, sirve para buscar la X


def suelo_lejano():
    """El punto neutro más alejado del último toque (el botón verde del menú abierto)."""
    ux, uy = _ultimo_clic[0] or TOQUE_SUELO
    return max(SUELOS_SEGUROS, key=lambda p: (p[0] - ux) ** 2 + (p[1] - uy) ** 2)


def salir_de_menus(pantalla=None, activador=None):
    """Cierra el menú. Si se le pasa el último fotograma (el de comprar_en_rafaga) lo
    reutiliza y se ahorra una captura entera. Los menús sin X (activador.salida ==
    "suelo": flechas roja y amarilla) se cierran tocando suelo neutro, sin buscar la X."""
    print("-> Saliendo de menús...")
    if activador is not None and activador.salida == "suelo":
        tocar(*suelo_lejano())
        time.sleep(ESPERA_CIERRE)
        return
    if pantalla is None:
        pantalla = capturar()
    if pantalla is None or not tocar_si(pantalla, CERRAR_MENU):
        guardar_fallo(pantalla, "salida_sin_X",
                      marcar=TOQUE_SUELO if SALIDA_FALLBACK == "suelo" else None)
        if SALIDA_FALLBACK == "suelo":
            tocar(*TOQUE_SUELO)
        elif SALIDA_FALLBACK == "atras":
            _adb(["adb", "shell", "input", "keyevent", "4"])      # KEYCODE_BACK
    time.sleep(ESPERA_CIERRE)


def ciclo(pantalla):
    """Un ciclo = un solo fotograma, todas las plantillas contra él.
    Devuelve True si ha hecho alguna acción."""
    # Popups bloqueantes primero: mientras estén abiertos no se puede hacer nada más
    if AUTO_SIGUIENTE_UBICACION:
        for obj in ACCIONES_UNICAS:
            if tocar_si(pantalla, obj):
                time.sleep(2.5)          # transición a la nueva ubicación
                return True
    for obj in ACTIVADORES:
        if obj.tipo in TIPOS_DIRECTOS:
            detector = DETECTORES.get(obj.tipo, buscar_todos)
            puntos = nuevos_puntos(detector(pantalla, obj))
            if _atascadas:
                print(f"AVISO: {obj.tipo} fija en {_atascadas}: no desaparece al tocarla; "
                      f"se ignora {BLOQUEO_PROPINA}s (¿anuncio, pantalla bloqueada?)")
                guardar_fallo(pantalla, "propina_atascada", marcar=_atascadas[0])
                _atascadas.clear()
            if DEBUG:
                print(f"{obj.archivo:22s} {len(puntos)} coincidencias nuevas")
            if puntos:
                tocar_varios(puntos)
                print(f"-> {len(puntos)} toque(s) en {obj.archivo}")
                return True
            continue
        if tocar_si(pantalla, obj):
            ultima = comprar_en_rafaga(obj)   # espera sola a que se abra el menú
            salir_de_menus(ultima, obj)       # reutiliza su último fotograma
            return True
    return tocar_si(pantalla, CERRAR_IDLE)   # por si quedó algo abierto


# ============================================================
# MAIN
# ============================================================
_CAPTURA_RAW_INICIAL = CAPTURA_RAW


def recuperar_adb():
    """Tras un fallo de captura: reinicia el servidor adb, espera al móvil y muestra el
    estado de la conexión (device / offline / unauthorized / ausente)."""
    global CAPTURA_RAW
    CAPTURA_RAW = _CAPTURA_RAW_INICIAL      # un fallo puntual no debe dejar el modo lento
    for cmd in (["adb", "start-server"], ["adb", "wait-for-device"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.stderr.strip():
                print(f"   {' '.join(cmd)}: {r.stderr.strip()}")
        except subprocess.TimeoutExpired:
            print(f"   {' '.join(cmd)}: sin respuesta tras 20 s (¿móvil desconectado?)")
    r = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    print("   adb devices -> " + " | ".join(r.stdout.strip().splitlines()[1:] or ["(ninguno)"]))


_scroll_abajo = [True]    # la dirección alterna en cada desplazamiento


def desplazar():
    """Desliza la pantalla SCROLL_REPETICIONES veces hacia abajo o hacia arriba (alterna)
    para revelar zonas del nivel que no se ven. Espera a que termine."""
    y0, y1 = SCROLL_Y if _scroll_abajo[0] else SCROLL_Y[::-1]
    print(f"-> Desplazando {'abajo' if _scroll_abajo[0] else 'arriba'}")
    cmd = " ; ".join(
        f"input swipe {SCROLL_X + random.randint(-30, 30)} {y0 + random.randint(-20, 20)} "
        f"{SCROLL_X + random.randint(-30, 30)} {y1 + random.randint(-20, 20)} {SCROLL_DURACION}"
        for _ in range(SCROLL_REPETICIONES))
    subprocess.run(["adb", "shell", cmd])
    _scroll_abajo[0] = not _scroll_abajo[0]
    time.sleep(0.5)          # que se asiente la pantalla antes de volver a mirar


def main():
    # Precarga: si falta algún PNG, falla ya y no a mitad de ejecución
    for obj in list(ACCIONES_UNICAS):        # estos no son críticos: sin PNG se avisa y se omiten
        try:
            plantilla(obj.archivo)
        except FileNotFoundError:
            print(f"AVISO: falta {obj.archivo}; ese botón se ignorará.")
            ACCIONES_UNICAS.remove(obj)
    for obj in ACTIVADORES + BOTONES_COMPRA + [CERRAR_MENU, CERRAR_IDLE]:
        if obj.tipo not in DETECTORES:       # los de detector propio no tienen PNG
            plantilla(obj.archivo)

    print("Bot de Frogs Kitchen iniciado (Ctrl+C para detener)")
    fallos = 0
    ultimo_scroll = time.time()
    espera_scroll = random.uniform(*SCROLL_CADA)
    while True:
        try:
            t0 = time.time()
            pantalla = capturar()
            if DEBUG:
                print(f"[captura {time.time() - t0:.2f}s]")
            if pantalla is None:
                fallos += 1
                print(f"Error de captura ADB ({fallos}/{MAX_FALLOS_CAPTURA})")
                if fallos >= MAX_FALLOS_CAPTURA:
                    print("Demasiados fallos seguidos, ¿teléfono desconectado? Saliendo.")
                    break
                if CAPTURA_MODO == "adb":
                    recuperar_adb()
                time.sleep(2)
                continue
            fallos = 0

            actuo = ciclo(pantalla)
            if DEBUG:
                print(f"[ciclo total {time.time() - t0:.2f}s]")
            # El ciclo ya terminó (menús cerrados): es un buen momento para desplazar
            if SCROLL_ACTIVO and time.time() - ultimo_scroll >= espera_scroll:
                desplazar()
                ultimo_scroll = time.time()
                espera_scroll = random.uniform(*SCROLL_CADA)
            elif not actuo:
                time.sleep(random.uniform(*PAUSA_INACTIVO))

        except KeyboardInterrupt:
            print("\nBot detenido correctamente.")
            break


if __name__ == "__main__":
    main()
