"""Nutrition domain LLM tools: foods, recipes, meal_logs, weight_logs, macro_targets.

Each table gets a get_<table> getter and a set_<table> setter following the
shared contract defined in the refactor proposal.
"""

from datetime import UTC, date, datetime

from sqlmodel import Session, col, select

from app.macro_targets import get_active_macro_target, macro_target_to_dict
from app.macros import MACRO_FIELDS, compute_food_macros, sum_macros, zero_macros
from app.models import (
    Food,
    MacroTarget,
    MealItem,
    MealLog,
    Recipe,
    RecipeComponent,
    WeightLog,
)

from .shared import (
    apply_filters,
    apply_fuzzy_post_filter,
    apply_sort,
    error_response,
    getter_response,
    parse_date_val,
    record_to_dict,
    setter_response,
    utcnow,
)

SERVING_FIELDS = [f"{m}_per_serving" for m in MACRO_FIELDS]


# =====================================================================
#  Foods
# =====================================================================

GET_FOODS_DEF = {
    "type": "function",
    "function": {
        "name": "get_foods",
        "description": (
            "Get food records. Search by name, brand, source, or macros. "
            "Supports fuzzy name matching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "Filter fields. Operators per field: "
                        "id({eq,in}), name({eq,fuzzy,contains}), "
                        "brand({eq,fuzzy,contains}), source({eq})."
                    ),
                },
                "sort": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "direction": {
                                "type": "string",
                                "enum": ["asc", "desc"],
                            },
                        },
                    },
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                },
            },
        },
    },
}

SET_FOODS_DEF = {
    "type": "function",
    "function": {
        "name": "set_foods",
        "description": (
            "Create, update, or delete food records. "
            "Set name, brand, serving_size_grams, and 8 macro "
            "values per serving (calories, fat, saturated_fat, "
            "cholesterol, sodium, carbs, fiber, protein)."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "update",
                                    "upsert",
                                    "delete",
                                ],
                            },
                            "match": {
                                "type": "object",
                                "description": (
                                    "Filter to select records. "
                                    "Supports id, name (fuzzy)."
                                ),
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "Fields: name, brand, source, "
                                    "serving_size_grams, "
                                    "calories_per_serving, "
                                    "fat_per_serving, "
                                    "saturated_fat_per_serving, "
                                    "cholesterol_per_serving, "
                                    "sodium_per_serving, "
                                    "carbs_per_serving, "
                                    "fiber_per_serving, "
                                    "protein_per_serving."
                                ),
                            },
                        },
                    },
                },
            },
        },
    },
}


def _food_to_dict(f: Food) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "brand": f.brand,
        "serving_size_grams": f.serving_size_grams,
        "source": f.source,
        **{fld: getattr(f, fld) for fld in SERVING_FIELDS},
    }


def handle_get_foods(args: dict, session: Session) -> dict:
    filters = args.get("filters")
    stmt = select(Food)
    stmt, fuzzy_specs = apply_filters(
        stmt, Food, filters, fuzzy_fields=["name", "brand"]
    )
    stmt = apply_sort(
        stmt, Food, args.get("sort") or [{"field": "name", "direction": "asc"}]
    )
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)
    stmt = stmt.offset(offset).limit(limit)
    records = list(session.exec(stmt).all())

    match_info: list[dict] = []
    if fuzzy_specs:
        records, match_info = apply_fuzzy_post_filter(records, fuzzy_specs)

    return getter_response(
        "foods",
        [_food_to_dict(f) for f in records],
        filters_applied=filters,
        match_info=match_info or None,
    )


_FOOD_SETTABLE = {
    "name", "brand", "source", "serving_size_grams",
    *SERVING_FIELDS,
}


