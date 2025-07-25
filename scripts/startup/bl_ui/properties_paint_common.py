# SPDX-FileCopyrightText: 2012-2023 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
from bpy.types import Menu, Panel
from bpy.app.translations import (
    contexts as i18n_contexts,
    pgettext_iface as iface_,
    pgettext_n as n_,
)


class BrushAssetShelf:
    bl_options = {'DEFAULT_VISIBLE', 'NO_ASSET_DRAG', 'STORE_ENABLED_CATALOGS_IN_PREFERENCES'}
    bl_activate_operator = "BRUSH_OT_asset_activate"
    bl_default_preview_size = 48
    brush_type_prop = None
    mode_prop = None

    @classmethod
    def poll(cls, context):
        return (ob := getattr(context, "object", None)) is not None and ob.mode == cls.mode

    @classmethod
    def has_tool_with_brush_type(cls, context, brush_type):
        from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
        space_type = context.space_data.type

        brush_type_items = bpy.types.Brush.bl_rna.properties[cls.brush_type_prop].enum_items

        tool_helper_cls = ToolSelectPanelHelper._tool_class_from_space_type(space_type)
        for item in ToolSelectPanelHelper._tools_flatten(
                tool_helper_cls.tools_from_context(context, mode=context.mode),
        ):
            if item is None:
                continue
            if item.idname in {
                    "builtin.arc",
                    "builtin.curve",
                    "builtin.line",
                    "builtin.box",
                    "builtin.circle",
                    "builtin.polyline",
            }:
                continue
            if item.options is None or ('USE_BRUSHES' not in item.options):
                continue
            if item.brush_type is not None:
                if brush_type_items[item.brush_type].value == brush_type:
                    return True

        return False

    @classmethod
    def brush_type_poll(cls, context, asset):
        from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
        tool = ToolSelectPanelHelper.tool_active_from_context(context)

        if not tool:
            return True
        if not cls.brush_type_prop:
            return True

        asset_brush_type = asset.metadata.get(cls.brush_type_prop)
        # Asset metadata doesn't store a brush type. Only show it when the tool doesn't require a
        # certain brush type.
        if asset_brush_type is None:
            return False

        # For the general brush that supports any brush type, filter out brushes that show up for
        # other tools already.
        if tool.brush_type == 'ANY':
            return not cls.has_tool_with_brush_type(context, asset_brush_type)

        brush_type_items = bpy.types.Brush.bl_rna.properties[cls.brush_type_prop].enum_items
        return brush_type_items[tool.brush_type].value == asset_brush_type

    @classmethod
    def asset_poll(cls, asset):
        if asset.id_type != 'BRUSH':
            return False
        if cls.mode_prop and not asset.metadata.get(cls.mode_prop, False):
            return False

        context = bpy.context
        prefs = context.preferences

        is_asset_shelf_region = context.region and context.region.type == 'ASSET_SHELF'
        # Show all brushes in the popup asset shelves. Otherwise filter out brushes that
        # are incompatible with the tool.
        if is_asset_shelf_region and prefs.view.use_filter_brushes_by_tool:
            return cls.brush_type_poll(context, asset)

        return True

    @classmethod
    def get_active_asset(cls):
        # Only show active highlight when using the brush tool.
        from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
        tool = ToolSelectPanelHelper.tool_active_from_context(bpy.context)
        if not tool or not tool.use_brushes:
            return None

        paint_settings = UnifiedPaintPanel.paint_settings(bpy.context)
        return paint_settings.brush_asset_reference if paint_settings else None

    @classmethod
    def draw_context_menu(self, context, asset, layout):
        # Currently this menu adds operators that deal with the affected brush and don't take the
        # asset into account. Luckily that is okay for now, since right clicking in the grid view
        # also activates the item.
        layout.menu_contents("VIEW3D_MT_brush_context_menu")

    @staticmethod
    def get_shelf_name_from_context(context):
        mode_map = {
            'SCULPT': "VIEW3D_AST_brush_sculpt",
            'PAINT_VERTEX': "VIEW3D_AST_brush_vertex_paint",
            'PAINT_WEIGHT': "VIEW3D_AST_brush_weight_paint",
            'PAINT_TEXTURE': "VIEW3D_AST_brush_texture_paint",
            'PAINT_2D': "IMAGE_AST_brush_paint",
            'PAINT_GREASE_PENCIL': "VIEW3D_AST_brush_gpencil_paint",
            'SCULPT_GREASE_PENCIL': "VIEW3D_AST_brush_gpencil_sculpt",
            'WEIGHT_GREASE_PENCIL': "VIEW3D_AST_brush_gpencil_weight",
            'VERTEX_GREASE_PENCIL': "VIEW3D_AST_brush_gpencil_vertex",
            'SCULPT_CURVES': "VIEW3D_AST_brush_sculpt_curves",
        }
        mode = UnifiedPaintPanel.get_brush_mode(context)
        if not mode:
            return None

        return mode_map[mode]

    @staticmethod
    def draw_popup_selector(layout, context, brush, show_name=True):
        preview_icon_id = brush.preview.icon_id if brush and brush.preview else 0

        shelf_name = BrushAssetShelf.get_shelf_name_from_context(context)
        if not shelf_name:
            return

        display_name = brush.name if (brush and show_name) else None
        if display_name and brush.has_unsaved_changes:
            display_name = display_name + "*"

        layout.template_asset_shelf_popover(
            shelf_name,
            name=display_name,
            icon='BRUSH_DATA' if not preview_icon_id else 'NONE',
            icon_value=preview_icon_id,
        )


