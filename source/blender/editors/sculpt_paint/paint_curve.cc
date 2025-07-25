/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edsculpt
 */

#include <climits>
#include <cstring>

#include "MEM_guardedalloc.h"

#include "DNA_brush_types.h"
#include "DNA_object_types.h"
#include "DNA_screen_types.h"
#include "DNA_space_types.h"
#include "DNA_view3d_types.h"

#include "BLI_math_vector.h"

#include "BLT_translation.hh"

#include "BKE_brush.hh"
#include "BKE_context.hh"
#include "BKE_lib_id.hh"
#include "BKE_paint.hh"

#include "ED_paint.hh"
#include "ED_view3d.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "RNA_access.hh"
#include "RNA_define.hh"

#include "UI_view2d.hh"

#include "paint_intern.hh"

#define PAINT_CURVE_SELECT_THRESHOLD 40.0f
#define PAINT_CURVE_POINT_SELECT(pcp, i) (*(&pcp->bez.f1 + i) = SELECT)

bool paint_curve_poll(bContext *C)
{
  Object *ob = CTX_data_active_object(C);
  RegionView3D *rv3d = CTX_wm_region_view3d(C);
  SpaceImage *sima;

  if (rv3d && !(ob && ((ob->mode & (OB_MODE_ALL_PAINT | OB_MODE_SCULPT_CURVES)) != 0))) {
    return false;
  }

  sima = CTX_wm_space_image(C);

  if (sima && sima->mode != SI_MODE_PAINT) {
    return false;
  }

  Paint *paint = BKE_paint_get_active_from_context(C);
  Brush *brush = (paint) ? BKE_paint_brush(paint) : nullptr;

  if (brush && (brush->flag & BRUSH_CURVE)) {
    return true;
  }

  return false;
}

#define SEL_F1 (1 << 0)
#define SEL_F2 (1 << 1)
#define SEL_F3 (1 << 2)

/* returns 0, 1, or 2 in point according to handle 1, pivot or handle 2 */
static PaintCurvePoint *paintcurve_point_get_closest(
    PaintCurve *pc, const float pos[2], bool ignore_pivot, const float threshold, char *point)
{
  PaintCurvePoint *pcp, *closest = nullptr;
  int i;
  float closest_dist = threshold;

  for (i = 0, pcp = pc->points; i < pc->tot_points; i++, pcp++) {
    float dist[3];
    char point_sel = 0;

    dist[0] = len_manhattan_v2v2(pos, pcp->bez.vec[0]);
    dist[1] = len_manhattan_v2v2(pos, pcp->bez.vec[1]);
    dist[2] = len_manhattan_v2v2(pos, pcp->bez.vec[2]);

    if (dist[1] < closest_dist) {
      closest_dist = dist[1];
      point_sel = SEL_F2;
    }
    if (dist[0] < closest_dist) {
      closest_dist = dist[0];
      point_sel = SEL_F1;
    }
    if (dist[2] < closest_dist) {
      closest_dist = dist[2];
      point_sel = SEL_F3;
    }
    if (point_sel) {
      closest = pcp;
      if (point) {
        if (ignore_pivot && point_sel == SEL_F2) {
          point_sel = (dist[0] < dist[2]) ? SEL_F1 : SEL_F3;
        }
        *point = point_sel;
      }
    }
  }

  return closest;
}

static int paintcurve_point_co_index(char sel)
{
  char i = 0;
  while (sel != 1) {
    sel >>= 1;
    i++;
  }
  return i;
}

static char paintcurve_point_side_index(const BezTriple *bezt,
                                        const bool is_first,
                                        const char fallback)
{
  /* when matching, guess based on endpoint side */
  if (BEZT_ISSEL_ANY(bezt)) {
    if ((bezt->f1 & SELECT) == (bezt->f3 & SELECT)) {
      return is_first ? SEL_F1 : SEL_F3;
    }
    if (bezt->f1 & SELECT) {
      return SEL_F1;
    }
    if (bezt->f3 & SELECT) {
      return SEL_F3;
    }
    return fallback;
  }
  return 0;
}

/******************* Operators *********************************/