def handle_set_foods(args: dict, session: Session) -> dict:
    results = []
    created = deleted = changed = 0
    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        match_spec = change.get("match")

        if op == "create":
            name = set_fields.get("name")
            if not name:
                return error_response("foods", "name is required for create")
            food = Food(
                name=name,
                brand=set_fields.get("brand"),
                serving_size_grams=set_fields.get("serving_size_grams", 100),
                source=set_fields.get("source", "label"),
                **{
                    fld: set_fields.get(fld, 0)
                    for fld in SERVING_FIELDS
                },
            )
            session.add(food)
            session.flush()
            results.append(_food_to_dict(food))
            created += 1

        elif op in ("update", "upsert"):
            from .shared import resolve_match
            records, mi, err = resolve_match(
                session, Food, match_spec,
                fuzzy_fields=["name", "brand"],
            )
            if not records and op == "upsert":
                # Create
                food = Food(
                    name=set_fields.get("name", ""),
                    brand=set_fields.get("brand"),
                    serving_size_grams=set_fields.get(
                        "serving_size_grams", 100
                    ),
                    source=set_fields.get("source", "custom"),
                    **{
                        fld: set_fields.get(fld, 0)
                        for fld in SERVING_FIELDS
                    },
                )
                session.add(food)
                session.flush()
                results.append(_food_to_dict(food))
                created += 1
            elif not records:
                return error_response("foods", err or "No match")
            else:
                for rec in records:
                    for k, v in set_fields.items():
                        if k in _FOOD_SETTABLE:
                            setattr(rec, k, v)
                    session.add(rec)
                    results.append(_food_to_dict(rec))
                    changed += 1

        elif op == "delete":
            from .shared import resolve_match
            records, _, err = resolve_match(
                session, Food, match_spec,
                fuzzy_fields=["name"],
            )
            if not records:
                return error_response("foods", err or "No match")
            for rec in records:
                session.delete(rec)
                results.append({"id": rec.id, "name": rec.name})
                deleted += 1

    session.commit()
    return setter_response(
        "foods", args["changes"][0]["operation"] if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
    )


# =====================================================================
#  Recipes
# =====================================================================

GET_RECIPES_DEF = {
    "type": "function",
    "function": {
        "name": "get_recipes",
        "description": (
            "Get recipe records with components and computed macro "
            "totals. Search by name (fuzzy) or component food name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "id({eq,in}), name({eq,fuzzy,contains})."
                    ),
                },
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "components",
                            "components.food",
                            "macro_totals",
                        ],
                    },
                    "default": [
                        "components",
                        "components.food",
                        "macro_totals",
                    ],
                },
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
}