class VIEW3D_PT_brush_asset_shelf_filter(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Filter"
    bl_parent_id = "ASSETSHELF_PT_display"

    @classmethod
    def poll(cls, context):
        if context.asset_shelf is None:
            return False
        return context.asset_shelf.bl_idname == BrushAssetShelf.get_shelf_name_from_context(context)

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences

        layout.prop(prefs.view, "use_filter_brushes_by_tool", text="By Active Tool")


class UnifiedPaintPanel:
    # subclass must set
    # bl_space_type = 'IMAGE_EDITOR'
    # bl_region_type = 'UI'

    @staticmethod
    def get_brush_mode(context):
        """ Get the correct mode for this context. For any context where this returns None,
            no brush options should be displayed."""
        mode = context.mode

        if mode == 'PARTICLE':
            # Particle brush settings currently completely do their own thing.
            return None

        from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
        tool = ToolSelectPanelHelper.tool_active_from_context(context)

        if not tool:
            # If there is no active tool, then there can't be an active brush.
            return None

        if not tool.use_brushes:
            return None

        space_data = context.space_data
        tool_settings = context.tool_settings

        if space_data:
            space_type = space_data.type
            if space_type == 'IMAGE_EDITOR':
                return 'PAINT_2D'
            elif space_type in {'VIEW_3D', 'PROPERTIES'}:
                if mode == 'PAINT_TEXTURE':
                    if tool_settings.image_paint:
                        return mode
                    else:
                        return None
                return mode
        return None

    @staticmethod
    def paint_settings(context):
        tool_settings = context.tool_settings

        mode = UnifiedPaintPanel.get_brush_mode(context)

        # 3D paint settings
        if mode == 'SCULPT':
            return tool_settings.sculpt
        elif mode == 'PAINT_VERTEX':
            return tool_settings.vertex_paint
        elif mode == 'PAINT_WEIGHT':
            return tool_settings.weight_paint
        elif mode == 'PAINT_TEXTURE':
            return tool_settings.image_paint
        elif mode == 'PARTICLE':
            return tool_settings.particle_edit
        # 2D paint settings
        elif mode == 'PAINT_2D':
            return tool_settings.image_paint
        # Grease Pencil settings
        elif mode == 'PAINT_GPENCIL':
            return tool_settings.gpencil_paint
        elif mode == 'SCULPT_GPENCIL':
            return tool_settings.gpencil_sculpt_paint
        elif mode == 'WEIGHT_GPENCIL':
            return tool_settings.gpencil_weight_paint
        elif mode == 'VERTEX_GPENCIL':
            return tool_settings.gpencil_vertex_paint
        elif mode == 'PAINT_GREASE_PENCIL':
            return tool_settings.gpencil_paint
        elif mode == 'SCULPT_CURVES':
            return tool_settings.curves_sculpt
        elif mode == 'PAINT_GREASE_PENCIL':
            return tool_settings.gpencil_paint
        elif mode == 'SCULPT_GREASE_PENCIL':
            return tool_settings.gpencil_sculpt_paint
        elif mode == 'WEIGHT_GREASE_PENCIL':
            return tool_settings.gpencil_weight_paint
        elif mode == 'VERTEX_GREASE_PENCIL':
            return tool_settings.gpencil_vertex_paint
        return None

    @staticmethod
    def prop_unified(
            layout,
            context,
            brush,
            prop_name,
            unified_name=None,
            pressure_name=None,
            icon='NONE',
            text=None,
            slider=False,
            header=False,
    ):
        """ Generalized way of adding brush options to the UI,
            along with their pen pressure setting and global toggle, if they exist. """
        row = layout.row(align=True)
        ups = UnifiedPaintPanel.paint_settings(context).unified_paint_settings
        prop_owner = brush
        if unified_name and getattr(ups, unified_name):
            prop_owner = ups

        row.prop(prop_owner, prop_name, icon=icon, text=text, slider=slider)

        if pressure_name:
            row.prop(brush, pressure_name, text="")

        if unified_name and not header:
            # NOTE: We don't draw UnifiedPaintSettings in the header to reduce clutter. D5928#136281
            row.prop(ups, unified_name, text="", icon='BRUSHES_ALL')

        return row

    @staticmethod
    def prop_unified_color(parent, context, brush, prop_name, *, text=None):
        ups = UnifiedPaintPanel.paint_settings(context).unified_paint_settings
        prop_owner = ups if ups.use_unified_color else brush
        parent.prop(prop_owner, prop_name, text=text)

    @staticmethod
    def prop_unified_color_picker(parent, context, brush, prop_name, value_slider=True):
        ups = UnifiedPaintPanel.paint_settings(context).unified_paint_settings
        prop_owner = ups if ups.use_unified_color else brush
        parent.template_color_picker(prop_owner, prop_name, value_slider=value_slider)


### Classes to let various paint modes' panels share code, by sub-classing these classes. ###
class BrushPanel(UnifiedPaintPanel):
    @classmethod
    def poll(cls, context):
        return cls.get_brush_mode(context) is not None


class BrushSelectPanel(BrushPanel):
    bl_label = "Brush Asset"

    # Use header preset function to set the title.
    def draw_header_preset(self, context):
        # layout = self.layout  # UNUSED.

        settings = self.paint_settings(context)
        if settings is None:
            return

        brush = settings.brush
        if brush is None:
            return

        if brush.has_unsaved_changes:
            self.bl_label = n_("Brush Asset (Unsaved)")
        else:
            self.bl_label = n_("Brush Asset")

    def draw(self, context):
        layout = self.layout
        settings = self.paint_settings(context)
        if settings is None:
            return

        brush = settings.brush

        row = layout.row()

        col = row.column(align=True)
        BrushAssetShelf.draw_popup_selector(col, context, brush, show_name=False)

        ## BFA - Changed layout to expose common operators to top level for consistency - START ##
        col = row.column(align=True)
        col.menu("VIEW3D_MT_brush_context_menu", icon="DOWNARROW_HLT", text="")

        if brush:
            if brush.library and brush.library.is_editable:
                col.separator()
                col.operator(
                    "brush.asset_save", text="", icon="FILE_TICK"
                )  # BFA - exposed to top
                col.operator(
                    "brush.asset_revert", text="", icon="UNDO"
                )  # BFA - exposed to top
            else:
                col.separator()
                col.operator(
                    "brush.asset_revert", text="", icon="UNDO"
                )  # BFA - exposed to top

        col.separator()

        # skip if no active brush
        if not brush:
            layout.label(text="No brush selected", icon="INFO")
            return

        if brush:
            row = layout.row(align=True)
            row.prop(brush, "name", text="")
            if brush.library and brush.library.is_editable:
                row.operator(
                    "brush.asset_save_as", text="", icon="ADD"
                )  # BFA - exposed to top
                row.operator(
                    "brush.asset_delete", text="", icon="X"
                )  # BFA - exposed to top
            else:
                row.operator(
                    "brush.asset_save_as", text="", icon="DUPLICATE"
                )  # BFA - exposed to top

        row = layout.row()

        if brush.has_unsaved_changes and bpy.ops.brush.asset_save.poll():
            row = layout.row()
            row.label(text="Settings Changed!", icon="INFO")  # BFA - made save explicit
            row = layout.row()
            row.label(
                text="Save to keep changes.", icon="BLANK1"
            )  # BFA - made save explicit

        ## BFA - Changed layout to expose common operators to top level for consistency - END ##

        if brush is None:
            return


class ColorPalettePanel(BrushPanel):
    bl_label = "Color Palette"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False

        settings = cls.paint_settings(context)
        if (brush := settings.brush) is None:
            return False

        if context.space_data.type == 'IMAGE_EDITOR' or context.image_paint_object:
            capabilities = brush.image_paint_capabilities
            return capabilities.has_color

        elif context.vertex_paint_object:
            capabilities = brush.vertex_paint_capabilities
            return capabilities.has_color

        elif context.sculpt_object:
            capabilities = brush.sculpt_capabilities
            return capabilities.has_color
        return False

    def draw(self, context):
        layout = self.layout
        settings = self.paint_settings(context)

        layout.template_ID(settings, "palette", new="palette.new")
        if settings.palette:
            layout.template_palette(settings, "palette", color=True)


class ClonePanel(BrushPanel):
    bl_label = "Clone"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False

        settings = cls.paint_settings(context)

        mode = cls.get_brush_mode(context)
        if mode == 'PAINT_TEXTURE':
            brush = settings.brush
            return brush.image_brush_type == 'CLONE'
        return False

    def draw_header(self, context):
        settings = self.paint_settings(context)
        self.layout.prop(settings, "use_clone_layer", text="")

    def draw(self, context):
        layout = self.layout
        settings = self.paint_settings(context)

        layout.active = settings.use_clone_layer

        ob = context.active_object
        col = layout.column()

        if settings.mode == 'MATERIAL':
            if len(ob.material_slots) > 1:
                col.label(text="Materials")
                col.template_list(
                    "MATERIAL_UL_matslots", "",
                    ob, "material_slots",
                    ob, "active_material_index",
                    rows=2,
                )

            mat = ob.active_material
            if mat:
                col.label(text="Source Clone Slot")
                col.template_list(
                    "TEXTURE_UL_texpaintslots", "",
                    mat, "texture_paint_slots",
                    mat, "paint_clone_slot",
                    rows=2,
                )

        elif settings.mode == 'IMAGE':
            mesh = ob.data

            clone_text = mesh.uv_layer_clone.name if mesh.uv_layer_clone else ""
            col.label(text="Source Clone Image")
            col.template_ID(settings, "clone_image")
            col.label(text="Source Clone UV Map")
            col.menu("VIEW3D_MT_tools_projectpaint_clone", text=clone_text, translate=False)


class TextureMaskPanel(BrushPanel):
    bl_label = "Texture Mask"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        brush = context.tool_settings.image_paint.brush
        mask_tex_slot = brush.mask_texture_slot

        col = layout.column()
        col.template_ID_preview(mask_tex_slot, "texture", new="texture.new", rows=3, cols=8)

        # map_mode
        layout.row().prop(mask_tex_slot, "mask_map_mode", text="Mask Mapping")

        if mask_tex_slot.map_mode == 'STENCIL':
            if brush.mask_texture and brush.mask_texture.type == 'IMAGE':
                layout.operator("brush.stencil_fit_image_aspect").mask = True
            layout.operator("brush.stencil_reset_transform").mask = True

        col = layout.column()
        col.prop(brush, "use_pressure_masking", text="Pressure Masking")
        # angle and texture_angle_source
        if mask_tex_slot.has_texture_angle:
            col = layout.column()
            col.prop(mask_tex_slot, "angle", text="Angle")
            if mask_tex_slot.has_texture_angle_source:
                col.prop(mask_tex_slot, "use_rake", text="Rake")

                if brush.brush_capabilities.has_random_texture_angle and mask_tex_slot.has_random_texture_angle:
                    col.prop(mask_tex_slot, "use_random", text="Random")
                    if mask_tex_slot.use_random:
                        col.prop(mask_tex_slot, "random_angle", text="Random Angle")

        # scale and offset
        col.prop(mask_tex_slot, "offset")
        col.prop(mask_tex_slot, "scale")


class StrokePanel(BrushPanel):
    bl_label = "Stroke"
    bl_options = {'DEFAULT_CLOSED'}
    bl_ui_units_x = 13

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        mode = self.get_brush_mode(context)
        settings = self.paint_settings(context)
        brush = settings.brush

        col = layout.column()

        col.prop(brush, "stroke_method")
        col.separator()

        if brush.use_anchor:
            col.use_property_split = False
            col.prop(brush, "use_edge_to_edge", text="Edge to Edge")
            col.use_property_split = True

        if brush.use_airbrush:
            col.prop(brush, "rate", text="Rate", slider=True)

        if brush.use_space:
            row = col.row(align=True)
            row.prop(brush, "spacing", text="Spacing")
            row.prop(brush, "use_pressure_spacing", toggle=True, text="")

        if brush.use_line or brush.use_curve:
            row = col.row(align=True)
            row.prop(brush, "spacing", text="Spacing")

        if mode == 'SCULPT':
            col.row().prop(brush, "use_scene_spacing", text="Spacing Distance", expand=True)

        if mode in {'PAINT_TEXTURE', 'PAINT_2D', 'SCULPT'}:
            if brush.image_paint_capabilities.has_space_attenuation or brush.sculpt_capabilities.has_space_attenuation:
                col.prop(brush, "use_space_attenuation")
                col.use_property_split = True

        if brush.use_curve:
            col.separator()
            col.template_ID(brush, "paint_curve", new="paintcurve.new")
            col.operator("paintcurve.draw")
            col.separator()

        if brush.use_space or brush.use_line or brush.use_curve:
            col.separator()
            row = col.row(align=True)
            col.prop(brush, "dash_ratio", text="Dash Ratio")
            col.prop(brush, "dash_samples", text="Dash Length")

        if (mode == 'SCULPT' and brush.sculpt_capabilities.has_jitter) or mode != 'SCULPT':
            col.separator()
            row = col.row(align=True)
            if brush.jitter_unit == 'BRUSH':
                row.prop(brush, "jitter", slider=True)
            else:
                row.prop(brush, "jitter_absolute")
            row.prop(brush, "use_pressure_jitter", toggle=True, text="")
            col.row().prop(brush, "jitter_unit", expand=True)

        col.separator()
        UnifiedPaintPanel.prop_unified(
            layout,
            context,
            brush,
            "input_samples",
            unified_name="use_unified_input_samples",
            slider=True,
        )


class SmoothStrokePanel(BrushPanel):
    bl_label = "Stabilize Stroke"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        settings = cls.paint_settings(context)
        brush = settings.brush
        if brush.brush_capabilities.has_smooth_stroke:
            return True
        return False

    def draw_header(self, context):
        settings = self.paint_settings(context)
        brush = settings.brush

        self.layout.use_property_split = False
        # self.layout.prop(brush, "use_smooth_stroke",
        #                 text=self.bl_label if self.is_popover else "")

        self.layout.prop(
            brush, "use_smooth_stroke", text="Stabilize Stroke"
        )  # bfa - we need the label

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        settings = self.paint_settings(context)
        brush = settings.brush

        col = layout.column()
        col.active = brush.use_smooth_stroke
        row = col.row()
        if self.is_popover:
            row.separator()
        row.prop(brush, "smooth_stroke_radius", text="Radius", slider=True)
        row = col.row()
        if self.is_popover:
            row.separator()
        row.prop(brush, "smooth_stroke_factor", text="Factor", slider=True)


class FalloffPanel(BrushPanel):
    bl_label = "Falloff"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        settings = cls.paint_settings(context)
        if not (settings and settings.brush and settings.brush.curve):
            return False
        if cls.get_brush_mode(context) == 'SCULPT_CURVES':
            brush = settings.brush
            if brush.curves_sculpt_brush_type in {'ADD', 'DELETE'}:
                return False
        return True

    def draw(self, context):
        layout = self.layout
        settings = self.paint_settings(context)
        mode = self.get_brush_mode(context)
        brush = settings.brush

        if brush is None:
            return

        col = layout.column(align=True)
        if context.region.type == 'TOOL_HEADER':
            col.prop(brush, "curve_preset", expand=True)
        else:
            row = col.row(align=True)
            col.prop(brush, "curve_preset", text="")

        if brush.curve_preset == 'CUSTOM':
            layout.template_curve_mapping(brush, "curve", brush=True)

            col = layout.column(align=True)
            row = col.row(align=True)
            row.operator(
                "brush.curve_preset", icon="SMOOTHCURVE", text=""
            ).shape = "SMOOTH"
            row.operator(
                "brush.curve_preset", icon="SPHERECURVE", text=""
            ).shape = "ROUND"
            row.operator("brush.curve_preset", icon="ROOTCURVE", text="").shape = "ROOT"
            row.operator(
                "brush.curve_preset", icon="SHARPCURVE", text=""
            ).shape = "SHARP"
            row.operator("brush.curve_preset", icon="LINCURVE", text="").shape = "LINE"
            row.operator("brush.curve_preset", icon="NOCURVE", text="").shape = "MAX"

        show_fallof_shape = False
        if mode in {'SCULPT', 'PAINT_VERTEX', 'PAINT_WEIGHT'} and brush.sculpt_brush_type != 'POSE':
            show_fallof_shape = True
        if (
            not show_fallof_shape
            and mode == 'SCULPT_CURVES'
            and context.space_data.type == 'PROPERTIES'
        ):
            show_fallof_shape = True

        if show_fallof_shape:
            col.separator()
            row = col.row(align=True)
            row.use_property_split = True
            row.use_property_decorate = False
            row.prop(brush, "falloff_shape", expand=True)


class DisplayPanel(BrushPanel):
    bl_label = "Brush Cursor"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        settings = self.paint_settings(context)
        if settings and not self.is_popover:
            self.layout.prop(settings, "show_brush", text="")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        mode = self.get_brush_mode(context)
        settings = self.paint_settings(context)
        brush = settings.brush
        tex_slot = brush.texture_slot
        tex_slot_mask = brush.mask_texture_slot

        if self.is_popover:
            row = layout.row(align=True)
            row.use_property_split = False
            row.prop(settings, "show_brush", text="Display Cursor")

        col = layout.column()
        col.active = settings.show_brush

        col.prop(brush, "cursor_color_add", text="Cursor Color")
        if mode == 'SCULPT' and brush.sculpt_capabilities.has_secondary_color:
            col.prop(brush, "cursor_color_subtract", text="Inverse Color")

        col.separator()

        row = col.row(align=True)
        row.active = settings.show_brush
        row.prop(brush, "cursor_overlay_alpha", text="Falloff Opacity")
        row.prop(
            brush,
            "use_cursor_overlay_override",
            toggle=True,
            text="",
            icon="BRUSH_DATA",
        )
        row.prop(
            brush,
            "use_cursor_overlay",
            text="",
            toggle=True,
            icon="HIDE_OFF" if brush.use_cursor_overlay else "HIDE_ON",
        )

        # TODO: These settings are a mess. Both `has_overlay` and the following two blocks should read the
        # appropriate texture depending on the mode, see `BKE_brush_mask_texture_get` vs `BKE_brush_color_texture_get`
        texture_overlay_settings_active = brush.brush_capabilities.has_overlay and settings.show_brush
        if mode in {'PAINT_2D', 'PAINT_TEXTURE', 'PAINT_VERTEX', 'SCULPT'}:
            row = col.row(align=True)
            row.active = texture_overlay_settings_active
            row.prop(brush, "texture_overlay_alpha", text="Texture Opacity")
            row.prop(
                brush,
                "use_primary_overlay_override",
                toggle=True,
                text="",
                icon="BRUSH_DATA",
            )
            if tex_slot.map_mode != 'STENCIL':
                row.prop(
                    brush,
                    "use_primary_overlay",
                    text="",
                    toggle=True,
                    icon="HIDE_OFF" if brush.use_primary_overlay else "HIDE_ON",
                )

        if mode in {'PAINT_TEXTURE', 'PAINT_2D'}:
            row = col.row(align=True)
            row.active = texture_overlay_settings_active
            row.prop(brush, "mask_overlay_alpha", text="Mask Texture Opacity")
            row.prop(
                brush,
                "use_secondary_overlay_override",
                toggle=True,
                text="",
                icon="BRUSH_DATA",
            )
            if tex_slot_mask.map_mode != "STENCIL":
                row.prop(
                    brush,
                    "use_secondary_overlay",
                    text="",
                    toggle=True,
                    icon="HIDE_OFF" if brush.use_secondary_overlay else "HIDE_ON",
                )


class VIEW3D_MT_tools_projectpaint_clone(Menu):
    bl_label = "Clone Layer"

    def draw(self, context):
        layout = self.layout

        for i, uv_layer in enumerate(context.active_object.data.uv_layers):
            props = layout.operator(
                "wm.context_set_int", text=uv_layer.name, translate=False
            )
            props.data_path = "active_object.data.uv_layer_clone_index"
            props.value = i


def brush_settings(layout, context, brush, popover=False):
    """Draw simple brush settings for Sculpt,
    Texture/Vertex/Weight Paint modes, or skip certain settings for the popover"""

    mode = UnifiedPaintPanel.get_brush_mode(context)

    ### Draw simple settings unique to each paint mode. ###
    brush_shared_settings(layout, context, brush, popover)

    # BFA - added from header to brush settings
    if mode == 'SCULPT_GREASE_PENCIL':
        col = layout.column()
        col.use_property_split = False
        col.prop(brush.gpencil_settings, "use_active_layer_only")

    # Sculpt Mode #
    if mode == 'SCULPT':
        capabilities = brush.sculpt_capabilities
        sculpt_brush_type = brush.sculpt_brush_type

        # normal_radius_factor
        layout.prop(brush, "normal_radius_factor", slider=True)

        if capabilities.has_tilt:
            layout.prop(brush, "tilt_strength_factor", slider=True)

        row = layout.row(align=True)
        row.prop(brush, "hardness", slider=True)
        row.prop(brush, "invert_hardness_pressure", text="")
        row.prop(brush, "use_hardness_pressure", text="")

        layout.separator()

        # auto_smooth_factor and use_inverse_smooth_pressure
        if capabilities.has_auto_smooth:
            UnifiedPaintPanel.prop_unified(
                layout,
                context,
                brush,
                "auto_smooth_factor",
                pressure_name="use_inverse_smooth_pressure",
                slider=True,
            )

        # topology_rake_factor
        if (
            capabilities.has_topology_rake
            and context.sculpt_object.use_dynamic_topology_sculpting
        ):
            layout.prop(brush, "topology_rake_factor", slider=True)

        # normal_weight
        if capabilities.has_normal_weight:
            layout.prop(brush, "normal_weight", slider=True)

        # crease_pinch_factor
        if capabilities.has_pinch_factor:
            text = iface_("Pinch")
            if sculpt_brush_type in {'BLOB', 'SNAKE_HOOK'}:
                text = iface_("Magnify")
            layout.prop(brush, "crease_pinch_factor", slider=True, text=text, translate=False)

        # rake_factor
        if capabilities.has_rake_factor:
            layout.prop(brush, "rake_factor", slider=True)

        # plane_offset, use_offset_pressure, use_plane_trim, plane_trim
        if capabilities.has_plane_offset:
            layout.separator()
            UnifiedPaintPanel.prop_unified(
                layout,
                context,
                brush,
                "plane_offset",
                pressure_name="use_offset_pressure",
                slider=True,
            )

            layout.separator()

            if sculpt_brush_type != 'PLANE':
                split = layout.split(factor=0.36)
                col = split.column()
                col.use_property_split = False
                col.prop(brush, "use_plane_trim", text="Plane Trim")
                col = split.column()
                if brush.use_plane_trim:
                    col.prop(brush, "plane_trim", slider=True, text="")
                else:
                    col.label(icon="DISCLOSURE_TRI_RIGHT")
            else:
                layout.label(text="Plane trim option not available with Plane sculpt tool", icon="ERROR")

        # height
        if capabilities.has_height:
            layout.prop(brush, "height", slider=True, text="Height")

        if capabilities.has_plane_height:
            layout.prop(brush, "plane_height", slider=True, text="Height")

        if capabilities.has_plane_depth:
            layout.prop(brush, "plane_depth", slider=True, text="Depth")

        # use_persistent, set_persistent_base
        if capabilities.has_persistence:
            layout.separator()
            layout.use_property_split = False
            layout.prop(brush, "use_persistent")
            layout.operator("sculpt.set_persistent_base")
            layout.separator()

        if capabilities.has_color:
            ups = UnifiedPaintPanel.paint_settings(context).unified_paint_settings
            row = layout.row(align=True)
            UnifiedPaintPanel.prop_unified_color(row, context, brush, "color", text="")
            UnifiedPaintPanel.prop_unified_color(row, context, brush, "secondary_color", text="")
            row.separator()
            row.operator(
                "paint.brush_colors_flip", icon="FILE_REFRESH", text="", emboss=False
            )
            row.prop(ups, "use_unified_color", text="", icon="BRUSHES_ALL")
            layout.prop(brush, "blend", text="Blend Mode")

        # Per sculpt tool options.

        if sculpt_brush_type == 'CLAY_STRIPS':
            row = layout.row()
            row.prop(brush, "tip_roundness")

            row = layout.row()
            row.prop(brush, "tip_scale_x")

        elif sculpt_brush_type == 'ELASTIC_DEFORM':
            layout.separator()
            layout.prop(brush, "elastic_deform_type")
            layout.prop(brush, "elastic_deform_volume_preservation", slider=True)
            layout.separator()

        elif sculpt_brush_type == 'SNAKE_HOOK':
            layout.separator()
            layout.prop(brush, "snake_hook_deform_type")
            layout.separator()

        elif sculpt_brush_type == 'POSE':
            layout.separator()
            layout.prop(brush, "deform_target")
            layout.separator()
            layout.prop(brush, "pose_deform_type")
            layout.prop(brush, "pose_origin_type")
            layout.prop(brush, "pose_offset")
            layout.prop(brush, "pose_smooth_iterations")
            if brush.pose_deform_type == 'ROTATE_TWIST' and brush.pose_origin_type in {'TOPOLOGY', 'FACE_SETS'}:
                layout.prop(brush, "pose_ik_segments")
            if brush.pose_deform_type == 'SCALE_TRANSLATE':
                layout.use_property_split = False
                layout.prop(brush, "use_pose_lock_rotation")

            layout.use_property_split = False
            layout.prop(brush, "use_pose_ik_anchored")
            layout.prop(brush, "use_connected_only")
            layout.use_property_split = True

            layout.prop(brush, "disconnected_distance_max")

            layout.separator()

        elif sculpt_brush_type == 'CLOTH':
            layout.separator()
            layout.use_property_split = True
            layout.prop(brush, "cloth_simulation_area_type")
            if brush.cloth_simulation_area_type != 'GLOBAL':
                layout.prop(brush, "cloth_sim_limit")
                layout.prop(brush, "cloth_sim_falloff")

            if brush.cloth_simulation_area_type == 'LOCAL':
                layout.use_property_split = False
                layout.prop(brush, "use_cloth_pin_simulation_boundary")
                layout.use_property_split = True

            layout.separator()
            layout.prop(brush, "cloth_deform_type")
            layout.prop(brush, "cloth_force_falloff_type")
            layout.separator()
            layout.prop(brush, "cloth_mass")
            layout.prop(brush, "cloth_damping")
            layout.prop(brush, "cloth_constraint_softbody_strength")
            layout.separator()
            layout.use_property_split = False
            layout.prop(brush, "use_cloth_collision")

            layout.separator()

        elif sculpt_brush_type == 'SCRAPE':
            row = layout.row(align=True)
            row.prop(brush, "area_radius_factor")
            row.prop(brush, "use_pressure_area_radius", text="")
            row = layout.row()
            row.use_property_split = False
            row.prop(brush, "invert_to_scrape_fill", text="Invert to Fill")

        elif sculpt_brush_type == 'FILL':
            row = layout.row(align=True)
            row.prop(brush, "area_radius_factor")
            row.prop(brush, "use_pressure_area_radius", text="")
            row = layout.row()
            row.use_property_split = False
            row.prop(brush, "invert_to_scrape_fill", text="Invert to Scrape")

        elif sculpt_brush_type == 'PLANE':
            row = layout.row(align=True)
            row.prop(brush, "area_radius_factor")
            row.prop(brush, "use_pressure_area_radius", text="")
            layout.separator()
            layout.prop(brush, "plane_inversion_mode")
            layout.separator()
            layout.prop(brush, "stabilize_normal")
            layout.prop(brush, "stabilize_plane")

        elif sculpt_brush_type == 'GRAB':
            layout.use_property_split = False
            layout.prop(brush, "use_grab_active_vertex")
            layout.prop(brush, "use_grab_silhouette")

        elif sculpt_brush_type == 'PAINT':
            row = layout.row(align=True)
            row.prop(brush, "flow")
            row.prop(brush, "invert_flow_pressure", text="")
            row.prop(brush, "use_flow_pressure", text="")

            row = layout.row(align=True)
            row.prop(brush, "wet_mix")
            row.prop(brush, "invert_wet_mix_pressure", text="")
            row.prop(brush, "use_wet_mix_pressure", text="")

            row = layout.row(align=True)
            row.prop(brush, "wet_persistence")
            row.prop(brush, "invert_wet_persistence_pressure", text="")
            row.prop(brush, "use_wet_persistence_pressure", text="")

            row = layout.row(align=True)
            row.prop(brush, "wet_paint_radius_factor")

            row = layout.row(align=True)
            row.prop(brush, "density")
            row.prop(brush, "invert_density_pressure", text="")
            row.prop(brush, "use_density_pressure", text="")

            row = layout.row()
            row.prop(brush, "tip_roundness")

            row = layout.row()
            row.prop(brush, "tip_scale_x")

        elif sculpt_brush_type == 'SMEAR':
            col = layout.column()
            col.prop(brush, "smear_deform_type")

        elif sculpt_brush_type == 'BOUNDARY':
            layout.prop(brush, "deform_target")
            layout.separator()
            col = layout.column()
            col.prop(brush, "boundary_deform_type")
            col.prop(brush, "boundary_falloff_type")
            col.prop(brush, "boundary_offset")

        elif sculpt_brush_type == 'TOPOLOGY':
            col = layout.column()
            col.prop(brush, "slide_deform_type")

        elif sculpt_brush_type == 'MULTIPLANE_SCRAPE':
            col = layout.column()
            col.prop(brush, "multiplane_scrape_angle")
            col.use_property_split = False
            col.prop(brush, "use_multiplane_scrape_dynamic")
            col.prop(brush, "show_multiplane_scrape_planes_preview")

        elif sculpt_brush_type == 'SMOOTH':
            col = layout.column()
            col.prop(brush, "smooth_deform_type")
            if brush.smooth_deform_type == 'SURFACE':
                col.prop(brush, "surface_smooth_shape_preservation")
                col.prop(brush, "surface_smooth_current_vertex")
                col.prop(brush, "surface_smooth_iterations")

        elif sculpt_brush_type == 'DISPLACEMENT_SMEAR':
            col = layout.column()
            col.prop(brush, "smear_deform_type")

        elif sculpt_brush_type == 'MASK':
            layout.row().prop(brush, "mask_tool", expand=True)

        # End sculpt_brush_type interface.

    # 3D and 2D Texture Paint Mode.
    elif mode in {"PAINT_TEXTURE", "PAINT_2D"}:
        capabilities = brush.image_paint_capabilities

        if brush.image_brush_type == 'FILL':
            # For some reason fill threshold only appears to be implemented in 2D paint.
            if brush.color_type == 'COLOR':
                if mode == 'PAINT_2D':
                    layout.prop(
                        brush, "fill_threshold", text="Fill Threshold", slider=True
                    )
            elif brush.color_type == 'GRADIENT':
                layout.row().prop(brush, "gradient_fill_mode", expand=True)

    elif mode == 'SCULPT_CURVES':
        if brush.curves_sculpt_brush_type == 'ADD':
            layout.use_property_split = True
            layout.prop(brush.curves_sculpt_settings, "add_amount")

            col = layout.column(align=True)
            col.use_property_split = False
            col.label(text="Interpolate")

            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings, "use_length_interpolate", text="Length"
            )
            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings, "use_radius_interpolate", text="Radius"
            )
            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings, "use_shape_interpolate", text="Shape"
            )
            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings,
                "use_point_count_interpolate",
                text="Point Count",
            )

            col = layout.column()
            col.active = not brush.curves_sculpt_settings.use_length_interpolate
            col.prop(brush.curves_sculpt_settings, "curve_length", text="Length")

            col = layout.column()
            col.active = not brush.curves_sculpt_settings.use_radius_interpolate
            col.prop(brush.curves_sculpt_settings, "curve_radius", text="Radius")

            col = layout.column()
            col.active = not brush.curves_sculpt_settings.use_point_count_interpolate
            col.prop(brush.curves_sculpt_settings, "points_per_curve", text="Points")

        if brush.curves_sculpt_brush_type == 'DENSITY':
            
            
            col = layout.column(align=True)
            col.prop(
                brush.curves_sculpt_settings, "density_add_attempts", text="Count Max"
            )
            col.use_property_split = False
            col.label(text="Interpolate")
            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings, "use_length_interpolate", text="Length"
            )
            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings, "use_radius_interpolate", text="Radius"
            )
            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings, "use_shape_interpolate", text="Shape"
            )
            row = col.row()
            row.separator()
            row.prop(
                brush.curves_sculpt_settings,
                "use_point_count_interpolate",
                text="Point Count",
            )

            col = layout.column()
            col.active = not brush.curves_sculpt_settings.use_length_interpolate
            col.prop(brush.curves_sculpt_settings, "curve_length", text="Length")

            col = layout.column()
            col.active = not brush.curves_sculpt_settings.use_radius_interpolate
            col.prop(brush.curves_sculpt_settings, "curve_radius", text="Radius")

            col = layout.column()
            col.active = not brush.curves_sculpt_settings.use_point_count_interpolate
            col.prop(brush.curves_sculpt_settings, "points_per_curve", text="Points")

        elif brush.curves_sculpt_brush_type == "GROW_SHRINK":
            layout.use_property_split = False
            layout.prop(brush.curves_sculpt_settings, "use_uniform_scale")
            layout.use_property_split = True
            layout.prop(brush.curves_sculpt_settings, "minimum_length")


