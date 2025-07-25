/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edasset
 *
 * Helpers to convert asset library references from and to enum values and RNA enums.
 * In some cases it's simply not possible to reference an asset library with
 * #AssetLibraryReferences. This API guarantees a safe translation to indices/enum values for as
 * long as there is no change in the order of registered custom asset libraries.
 */

#include "BLI_listbase.h"

#include "BKE_preferences.h"

#include "DNA_userdef_types.h"

#include "UI_resources.hh"

#include "RNA_define.hh"
#include "RNA_enum_types.hh"

#include "ED_asset_library.hh"

namespace blender::ed::asset {

int library_reference_to_enum_value(const AssetLibraryReference *library)
{
  /* Simple case: Predefined repository, just set the value. */
  if (library->type < ASSET_LIBRARY_CUSTOM) {
    return library->type;
  }

  /* Note that the path isn't checked for validity here. If an invalid library path is used, the
   * Asset Browser can give a nice hint on what's wrong. */
  const bUserAssetLibrary *user_library = BKE_preferences_asset_library_find_index(
      &U, library->custom_library_index);
  if (user_library) {
    return ASSET_LIBRARY_CUSTOM + library->custom_library_index;
  }

  return ASSET_LIBRARY_LOCAL;
}

AssetLibraryReference library_reference_from_enum_value(int value)
{
  AssetLibraryReference library;

  /* Simple case: Predefined repository, just set the value. */
  if (value < ASSET_LIBRARY_CUSTOM) {
    library.type = value;
    library.custom_library_index = -1;
    BLI_assert(ELEM(value, ASSET_LIBRARY_ALL, ASSET_LIBRARY_LOCAL, ASSET_LIBRARY_ESSENTIALS));
    return library;
  }

  const bUserAssetLibrary *user_library = BKE_preferences_asset_library_find_index(
      &U, value - ASSET_LIBRARY_CUSTOM);

  /* Note that there is no check if the path exists here. If an invalid library path is used, the
   * Asset Browser can give a nice hint on what's wrong. */
  if (!user_library) {
    library.type = ASSET_LIBRARY_ALL;
    library.custom_library_index = -1;
  }
  else {
    const bool is_valid = (user_library->name[0] && user_library->dirpath[0]);
    if (is_valid) {
      library.custom_library_index = value - ASSET_LIBRARY_CUSTOM;
      library.type = ASSET_LIBRARY_CUSTOM;
    }
  }
  return library;
}

static void rna_enum_add_custom_libraries(EnumPropertyItem **item, int *totitem)
{
  int i;
  LISTBASE_FOREACH_INDEX (bUserAssetLibrary *, user_library, &U.asset_libraries, i) {
    /* Note that the path itself isn't checked for validity here. If an invalid library path is
     * used, the Asset Browser can give a nice hint on what's wrong. */
    const bool is_valid = (user_library->name[0] && user_library->dirpath[0]);
    if (!is_valid) {
      continue;
    }

    AssetLibraryReference library_reference;
    library_reference.type = ASSET_LIBRARY_CUSTOM;
    library_reference.custom_library_index = i;

    const int enum_value = library_reference_to_enum_value(&library_reference);
    /* Use library path as description, it's a nice hint for users. */
    EnumPropertyItem tmp = {enum_value,
                            user_library->name,
                            ICON_FILE_FOLDER,
                            user_library->name,
                            user_library->dirpath}; /*BFA icon*/
    RNA_enum_item_add(item, totitem, &tmp);
  }
}

const EnumPropertyItem *library_reference_to_rna_enum_itemf(const bool include_readonly,
                                                            const bool include_current_file)
{
  EnumPropertyItem *item = nullptr;
  int totitem = 0;

  if (include_readonly) {
    BLI_assert(rna_enum_asset_library_type_items[0].value == ASSET_LIBRARY_ALL);
    RNA_enum_item_add(&item, &totitem, &rna_enum_asset_library_type_items[0]);
    RNA_enum_item_add_separator(&item, &totitem);
  }
  if (include_current_file) {
    BLI_assert(rna_enum_asset_library_type_items[1].value == ASSET_LIBRARY_LOCAL);
    RNA_enum_item_add(&item, &totitem, &rna_enum_asset_library_type_items[1]);
  }
  if (include_readonly) {
    BLI_assert(rna_enum_asset_library_type_items[2].value == ASSET_LIBRARY_ESSENTIALS);
    RNA_enum_item_add(&item, &totitem, &rna_enum_asset_library_type_items[2]);
  }

  /* Add separator if needed. */
  if (!BLI_listbase_is_empty(&U.asset_libraries) && (include_readonly || include_current_file)) {
    RNA_enum_item_add_separator(&item, &totitem);
  }
  rna_enum_add_custom_libraries(&item, &totitem);

  RNA_enum_item_end(&item, &totitem);
  return item;
}

const EnumPropertyItem *custom_libraries_rna_enum_itemf()
{
  EnumPropertyItem *item = nullptr;
  int totitem = 0;

  rna_enum_add_custom_libraries(&item, &totitem);

  RNA_enum_item_end(&item, &totitem);
  return item;
}

}  // namespace blender::ed::asset