SET_RECIPES_DEF = {
    "type": "function",
    "function": {
        "name": "set_recipes",
        "description": (
            "Create, update, or delete recipes. Manage component "
            "lists through the components relation with mode=replace."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "update",
                                    "upsert",
                                    "delete",
                                ],
                            },
                            "match": {"type": "object"},
                            "set": {
                                "type": "object",
                                "description": "Fields: name.",
                            },
                            "relations": {
                                "type": "object",
                                "properties": {
                                    "components": {
                                        "type": "object",
                                        "properties": {
                                            "mode": {
                                                "type": "string",
                                                "enum": ["replace"],
                                            },
                                            "records": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "required": [
                                                        "food_id",
                                                        "amount_grams",
                                                    ],
                                                    "properties": {
                                                        "food_id": {
                                                            "type": "integer",
                                                        },
                                                        "amount_grams": {
                                                            "type": "number",
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def _build_recipe_dict(recipe: Recipe, session: Session) -> dict:
    components = session.exec(
        select(RecipeComponent).where(
            RecipeComponent.recipe_id == recipe.id
        )
    ).all()
    comp_details = []
    total_grams = 0.0
    for comp in components:
        food = session.get(Food, comp.food_id)
        if food:
            macros = compute_food_macros(food, comp.amount_grams)
            total_grams += comp.amount_grams
            comp_details.append({
                "id": comp.id,
                "food_id": comp.food_id,
                "food_name": food.name,
                "amount_grams": comp.amount_grams,
                **macros,
            })
    totals = sum_macros(comp_details)
    return {
        "id": recipe.id,
        "name": recipe.name,
        "created_at": recipe.created_at.isoformat()
        if isinstance(recipe.created_at, datetime) else str(recipe.created_at),
        "components": comp_details,
        "total_grams": round(total_grams, 1),
        **totals,
    }


def handle_get_recipes(args: dict, session: Session) -> dict:
    filters = args.get("filters")
    stmt = select(Recipe)
    stmt, fuzzy_specs = apply_filters(
        stmt, Recipe, filters, fuzzy_fields=["name"]
    )
    stmt = apply_sort(
        stmt, Recipe, args.get("sort") or [{"field": "name", "direction": "asc"}]
    )
    limit = args.get("limit", 25)
    stmt = stmt.limit(limit)
    records = list(session.exec(stmt).all())

    match_info: list[dict] = []
    if fuzzy_specs:
        records, match_info = apply_fuzzy_post_filter(records, fuzzy_specs)

    return getter_response(
        "recipes",
        [_build_recipe_dict(r, session) for r in records],
        filters_applied=filters,
        match_info=match_info or None,
    )


def _replace_components(
    recipe: Recipe,
    records: list[dict],
    session: Session,
) -> list[str]:
    """Replace all components for a recipe. Returns warnings."""
    warnings = []
    old = session.exec(
        select(RecipeComponent).where(
            RecipeComponent.recipe_id == recipe.id
        )
    ).all()
    for c in old:
        session.delete(c)
    for rec in records:
        food = session.get(Food, rec["food_id"])
        if not food:
            warnings.append(f"Food {rec['food_id']} not found")
            continue
        session.add(RecipeComponent(
            recipe_id=recipe.id,
            food_id=rec["food_id"],
            amount_grams=rec["amount_grams"],
        ))
    return warnings


def handle_set_recipes(args: dict, session: Session) -> dict:
    results = []
    created = deleted = changed = 0
    all_warnings: list[str] = []

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        match_spec = change.get("match")
        relations = change.get("relations", {})

        if op == "create":
            name = set_fields.get("name")
            if not name:
                return error_response("recipes", "name required")
            recipe = Recipe(name=name)
            session.add(recipe)
            session.flush()
            if relations.get("components"):
                w = _replace_components(
                    recipe,
                    relations["components"].get("records", []),
                    session,
                )
                all_warnings.extend(w)
            results.append(_build_recipe_dict(recipe, session))
            created += 1

        elif op in ("update", "upsert"):
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, Recipe, match_spec,
                fuzzy_fields=["name"],
            )
            if not recs and op == "upsert":
                recipe = Recipe(name=set_fields.get("name", ""))
                session.add(recipe)
                session.flush()
                if relations.get("components"):
                    w = _replace_components(
                        recipe,
                        relations["components"].get("records", []),
                        session,
                    )
                    all_warnings.extend(w)
                results.append(_build_recipe_dict(recipe, session))
                created += 1
            elif not recs:
                return error_response("recipes", err or "No match")
            else:
                for rec in recs:
                    if "name" in set_fields:
                        rec.name = set_fields["name"]
                    session.add(rec)
                    if relations.get("components"):
                        w = _replace_components(
                            rec,
                            relations["components"].get("records", []),
                            session,
                        )
                        all_warnings.extend(w)
                    results.append(_build_recipe_dict(rec, session))
                    changed += 1

        elif op == "delete":
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, Recipe, match_spec,
                fuzzy_fields=["name"],
            )
            if not recs:
                return error_response("recipes", err or "No match")
            for rec in recs:
                for c in session.exec(
                    select(RecipeComponent).where(
                        RecipeComponent.recipe_id == rec.id
                    )
                ).all():
                    session.delete(c)
                session.delete(rec)
                results.append({"id": rec.id, "name": rec.name})
                deleted += 1

    session.commit()
    return setter_response(
        "recipes",
        op if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
        warnings=all_warnings or None,
    )


# =====================================================================
#  Meal Logs
# =====================================================================

GET_MEAL_LOGS_DEF = {
    "type": "function",
    "function": {
        "name": "get_meal_logs",
        "description": (
            "Get meal log records with items, food/recipe details, "
            "and computed macro totals. Filter by date, meal_type, "
            "or search by food/recipe name in items."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "id({eq,in}), date({eq,gte,lte}), "
                        "meal_type({eq,in})."
                    ),
                },
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "items",
                            "items.food",
                            "items.recipe",
                            "macro_totals",
                        ],
                    },
                    "default": [
                        "items",
                        "items.food",
                        "items.recipe",
                        "macro_totals",
                    ],
                },
                "sort": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "direction": {
                                "type": "string",
                                "enum": ["asc", "desc"],
                            },
                        },
                    },
                },
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
}