def brush_shared_settings(layout, context, brush, popover=False):
    """Draw simple brush settings that are shared between different paint modes."""

    mode = UnifiedPaintPanel.get_brush_mode(context)
    mode_string = context.mode

    ### Determine which settings to draw. ###
    blend_mode = False
    size = False
    size_mode = False
    strength = False
    strength_pressure = False
    weight = False
    direction = False

    # 3D and 2D Texture Paint #
    if mode in {'PAINT_TEXTURE', 'PAINT_2D'}:
        if not popover:
            blend_mode = brush.image_paint_capabilities.has_color
            size = brush.image_paint_capabilities.has_radius
            strength = strength_pressure = True

    # Sculpt #
    if mode == 'SCULPT':
        size_mode = True
        if not popover:
            size = True
            strength = True
            strength_pressure = brush.sculpt_capabilities.has_strength_pressure
            direction = brush.sculpt_capabilities.has_direction

    # Vertex Paint #
    if mode == 'PAINT_VERTEX':
        if not popover:
            blend_mode = True
            size = True
            strength = True
            strength_pressure = True

    # Weight Paint #
    if mode == 'PAINT_WEIGHT':
        if not popover:
            size = True
            weight = brush.weight_paint_capabilities.has_weight
            strength = strength_pressure = True
        # Only draw blend mode for the Draw tool, because for other tools it is pointless. D5928#137944
        if brush.weight_brush_type == 'DRAW':
            blend_mode = True

    # Sculpt Curves #
    if mode == 'SCULPT_CURVES':
        tool = brush.curves_sculpt_brush_type
        size = True
        strength = tool not in {'ADD', 'DELETE'}
        direction = tool in {'GROW_SHRINK', 'SELECTION_PAINT'}
        strength_pressure = tool not in {'SLIDE', 'ADD', 'DELETE'}

    # Grease Pencil #
    if mode == 'PAINT_GREASE_PENCIL':
        size_mode = True
        size = True
        strength = True

    # Grease Pencil #
    if mode == 'SCULPT_GREASE_PENCIL':
        size = True
        strength = True

    ### Draw settings. ###
    ups = UnifiedPaintPanel.paint_settings(context).unified_paint_settings

    if blend_mode:
        layout.prop(brush, "blend", text="Blend")
        layout.separator()

    if weight:
        UnifiedPaintPanel.prop_unified(
            layout,
            context,
            brush,
            "weight",
            unified_name="use_unified_weight",
            slider=True,
        )

    size_owner = ups if ups.use_unified_size else brush
    size_prop = "size"
    if size_mode and (size_owner.use_locked_size == 'SCENE'):
        size_prop = "unprojected_radius"
    if size or size_mode:
        if size:
            UnifiedPaintPanel.prop_unified(
                layout,
                context,
                brush,
                size_prop,
                unified_name="use_unified_size",
                pressure_name="use_pressure_size",
                text="Radius",
                slider=True,
            )
        if size_mode:
            layout.row().prop(size_owner, "use_locked_size", expand=False) # BFA
            layout.separator()

    if strength:
        pressure_name = "use_pressure_strength" if strength_pressure else None
        UnifiedPaintPanel.prop_unified(
            layout,
            context,
            brush,
            "strength",
            unified_name="use_unified_strength",
            pressure_name=pressure_name,
            slider=True,
        )
        layout.separator()

    if direction:
        layout.row().prop(brush, "direction", expand=True)


