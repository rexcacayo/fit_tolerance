bl_info = {'name': 'Fit Tolerance', 'author': 'Ricardo Lugaresi', 'version': (1, 0, 0),
           'blender': (4, 2, 0), 'location': 'Vista 3D > N > Fit Tolerance',
           'description': 'Agujeros y ejes a medida según el material, huecos para insertos de rosca '
                          'y chaflán anti pata de elefante',
           'category': 'Object'}
import bpy

from . import common, fitting

# -------------------------------------------------------------------- propiedades
_cache = {'items': []}


def _materiales(self, context):
    _cache['items'] = [(k, k, '') for k in fitting.cargar_materiales()]
    return _cache['items']


def _al_cambiar_material(self, context):
    m = fitting.cargar_materiales().get(self.material, fitting.MATERIALES_DEFECTO['PLA'])
    self['agujero'] = m['agujero']
    self['eje'] = m['eje']
    self['chaflan'] = m.get('pata', 0.4)


AJUSTE_ITEMS = [('APRETADO', 'Apretado', 'A presión: pasadores, rodamientos (+0,0 mm)'),
                ('DESLIZANTE', 'Deslizante', 'Entra y sale con la mano, sin juego (+0,2 mm)'),
                ('HOLGADO', 'Holgado', 'Gira o desliza suelto: bisagras, ejes (+0,4 mm)')]
INSERTO_ITEMS = [(k, k, f'Agujero Ø{d:g} mm, {p:g} mm de profundo') for k, (d, p) in fitting.INSERTOS.items()]


class FitProps(bpy.types.PropertyGroup):
    material: bpy.props.EnumProperty(name='Material', items=_materiales, update=_al_cambiar_material)
    agujero: bpy.props.FloatProperty(name='Agujeros salen pequeños', description='Diámetro que pierde un agujero al imprimir (mm)',
                                     default=0.14, min=-1, max=2, precision=2, step=1)
    eje: bpy.props.FloatProperty(name='Ejes salen grandes', description='Diámetro que gana un eje o pivote al imprimir (mm)',
                                 default=0.14, min=-1, max=2, precision=2, step=1)
    ajuste: bpy.props.EnumProperty(name='Ajuste', items=AJUSTE_ITEMS, default='DESLIZANTE')
    nominal: bpy.props.FloatProperty(name='Medida nominal (mm)', description='0 = la adivina redondeando a 0,5 mm',
                                     default=0.0, min=0.0, max=500, precision=2)
    metrica: bpy.props.EnumProperty(name='Inserto', items=INSERTO_ITEMS, default='M3')
    chaflan: bpy.props.FloatProperty(name='Chaflán (mm)', default=0.4, min=0.1, max=1.5, precision=2, step=5)
    cal_nominal: bpy.props.FloatProperty(name='Medida probada', default=8.0, min=1, max=50, precision=1)
    cal_agujero: bpy.props.FloatProperty(name='Agujero medido', default=0.0, min=0, max=60, precision=2)
    cal_eje: bpy.props.FloatProperty(name='Pivote medido', default=0.0, min=0, max=60, precision=2)
    show_cal: bpy.props.BoolProperty(name='Calibrar con la pieza de prueba', default=False)
    done: bpy.props.StringProperty()


def _tabla_actual(p):
    """La tabla con los valores que hay ahora en el panel para el material elegido."""
    tabla = fitting.cargar_materiales()
    m = tabla.setdefault(p.material, dict(fitting.MATERIALES_DEFECTO['PLA']))
    m['agujero'], m['eje'], m['pata'] = round(p.agujero, 3), round(p.eje, 3), round(p.chaflan, 3)
    return tabla


# -------------------------------------------------------------------- operadores de clic
def _marcador(context, model, size_mm):
    mm = common.factor(context.scene, 'AUTO', model)
    bm = common.primitive_bmesh('SPHERE', (size_mm / mm,) * 3)
    col = model.users_collection[0] if model.users_collection else context.scene.collection
    obj = common.new_object(bm, 'Fit_marcador', col)
    bm.free()
    obj[common.ROLE] = common.ROLE_CUTTER
    obj.display_type = 'WIRE'
    obj.show_in_front = True
    return obj