SET_MEAL_LOGS_DEF = {
    "type": "function",
    "function": {
        "name": "set_meal_logs",
        "description": (
            "Create, update, or delete meal logs. Moving a meal "
            "to another date is just updating its date field. "
            "Manage items through the items relation (mode=replace)."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "update",
                                    "delete",
                                ],
                            },
                            "match": {
                                "type": "object",
                                "description": (
                                    "id({eq}), date({eq}), "
                                    "meal_type({eq})."
                                ),
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "Fields: date (YYYY-MM-DD), "
                                    "meal_type (breakfast/lunch/"
                                    "dinner/snack), notes."
                                ),
                            },
                            "relations": {
                                "type": "object",
                                "properties": {
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "mode": {
                                                "type": "string",
                                                "enum": [
                                                    "replace",
                                                    "append",
                                                ],
                                            },
                                            "records": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "food_id": {
                                                            "type": "integer",
                                                        },
                                                        "recipe_id": {
                                                            "type": "integer",
                                                        },
                                                        "amount_grams": {
                                                            "type": "number",
                                                        },
                                                    },
                                                    "required": [
                                                        "amount_grams",
                                                    ],
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "match": {
                    "type": "object",
                    "properties": {
                        "allow_multiple": {
                            "type": "boolean",
                            "default": False,
                        },
                    },
                },
            },
        },
    },
}


def _compute_item_macros(item: MealItem, session: Session) -> dict:
    """Compute macros for a single meal item."""
    if item.food_id:
        food = session.get(Food, item.food_id)
        if not food:
            return {
                "id": item.id, "food_id": item.food_id,
                "recipe_id": None, "name": "Unknown",
                "grams": item.amount_grams, **zero_macros(),
            }
        macros = compute_food_macros(food, item.amount_grams)
        return {
            "id": item.id, "food_id": item.food_id,
            "recipe_id": None, "name": food.name,
            "grams": item.amount_grams, **macros,
        }
    if item.recipe_id:
        recipe = session.get(Recipe, item.recipe_id)
        if not recipe:
            return {
                "id": item.id, "food_id": None,
                "recipe_id": item.recipe_id,
                "name": "Unknown recipe",
                "grams": item.amount_grams, **zero_macros(),
            }
        comps = session.exec(
            select(RecipeComponent).where(
                RecipeComponent.recipe_id == recipe.id
            )
        ).all()
        recipe_totals = {m: 0.0 for m in MACRO_FIELDS}
        recipe_grams = 0.0
        for comp in comps:
            food = session.get(Food, comp.food_id)
            if food:
                cm = compute_food_macros(food, comp.amount_grams)
                for m in MACRO_FIELDS:
                    recipe_totals[m] += cm[m]
                recipe_grams += comp.amount_grams
        scale = item.amount_grams / recipe_grams if recipe_grams > 0 else 0
        scaled = {m: round(recipe_totals[m] * scale, 1) for m in MACRO_FIELDS}
        return {
            "id": item.id, "food_id": None,
            "recipe_id": item.recipe_id, "name": recipe.name,
            "grams": item.amount_grams, **scaled,
        }
    return {
        "id": item.id, "food_id": None,
        "recipe_id": None, "name": "Empty",
        "grams": 0, **zero_macros(),
    }


def _build_meal_dict(meal: MealLog, session: Session) -> dict:
    items = session.exec(
        select(MealItem).where(MealItem.meal_log_id == meal.id)
    ).all()
    item_details = [_compute_item_macros(it, session) for it in items]
    totals = sum_macros(item_details)
    return {
        "id": meal.id,
        "date": str(meal.date),
        "meal_type": meal.meal_type,
        "notes": meal.notes,
        "created_at": meal.created_at.isoformat()
        if isinstance(meal.created_at, datetime) else str(meal.created_at),
        "items": item_details,
        **totals,
    }


def handle_get_meal_logs(args: dict, session: Session) -> dict:
    filters = args.get("filters")
    stmt = select(MealLog)
    stmt, fuzzy_specs = apply_filters(stmt, MealLog, filters)
    default_sort = [
        {"field": "date", "direction": "desc"},
        {"field": "created_at", "direction": "desc"},
    ]
    stmt = apply_sort(stmt, MealLog, args.get("sort") or default_sort)
    limit = args.get("limit", 25)
    stmt = stmt.limit(limit)
    records = list(session.exec(stmt).all())

    return getter_response(
        "meal_logs",
        [_build_meal_dict(m, session) for m in records],
        filters_applied=filters,
    )


