"""Lógica de Fit Tolerance: tabla de materiales, ajuste de agujeros y ejes, insertos de
rosca, chaflán anti pata de elefante y pieza de prueba. Todo en milímetros.

Convenciones:
- La compensación es de DIÁMETRO. Agujero: lo que sale de pequeño (se suma).
  Eje: lo que sale de grande (se resta). Se miden con la pieza de prueba.
- Se trabaja siempre sobre una copia «<modelo>_ajustado»; el original queda oculto.
"""
import json
import math
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import common

# ----------------------------------------------------------------- materiales
# agujero: cuánto sale de pequeño un agujero (diámetro, mm) -> se agranda eso
# eje: cuánto sale de grande un eje/pivote (diámetro, mm)    -> se adelgaza eso
# pata: chaflán anti pata de elefante recomendado (mm)
MATERIALES_DEFECTO = {
    'PLA': {'agujero': 0.14, 'eje': 0.14, 'pata': 0.4},
    'PETG': {'agujero': 0.20, 'eje': 0.15, 'pata': 0.5},
    'ASA': {'agujero': 0.20, 'eje': 0.15, 'pata': 0.5},
    'ABS': {'agujero': 0.20, 'eje': 0.15, 'pata': 0.5},
    'TPU': {'agujero': 0.25, 'eje': 0.10, 'pata': 0.3},
    'PA-CF': {'agujero': 0.20, 'eje': 0.10, 'pata': 0.4},
}

AJUSTES = {   # holgura de diámetro que se añade al agujero según el uso
    'APRETADO': 0.0,      # a presión: pasadores, rodamientos
    'DESLIZANTE': 0.2,    # entra y sale con la mano, sin juego visible
    'HOLGADO': 0.4,       # gira o desliza suelto: bisagras, ejes que giran
}

# insertos de rosca en caliente (tipo Ruthex / CNC Kitchen): (Ø agujero, profundidad)
INSERTOS = {
    'M2': (3.2, 4.0),
    'M2.5': (3.6, 5.0),
    'M3': (4.0, 6.0),
    'M4': (5.6, 8.5),
    'M5': (6.4, 10.0),
}


def _ruta_tabla():
    try:
        carpeta = Path(bpy.utils.user_resource('CONFIG', path='fit_tolerance', create=True))
    except Exception:
        carpeta = Path.home() / '.fit_tolerance'
        carpeta.mkdir(exist_ok=True)
    return carpeta / 'materiales.json'


def cargar_materiales():
    """Tabla del usuario (se conserva al actualizar el complemento) + materiales por defecto."""
    tabla = json.loads(json.dumps(MATERIALES_DEFECTO))
    ruta = _ruta_tabla()
    if ruta.exists():
        try:
            for k, v in json.loads(ruta.read_text(encoding='utf-8')).items():
                tabla.setdefault(k, {}).update(v)
        except (ValueError, OSError):
            pass
    return tabla


def guardar_materiales(tabla):
    _ruta_tabla().write_text(json.dumps(tabla, indent=2, ensure_ascii=False), encoding='utf-8')


def calibrar(tabla, material, nominal, medido_agujero=None, medido_eje=None):
    """Mete en la tabla lo medido en la pieza de prueba. Devuelve la tabla actualizada."""
    m = tabla.setdefault(material, dict(MATERIALES_DEFECTO.get('PLA')))
    if medido_agujero:
        m['agujero'] = round(nominal - medido_agujero, 3)
    if medido_eje:
        m['eje'] = round(medido_eje - nominal, 3)
    guardar_materiales(tabla)
    return tabla


def diametro_objetivo(tipo, nominal, material, ajuste, tabla=None):
    """Diámetro a modelar para que, impreso, salga al nominal (+ holgura del ajuste)."""
    m = (tabla or cargar_materiales()).get(material, MATERIALES_DEFECTO['PLA'])
    if tipo == 'AGUJERO':
        return nominal + m['agujero'] + AJUSTES.get(ajuste, 0.0)
    return nominal - m['eje']


def nominal_probable(diametro):
    """Medida nominal más probable: redondea a 0,5 mm (8,86 -> 9,0 · 7,9 -> 8,0)."""
    return round(diametro * 2) / 2


# ----------------------------------------------------------------- copia de trabajo
WORK = 'fit_copia'