class OBJECT_OT_fit_cylinder(common.ClickPlacer, bpy.types.Operator):
    """Clic en la pared de un agujero o de un eje: lo deja a la medida para tu material"""
    bl_idname = 'object.fit_cylinder'; bl_label = 'Clic en agujero o eje'; bl_options = {'REGISTER', 'UNDO'}
    hint = 'Clic en la PARED del agujero o del eje · Esc: cancelar'

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context)

    def create_helper(self, context, model):
        return _marcador(context, model, 1.5)

    def place(self, context, helper, loc, normal):
        helper.location = loc

    def commit(self, context, model, helper):
        p = context.scene.fit_props
        loc = helper.location.copy()
        helper.hide_set(True)
        fitting.guardar_materiales(_tabla_actual(p))
        obj, msg = fitting.ajustar_cilindro(context, model, loc, p.nominal, p.material, p.ajuste)
        common.remove_objects([helper])        # solo si ha ido bien: si falla lo quita ClickPlacer
        common._select_only(context, obj)
        p.done = msg
        return msg


class OBJECT_OT_fit_insert(common.ClickPlacer, bpy.types.Operator):
    """Clic en una cara: abre el hueco para un inserto de rosca en caliente"""
    bl_idname = 'object.fit_insert'; bl_label = 'Clic en la cara para el inserto'; bl_options = {'REGISTER', 'UNDO'}
    hint = 'Clic en la cara donde va el inserto · Esc: cancelar'

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context)

    def create_helper(self, context, model):
        p = context.scene.fit_props
        d, prof = fitting.INSERTOS[p.metrica]
        mm = common.factor(context.scene, 'AUTO', model)
        col = model.users_collection[0] if model.users_collection else context.scene.collection
        bm = common.cylinder_bmesh((0, 0, -prof / 2 / mm), (0, 0, 1), d / 2 / mm, prof / mm, 32)
        obj = common.new_object(bm, 'Fit_inserto_vista', col)
        bm.free()
        obj[common.ROLE] = common.ROLE_CUTTER
        obj.display_type = 'WIRE'
        obj.show_in_front = True
        return obj

    def place(self, context, helper, loc, normal):
        common.place_cutter(helper, loc, normal)
        self._normal = normal.copy()

    def commit(self, context, model, helper):
        p = context.scene.fit_props
        loc, normal = helper.location.copy(), self._normal
        helper.hide_set(True)
        obj, msg = fitting.insertar_rosca(context, model, loc, normal, p.metrica)
        common.remove_objects([helper])
        common._select_only(context, obj)
        p.done = msg
        return msg


# -------------------------------------------------------------------- operadores directos
class OBJECT_OT_fit_chamfer(bpy.types.Operator):
    """Bisela el borde de la base para que la primera capa no sobresalga (pata de elefante)"""
    bl_idname = 'object.fit_chamfer'; bl_label = 'Chaflán anti pata de elefante'; bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context)

    def execute(self, context):
        p = context.scene.fit_props
        try:
            obj, msg = fitting.chaflan_base(context, common.pick_model(context), p.chaflan)
        except common.AddonError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        common._select_only(context, obj)
        p.done = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class OBJECT_OT_fit_test_piece(bpy.types.Operator):
    """Crea una placa con agujeros y pivotes de 3 a 10 mm para medir tu impresora"""
    bl_idname = 'object.fit_test_piece'; bl_label = 'Crear pieza de prueba'; bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context)

    def execute(self, context):
        try:
            _obj, msg = fitting.pieza_de_prueba(context)
        except common.AddonError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        context.scene.fit_props.done = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class OBJECT_OT_fit_calibrate(bpy.types.Operator):
    """Guarda lo medido con el calibre en la tabla del material"""
    bl_idname = 'object.fit_calibrate'; bl_label = 'Guardar medidas en la tabla'

    def execute(self, context):
        p = context.scene.fit_props
        if not (p.cal_agujero or p.cal_eje):
            self.report({'ERROR'}, 'Escribe al menos una medida del calibre.'); return {'CANCELLED'}
        tabla = fitting.calibrar(_tabla_actual(p), p.material, p.cal_nominal,
                                 p.cal_agujero or None, p.cal_eje or None)
        m = tabla[p.material]
        p['agujero'], p['eje'] = m['agujero'], m['eje']
        p.done = f"{p.material}: agujeros {m['agujero']:+.2f} mm · ejes {m['eje']:+.2f} mm (guardado)"
        self.report({'INFO'}, p.done)
        return {'FINISHED'}