static PaintCurve *paintcurve_for_brush_add(Main *bmain, const char *name, const Brush *brush)
{
  PaintCurve *curve = BKE_paint_curve_add(bmain, name);
  BKE_id_move_to_same_lib(*bmain, curve->id, brush->id);
  return curve;
}

static wmOperatorStatus paintcurve_new_exec(bContext *C, wmOperator * /*op*/)
{
  Paint *paint = BKE_paint_get_active_from_context(C);
  Brush *brush = (paint) ? BKE_paint_brush(paint) : nullptr;
  Main *bmain = CTX_data_main(C);

  if (brush) {
    brush->paint_curve = paintcurve_for_brush_add(bmain, DATA_("PaintCurve"), brush);
    BKE_brush_tag_unsaved_changes(brush);
  }

  WM_event_add_notifier(C, NC_PAINTCURVE | NA_ADDED, nullptr);

  return OPERATOR_FINISHED;
}

void PAINTCURVE_OT_new(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Add New Paint Curve";
  ot->description = "Add new paint curve";
  ot->idname = "PAINTCURVE_OT_new";

  /* API callbacks. */
  ot->exec = paintcurve_new_exec;
  ot->poll = paint_curve_poll;

  /* flags */
  ot->flag = OPTYPE_REGISTER | OPTYPE_UNDO;
}

static void paintcurve_point_add(bContext *C, wmOperator *op, const int loc[2])
{
  Paint *paint = BKE_paint_get_active_from_context(C);
  Brush *br = BKE_paint_brush(paint);
  Main *bmain = CTX_data_main(C);
  wmWindow *window = CTX_wm_window(C);
  ARegion *region = CTX_wm_region(C);
  const float vec[3] = {float(loc[0]), float(loc[1]), 0.0f};

  PaintCurve *pc = br->paint_curve;
  if (!pc) {
    br->paint_curve = pc = paintcurve_for_brush_add(bmain, DATA_("PaintCurve"), br);
  }

  ED_paintcurve_undo_push_begin(op->type->name);

  PaintCurvePoint *pcp = MEM_malloc_arrayN<PaintCurvePoint>((pc->tot_points + 1),
                                                            "PaintCurvePoint");
  int add_index = pc->add_index;

  if (pc->points) {
    if (add_index > 0) {
      memcpy(pcp, pc->points, add_index * sizeof(PaintCurvePoint));
    }
    if (add_index < pc->tot_points) {
      memcpy(pcp + add_index + 1,
             pc->points + add_index,
             (pc->tot_points - add_index) * sizeof(PaintCurvePoint));
    }

    MEM_freeN(pc->points);
  }
  pc->points = pcp;
  pc->tot_points++;

  /* initialize new point */
  pcp[add_index] = PaintCurvePoint{};
  copy_v3_v3(pcp[add_index].bez.vec[0], vec);
  copy_v3_v3(pcp[add_index].bez.vec[1], vec);
  copy_v3_v3(pcp[add_index].bez.vec[2], vec);

  /* last step, clear selection from all bezier handles expect the next */
  for (int i = 0; i < pc->tot_points; i++) {
    pcp[i].bez.f1 = pcp[i].bez.f2 = pcp[i].bez.f3 = 0;
  }

  BKE_paint_curve_clamp_endpoint_add_index(pc, add_index);

  if (pc->add_index != 0) {
    pcp[add_index].bez.f3 = SELECT;
    pcp[add_index].bez.h2 = HD_ALIGN;
  }
  else {
    pcp[add_index].bez.f1 = SELECT;
    pcp[add_index].bez.h1 = HD_ALIGN;
  }

  ED_paintcurve_undo_push_end(C);
  BKE_brush_tag_unsaved_changes(br);

  WM_paint_cursor_tag_redraw(window, region);
}

static wmOperatorStatus paintcurve_add_point_invoke(bContext *C,
                                                    wmOperator *op,
                                                    const wmEvent *event)
{
  const int loc[2] = {event->mval[0], event->mval[1]};
  paintcurve_point_add(C, op, loc);
  RNA_int_set_array(op->ptr, "location", loc);
  return OPERATOR_FINISHED;
}