def color_jitter_panel(layout, context, brush):
    mode = UnifiedPaintPanel.get_brush_mode(context)
    ups = UnifiedPaintPanel.paint_settings(context).unified_paint_settings

    is_sculpt_paint_mode = mode == 'SCULPT' and brush.sculpt_capabilities.has_color
    if mode in {'PAINT_TEXTURE', 'PAINT_2D', 'PAINT_VERTEX'} or is_sculpt_paint_mode:
        prop_owner = ups if ups.use_unified_color else brush
        layout.use_property_split = False

        header, panel = layout.panel("color_jitter_panel", default_closed=True)
        header.prop(prop_owner, "use_color_jitter", text="Randomize Color")
        if panel:
            panel.use_property_split = True
            panel.use_property_decorate = False

            col = panel.column(align=True)
            col.use_property_split = True

            row = col.row(align=True)
            row.enabled = prop_owner.use_color_jitter
            row.prop(prop_owner, "hue_jitter", slider=True, text="Hue")
            row.prop(prop_owner, "use_stroke_random_hue", text="", icon='GP_SELECT_STROKES')
            row.prop(prop_owner, "use_random_press_hue", text="", icon='STYLUS_PRESSURE')

            row = col.row(align=True)
            row.enabled = prop_owner.use_color_jitter
            row.prop(prop_owner, "saturation_jitter", slider=True, text="Saturation")
            row.prop(prop_owner, "use_stroke_random_sat", text="", icon='GP_SELECT_STROKES')
            row.prop(prop_owner, "use_random_press_sat", text="", icon='STYLUS_PRESSURE')

            row = col.row(align=True)
            row.enabled = prop_owner.use_color_jitter
            row.prop(prop_owner, "value_jitter", slider=True, text="Value")
            row.prop(prop_owner, "use_stroke_random_val", text="", icon='GP_SELECT_STROKES')
            row.prop(prop_owner, "use_random_press_val", text="", icon='STYLUS_PRESSURE')