def copia_de_trabajo(context, obj):
    """La primera operación crea «<modelo>_ajustado» (transformación aplicada) y oculta el
    original; las siguientes trabajan sobre esa misma copia."""
    if obj.get(WORK):
        return obj
    common.require_solid(context, obj)
    col = obj.users_collection[0] if obj.users_collection else context.scene.collection
    me = obj.data.copy()
    me.transform(obj.matrix_world)
    if obj.matrix_world.determinant() < 0:
        me.flip_normals()
    copia = bpy.data.objects.new(f'{obj.name}_ajustado', me)
    col.objects.link(copia)
    copia[WORK] = True
    copia['fit_origen'] = obj.name
    if common.UNIT_PROP in obj:
        copia[common.UNIT_PROP] = obj[common.UNIT_PROP]
    else:
        copia[common.UNIT_PROP] = common.resolve_unit(context.scene, 'AUTO', obj)[0]
    obj.hide_set(True)
    obj.hide_render = True
    for o in context.selected_objects:
        o.select_set(False)
    copia.select_set(True)
    context.view_layer.objects.active = copia
    return copia


# ----------------------------------------------------------------- detectar cilindros
def _bm_mundo(context, obj):
    bm = common.world_bmesh(context, obj)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    return bm


def _circulo(pts2):
    """Círculo por mínimos cuadrados (Kasa). Devuelve (centro, radio, error medio)."""
    x, y = pts2[:, 0], pts2[:, 1]
    A = np.c_[2 * x, 2 * y, np.ones(len(x))]
    b = x * x + y * y
    (cx, cy, c), *_ = np.linalg.lstsq(A, b, rcond=None)
    r = math.sqrt(max(c + cx * cx + cy * cy, 0.0))
    err = float(np.abs(np.hypot(x - cx, y - cy) - r).mean())
    return np.array([cx, cy]), r, err


def _base_perpendicular(eje):
    u = np.cross(eje, [1, 0, 0] if abs(eje[0]) < 0.9 else [0, 1, 0])
    u /= np.linalg.norm(u)
    return u, np.cross(eje, u)


def _eje_por_normales(N, A):
    """Dirección a la que todas las normales son perpendiculares (menor autovalor)."""
    C = (N * A[:, None]).T @ N
    val, vec = np.linalg.eigh(C)
    return vec[:, 0], val


def _crecer(f0, acepta, angulo_max, limite):
    """Crecimiento en anchura (anillos alrededor de la cara pulsada)."""
    from collections import deque
    lim = math.cos(math.radians(angulo_max))
    vistas = {f0.index}
    cola, caras = deque([f0]), []
    while cola and len(caras) < limite:
        f = cola.popleft()
        caras.append(f)
        for e in f.edges:
            for g in e.link_faces:
                if g.index not in vistas and g.normal.dot(f.normal) >= lim:
                    vistas.add(g.index)
                    if acepta(g):
                        cola.append(g)
    return caras