static wmOperatorStatus paintcurve_add_point_exec(bContext *C, wmOperator *op)
{
  int loc[2];

  if (RNA_struct_property_is_set(op->ptr, "location")) {
    RNA_int_get_array(op->ptr, "location", loc);
    paintcurve_point_add(C, op, loc);
    return OPERATOR_FINISHED;
  }

  return OPERATOR_CANCELLED;
}

void PAINTCURVE_OT_add_point(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Add New Paint Curve Point";
  ot->description = ot->name;
  ot->idname = "PAINTCURVE_OT_add_point";

  /* API callbacks. */
  ot->invoke = paintcurve_add_point_invoke;
  ot->exec = paintcurve_add_point_exec;
  ot->poll = paint_curve_poll;

  /* flags */
  ot->flag = OPTYPE_UNDO | OPTYPE_REGISTER;

  /* properties */
  RNA_def_int_vector(ot->srna,
                     "location",
                     2,
                     nullptr,
                     0,
                     SHRT_MAX,
                     "Location",
                     "Location of vertex in area space",
                     0,
                     SHRT_MAX);
}

static wmOperatorStatus paintcurve_delete_point_exec(bContext *C, wmOperator *op)
{
  Paint *paint = BKE_paint_get_active_from_context(C);
  Brush *br = BKE_paint_brush(paint);
  PaintCurve *pc;
  PaintCurvePoint *pcp;
  wmWindow *window = CTX_wm_window(C);
  ARegion *region = CTX_wm_region(C);
  int i;
  int tot_del = 0;
  pc = br->paint_curve;

  if (!pc || pc->tot_points == 0) {
    return OPERATOR_CANCELLED;
  }

  ED_paintcurve_undo_push_begin(op->type->name);

#define DELETE_TAG 2

  for (i = 0, pcp = pc->points; i < pc->tot_points; i++, pcp++) {
    if (BEZT_ISSEL_ANY(&pcp->bez)) {
      pcp->bez.f2 |= DELETE_TAG;
      tot_del++;
    }
  }

  if (tot_del > 0) {
    int j = 0;
    int new_tot = pc->tot_points - tot_del;
    PaintCurvePoint *points_new = nullptr;
    if (new_tot > 0) {
      points_new = MEM_malloc_arrayN<PaintCurvePoint>(new_tot, "PaintCurvePoint");
    }

    for (i = 0, pcp = pc->points; i < pc->tot_points; i++, pcp++) {
      if (!(pcp->bez.f2 & DELETE_TAG)) {
        points_new[j] = pc->points[i];

        if ((i + 1) == pc->add_index) {
          BKE_paint_curve_clamp_endpoint_add_index(pc, j);
        }
        j++;
      }
      else if ((i + 1) == pc->add_index) {
        /* prefer previous point */
        pc->add_index = j;
      }
    }
    MEM_freeN(pc->points);

    pc->points = points_new;
    pc->tot_points = new_tot;
  }

#undef DELETE_TAG

  ED_paintcurve_undo_push_end(C);
  BKE_brush_tag_unsaved_changes(br);

  WM_paint_cursor_tag_redraw(window, region);

  return OPERATOR_FINISHED;
}

void PAINTCURVE_OT_delete_point(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Remove Paint Curve Point";
  ot->description = ot->name;
  ot->idname = "PAINTCURVE_OT_delete_point";

  /* API callbacks. */
  ot->exec = paintcurve_delete_point_exec;
  ot->poll = paint_curve_poll;

  /* flags */
  ot->flag = OPTYPE_UNDO;
}