def brush_settings_advanced(layout, context, settings, brush, popover=False):
    """Draw advanced brush settings for Sculpt, Texture/Vertex/Weight Paint modes."""

    mode = UnifiedPaintPanel.get_brush_mode(context)

    # In the popover we want to combine advanced brush settings with non-advanced brush settings.
    if popover:
        brush_settings(layout, context, brush, popover=True)
        layout.separator()
        layout.label(text="Advanced")

    # These options are shared across many modes.
    use_accumulate = False
    use_frontface = False

    if mode == 'SCULPT':
        layout.prop(brush, "sculpt_brush_type")
        layout.separator()

        capabilities = brush.sculpt_capabilities
        use_accumulate = capabilities.has_accumulate
        use_frontface = True

        col = layout.column(align=True)
        col.label(text="Auto Masking")

        # topology automasking
        col.use_property_split = False
        row = col.row()
        row.separator()
        row.prop(brush, "use_automasking_topology")

        # face masks automasking
        row = col.row()
        row.separator()
        row.prop(brush, "use_automasking_face_sets")

        col = layout.column(align=True)
        col.use_property_split = False

        col = layout.column()
        split = col.split(factor=0.9)
        split.use_property_split = False
        row = split.row()
        row.separator()
        row.prop(brush, "use_automasking_boundary_edges", text="Mesh Boundary")

        if (
            brush.use_automasking_boundary_edges
            or brush.use_automasking_boundary_face_sets
        ):
            split.label(icon="DISCLOSURE_TRI_DOWN")
        else:
            split.label(icon="DISCLOSURE_TRI_RIGHT")

        # col = layout.column()
        split = col.split(factor=0.9)
        split.use_property_split = False
        row = split.row()
        row.separator()
        row.prop(brush, "use_automasking_boundary_face_sets", text="Face Sets Boundary")

        if (
            brush.use_automasking_boundary_edges
            or brush.use_automasking_boundary_face_sets
        ):
            split.label(icon="DISCLOSURE_TRI_DOWN")
        else:
            split.label(icon="DISCLOSURE_TRI_RIGHT")

        if (
            brush.use_automasking_boundary_edges
            or brush.use_automasking_boundary_face_sets
        ):
            col = layout.column()
            col.use_property_split = True
            row = col.row()
            row.separator(factor=3.5)
            row.prop(
                brush, "automasking_boundary_edges_propagation_steps", text="Steps"
            )

        col = layout.column()
        split = col.split(factor=0.9)
        split.use_property_split = False
        row = split.row()
        row.separator()
        row.prop(brush, "use_automasking_cavity", text="Cavity")

        is_cavity_active = (
            brush.use_automasking_cavity or brush.use_automasking_cavity_inverted
        )

        if is_cavity_active:
            props = row.operator("sculpt.mask_from_cavity", text="Create Mask")
            props.settings_source = "BRUSH"
            split.label(icon="DISCLOSURE_TRI_DOWN")
        else:
            split.label(icon="DISCLOSURE_TRI_RIGHT")

        # col = layout.column()
        split = col.split(factor=0.9)
        split.use_property_split = False
        row = split.row()
        row.separator()
        row.prop(brush, "use_automasking_cavity_inverted", text="Cavity (inverted)")

        is_cavity_active = (
            brush.use_automasking_cavity or brush.use_automasking_cavity_inverted
        )

        if is_cavity_active:
            split.label(icon="DISCLOSURE_TRI_DOWN")
        else:
            split.label(icon="DISCLOSURE_TRI_RIGHT")

        if is_cavity_active:
            col = layout.column(align=True)
            row = col.row()
            row.separator(factor=3.5)
            props = row.operator("sculpt.mask_from_cavity", text="Create Mask")
            props.settings_source = "BRUSH"
            row = col.row()
            row.separator(factor=3.5)
            row.prop(brush, "automasking_cavity_factor", text="Factor")
            row = col.row()
            row.separator(factor=3.5)
            row.prop(brush, "automasking_cavity_blur_steps", text="Blur")

            col = layout.column()
            col.use_property_split = False
            row = col.row()
            row.separator(factor=3.5)
            row.prop(brush, "use_automasking_custom_cavity_curve", text="Custom Curve")

            if brush.use_automasking_custom_cavity_curve:
                col.template_curve_mapping(brush, "automasking_cavity_curve")

        col = layout.column()
        split = col.split(factor=0.9)
        split.use_property_split = False
        row = split.row()
        row.separator()
        row.prop(brush, "use_automasking_view_normal", text="View Normal")

        if brush.use_automasking_view_normal:
            split.label(icon="DISCLOSURE_TRI_DOWN")
        else:
            split.label(icon="DISCLOSURE_TRI_RIGHT")

        if brush.use_automasking_view_normal:
            row = col.row()
            row.use_property_split = False
            row.separator(factor=3.5)
            row.prop(brush, "use_automasking_view_occlusion", text="Occlusion")
            subcol = col.column(align=True)
            if not brush.use_automasking_view_occlusion:
                subcol.use_property_split = True
                row = subcol.row()
                row.separator(factor=3.5)
                row.prop(brush, "automasking_view_normal_limit", text="Limit")
                row = subcol.row()
                row.separator(factor=3.5)
                row.prop(brush, "automasking_view_normal_falloff", text="Falloff")

        # col = layout.column()
        split = col.split(factor=0.9)
        split.use_property_split = False
        row = split.row()
        row.separator()
        row.prop(brush, "use_automasking_start_normal", text="Area Normal")

        if brush.use_automasking_start_normal:
            split.label(icon="DISCLOSURE_TRI_DOWN")
        else:
            split.label(icon="DISCLOSURE_TRI_RIGHT")

        if brush.use_automasking_start_normal:
            col = layout.column(align=True)
            row = col.row()
            row.separator(factor=3.5)
            row.prop(brush, "automasking_start_normal_limit", text="Limit")
            row = col.row()
            row.separator(factor=3.5)
            row.prop(brush, "automasking_start_normal_falloff", text="Falloff")
            col.separator()

        layout.separator()

        # sculpt plane settings
        if capabilities.has_sculpt_plane:
            col.use_property_split = True
            col.prop(brush, "sculpt_plane")
            col.use_property_split = False

            if brush.sculpt_brush_type != 'PLANE':
                col = layout.column()
                col.label(text="Use Original")
                col.use_property_split = False
                row = col.row()
                row.separator()
                row.prop(brush, "use_original_normal", text="Normal")
                row = col.row()
                row.separator()
                row.prop(brush, "use_original_plane", text="Plane")
            else:
                layout.label(text="Using original plane and normals is not available with the plane sculpt tool", icon="ERROR")

            layout.separator()

    elif mode == 'SCULPT_GREASE_PENCIL':
        gp_settings = brush.gpencil_settings

        col = layout.column()  # BFA - float column left, update label
        col.label(text="Affect")
        col.use_property_split = False

        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(gp_settings, "use_edit_position", text="Position")
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(
            gp_settings,
            "use_edit_strength",
            text="Strength",
            text_ctxt=i18n_contexts.id_gpencil,
        )
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(gp_settings, "use_edit_thickness", text="Thickness")
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(gp_settings, "use_edit_uv", text="UV")

    # 3D and 2D Texture Paint.
    elif mode in {"PAINT_TEXTURE", "PAINT_2D"}:
        layout.prop(brush, "image_brush_type")
        layout.separator()

        capabilities = brush.image_paint_capabilities
        use_accumulate = capabilities.has_accumulate

        layout.use_property_split = False  # BFA

        if mode == 'PAINT_2D':
            layout.prop(brush, "use_paint_antialiasing")
        else:
            layout.prop(brush, "use_alpha")

        # Tool specific settings
        if brush.image_brush_type == 'SOFTEN':
            layout.separator()
            layout.row().prop(brush, "direction", expand=True)
            layout.prop(brush, "sharp_threshold")
            if mode == 'PAINT_2D':
                layout.prop(brush, "blur_kernel_radius")
            layout.prop(brush, "blur_mode")

        elif brush.image_brush_type == 'MASK':
            layout.prop(brush, "weight", text="Mask Value", slider=True)

        elif brush.image_brush_type == 'CLONE':
            if mode == 'PAINT_2D':
                layout.prop(settings, "clone_image", text="Image")
                layout.prop(settings, "clone_alpha", text="Alpha")

    # Vertex Paint #
    elif mode == 'PAINT_VERTEX':
        layout.use_property_split = False  # BFA
        layout.prop(brush, "vertex_brush_type")
        layout.separator()

        layout.use_property_split = False  # BFA

        layout.prop(brush, "use_alpha")
        if brush.vertex_brush_type != 'SMEAR':
            use_accumulate = True
        use_frontface = True

    # Weight Paint
    elif mode == 'PAINT_WEIGHT':
        layout.use_property_split = False  # BFA
        layout.prop(brush, "weight_brush_type")
        layout.separator()

        layout.use_property_split = False  # BFA

        if brush.weight_brush_type != 'SMEAR':
            use_accumulate = True
        use_frontface = True

    # Sculpt Curves
    elif mode == 'SCULPT_CURVES':
        layout.use_property_split = False  # BFA

        layout.prop(brush, "curves_sculpt_brush_type")

    # Draw shared settings.
    if use_accumulate:
        layout.use_property_split = False  # BFA
        layout.prop(brush, "use_accumulate")

    if use_frontface:
        layout.use_property_split = False  # BFA
        layout.prop(brush, "use_frontface", text="Front Faces Only")

    # BFA - exposed in all areas
    color_jitter_panel(layout, context, brush)

    # Brush modes
    header, panel = layout.panel("modes", default_closed=True)
    header.label(text="Modes")
    if panel:
        panel.use_property_split = False  # BFA - float column left in panel
        panel.use_property_decorate = False

        col = panel.column()  # BFA - float column left
        col.use_property_split = False

        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(brush, "use_paint_sculpt", text="Sculpt")
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(brush, "use_paint_uv_sculpt", text="UV Sculpt")
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(brush, "use_paint_vertex", text="Vertex Paint")
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(brush, "use_paint_weight", text="Weight Paint")
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(brush, "use_paint_image", text="Texture Paint")
        row = col.row()  # BFA - make prop a new row
        row.separator()
        row.prop(brush, "use_paint_sculpt_curves", text="Sculpt Curves")