class OBJECT_OT_fit_save(bpy.types.Operator):
    """Guarda los valores del panel como los de este material"""
    bl_idname = 'object.fit_save'; bl_label = 'Guardar'

    def execute(self, context):
        p = context.scene.fit_props
        fitting.guardar_materiales(_tabla_actual(p))
        self.report({'INFO'}, f'{p.material} guardado')
        return {'FINISHED'}


# -------------------------------------------------------------------- panel
class VIEW3D_PT_fit(bpy.types.Panel):
    bl_label = 'Fit Tolerance'; bl_idname = 'VIEW3D_PT_fit'; bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'; bl_category = 'Fit Tolerance'

    def draw(self, context):
        layout = self.layout
        p = context.scene.fit_props
        if common.draw_mode_warning(layout, context):
            return
        try:
            model = common.pick_model(context)
        except common.AddonError:
            model = None

        box = layout.box()
        box.label(text='1 · Material', icon='MATERIAL')
        if model is None:
            r = box.row(); r.alert = True; r.label(text='Selecciona el modelo', icon='ERROR')
        else:
            box.label(text=model.name, icon='OBJECT_DATA')
        box.prop(p, 'material', text='')
        col = box.column(align=True)
        col.prop(p, 'agujero')
        col.prop(p, 'eje')
        col.operator('object.fit_save', icon='FILE_TICK')

        box = layout.box()
        box.label(text='2 · Agujeros y ejes', icon='MESH_CYLINDER')
        box.row().prop(p, 'ajuste', expand=True)
        box.prop(p, 'nominal')
        col = box.column(); col.scale_y = 1.5
        col.operator('object.fit_cylinder', icon='RESTRICT_SELECT_OFF')
        t = box.column(align=True); t.scale_y = .8
        t.label(text='Detecta Ø, lo redondea a la medida nominal')
        t.label(text='y lo compensa para que salga a medida.')

        box = layout.box()
        box.label(text='3 · Insertos de rosca', icon='MOD_SCREW')
        box.row().prop(p, 'metrica', expand=True)
        d, prof = fitting.INSERTOS[p.metrica]
        box.label(text=f'Agujero Ø{d:g} × {prof + 0.5:g} mm + chaflán', icon='INFO')
        col = box.column(); col.scale_y = 1.3
        col.operator('object.fit_insert', icon='RESTRICT_SELECT_OFF')

        box = layout.box()
        box.label(text='4 · Pata de elefante', icon='MOD_BEVEL')
        box.prop(p, 'chaflan')
        col = box.column(); col.scale_y = 1.3
        col.operator('object.fit_chamfer', icon='MOD_BEVEL')

        box = layout.box()
        box.prop(p, 'show_cal', icon='TRIA_DOWN' if p.show_cal else 'TRIA_RIGHT', emboss=False)
        if p.show_cal:
            box.operator('object.fit_test_piece', icon='MESH_GRID')
            t = box.column(align=True); t.scale_y = .8
            t.label(text='Imprímela SIN compensación con este material')
            t.label(text='y mide un agujero y su pivote con el calibre.')
            box.prop(p, 'cal_nominal')
            box.prop(p, 'cal_agujero')
            box.prop(p, 'cal_eje')
            box.operator('object.fit_calibrate', icon='FILE_TICK')

        if p.done:
            layout.label(text=p.done, icon='CHECKMARK')
        layout.label(text='El original queda oculto: se trabaja en una copia.', icon='INFO')


classes = (FitProps, OBJECT_OT_fit_cylinder, OBJECT_OT_fit_insert, OBJECT_OT_fit_chamfer,
           OBJECT_OT_fit_test_piece, OBJECT_OT_fit_calibrate, OBJECT_OT_fit_save, VIEW3D_PT_fit)


def register():
    common.register_classes(classes, 'fit_props', FitProps)


def unregister():
    common.unregister_classes(classes, 'fit_props')