static bool paintcurve_point_select(
    bContext *C, wmOperator *op, const int loc[2], bool toggle, bool extend)
{
  wmWindow *window = CTX_wm_window(C);
  ARegion *region = CTX_wm_region(C);
  Paint *paint = BKE_paint_get_active_from_context(C);
  Brush *br = BKE_paint_brush(paint);
  PaintCurve *pc;
  int i;
  const float loc_fl[2] = {float(loc[0]), float(loc[1])};

  pc = br->paint_curve;

  if (!pc) {
    return false;
  }

  ED_paintcurve_undo_push_begin(op->type->name);

  if (toggle) {
    PaintCurvePoint *pcp;
    char select = 0;
    bool selected = false;

    pcp = pc->points;

    for (i = 0; i < pc->tot_points; i++) {
      if (pcp[i].bez.f1 || pcp[i].bez.f2 || pcp[i].bez.f3) {
        selected = true;
        break;
      }
    }

    if (!selected) {
      select = SELECT;
    }

    for (i = 0; i < pc->tot_points; i++) {
      pc->points[i].bez.f1 = pc->points[i].bez.f2 = pc->points[i].bez.f3 = select;
    }
  }
  else {
    PaintCurvePoint *pcp;
    char selflag;

    pcp = paintcurve_point_get_closest(pc, loc_fl, false, PAINT_CURVE_SELECT_THRESHOLD, &selflag);

    if (pcp) {
      BKE_paint_curve_clamp_endpoint_add_index(pc, pcp - pc->points);

      if (selflag == SEL_F2) {
        if (extend) {
          pcp->bez.f2 ^= SELECT;
        }
        else {
          pcp->bez.f2 |= SELECT;
        }
      }
      else if (selflag == SEL_F1) {
        if (extend) {
          pcp->bez.f1 ^= SELECT;
        }
        else {
          pcp->bez.f1 |= SELECT;
        }
      }
      else if (selflag == SEL_F3) {
        if (extend) {
          pcp->bez.f3 ^= SELECT;
        }
        else {
          pcp->bez.f3 |= SELECT;
        }
      }
    }

    /* clear selection for unselected points if not extending and if a point has been selected */
    if (!extend && pcp) {
      for (i = 0; i < pc->tot_points; i++) {
        pc->points[i].bez.f1 = pc->points[i].bez.f2 = pc->points[i].bez.f3 = 0;

        if ((pc->points + i) == pcp) {
          char index = paintcurve_point_co_index(selflag);
          PAINT_CURVE_POINT_SELECT(pcp, index);
        }
      }
    }

    if (!pcp) {
      ED_paintcurve_undo_push_end(C);
      return false;
    }
  }

  ED_paintcurve_undo_push_end(C);

  WM_paint_cursor_tag_redraw(window, region);

  return true;
}

static wmOperatorStatus paintcurve_select_point_invoke(bContext *C,
                                                       wmOperator *op,
                                                       const wmEvent *event)
{
  const int loc[2] = {event->mval[0], event->mval[1]};
  bool toggle = RNA_boolean_get(op->ptr, "toggle");
  bool extend = RNA_boolean_get(op->ptr, "extend");
  if (paintcurve_point_select(C, op, loc, toggle, extend)) {
    RNA_int_set_array(op->ptr, "location", loc);
    return OPERATOR_FINISHED;
  }
  return OPERATOR_CANCELLED;
}

static wmOperatorStatus paintcurve_select_point_exec(bContext *C, wmOperator *op)
{
  int loc[2];

  if (RNA_struct_property_is_set(op->ptr, "location")) {
    bool toggle = RNA_boolean_get(op->ptr, "toggle");
    bool extend = RNA_boolean_get(op->ptr, "extend");
    RNA_int_get_array(op->ptr, "location", loc);
    if (paintcurve_point_select(C, op, loc, toggle, extend)) {
      return OPERATOR_FINISHED;
    }
  }

  return OPERATOR_CANCELLED;
}

void PAINTCURVE_OT_select(wmOperatorType *ot)
{
  PropertyRNA *prop;

  /* identifiers */
  ot->name = "Select Paint Curve Point";
  ot->description = "Select a paint curve point";
  ot->idname = "PAINTCURVE_OT_select";

  /* API callbacks. */
  ot->invoke = paintcurve_select_point_invoke;
  ot->exec = paintcurve_select_point_exec;
  ot->poll = paint_curve_poll;

  /* flags */
  ot->flag = OPTYPE_UNDO | OPTYPE_REGISTER;

  /* properties */
  RNA_def_int_vector(ot->srna,
                     "location",
                     2,
                     nullptr,
                     0,
                     SHRT_MAX,
                     "Location",
                     "Location of vertex in area space",
                     0,
                     SHRT_MAX);
  prop = RNA_def_boolean(ot->srna, "toggle", false, "Toggle", "(De)select all");
  RNA_def_property_flag(prop, PROP_SKIP_SAVE);
  prop = RNA_def_boolean(ot->srna, "extend", false, "Extend", "Extend selection");
  RNA_def_property_flag(prop, PROP_SKIP_SAVE);
}