def detectar_cilindro(bm, cara_inicial, angulo_max=35.0, limite=20000, semilla=40):
    """Desde la cara pulsada busca el cilindro (agujero o eje) al que pertenece.

    Crece por caras vecinas de pliegue suave, pero solo acepta las que están sobre el
    cilindro estimado (distancia al eje ~ radio y normal radial). Se reajusta varias
    veces, así no se escapa por superficies curvas o texturizadas de alrededor.
    Devuelve centro, eje, radio, extremos, tipo ('AGUJERO' o 'EJE') y calidad."""
    c0 = np.array(cara_inicial.calc_center_median())
    # 1) semilla: vecinas con pliegue suave, hasta que abarquen bastante curva (las
    #    normales se abren) para que el primer ajuste del radio sea fiable
    n = semilla
    while True:
        caras = _crecer(cara_inicial, lambda g: True, angulo_max, n)
        N = np.array([f.normal for f in caras])
        A = np.array([max(f.calc_area(), 1e-12) for f in caras])
        _e, val = _eje_por_normales(N, A)
        if val[1] >= 0.08 * val.sum() or len(caras) < n or n >= 6000:
            break
        n *= 2
    eje = None
    for _ in range(8):
        N = np.array([f.normal for f in caras])
        A = np.array([max(f.calc_area(), 1e-12) for f in caras])
        eje_n, val = _eje_por_normales(N, A)
        if val[1] < 1e-3 * val.sum() and eje is None:
            # todavía plano (normales iguales): crecer más sin condiciones
            caras = _crecer(cara_inicial, lambda g: True, angulo_max, len(caras) * 3)
            if len(caras) >= limite:
                break
            continue
        eje = eje_n
        u, v = _base_perpendicular(eje)
        idx = sorted({vv.index for f in caras for vv in f.verts})
        P = np.array([bm.verts[i].co for i in idx])
        c2, r, _err = _circulo(np.c_[P @ u, P @ v])
        centro = c2[0] * u + c2[1] * v
        tol = max(0.06 * r, 1e-4)

        def acepta(g, centro=centro, eje=eje, r=r, tol=tol):
            p = np.array(g.calc_center_median()) - centro
            rad = p - (p @ eje) * eje
            d = np.linalg.norm(rad)
            if abs(d - r) > tol + 0.5 * math.sqrt(g.calc_area()) * 0.2:
                return False
            return abs(np.dot(np.array(g.normal), rad / (d + 1e-12))) > 0.8
        nuevas = _crecer(cara_inicial, acepta, angulo_max, limite)
        if len(nuevas) == len(caras) and set(f.index for f in nuevas) == set(f.index for f in caras):
            break
        caras = nuevas
    if eje is None or len(caras) < 3:
        raise common.AddonError('Ahí no hay una pared curva: haz clic en la pared del agujero o del eje.')
    N = np.array([f.normal for f in caras])
    A = np.array([max(f.calc_area(), 1e-12) for f in caras])
    eje, val = _eje_por_normales(N, A)
    if val[0] > 0.15 * val.sum():
        raise common.AddonError('Esa zona no parece un cilindro (las caras no rodean un eje).')
    u, v = _base_perpendicular(eje)
    idx = sorted({vv.index for f in caras for vv in f.verts})
    P = np.array([bm.verts[i].co for i in idx])
    c2, r, err = _circulo(np.c_[P @ u, P @ v])
    t = P @ eje
    centro = c2[0] * u + c2[1] * v
    Fc = np.array([f.calc_center_median() for f in caras])
    radial = (Fc - centro) - np.outer((Fc - centro) @ eje, eje)
    hacia_fuera = np.einsum('ij,ij->i', N, radial)
    tipo = 'AGUJERO' if (hacia_fuera * A).sum() < 0 else 'EJE'
    # cobertura angular: 1 - el mayor hueco sin vértices (no depende de cuántas caras tenga)
    ang = np.sort(np.arctan2(P @ v - c2[1], P @ u - c2[0]))
    huecos = np.diff(np.r_[ang, ang[0] + 2 * math.pi])
    cobertura = 1.0 - float(huecos.max()) / (2 * math.pi)
    _ = c0
    return {
        'centro': centro, 'eje': eje, 'radio': r, 't0': float(t.min()), 't1': float(t.max()),
        'tipo': tipo, 'error': err, 'cobertura': cobertura, 'caras': len(caras),
    }


def _dentro(tree, punto, direcciones=((0, 0, 1), (1, 0, 0), (0, 1, 0))):
    """¿El punto está dentro del sólido? Paridad de cortes en tres direcciones (mayoría)."""
    votos = 0
    for d in direcciones:
        d = Vector(d)
        n, o = 0, Vector(punto)
        for _ in range(200):
            loc, _nrm, _i, _dist = tree.ray_cast(o, d)
            if loc is None:
                break
            n += 1
            o = loc + d * 1e-5
        votos += n % 2
    return votos >= 2


# ----------------------------------------------------------------- cortadores
def _tubo_bmesh(centro, eje, r_in, r_out, t0, t1, segmentos=96):
    """Tubo (o cilindro si r_in == 0) entre t0 y t1 a lo largo del eje, cerrado y con
    las normales hacia fuera."""
    if r_in <= 0:
        return common.cylinder_bmesh(Vector(centro) + Vector(eje) * (t0 + t1) / 2, eje, r_out, t1 - t0, segmentos)
    e = Vector(eje).normalized()
    u = e.orthogonal().normalized()
    v = e.cross(u)
    c = Vector(centro)
    bm = bmesh.new()
    anillos = []
    for t in (t0, t1):
        for r in (r_out, r_in):
            anillos.append([bm.verts.new(c + e * t + (u * math.cos(a) + v * math.sin(a)) * r)
                            for a in (2 * math.pi * k / segmentos for k in range(segmentos))])
    o0, i0, o1, i1 = anillos
    for k in range(segmentos):
        n = (k + 1) % segmentos
        bm.faces.new((o0[k], o0[n], o1[n], o1[k]))      # pared exterior
        bm.faces.new((i0[n], i0[k], i1[k], i1[n]))      # pared interior
        bm.faces.new((o1[k], o1[n], i1[n], i1[k]))      # tapa superior
        bm.faces.new((o0[n], o0[k], i0[k], i0[n]))      # tapa inferior
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.normal_update()
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    return bm