def _write_meal_items(
    meal: MealLog,
    records: list[dict],
    session: Session,
    *,
    mode: str = "replace",
) -> list[str]:
    """Write meal items. mode=replace deletes existing first."""
    warnings = []
    if mode == "replace":
        old = session.exec(
            select(MealItem).where(MealItem.meal_log_id == meal.id)
        ).all()
        for i in old:
            session.delete(i)
    for rec in records:
        food_id = rec.get("food_id")
        recipe_id = rec.get("recipe_id")
        if not food_id and not recipe_id:
            warnings.append("Item missing food_id or recipe_id, skipped")
            continue
        session.add(MealItem(
            meal_log_id=meal.id,
            food_id=food_id,
            recipe_id=recipe_id,
            amount_grams=rec["amount_grams"],
        ))
    return warnings


def _items_fingerprint(records: list[dict]) -> frozenset:
    """Return a frozenset of (food_id, recipe_id, rounded_grams) for dedup."""
    return frozenset(
        (r.get("food_id"), r.get("recipe_id"), round(float(r.get("amount_grams", 0)), 1))
        for r in records
    )


def _db_items_fingerprint(items: list[MealItem]) -> frozenset:
    """Return a frozenset of (food_id, recipe_id, rounded_grams) from DB rows."""
    return frozenset(
        (i.food_id, i.recipe_id, round(i.amount_grams, 1))
        for i in items
    )


_DEDUP_WINDOW_SECONDS = 60


def _find_recent_duplicate(
    session: Session,
    d: date,
    meal_type: str,
    new_records: list[dict],
) -> "MealLog | None":
    """Return an existing meal if an identical one was created within the dedup window."""
    from datetime import timedelta
    cutoff = datetime.now(UTC) - timedelta(seconds=_DEDUP_WINDOW_SECONDS)
    recent = session.exec(
        select(MealLog).where(
            MealLog.date == d,
            MealLog.meal_type == meal_type,
            MealLog.created_at >= cutoff,
        )
    ).all()
    if not recent:
        return None
    new_fp = _items_fingerprint(new_records)
    for candidate in recent:
        existing_items = session.exec(
            select(MealItem).where(MealItem.meal_log_id == candidate.id)
        ).all()
        if _db_items_fingerprint(existing_items) == new_fp:
            return candidate
    return None


def handle_set_meal_logs(args: dict, session: Session) -> dict:
    results = []
    created = deleted = changed = 0
    all_warnings: list[str] = []
    allow_multi = (args.get("match") or {}).get("allow_multiple", False)

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        match_spec = change.get("match")
        relations = change.get("relations", {})

        if op == "create":
            d = parse_date_val(set_fields.get("date", str(date.today())))
            mt = set_fields.get("meal_type", "snack")
            new_records = (relations.get("items") or {}).get("records", [])

            # Dedup: return an existing identical meal created within the last minute
            dup = _find_recent_duplicate(session, d, mt, new_records)
            if dup:
                results.append(_build_meal_dict(dup, session))
                all_warnings.append(
                    f"Duplicate meal detected (id={dup.id}); returning existing record."
                )
                continue

            meal = MealLog(
                date=d,
                meal_type=mt,
                notes=set_fields.get("notes"),
            )
            session.add(meal)
            session.flush()
            if relations.get("items"):
                w = _write_meal_items(
                    meal,
                    new_records,
                    session,
                    mode=relations["items"].get("mode", "replace"),
                )
                all_warnings.extend(w)
            results.append(_build_meal_dict(meal, session))
            created += 1

        elif op == "update":
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, MealLog, match_spec,
                allow_multiple=allow_multi,
            )
            if not recs:
                return error_response("meal_logs", err or "No match")
            for rec in recs:
                if "date" in set_fields:
                    rec.date = parse_date_val(set_fields["date"])
                if "meal_type" in set_fields:
                    rec.meal_type = set_fields["meal_type"]
                if "notes" in set_fields:
                    rec.notes = set_fields["notes"]
                session.add(rec)
                if relations.get("items"):
                    w = _write_meal_items(
                        rec,
                        relations["items"].get("records", []),
                        session,
                        mode=relations["items"].get("mode", "replace"),
                    )
                    all_warnings.extend(w)
                results.append(_build_meal_dict(rec, session))
                changed += 1

        elif op == "delete":
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, MealLog, match_spec,
                allow_multiple=allow_multi,
            )
            if not recs:
                return error_response("meal_logs", err or "No match")
            for rec in recs:
                items = session.exec(
                    select(MealItem).where(
                        MealItem.meal_log_id == rec.id
                    )
                ).all()
                for i in items:
                    session.delete(i)
                session.delete(rec)
                results.append({"id": rec.id, "date": str(rec.date)})
                deleted += 1

    session.commit()
    return setter_response(
        "meal_logs",
        op if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
        warnings=all_warnings or None,
    )