struct PointSlideData {
  PaintCurvePoint *pcp;
  char select;
  int initial_loc[2];
  float point_initial_loc[3][2];
  int event;
  bool align;
};

static wmOperatorStatus paintcurve_slide_invoke(bContext *C, wmOperator *op, const wmEvent *event)
{
  Paint *paint = BKE_paint_get_active_from_context(C);
  const float loc_fl[2] = {float(event->mval[0]), float(event->mval[1])};
  char select;
  int i;
  bool do_select = RNA_boolean_get(op->ptr, "select");
  bool align = RNA_boolean_get(op->ptr, "align");
  Brush *br = BKE_paint_brush(paint);
  PaintCurve *pc = br->paint_curve;
  PaintCurvePoint *pcp;

  if (!pc) {
    return OPERATOR_PASS_THROUGH;
  }

  if (do_select) {
    pcp = paintcurve_point_get_closest(pc, loc_fl, align, PAINT_CURVE_SELECT_THRESHOLD, &select);
  }
  else {
    pcp = nullptr;
    /* just find first selected point */
    for (i = 0; i < pc->tot_points; i++) {
      if ((select = paintcurve_point_side_index(&pc->points[i].bez, i == 0, SEL_F3))) {
        pcp = &pc->points[i];
        break;
      }
    }
  }

  if (pcp) {
    ARegion *region = CTX_wm_region(C);
    wmWindow *window = CTX_wm_window(C);
    PointSlideData *psd = MEM_mallocN<PointSlideData>("PointSlideData");
    copy_v2_v2_int(psd->initial_loc, event->mval);
    psd->event = event->type;
    psd->pcp = pcp;
    psd->select = paintcurve_point_co_index(select);
    for (i = 0; i < 3; i++) {
      copy_v2_v2(psd->point_initial_loc[i], pcp->bez.vec[i]);
    }
    psd->align = align;
    op->customdata = psd;

    /* first, clear all selection from points */
    for (i = 0; i < pc->tot_points; i++) {
      pc->points[i].bez.f1 = pc->points[i].bez.f3 = pc->points[i].bez.f2 = 0;
    }

    /* only select the active point */
    PAINT_CURVE_POINT_SELECT(pcp, psd->select);
    BKE_paint_curve_clamp_endpoint_add_index(pc, pcp - pc->points);
    BKE_brush_tag_unsaved_changes(br);

    WM_event_add_modal_handler(C, op);
    WM_paint_cursor_tag_redraw(window, region);
    return OPERATOR_RUNNING_MODAL;
  }

  return OPERATOR_PASS_THROUGH;
}

static wmOperatorStatus paintcurve_slide_modal(bContext *C, wmOperator *op, const wmEvent *event)
{
  PointSlideData *psd = static_cast<PointSlideData *>(op->customdata);

  if (event->type == psd->event && event->val == KM_RELEASE) {
    MEM_freeN(psd);
    ED_paintcurve_undo_push_begin(op->type->name);
    ED_paintcurve_undo_push_end(C);
    return OPERATOR_FINISHED;
  }

  switch (event->type) {
    case MOUSEMOVE: {
      ARegion *region = CTX_wm_region(C);
      wmWindow *window = CTX_wm_window(C);
      float diff[2] = {float(event->mval[0] - psd->initial_loc[0]),
                       float(event->mval[1] - psd->initial_loc[1])};
      if (psd->select == 1) {
        int i;
        for (i = 0; i < 3; i++) {
          add_v2_v2v2(psd->pcp->bez.vec[i], diff, psd->point_initial_loc[i]);
        }
      }
      else {
        add_v2_v2(diff, psd->point_initial_loc[psd->select]);
        copy_v2_v2(psd->pcp->bez.vec[psd->select], diff);

        if (psd->align) {
          char opposite = (psd->select == 0) ? 2 : 0;
          sub_v2_v2v2(diff, psd->pcp->bez.vec[1], psd->pcp->bez.vec[psd->select]);
          add_v2_v2v2(psd->pcp->bez.vec[opposite], psd->pcp->bez.vec[1], diff);
        }
      }
      WM_paint_cursor_tag_redraw(window, region);
      break;
    }
    default:
      break;
  }

  return OPERATOR_RUNNING_MODAL;
}