def draw_color_settings(context, layout, brush, color_type=False):
    """Draw color wheel and gradient settings."""
    ups = UnifiedPaintPanel.paint_settings(context).unified_paint_settings

    if color_type:
        row = layout.row()
        row.use_property_split = False
        row.prop(brush, "color_type", expand=True)

    # Color wheel
    if brush.color_type == 'COLOR':
        UnifiedPaintPanel.prop_unified_color_picker(
            layout, context, brush, "color", value_slider=True
        )

        row = layout.row(align=True)
        UnifiedPaintPanel.prop_unified_color(row, context, brush, "color", text="")
        UnifiedPaintPanel.prop_unified_color(
            row, context, brush, "secondary_color", text=""
        )
        row.separator()
        row.operator(
            "paint.brush_colors_flip", icon="FILE_REFRESH", text="", emboss=False
        )
        row.prop(ups, "use_unified_color", text="", icon="BRUSHES_ALL")
    # Gradient
    elif brush.color_type == 'GRADIENT':
        layout.template_color_ramp(brush, "gradient", expand=True)

        layout.use_property_split = True

        col = layout.column()

        if brush.image_brush_type == 'DRAW':
            UnifiedPaintPanel.prop_unified(
                col,
                context,
                brush,
                "secondary_color",
                unified_name="use_unified_color",
                text="Background Color",
                header=True,
            )

            col.prop(brush, "gradient_stroke_mode", text="Gradient Mapping")
            if brush.gradient_stroke_mode in {'SPACING_REPEAT', 'SPACING_CLAMP'}:
                col.prop(brush, "grad_spacing")


# Used in both the View3D toolbar and texture properties
def brush_texture_settings(layout, brush, sculpt):
    tex_slot = brush.texture_slot

    layout.use_property_split = True
    layout.use_property_decorate = False

    # map_mode
    layout.prop(tex_slot, "map_mode", text="Mapping")

    layout.separator()

    if tex_slot.map_mode == 'STENCIL':
        if brush.texture and brush.texture.type == 'IMAGE':
            layout.operator("brush.stencil_fit_image_aspect").mask = False
        layout.operator("brush.stencil_reset_transform").mask = False

    # angle and texture_angle_source
    if tex_slot.has_texture_angle:
        col = layout.column()
        col.prop(tex_slot, "angle", text="Angle")
        if tex_slot.has_texture_angle_source:
            col.use_property_split = False  # BFA
            col.prop(tex_slot, "use_rake", text="Rake")

            if brush.brush_capabilities.has_random_texture_angle and tex_slot.has_random_texture_angle:
                if sculpt:
                    if brush.sculpt_capabilities.has_random_texture_angle:
                        col.use_property_split = False  # BFA
                        col.prop(tex_slot, "use_random", text="Random")
                        if tex_slot.use_random:
                            col.use_property_split = True  # BFA
                            col.prop(tex_slot, "random_angle", text="Random Angle")
                else:
                    col.prop(tex_slot, "use_random", text="Random")
                    if tex_slot.use_random:
                        col.prop(tex_slot, "random_angle", text="Random Angle")

    # scale and offset
    layout.prop(tex_slot, "offset")
    layout.prop(tex_slot, "scale")

    if sculpt:
        # texture_sample_bias
        layout.prop(brush, "texture_sample_bias", slider=True, text="Sample Bias")

        if brush.sculpt_brush_type == 'DRAW':
            col = layout.column()
            if tex_slot.map_mode == "AREA_PLANE":
                col.use_property_split = False
                col.prop(brush, "use_color_as_displacement", text="Vector Displacement")


def brush_mask_texture_settings(layout, brush):
    mask_tex_slot = brush.mask_texture_slot

    layout.use_property_split = True
    layout.use_property_decorate = False

    # map_mode
    layout.row().prop(mask_tex_slot, "mask_map_mode", text="Mask Mapping")

    if mask_tex_slot.map_mode == 'STENCIL':
        if brush.mask_texture and brush.mask_texture.type == 'IMAGE':
            layout.operator("brush.stencil_fit_image_aspect").mask = True
        layout.operator("brush.stencil_reset_transform").mask = True

    col = layout.column()
    col.prop(brush, "use_pressure_masking", text="Pressure Masking")
    # angle and texture_angle_source
    if mask_tex_slot.has_texture_angle:
        col = layout.column()
        col.prop(mask_tex_slot, "angle", text="Angle")
        if mask_tex_slot.has_texture_angle_source:
            col.use_property_split = False  # BFA
            col.prop(mask_tex_slot, "use_rake", text="Rake")

            if brush.brush_capabilities.has_random_texture_angle and mask_tex_slot.has_random_texture_angle:
                col.prop(mask_tex_slot, "use_random", text="Random")
                if mask_tex_slot.use_random:
                    col.prop(mask_tex_slot, "random_angle", text="Random Angle")

    # scale and offset
    col.use_property_split = True  # BFA
    col.prop(mask_tex_slot, "offset")
    col.prop(mask_tex_slot, "scale")