# =====================================================================
#  Weight Logs
# =====================================================================

GET_WEIGHT_LOGS_DEF = {
    "type": "function",
    "function": {
        "name": "get_weight_logs",
        "description": (
            "Get weight log records. Filter by date range. "
            "Returns weight history in pounds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "logged_at({gte,lte}) with ISO timestamps."
                    ),
                },
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
}

SET_WEIGHT_LOGS_DEF = {
    "type": "function",
    "function": {
        "name": "set_weight_logs",
        "description": (
            "Log body weight. Create new weight entries in pounds. "
            "If the user gives kilograms, convert to pounds first "
            "(1 kg = 2.20462 lb)."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": ["create", "delete"],
                            },
                            "match": {
                                "type": "object",
                                "description": "id({eq}) for delete.",
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "weight_lb (number, required), "
                                    "logged_at (ISO timestamp, "
                                    "optional, defaults to now)."
                                ),
                                "properties": {
                                    "weight_lb": {"type": "number"},
                                    "logged_at": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def handle_get_weight_logs(args: dict, session: Session) -> dict:
    filters = args.get("filters")
    stmt = select(WeightLog)
    stmt, _ = apply_filters(stmt, WeightLog, filters)
    stmt = stmt.order_by(col(WeightLog.logged_at).desc())
    limit = args.get("limit", 30)
    stmt = stmt.limit(limit)
    records = list(session.exec(stmt).all())
    return getter_response(
        "weight_logs",
        [record_to_dict(r) for r in records],
        filters_applied=filters,
    )


def handle_set_weight_logs(args: dict, session: Session) -> dict:
    results = []
    created = deleted = 0
    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})

        if op == "create":
            logged_at_raw = set_fields.get("logged_at")
            if logged_at_raw:
                logged_at = datetime.fromisoformat(logged_at_raw)
                if logged_at.tzinfo is None:
                    logged_at = logged_at.replace(tzinfo=UTC)
            else:
                logged_at = utcnow()
            wl = WeightLog(
                weight_lb=float(set_fields["weight_lb"]),
                logged_at=logged_at,
            )
            session.add(wl)
            session.flush()
            results.append(record_to_dict(wl))
            created += 1

        elif op == "delete":
            match_spec = change.get("match")
            from .shared import resolve_match
            recs, _, err = resolve_match(session, WeightLog, match_spec)
            if not recs:
                return error_response("weight_logs", err or "No match")
            for rec in recs:
                session.delete(rec)
                results.append({"id": rec.id})
                deleted += 1

    session.commit()
    return setter_response(
        "weight_logs",
        args["changes"][0]["operation"] if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        deleted_count=deleted,
    )


# =====================================================================
#  Macro Targets
# =====================================================================

GET_MACRO_TARGETS_DEF = {
    "type": "function",
    "function": {
        "name": "get_macro_targets",
        "description": (
            "Get macro target records. This is a log table — each row records "
            "the targets effective from that day onward. The currently active "
            "targets are the row with the greatest day that is <= today. "
            "To find current targets, query without filters (or with day({lte}) "
            "set to today) and take the last record, or use day({eq}) with "
            "today's date to retrieve the active target directly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "day({eq,gte,lte}).",
                },
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
}

SET_MACRO_TARGETS_DEF = {
    "type": "function",
    "function": {
        "name": "set_macro_targets",
        "description": (
            "Create or update daily macro targets. Day is unique "
            "so upsert is natural. Set any combination of: "
            "calories, fat, saturated_fat, cholesterol, sodium, "
            "carbs, fiber, protein. Unspecified fields stay unchanged "
            "on update or default to 0 on create."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "update",
                                    "upsert",
                                    "delete",
                                ],
                            },
                            "match": {
                                "type": "object",
                                "description": "day({eq}).",
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "day (YYYY-MM-DD), calories, fat, "
                                    "saturated_fat, cholesterol, sodium, "
                                    "carbs, fiber, protein."
                                ),
                            },
                        },
                    },
                },
            },
        },
    },
}