void PAINTCURVE_OT_slide(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Slide Paint Curve Point";
  ot->description = "Select and slide a paint curve point"; /* BFA */
  ot->idname = "PAINTCURVE_OT_slide";

  /* API callbacks. */
  ot->invoke = paintcurve_slide_invoke;
  ot->modal = paintcurve_slide_modal;
  ot->poll = paint_curve_poll;

  /* flags */
  ot->flag = OPTYPE_UNDO;

  /* properties */
  RNA_def_boolean(
      ot->srna, "align", false, "Align Handles", "Aligns opposite point handle during transform");
  RNA_def_boolean(
      ot->srna, "select", true, "Select", "Attempt to select a point handle before transform");
}

static wmOperatorStatus paintcurve_draw_exec(bContext *C, wmOperator * /*op*/)
{
  PaintMode mode = BKE_paintmode_get_active_from_context(C);
  const char *name;

  switch (mode) {
    case PaintMode::Texture2D:
    case PaintMode::Texture3D:
      name = "PAINT_OT_image_paint";
      break;
    case PaintMode::Weight:
      name = "PAINT_OT_weight_paint";
      break;
    case PaintMode::Vertex:
      name = "PAINT_OT_vertex_paint";
      break;
    case PaintMode::Sculpt:
      name = "SCULPT_OT_brush_stroke";
      break;
    case PaintMode::SculptCurves:
      name = "SCULPT_CURVES_OT_brush_stroke";
      break;
    case PaintMode::GPencil:
      name = "GREASE_PENCIL_OT_brush_stroke";
      break;
    default:
      return OPERATOR_PASS_THROUGH;
  }

  return WM_operator_name_call(C, name, WM_OP_INVOKE_DEFAULT, nullptr, nullptr);
}

void PAINTCURVE_OT_draw(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Draw Curve";
  ot->description = "Draw a curve"; /* BFA */
  ot->idname = "PAINTCURVE_OT_draw";

  /* API callbacks. */
  ot->exec = paintcurve_draw_exec;
  ot->poll = paint_curve_poll;

  /* flags */
  ot->flag = OPTYPE_UNDO;
}

static wmOperatorStatus paintcurve_cursor_invoke(bContext *C,
                                                 wmOperator * /*op*/,
                                                 const wmEvent *event)
{
  PaintMode mode = BKE_paintmode_get_active_from_context(C);

  switch (mode) {
    case PaintMode::Texture2D: {
      ARegion *region = CTX_wm_region(C);
      SpaceImage *sima = CTX_wm_space_image(C);
      float location[2];

      if (!sima) {
        return OPERATOR_CANCELLED;
      }

      UI_view2d_region_to_view(
          &region->v2d, event->mval[0], event->mval[1], &location[0], &location[1]);
      copy_v2_v2(sima->cursor, location);
      WM_event_add_notifier(C, NC_SPACE | ND_SPACE_IMAGE, nullptr);
      break;
    }
    default:
      ED_view3d_cursor3d_update(C, event->mval, true, V3D_CURSOR_ORIENT_VIEW);
      break;
  }

  return OPERATOR_FINISHED;
}

void PAINTCURVE_OT_cursor(wmOperatorType *ot)
{
  /* identifiers */
  ot->name = "Place Cursor";
  ot->description = "Place the cursor"; /* BFA */
  ot->idname = "PAINTCURVE_OT_cursor";

  /* API callbacks. */
  ot->invoke = paintcurve_cursor_invoke;
  ot->poll = paint_curve_poll;

  /* flags */
  ot->flag = 0;
}