def _booleana(context, obj, cortador, operacion):
    """Booleana sobre la malla triangulada: las caras grandes con agujeros que dejan las
    booleanas anteriores confunden al solver exacto."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    common.boolean(context, obj, cortador, operacion)


def _objeto_cortador(bm, nombre, col):
    try:
        return common.new_object(bm, nombre, col)
    finally:
        bm.free()


def ajustar_cilindro(context, obj, hit_loc, nominal_mm=0.0, material='PLA', ajuste='DESLIZANTE'):
    """Clic en un agujero o un eje: lo deja a la medida que hace falta para que,
    impreso, salga al nominal. Devuelve (copia, mensaje)."""
    obj = copia_de_trabajo(context, obj)
    mm = common.factor(context.scene, 'AUTO', obj)
    bm = _bm_mundo(context, obj)
    try:
        tree = BVHTree.FromBMesh(bm)
        _loc, _n, idx, _d = tree.find_nearest(Vector(hit_loc))
        if idx is None:
            raise common.AddonError('No encuentro la superficie pulsada.')
        cil = detectar_cilindro(bm, bm.faces[idx])
        if cil['cobertura'] < 0.85:          # segundo intento con una semilla más grande
            otro = detectar_cilindro(bm, bm.faces[idx], semilla=400)
            if otro['cobertura'] > cil['cobertura']:
                cil = otro
    finally:
        bm.free()
    if cil['cobertura'] < 0.85:
        raise common.AddonError('Solo veo un trozo de curva: no parece un agujero o eje completo.')
    largo = (cil['t1'] - cil['t0']) * mm
    d_actual = 2 * cil['radio'] * mm
    if largo < max(0.8, 0.15 * d_actual) or cil['error'] * mm > 0.05 * d_actual:
        raise common.AddonError(f'Eso parece un borde redondeado, no un agujero o un eje '
                                f'(curva de {largo:.1f} mm de largo). Haz clic en la pared del cilindro.')
    nominal = nominal_mm or nominal_probable(d_actual)
    d_obj = diametro_objetivo(cil['tipo'], nominal, material, ajuste)
    if abs(d_obj - d_actual) < 0.01:
        return obj, f'{cil["tipo"].lower()} Ø{d_actual:.2f} ya está a la medida'
    eje = Vector(cil['eje'])
    centro = Vector(cil['centro'])
    t0, t1 = cil['t0'], cil['t1']
    margen = 0.6 / mm
    # extremos libres: si justo después del extremo, en el eje, no hay material, se puede
    # alargar el cortador por ahí sin tocar nada más
    bm = _bm_mundo(context, obj)
    try:
        tree = BVHTree.FromBMesh(bm)
        libre0 = not _dentro(tree, centro + eje * (t0 - margen))
        libre1 = not _dentro(tree, centro + eje * (t1 + margen))
        if cil['tipo'] == 'EJE':
            # en un eje el centro es macizo: miramos justo fuera del radio
            fuera = Vector(cil['centro']) + Vector(np.cross(cil['eje'], [0.0, 0.0, 1.0]) if abs(cil['eje'][2]) < 0.9
                                                  else np.cross(cil['eje'], [1.0, 0.0, 0.0])).normalized() * (cil['radio'] * 0.5)
            libre0 = not _dentro(tree, fuera + eje * (t0 - margen))
            libre1 = not _dentro(tree, fuera + eje * (t1 + margen))
    finally:
        bm.free()
    a = t0 - (margen if libre0 else -0.002 / mm)
    b = t1 + (margen if libre1 else -0.002 / mm)
    col = obj.users_collection[0] if obj.users_collection else context.scene.collection
    r_obj, r_act = d_obj / 2 / mm, d_actual / 2 / mm
    tipo = cil['tipo']
    if tipo == 'AGUJERO' and d_obj > d_actual:            # agrandar: se resta un cilindro
        cutter, op = _objeto_cortador(_tubo_bmesh(centro, eje, 0, r_obj, a, b), 'Fit_cortador', col), 'DIFFERENCE'
    elif tipo == 'AGUJERO':                                # estrechar: se suma un tubo
        cutter = _objeto_cortador(_tubo_bmesh(centro, eje, r_obj, r_act + 0.3 / mm, t0 + 0.002 / mm,
                                              t1 - 0.002 / mm), 'Fit_relleno', col)
        op = 'UNION'
    elif d_obj < d_actual:                                 # adelgazar el eje: se resta un tubo
        cutter = _objeto_cortador(_tubo_bmesh(centro, eje, r_obj, r_act + 1.0 / mm, a, b), 'Fit_cortador', col)
        op = 'DIFFERENCE'
    else:                                                  # engordar el eje: se suma un cilindro
        cutter = _objeto_cortador(_tubo_bmesh(centro, eje, 0, r_obj, t0 + 0.002 / mm, t1 - 0.002 / mm),
                                  'Fit_relleno', col)
        op = 'UNION'
    try:
        _booleana(context, obj, cutter, op)
    finally:
        common.remove_objects([cutter])
    nombre = 'agujero' if tipo == 'AGUJERO' else 'eje'
    return obj, (f'{nombre} Ø{d_actual:.2f} → Ø{d_obj:.2f} mm (nominal {nominal:g}, {material}'
                 + (f', {ajuste.lower()})' if tipo == 'AGUJERO' else ')'))


def insertar_rosca(context, obj, hit_loc, normal, metrica='M3', extra_mm=0.5, chaflan_mm=0.5):
    """Hueco para un inserto de rosca en caliente, perpendicular a la cara pulsada."""
    obj = copia_de_trabajo(context, obj)
    mm = common.factor(context.scene, 'AUTO', obj)
    d, prof = INSERTOS[metrica]
    n = Vector(normal).normalized()
    p = Vector(hit_loc)
    col = obj.users_collection[0] if obj.users_collection else context.scene.collection
    largo = (prof + extra_mm) / mm
    sobra = 0.6 / mm
    r, ch = d / 2 / mm, chaflan_mm / mm
    # perfil (radio, altura sobre la cara): sobresale por fuera, chaflán de entrada y fondo plano
    perfil = [(r + ch, sobra), (r + ch, 0.0), (r, -ch), (r, -largo)]
    bm = _revolucion(perfil, p, n)
    cutter = _objeto_cortador(bm, 'Fit_inserto', col)
    try:
        # comprobación de pared: el inserto necesita material alrededor
        bmw = _bm_mundo(context, obj)
        try:
            tree = BVHTree.FromBMesh(bmw)
            fondo = p - n * largo
            if not _dentro(tree, fondo - n * (0.8 / mm)):
                raise common.AddonError(f'No hay {prof + extra_mm + 0.8:.1f} mm de material bajo la cara '
                                        f'para un inserto {metrica}: elige una métrica más corta.')
        finally:
            bmw.free()
        _booleana(context, obj, cutter, 'DIFFERENCE')
    finally:
        common.remove_objects([cutter])
    return obj, f'Hueco para inserto {metrica}: Ø{d:g} × {prof + extra_mm:g} mm con chaflán de entrada'


def _revolucion(perfil, centro, eje, segmentos=64):
    """Sólido de revolución cerrado: el perfil (radio, altura) va de arriba abajo y se
    cierra con dos tapas planas en el eje."""
    e = Vector(eje).normalized()
    u = e.orthogonal().normalized()
    v = e.cross(u)
    c = Vector(centro)
    bm = bmesh.new()
    anillos = [[bm.verts.new(c + e * z + (u * math.cos(a) + v * math.sin(a)) * r)
                for a in (2 * math.pi * k / segmentos for k in range(segmentos))] for r, z in perfil]
    for A, B in zip(anillos, anillos[1:]):
        for k in range(segmentos):
            n = (k + 1) % segmentos
            bm.faces.new((A[k], A[n], B[n], B[k]))
    bm.faces.new(anillos[0])
    bm.faces.new(list(reversed(anillos[-1])))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])     # todas hacia fuera
    bm.normal_update()
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    return bm


def _matriz(centro, eje):
    from mathutils import Matrix
    q = Vector(eje).normalized().to_track_quat('Z', 'Y')
    return Matrix.Translation(Vector(centro)) @ q.to_matrix().to_4x4()


# ----------------------------------------------------------------- pata de elefante
def chaflan_base(context, obj, chaflan_mm=0.4):
    """Bisela el borde de la cara que apoya en la cama (z mínima) hacia dentro, para que
    la primera capa aplastada no sobresalga. También abre un poco los agujeros de la base."""
    obj = copia_de_trabajo(context, obj)
    mm = common.factor(context.scene, 'AUTO', obj)
    c = chaflan_mm / mm
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        zmin = min(v.co.z for v in bm.verts)
        tol = 1e-4 / mm
        base = [f for f in bm.faces if f.normal.z < -0.999 and all(abs(v.co.z - zmin) < tol for v in f.verts)]
        if not base:
            raise common.AddonError('El modelo no apoya sobre una cara plana. Hazle antes una base plana '
                                    'con Base Support.')
        area = sum(f.calc_area() for f in base) * mm * mm
        # anillo a la altura del chaflán
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, zmin + c), plane_no=(0, 0, 1))
        bm.faces.ensure_lookup_table()
        base_set = {f for f in bm.faces if f.normal.z < -0.999 and all(abs(v.co.z - zmin) < tol for v in f.verts)}
        # aristas de borde de la base: una cara de base y otra lateral
        mov = {}
        for e in {e for f in base_set for e in f.edges}:
            fs = e.link_faces
            if sum(1 for f in fs if f in base_set) != 1:
                continue
            fb = next(f for f in fs if f in base_set)
            a, b = e.verts
            d = (b.co - a.co)
            d.z = 0
            if d.length == 0:
                continue
            perp = Vector((-d.y, d.x, 0)).normalized()
            if perp.dot(fb.calc_center_median() - (a.co + b.co) / 2) < 0:
                perp = -perp                       # hacia dentro de la base (hacia el material)
            for v in (a, b):
                mov.setdefault(v, []).append(perp)
        movidos = 0
        for v, perps in mov.items():
            s = Vector((0, 0, 0))
            for p in perps:
                s += p
            if s.length < 1e-9:
                continue
            s.normalize()
            cosang = max(min(p.dot(s) for p in perps), 0.5)   # esquinas: compensar el ángulo
            v.co += s * (c / cosang)
            movidos += 1
        bm.normal_update()
        bm.to_mesh(me)
        me.update()
    finally:
        bm.free()
    closed, vol = common.solid_info(obj)
    if not closed or vol <= 0:
        raise common.AddonError('El chaflán ha dejado la malla abierta: prueba con uno más pequeño.')
    return obj, f'Chaflán de {chaflan_mm:g} mm en la base ({movidos} vértices del borde, apoyo {area:.0f} mm²)'


# ----------------------------------------------------------------- pieza de prueba
PRUEBA = (3.0, 4.0, 5.0, 6.0, 8.0, 10.0)


def pieza_de_prueba(context, nominales=PRUEBA, grosor=4.0, alto_eje=8.0):
    """Placa con agujeros y fila de pivotes a medida nominal, sin compensar, en mm.
    Se imprime con cada material y se mide con calibre."""
    col = context.collection
    paso = [max(d, 3.0) + 6.0 for d in nominales]
    largo = sum(paso) + 4.0
    ancho = 2 * (max(nominales) + 6.0) + 6.0
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(largo, ancho, grosor), verts=bm.verts[:])
    bmesh.ops.translate(bm, vec=(largo / 2, ancho / 2, grosor / 2), verts=bm.verts[:])
    placa = common.new_object(bm, 'Prueba_holguras', col)
    bm.free()
    placa[common.UNIT_PROP] = 'MM'
    x = 2.0
    fila_ag = ancho * 0.75
    fila_ej = ancho * 0.25
    cortadores, pivotes = [], []
    for d, p in zip(nominales, paso):
        cx = x + p / 2
        cortadores.append(_objeto_cortador(common.cylinder_bmesh((cx, fila_ag, grosor / 2), (0, 0, 1), d / 2,
                                                                 grosor + 2, 96), 'tmp', col))
        pivotes.append(_objeto_cortador(common.cylinder_bmesh((cx, fila_ej, grosor + alto_eje / 2 - 0.01),
                                                              (0, 0, 1), d / 2, alto_eje + 0.02, 96), 'tmp', col))
        # marca de lectura: tantas muescas como posición (1 = 3 mm, 6 = 10 mm)
        x += p
    try:
        for cut in cortadores:
            _booleana(context, placa, cut, 'DIFFERENCE')
        for piv in pivotes:
            _booleana(context, placa, piv, 'UNION')
    finally:
        common.remove_objects(cortadores + pivotes)
    for o in context.selected_objects:
        o.select_set(False)
    placa.select_set(True)
    context.view_layer.objects.active = placa
    return placa, ('Pieza de prueba: agujeros y pivotes de ' + ', '.join(f'{d:g}' for d in nominales)
                   + ' mm, de izquierda a derecha. Imprímela sin compensación y mide.')