def brush_basic_texpaint_settings(layout, context, brush, *, compact=False):
    """Draw Tool Settings header for Vertex Paint and 2D and 3D Texture Paint modes."""
    capabilities = brush.image_paint_capabilities

    if capabilities.has_color:
        row = layout.row(align=True)
        row.ui_units_x = 4
        UnifiedPaintPanel.prop_unified_color(row, context, brush, "color", text="")
        UnifiedPaintPanel.prop_unified_color(
            row, context, brush, "secondary_color", text=""
        )

        row.separator()

        row.operator(
            "paint.brush_colors_flip", icon="FILE_REFRESH", text="", emboss=False
        )  # BFA
        layout.prop(
            brush, "blend", text="" if compact else iface_("Blend"), translate=False
        )

    UnifiedPaintPanel.prop_unified(
        layout,
        context,
        brush,
        "size",
        pressure_name="use_pressure_size",
        unified_name="use_unified_size",
        slider=True,
        text="Radius",
        header=True,
    )
    UnifiedPaintPanel.prop_unified(
        layout,
        context,
        brush,
        "strength",
        pressure_name="use_pressure_strength",
        unified_name="use_unified_strength",
        header=True,
    )


def brush_basic__draw_color_selector(context, layout, brush, gp_settings):
    tool_settings = context.scene.tool_settings
    settings = tool_settings.gpencil_paint
    ma = gp_settings.material

    row = layout.row(align=True)
    if not gp_settings.use_material_pin:
        ma = context.object.active_material
    icon_id = 0
    txt_ma = ""
    if ma:
        ma.id_data.preview_ensure()
        if ma.id_data.preview:
            icon_id = ma.id_data.preview.icon_id
            txt_ma = ma.name
            maxw = 25
            if len(txt_ma) > maxw:
                txt_ma = txt_ma[:maxw - 5] + '..' + txt_ma[-3:]

    sub = row.row(align=True)
    sub.enabled = not gp_settings.use_material_pin
    sub.ui_units_x = 8
    sub.popover(
        panel="TOPBAR_PT_grease_pencil_materials",
        text=txt_ma,
        translate=False,
        icon_value=icon_id,
    )

    row.prop(gp_settings, "use_material_pin", text="")

    if brush.gpencil_brush_type in {'DRAW', 'FILL'}:
        row.separator(factor=1.0)
        sub_row = row.row(align=True)
        pin_draw_mode = gp_settings.pin_draw_mode
        sub_row.enabled = not pin_draw_mode
        if pin_draw_mode:
            sub_row.prop_enum(
                gp_settings, "brush_draw_mode", "MATERIAL", text="", icon="MATERIAL"
            )
            sub_row.prop_enum(
                gp_settings,
                "brush_draw_mode",
                "VERTEXCOLOR",
                text="",
                icon="VPAINT_HLT",
            )
        else:
            sub_row.prop_enum(
                settings, "color_mode", "MATERIAL", text="", icon="MATERIAL"
            )
            sub_row.prop_enum(
                settings, "color_mode", "VERTEXCOLOR", text="", icon="VPAINT_HLT"
            )

        show_vertex_color = (
            (not pin_draw_mode) and settings.color_mode == "VERTEXCOLOR"
        ) or (pin_draw_mode and gp_settings.brush_draw_mode == "VERTEXCOLOR")

        if show_vertex_color:
            row = row.row(align=True)
            row.scale_x = 0.33
            row.prop_with_popover(
                brush, "color", text="", panel="TOPBAR_PT_grease_pencil_vertex_color"
            )
            row.prop(brush, "secondary_color", text="")
            # bfa - move brush_colors_flip and pin_draw_mode to their own row has they get squashed.
            row = row.row(align=True)
            row.scale_x = 1.75
            row.operator("paint.brush_colors_flip", icon="FILE_REFRESH", text="")  # BFA
            row.prop(gp_settings, "pin_draw_mode", text="")


def brush_basic_gpencil_paint_settings(layout, context, brush, *, compact=False):
    tool_settings = context.tool_settings
    settings = tool_settings.gpencil_paint
    gp_settings = brush.gpencil_settings
    ups = tool_settings.unified_paint_settings
    brush_prop_owner = ups if ups.use_unified_size else brush
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    if gp_settings is None:
        return

    # Brush details
    if brush.gpencil_brush_type == 'ERASE':
        row = layout.row(align=True)
        row.prop(brush, "size", text="Radius")
        row.prop(brush, "use_pressure_size", text="", icon='STYLUS_PRESSURE')
        row.prop(gp_settings, "use_occlude_eraser", text="", icon='XRAY')

        row = layout.row(align=True)
        row.prop(gp_settings, "eraser_mode", expand=True)
        if gp_settings.eraser_mode == 'SOFT':
            row = layout.row(align=True)
            row.prop(brush_prop_owner, "strength", slider=True)
            row.prop(brush, "use_pressure_strength", text="", icon='STYLUS_PRESSURE')
            row.prop(ups, "use_unified_strength", text="", icon='BRUSHES_ALL')
            row = layout.row(align=True)
            row.prop(gp_settings, "eraser_strength_factor")
            row = layout.row(align=True)
            row.prop(gp_settings, "eraser_thickness_factor")

    # FIXME: tools must use their own UI drawing!
    elif brush.gpencil_brush_type == 'FILL':
        use_property_split_prev = layout.use_property_split
        if compact:
            row = layout.row(align=True)
            row.prop(gp_settings, "fill_direction", text="", expand=True)
        else:
            layout.use_property_split = False
            row = layout.row(align=True)
            row.prop(gp_settings, "fill_direction", expand=True)

        row = layout.row(align=True)
        row.prop(gp_settings, "fill_factor")
        row = layout.row(align=True)
        row.prop(gp_settings, "dilate")
        row = layout.row(align=True)
        row.prop(brush, "size", text="Thickness")
        layout.use_property_split = use_property_split_prev

    else:  # brush.gpencil_brush_type == 'DRAW/TINT':
        row = layout.row(align=True)
        row.prop(brush, "size", text="Radius")
        row.prop(gp_settings, "use_pressure", text="", icon='STYLUS_PRESSURE')

        if gp_settings.use_pressure and not compact:
            row = layout.row()
            row.separator()
            row.popover(
                panel="VIEW3D_PT_gpencil_brush_settings_radius",
                text="Radius Pressure Curve",
            )

        row = layout.row(align=True)
        row.prop(brush_prop_owner, "strength", slider=True)
        row.prop(brush, "use_pressure_strength", text="", icon='STYLUS_PRESSURE')
        row.prop(ups, "use_unified_strength", text="", icon='BRUSHES_ALL')

        if gp_settings.use_strength_pressure and not compact:
            row = layout.row()
            row.separator()
            row.popover(
                panel="VIEW3D_PT_gpencil_brush_settings_strength",
                text="Strength Pressure Curve",
            )

        if brush.gpencil_brush_type == 'TINT':
            row = layout.row(align=True)
            row.prop(gp_settings, "vertex_mode", text="Mode")
        else:
            row = layout.row(align=True)
            if context.region.type == 'TOOL_HEADER':
                row.prop(gp_settings, "caps_type", text="", expand=True)
            else:
                row.prop(gp_settings, "caps_type", text="Caps Type")

    # FIXME: tools must use their own UI drawing!
    if tool.idname in {
        "builtin.arc",
        "builtin.curve",
        "builtin.line",
        "builtin.box",
        "builtin.circle",
        "builtin.polyline",
    }:
        settings = context.tool_settings.gpencil_sculpt
        if compact:
            row = layout.row(align=True)
            row.prop(settings, "use_thickness_curve", text="", icon='SPHERECURVE')
            sub = row.row(align=True)
            sub.active = settings.use_thickness_curve
            sub.popover(
                panel="TOPBAR_PT_gpencil_primitive",
                text="Thickness Profile",
            )
        else:
            row = layout.row(align=True)
            row.prop(settings, "use_thickness_curve", text="Use Thickness Profile")
            sub = row.row(align=True)
            if settings.use_thickness_curve:
                # Curve
                layout.template_curve_mapping(settings, "thickness_primitive_curve", brush=True)