def _macro_target_dict(t: MacroTarget) -> dict:
    return {
        "id": t.id,
        "day": str(t.day),
        **{m: float(getattr(t, m)) for m in MACRO_FIELDS},
    }


def handle_get_macro_targets(args: dict, session: Session) -> dict:
    filters = args.get("filters") or {}

    # Special case: single day lookup returns the active target
    day_filter = filters.get("day")
    if isinstance(day_filter, dict) and "eq" in day_filter:
        target_day = parse_date_val(day_filter["eq"])
        active = get_active_macro_target(target_day, session)
        return getter_response(
            "macro_targets",
            [active] if active else [],
            filters_applied=filters,
        )

    stmt = select(MacroTarget)
    stmt, _ = apply_filters(stmt, MacroTarget, filters)
    stmt = stmt.order_by(MacroTarget.day)
    limit = args.get("limit", 25)
    stmt = stmt.limit(limit)
    targets = list(session.exec(stmt).all())

    result = []
    for i, t in enumerate(targets):
        next_day = targets[i + 1].day if i + 1 < len(targets) else None
        d = macro_target_to_dict(t, next_day=next_day)
        result.append(d)

    return getter_response(
        "macro_targets", result, filters_applied=filters
    )


def handle_set_macro_targets(args: dict, session: Session) -> dict:
    results = []
    created = deleted = changed = 0

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        match_spec = change.get("match")

        day_raw = set_fields.get("day") or (
            match_spec.get("day", {}).get("eq") if match_spec else None
        )

        if op in ("create", "upsert"):
            if not day_raw:
                return error_response("macro_targets", "day is required")
            day = parse_date_val(day_raw)
            existing = session.exec(
                select(MacroTarget).where(MacroTarget.day == day)
            ).first()

            if existing and op == "create":
                return error_response(
                    "macro_targets",
                    f"Target for {day} already exists, use upsert",
                )
            if existing:
                for m in MACRO_FIELDS:
                    if m in set_fields and set_fields[m] is not None:
                        setattr(existing, m, float(set_fields[m]))
                session.add(existing)
                results.append(_macro_target_dict(existing))
                changed += 1
            else:
                target = MacroTarget(
                    day=day,
                    **{
                        m: float(set_fields.get(m, 0))
                        for m in MACRO_FIELDS
                    },
                )
                session.add(target)
                session.flush()
                results.append(_macro_target_dict(target))
                created += 1

        elif op == "update":
            if not match_spec:
                return error_response(
                    "macro_targets", "match required for update"
                )
            from .shared import resolve_match
            recs, _, err = resolve_match(session, MacroTarget, match_spec)
            if not recs:
                return error_response("macro_targets", err or "No match")
            for rec in recs:
                for m in MACRO_FIELDS:
                    if m in set_fields and set_fields[m] is not None:
                        setattr(rec, m, float(set_fields[m]))
                session.add(rec)
                results.append(_macro_target_dict(rec))
                changed += 1

        elif op == "delete":
            from .shared import resolve_match
            recs, _, err = resolve_match(session, MacroTarget, match_spec)
            if not recs:
                return error_response("macro_targets", err or "No match")
            for rec in recs:
                session.delete(rec)
                results.append({"id": rec.id, "day": str(rec.day)})
                deleted += 1

    session.commit()
    return setter_response(
        "macro_targets",
        op if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
    )


# =====================================================================
#  Tool registration
# =====================================================================

NUTRITION_TOOL_DEFINITIONS = [
    GET_FOODS_DEF, SET_FOODS_DEF,
    GET_RECIPES_DEF, SET_RECIPES_DEF,
    GET_MEAL_LOGS_DEF, SET_MEAL_LOGS_DEF,
    GET_WEIGHT_LOGS_DEF, SET_WEIGHT_LOGS_DEF,
    GET_MACRO_TARGETS_DEF, SET_MACRO_TARGETS_DEF,
]

NUTRITION_TOOL_HANDLERS = {
    "get_foods": handle_get_foods,
    "set_foods": handle_set_foods,
    "get_recipes": handle_get_recipes,
    "set_recipes": handle_set_recipes,
    "get_meal_logs": handle_get_meal_logs,
    "set_meal_logs": handle_set_meal_logs,
    "get_weight_logs": handle_get_weight_logs,
    "set_weight_logs": handle_set_weight_logs,
    "get_macro_targets": handle_get_macro_targets,
    "set_macro_targets": handle_set_macro_targets,
}