def brush_basic_grease_pencil_paint_settings(layout, context, brush, props, *, compact=False):
    gp_settings = brush.gpencil_settings
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    if gp_settings is None:
        return

    grease_pencil_brush_type = brush.gpencil_brush_type

    if grease_pencil_brush_type in {'DRAW', 'ERASE', 'TINT'} or tool.idname in {
            "builtin.arc",
            "builtin.curve",
            "builtin.line",
            "builtin.box",
            "builtin.circle",
            "builtin.polyline",
    }:
        size = "size"
        if brush.use_locked_size == 'SCENE' and (grease_pencil_brush_type == 'DRAW' or tool.idname in {
            "builtin.arc",
            "builtin.curve",
            "builtin.line",
            "builtin.box",
            "builtin.circle",
            "builtin.polyline",
        }):
            size = "unprojected_radius"
        row = layout.row(align=True)
        row.prop(brush, size, slider=True, text="Radius")
        row.prop(brush, "use_pressure_size", text="")

        if brush.use_pressure_size and not compact:
            row = layout.row()
            row.separator()
            row.popover(
                panel="VIEW3D_PT_gpencil_brush_settings_radius",
                text="Radius Pressure Curve",
            )  # BFA - collapsed

        row = layout.row(align=True)
        row.prop(brush, "strength", slider=True, text="Strength")
        row.prop(brush, "use_pressure_strength", text="")

        if brush.use_pressure_strength and not compact:
            # col = layout.column()
            # col.template_curve_mapping(gp_settings, "curve_strength", brush=True, use_negative_slope=True)
            row = layout.row()
            row.separator()
            row.popover(
                panel="VIEW3D_PT_gpencil_brush_settings_strength",
                text="Strength Pressure Curve",
            )

    if props:
        layout.prop(props, "subdivision")

    # Brush details
    if tool.idname in {
        "builtin.arc",
        "builtin.curve",
        "builtin.line",
        "builtin.box",
        "builtin.circle",
        "builtin.polyline",
    }:
        row = layout.row(align=True)
        if context.region.type == 'TOOL_HEADER':
            row.prop(gp_settings, "caps_type", text="", expand=True)
        else:
            row.prop(gp_settings, "caps_type", text="Caps Type")

        settings = context.tool_settings.gpencil_sculpt
        if compact:
            row = layout.row(align=True)
            row.prop(settings, "use_thickness_curve", text="", icon="SPHERECURVE")
            sub = row.row(align=True)
            sub.active = settings.use_thickness_curve
            sub.popover(
                panel="TOPBAR_PT_gpencil_primitive",
                text="Thickness Profile",
            )
        else:
            row = layout.row(align=True)
            row.use_property_split = False
            row.prop(settings, "use_thickness_curve", text="Use Thickness Profile")
            sub = row.row(align=True)
            if settings.use_thickness_curve:
                # Pressure curve.
                layout.template_curve_mapping(
                    settings, "thickness_primitive_curve", brush=True
                )
    elif grease_pencil_brush_type == 'DRAW':
        row = layout.row(align=True)
        if compact:
            row.prop(gp_settings, "caps_type", text="", expand=True)
        else:
            row.prop(gp_settings, "caps_type", text="Caps Type")
    elif brush.gpencil_brush_type == 'FILL':
        use_property_split_prev = layout.use_property_split
        if compact:
            row = layout.row(align=True)
            row.prop(gp_settings, "fill_direction", text="", expand=True)
        else:
            layout.use_property_split = False
            row = layout.row(align=True)
            row.prop(gp_settings, "fill_direction", expand=True)

        row = layout.row(align=True)
        row.prop(gp_settings, "fill_factor")
        row = layout.row(align=True)
        row.prop(gp_settings, "dilate")
        row = layout.row(align=True)
        row.prop(brush, "size", text="Thickness")
        layout.use_property_split = use_property_split_prev
    elif grease_pencil_brush_type == 'ERASE':
        layout.prop(gp_settings, "eraser_mode", expand=True)
        if gp_settings.eraser_mode in {'HARD', 'SOFT'}:
            layout.use_property_split = False
            layout.prop(gp_settings, "use_keep_caps_eraser")
        layout.use_property_split = False
        layout.prop(gp_settings, "use_active_layer_only")
    elif grease_pencil_brush_type == 'TINT':
        layout.prop(gp_settings, "vertex_mode", text="Mode")
        layout.use_property_split = False
        layout.prop(gp_settings, "use_active_layer_only")


def brush_basic_gpencil_sculpt_settings(layout, _context, brush, *, compact=False):
    if brush is None:
        return
    gp_settings = brush.gpencil_settings
    if gp_settings is None:
        return
    tool = brush.gpencil_sculpt_brush_type

    row = layout.row(align=True)
    row.prop(brush, "size", slider=True)
    sub = row.row(align=True)
    sub.enabled = tool not in {'GRAB', 'CLONE'}
    sub.prop(gp_settings, "use_pressure", text="")

    row = layout.row(align=True)
    row.prop(brush, "strength", slider=True)
    row.prop(brush, "use_pressure_strength", text="")

    if compact:
        if tool in {'THICKNESS', 'STRENGTH', 'PINCH', 'TWIST'}:
            row.separator()
            row.prop(brush, "direction", expand=True, text="")
    else:
        use_property_split_prev = layout.use_property_split
        layout.use_property_split = False
        if tool in {'THICKNESS', 'STRENGTH'}: # BFA
            layout.row().prop(brush, "direction", expand=True)
        # BFA WIP - extra exposed options
        elif tool == 'PINCH':
            row = layout.row(align=True)
            row.prop_enum(brush, "direction", value="ADD", text="Pinch")
            row.prop_enum(brush, "direction", value="SUBTRACT", text="Inflate")
        elif tool == 'TWIST':
            row = layout.row(align=True)
            row.prop_enum(brush, "direction", value="ADD", text="CCW")
            row.prop_enum(brush, "direction", value="SUBTRACT", text="CW")
        layout.use_property_split = use_property_split_prev


# BFA - legacy
def brush_basic_gpencil_weight_settings(layout, _context, brush, *, compact=False):
    # BFA - order changed to be consistent with others

    if brush.gpencil_weight_brush_type in {'WEIGHT'}:
        layout.prop(brush, "weight", slider=True)

        layout.prop(
            brush, "direction", expand=True, text="" if compact else "Direction"
        )

    layout.prop(brush, "size", slider=True)

    row = layout.row(align=True)
    row.prop(brush, "strength", slider=True)
    row.prop(brush, "use_pressure_strength", text="")


def brush_basic_gpencil_vertex_settings(layout, context, brush, *, compact=False):
    del compact  # UNUSED.
    gp_settings = brush.gpencil_settings
    ups = context.tool_settings.unified_paint_settings
    brush_prop_owner = ups if ups.use_unified_size else brush

    # Brush details
    row = layout.row(align=True)
    row.prop(brush, "size", text="Radius")
    row.prop(brush, "use_pressure_size", text="", icon='STYLUS_PRESSURE')

    if brush.gpencil_vertex_brush_type in {'DRAW', 'BLUR', 'SMEAR'}:
        row = layout.row(align=True)
        row.prop(brush_prop_owner, "strength", slider=True)
        row.prop(brush, "use_pressure_strength", text="", icon='STYLUS_PRESSURE')
        row.prop(ups, "use_unified_strength", text="", icon='BRUSHES_ALL')

    if brush.gpencil_vertex_brush_type in {'DRAW', 'REPLACE'}:
        row = layout.row(align=True)
        row.prop(gp_settings, "vertex_mode", text="Mode")


def brush_basic_grease_pencil_weight_settings(layout, context, brush, *, compact=False):
    UnifiedPaintPanel.prop_unified(
        layout,
        context,
        brush,
        "size",
        pressure_name="use_pressure_size",
        unified_name="use_unified_size",
        text="Radius",
        slider=True,
        header=compact,
    )

    capabilities = brush.sculpt_capabilities
    pressure_name = "use_pressure_strength" if capabilities.has_strength_pressure else None
    UnifiedPaintPanel.prop_unified(
        layout,
        context,
        brush,
        "strength",
        pressure_name=pressure_name,
        unified_name="use_unified_strength",
        text="Strength",
        header=compact,
    )

    if brush.gpencil_weight_brush_type in {'WEIGHT'}:
        UnifiedPaintPanel.prop_unified(
            layout,
            context,
            brush,
            "weight",
            unified_name="use_unified_weight",
            text="Weight",
            slider=True,
            header=compact,
        )
        layout.prop(
            brush, "direction", expand=True, text="" if compact else "Direction"
        )


# BFA menu
class VIEW3D_PT_gpencil_brush_settings_radius(Panel):
    bl_space_type = 'VIEW_3D'
    bl_label = "Radius"
    bl_region_type = "HEADER"
    bl_ui_units_x = 10

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        tool_settings = context.scene.tool_settings
        gpencil_paint = tool_settings.gpencil_paint
        brush = gpencil_paint.brush
        gp_settings = brush.gpencil_settings

        layout.template_curve_mapping(
            gp_settings, "curve_sensitivity", brush=True, use_negative_slope=True
        )


# BFA menu
class VIEW3D_PT_gpencil_brush_settings_strength(Panel):
    bl_space_type = 'VIEW_3D'
    bl_label = "Strength"
    bl_region_type = "HEADER"
    bl_ui_units_x = 10

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        tool_settings = context.scene.tool_settings
        gpencil_paint = tool_settings.gpencil_paint
        brush = gpencil_paint.brush
        gp_settings = brush.gpencil_settings

        layout.template_curve_mapping(
            gp_settings, "curve_strength", brush=True, use_negative_slope=True
        )


def brush_basic_grease_pencil_vertex_settings(layout, context, brush, *, compact=False):
    UnifiedPaintPanel.prop_unified(
        layout,
        context,
        brush,
        "size",
        pressure_name="use_pressure_size",
        unified_name="use_unified_size",
        text="Radius",
        slider=True,
        header=compact,
    )

    if brush.gpencil_vertex_brush_type in {'DRAW', 'BLUR', 'SMEAR'}:
        UnifiedPaintPanel.prop_unified(
            layout,
            context,
            brush,
            "strength",
            pressure_name="use_pressure_strength",
            unified_name="use_unified_strength",
            text="Strength",
            header=compact,
        )

    gp_settings = brush.gpencil_settings
    if brush.gpencil_vertex_brush_type in {'DRAW', 'REPLACE'}:
        row = layout.row(align=True)
        row.prop(gp_settings, "vertex_mode", text="Mode")


classes = (
    VIEW3D_PT_brush_asset_shelf_filter,
    VIEW3D_MT_tools_projectpaint_clone,
    VIEW3D_PT_gpencil_brush_settings_radius,  # BFA menu
    VIEW3D_PT_gpencil_brush_settings_strength,  # BFA menu
)

if __name__ == "__main__":  # only for live edit.
    from bpy.utils import register_class

    for cls in classes:
        register_class(cls)
